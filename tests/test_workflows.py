"""The workflow files are release artefacts — run their real lines locally.

``v7.0.0`` was tagged, pushed, and produced **no release**, because two lines
that only ever executed inside GitHub Actions were broken:

* ``release.yml`` ran ``sha256sum -c build/sr.pyz.sha256`` from the repository
  root, but the checksum file names the asset bare (``<digest>  sr.pyz``) and
  ``sha256sum -c`` resolves that name against the CWD — the step could only
  ever pass from ``build/``;
* a ``ci.yml`` gate grepped for ``ASSET = "sr.pyz"`` while the installer has
  always said ``ASSET="sr.pyz"`` — a line that could never pass.

Every local gate verified the same *properties* with its own commands, so the
drift stayed invisible until a runner met it.  ``tools/check_workflows.py`` now
executes the curated workflow lines verbatim; these tests are its regression
lock.  They run the two steps that actually failed in production, plus the
structural checks for the third v7.0.0 defect: the code required Python 3.10
(``dataclass(slots=True)``) while the metadata and the CI matrix declared 3.9.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "check_workflows.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

#: The verbatim steps only write inside the copy, so VCS metadata and local
#: caches are the sole things worth excluding.
COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage.xml",
    "node_modules",
    "npm/app",
    "preflight",
    ".ragraf",
    ".zwork",
)


def run_tool(*args: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke tools/check_workflows.py the same way `make workflow-check` does."""
    command = [sys.executable, str(TOOL)]
    if root is not None:
        command += ["--root", str(root)]
    command += list(args)
    return subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A disposable copy of the repository for steps that build into ``build/``."""
    target = tmp_path_factory.mktemp("workflow-checkout") / "sr-search-replace"
    shutil.copytree(REPO_ROOT, target, ignore=COPY_IGNORE, symlinks=True)
    return target


# ---------------------------------------------------------------------------
# Lint: every workflow parses and every run: block is valid bash
# ---------------------------------------------------------------------------


def test_every_run_block_is_valid_bash():
    completed = run_tool("--lint-only")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "pass bash -n" in completed.stdout


def test_curated_steps_resolve(checkout: Path):
    """A renamed/removed curated step must fail loudly, not vanish from the gate."""
    completed = run_tool("--list", root=checkout)
    assert completed.returncode == 0, completed.stderr
    keys = completed.stdout.splitlines()
    assert "release.yml/build/Verify the zipapp" in keys
    assert "ci.yml/installer/Checksum asset name agrees across build, updater and installer" in keys
    assert len(keys) >= 15, f"the curated set shrank unexpectedly: {keys}"


# ---------------------------------------------------------------------------
# Verbatim execution of the two steps that failed on the v7.0.0 runners
# ---------------------------------------------------------------------------


def test_installer_checksum_gate_passes_verbatim(checkout: Path):
    """Regression (v7.0.0, defect 2): the gate grepped for a spelling that the
    installer never had, so the job was red from the day it was merged."""
    completed = run_tool(
        "--only",
        "ci.yml/installer/Checksum asset name agrees across build, updater and installer",
        root=checkout,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "checksum asset name agrees" in completed.stdout


@pytest.mark.slow
@pytest.mark.skipif(
    shutil.which("sha256sum") is None,
    reason="sha256sum (coreutils) unavailable; CI exercises this on ubuntu-latest",
)
def test_release_zipapp_verify_passes_verbatim(checkout: Path):
    """Regression (v7.0.0, defect 1): the release died on `sha256sum -c`
    running from the wrong directory.  The fixed step verifies from build/ —
    and this test proves the line as written actually passes."""
    completed = run_tool(
        "--only",
        "release.yml/build/Build the zipapp",
        "--only",
        "release.yml/build/Verify the zipapp",
        root=checkout,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (checkout / "build" / "sr.pyz").is_file()
    checksum = (checkout / "build" / "sr.pyz.sha256").read_text(encoding="utf-8")
    digest, _, name = checksum.strip().partition("  ")
    assert re.fullmatch(r"[0-9a-f]{64}", digest), checksum
    assert name == "sr.pyz", "the released checksum must name the asset bare"


# ---------------------------------------------------------------------------
# Structural locks for the third v7.0.0 defect (declared vs. required Python)
# ---------------------------------------------------------------------------


def _declared_floor() -> tuple[int, int]:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)"', text)
    assert match, "pyproject.toml must declare requires-python = '>=X.Y'"
    return int(match.group(1)), int(match.group(2))


def _pair(text: str) -> tuple[int, int]:
    major, minor, *_ = text.split(".")
    return int(major), int(minor)


def test_ci_matrix_and_pinned_pythons_honour_the_floor():
    """Regression (v7.0.0, defect 3): both 3.9 legs died during collection
    because the package uses dataclass(slots=True) — a 3.10 feature."""
    floor = _declared_floor()
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))

    matrix = ci["jobs"]["test"]["strategy"]["matrix"]
    tested = [_pair(version) for version in matrix["python"]]
    tested += [_pair(entry["python"]) for entry in matrix.get("include", [])]
    assert all(version >= floor for version in tested), f"CI matrix {tested} tests below floor {floor}"
    assert floor in tested, f"the declared floor {floor} must itself be in the CI matrix"
    assert len(set(tested)) == len(tested), f"duplicate matrix legs: {tested}"

    for name, doc in (("ci.yml", ci), ("release.yml", release)):
        for job_id, job in doc["jobs"].items():
            for step in job.get("steps", []):
                pinned = (step.get("with") or {}).get("python-version")
                if isinstance(pinned, str) and re.fullmatch(r"\d+\.\d+", pinned):
                    assert _pair(pinned) >= floor, f"{name}: job {job_id} pins Python {pinned} < {floor}"

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    classifiers = re.findall(r"Programming Language :: Python :: (\d+\.\d+)", pyproject)
    assert classifiers, "pyproject.toml should list the supported Pythons as classifiers"
    assert all(_pair(item) >= floor for item in classifiers), f"classifier below floor: {classifiers}"


def test_every_launcher_declares_the_same_floor():
    """The installer, both launchers and `sr doctor` must agree with pyproject."""
    floor = _declared_floor()

    install_sh = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"sys\.version_info >= \((\d+), (\d+)\)", install_sh)
    assert match, "install.sh must gate on sys.version_info"
    assert (int(match.group(1)), int(match.group(2))) == floor

    sr_cli = (REPO_ROOT / "sr-cli").read_text(encoding="utf-8")
    match = re.search(r"SR_PYTHON_MIN:-([0-9]+)\.([0-9]+)", sr_cli)
    assert match, "sr-cli must declare SR_PYTHON_MIN"
    assert (int(match.group(1)), int(match.group(2))) == floor

    npm_bin = (REPO_ROOT / "npm" / "bin" / "sr").read_text(encoding="utf-8")
    match = re.search(r"MIN_PYTHON = \[(\d+), (\d+)\]", npm_bin)
    assert match, "npm/bin/sr must declare MIN_PYTHON"
    assert (int(match.group(1)), int(match.group(2))) == floor

    doctor = (REPO_ROOT / "sr" / "commands" / "doctor.py").read_text(encoding="utf-8")
    match = re.search(r"MIN_PYTHON = \((\d+), (\d+)\)", doctor)
    assert match, "doctor.py must declare MIN_PYTHON"
    assert (int(match.group(1)), int(match.group(2))) == floor


def test_ci_man_page_gate_is_date_insensitive():
    """The .TH line carries the generation date: a raw `diff -u` against the
    committed page passes only when the runner's calendar agrees with the day
    the page was generated.  The gate must use --check, which ignores .TH."""
    ci_text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "diff -u docs/man/sr.1" not in ci_text
    assert "build_manpage.py --output docs/man/sr.1 --check" in ci_text

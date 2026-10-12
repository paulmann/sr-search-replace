#!/usr/bin/env python3
"""Execute the hermetic steps of the CI/release workflows verbatim.

Why this exists
---------------
The ``v7.0.0`` tag produced no release because of two lines that only ever ran
inside GitHub Actions:

* ``release.yml`` verified the asset with ``sha256sum -c build/sr.pyz.sha256``
  from the repository root — but ``sha256sum -c`` resolves the *bare* file name
  stored inside the checksum file against the CWD, so the check could only ever
  pass from ``build/``;
* a ``ci.yml`` gate grepped for ``ASSET = "sr.pyz"`` while the installer has
  always said ``ASSET="sr.pyz"`` — a gate line that could never pass.

``make check`` verified the same *properties* with its own commands, so both
broken lines sailed through every local gate and failed on the runner.  This
tool closes that gap: it parses ``.github/workflows/*.yml``, syntax-checks every
``run:`` block, and executes a curated list of hermetic steps **exactly as
written**, from the repository root, with the workflows' own ``env:``.  If a
workflow line and the repository disagree, the disagreement now fails locally,
before the push — not after the tag.

Curation is explicit (:data:`VERBATIM`).  A curated step that is renamed,
removed, or grows a ``${{ … }}`` expression makes this tool fail on purpose:
the value is in running the real lines, not approximations of them.  Steps
that need the network (``pip install``, querying the GitHub API) or the Actions
runtime are deliberately *not* curated; CI still runs them for real.

Usage::

    python3 tools/check_workflows.py                # lint + run every curated step
    python3 tools/check_workflows.py --lint-only    # YAML + bash -n + curation
    python3 tools/check_workflows.py --list         # show the curated steps
    python3 tools/check_workflows.py --only 'ci.yml/installer/--help works without network'
    python3 tools/check_workflows.py --root DIR     # run against a checkout copy

Requires ``bash`` and PyYAML (a dev extra).  ``sha256sum`` (coreutils) is
needed by the checksum steps: on hosts without it (stock macOS) those steps are
skipped *loudly* — never silently — and CI exercises them on ubuntu-latest.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

try:
    import yaml
except ImportError:  # pragma: no cover - environment problem, reported in main
    yaml = None

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

GH_EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)

#: workflow file → job id → step names, executed in curation order.
#: Only hermetic steps belong here: no network, no ``${{ … }}``, no Actions
#: runtime.  A step listed here that no longer exists fails the tool — that is
#: the point; update the curation together with the workflow.
VERBATIM: dict[str, dict[str, list[str]]] = {
    "release.yml": {
        "build": [
            "Build the zipapp",
            "Verify the zipapp",
        ],
    },
    "ci.yml": {
        "artefacts": [
            "Build the zipapp",
            "Verify the zipapp end to end",
            "Run the smoke test *inside* the zipapp",
            "Exercise the zipapp on a scratch project",
            "Regenerate and compare the man page",
            "Regenerate and compare the completions",
            "Validate the bash completion script",
            "Regenerate and compare the documentation tables",
            "Version strings agree",
        ],
        "installer": [
            "Syntax and shellcheck",
            "--help works without network",
            "Checksum asset name agrees across build, updater and installer",
            "Updater refuses an unverifiable download",
        ],
    },
}

#: The workflows set these at the top level; the verbatim run must see them too.
WORKFLOW_ENV = {
    "PYTHONUNBUFFERED": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "SR_NO_UPDATE_CHECK": "1",
    "NO_COLOR": "1",
}

#: Steps that drive real write commands through the CLI.  sr refuses to modify
#: files as root (a bulk-edit tool running as uid 0 is a foot-gun), so these
#: steps only execute under an unprivileged account — exactly what a GitHub
#: runner provides (user `runner`).  Under root they are skipped *loudly*; the
#: preflight protocol mandates a non-root gate run before every release, where
#: they execute for real.  When curating a new step that writes through sr,
#: decide its root behaviour explicitly and list it here if needed.
ROOT_UNSAFE = frozenset(
    {
        "ci.yml/artefacts/Exercise the zipapp on a scratch project",
    }
)

CuratedStep = tuple[str, str, str, str, str]  # key, workflow, job, name, run text


def _die(message: str) -> NoReturn:
    print(f"check_workflows: error: {message}", file=sys.stderr)
    raise SystemExit(2)


def load_workflows(root: Path) -> dict[str, dict]:
    """Parse every workflow file; invalid YAML is a hard failure."""
    if yaml is None:
        _die("PyYAML is required — install the dev extras: pip install -e '.[dev]'")
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        _die(f"{directory}: no such directory (does --root point at a checkout?)")
    workflows: dict[str, dict] = {}
    for path in sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            _die(f"{path}: invalid YAML: {exc}")
        if not isinstance(doc, dict):
            _die(f"{path}: top level is not a mapping")
        workflows[path.name] = doc
    if not workflows:
        _die(f"{directory}: no workflow files found")
    return workflows


def iter_run_steps(doc: dict):
    """Yield ``(job_id, step_index, step)`` for every step with a run: block."""
    jobs = doc.get("jobs")
    for job_id, job in (jobs or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield job_id, index, step


def lint_run_blocks(workflows: dict[str, dict]) -> tuple[int, list[str]]:
    """``bash -n`` every run: block, with ${{ … }} neutralised.

    GitHub Actions substitutes ``${{ … }}`` *before* bash sees the script, so
    the expression is replaced with a literal token and the surrounding shell
    syntax is checked as written.
    """
    bash = shutil.which("bash")
    if bash is None:
        _die("bash is required to lint workflow run: blocks")
    problems: list[str] = []
    count = 0
    with tempfile.TemporaryDirectory(prefix="sr-wf-lint-") as tmp:
        script = Path(tmp) / "block.sh"
        for name, doc in workflows.items():
            for job_id, index, step in iter_run_steps(doc):
                count += 1
                text = GH_EXPRESSION.sub("actions_expression", step["run"])
                script.write_text(text, encoding="utf-8")
                completed = subprocess.run(
                    [bash, "--noprofile", "--norc", "-n", str(script)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    label = step.get("name") or f"step #{index + 1}"
                    problems.append(
                        f"{name} · {job_id} · {label}: bash -n failed:\n{completed.stderr.strip()}"
                    )
    return count, problems


def resolve_curated(workflows: dict[str, dict], only: list[str] | None) -> list[CuratedStep]:
    """Map :data:`VERBATIM` onto the parsed workflows; drift is a hard failure."""
    resolved: list[CuratedStep] = []
    problems: list[str] = []
    for workflow_name, jobs in VERBATIM.items():
        doc = workflows.get(workflow_name)
        if doc is None:
            problems.append(f"{workflow_name}: workflow file not found")
            continue
        for job_id, step_names in jobs.items():
            job = (doc.get("jobs") or {}).get(job_id)
            if not isinstance(job, dict):
                problems.append(f"{workflow_name}: job {job_id!r} not found")
                continue
            steps_by_name = {
                step.get("name"): step for step in (job.get("steps") or []) if isinstance(step, dict)
            }
            for step_name in step_names:
                where = f"{workflow_name} · {job_id} · {step_name!r}"
                step = steps_by_name.get(step_name)
                if step is None:
                    problems.append(
                        f"{where}: step not found — renamed or removed; "
                        "update VERBATIM in tools/check_workflows.py"
                    )
                    continue
                run_text = step.get("run")
                if not isinstance(run_text, str):
                    problems.append(f"{where}: step has no run: block any more")
                    continue
                if "${{" in run_text:
                    problems.append(
                        f"{where}: run block contains a ${{{{…}}}} expression and "
                        "cannot execute verbatim locally — re-curate"
                    )
                    continue
                resolved.append(
                    (f"{workflow_name}/{job_id}/{step_name}", workflow_name, job_id, step_name, run_text)
                )
    if problems:
        for problem in problems:
            print(f"check_workflows: curation drift: {problem}", file=sys.stderr)
        raise SystemExit(2)
    if only:
        wanted = set(only)
        known = {key for key, *_ in resolved}
        unknown = sorted(wanted - known)
        if unknown:
            for key in unknown:
                print(f"check_workflows: --only: unknown step {key!r}", file=sys.stderr)
            print(f"check_workflows: known steps: {', '.join(sorted(known))}", file=sys.stderr)
            raise SystemExit(2)
        resolved = [item for item in resolved if item[0] in wanted]
    return resolved


def _make_python_shim(shim_dir: Path) -> None:
    """The runners have ``python``; a local venv often only has ``python3``.

    A wrapper script, not a symlink: Python derives its module search prefix
    from the executable path, and a symlink in a temp directory loses
    site-packages (``python: No module named pytest``) on at least some builds.
    """
    shim = shim_dir / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)


def execute(root: Path, curated: list[CuratedStep]) -> int:
    """Run each curated step exactly as written; fail fast on the first error."""
    bash = shutil.which("bash")
    if bash is None:
        _die("bash is required to execute workflow steps")
    has_sha256sum = shutil.which("sha256sum") is not None
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    failures = 0
    skipped: list[str] = []
    with (
        tempfile.TemporaryDirectory(prefix="sr-wf-bin-") as shim_parent,
        tempfile.TemporaryDirectory(prefix="sr-wf-run-") as script_parent,
    ):
        shim_dir = Path(shim_parent)
        _make_python_shim(shim_dir)
        env = dict(os.environ)
        env.update(WORKFLOW_ENV)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', os.defpath)}"
        # Isolation: a stray PYTHONPATH/PYTHONHOME must not leak the developer's
        # interpreter or source tree into what is supposed to be a verbatim run.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        for number, (key, _wf, _job, _name, run_text) in enumerate(curated, start=1):
            print(f"\n=== [{number}/{len(curated)}] {key} ===", flush=True)
            if "sha256sum" in run_text and not has_sha256sum:
                skipped.append(key)
                print(f"SKIPPED: sha256sum (coreutils) is not available on this host — {key}")
                continue
            if is_root and key in ROOT_UNSAFE:
                skipped.append(f"{key} (running as root)")
                print(f"SKIPPED: running as root and sr refuses bulk writes as uid 0 — {key}")
                print("         GitHub runners execute it as the unprivileged `runner` user;")
                print("         re-run `make workflow-check` as non-root to exercise it locally.")
                continue
            script = Path(script_parent) / f"step-{number}.sh"
            script.write_text(run_text, encoding="utf-8")
            completed = subprocess.run(
                [bash, "--noprofile", "--norc", "-e", str(script)],
                cwd=root,
                env=env,
                check=False,
            )
            if completed.returncode != 0:
                failures += 1
                print(
                    f"\ncheck_workflows: FAILED (exit {completed.returncode}): {key}\n"
                    "The workflow line above and the repository disagree — this is\n"
                    "exactly the class of defect that killed the v7.0.0 release.",
                    file=sys.stderr,
                )
                break
    if skipped:
        print(
            "\ncheck_workflows: NOTE — skipped for missing host tools "
            f"(CI runs them on ubuntu-latest): {', '.join(skipped)}"
        )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Checkout to run against (default: the repository this tool lives in).",
    )
    parser.add_argument(
        "--lint-only", action="store_true", help="Parse YAML and bash -n every run: block; do not execute."
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_steps", help="List the curated steps and exit."
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="WORKFLOW/JOB/STEP",
        help="Execute only these curated steps (repeatable).",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    workflows = load_workflows(root)
    curated = resolve_curated(workflows, only=args.only)

    if args.list_steps:
        for key, *_ in curated:
            print(key)
        return 0

    blocks, problems = lint_run_blocks(workflows)
    for problem in problems:
        print(f"check_workflows: {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.lint_only:
        print(
            f"check_workflows: {blocks} run: blocks pass bash -n; "
            f"{len(curated)} curated steps resolve in {len(workflows)} workflows"
        )
        return 0

    print(f"check_workflows: {blocks} run: blocks pass bash -n")
    status = execute(root, curated)
    if status == 0:
        print(f"\ncheck_workflows: all {len(curated)} curated steps passed verbatim")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

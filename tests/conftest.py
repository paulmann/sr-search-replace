"""Pytest configuration and shared fixtures.

Fixtures live here (not in ``helpers.py``) because pytest only collects
fixtures from ``conftest.py`` or from explicitly registered plugins.

Rules for this suite:

* no network access — every remote interaction is faked
* no writes outside ``tmp_path`` — ``HOME``/``XDG_*`` are redirected per test
* no dependence on the current working directory
* no dependence on optional external tools (``file``, ``bc``, ``git``, ``jq``)
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make `helpers`/`cli_helpers` importable, and the in-repository `sr` package
# importable when the suite runs from a fresh clone without an editable install.
#
# `sr` is only prepended when it is not already importable: a copy of the tree
# (tarball, CI workspace, a reviewer's scratch clone) must test *itself* rather
# than silently picking up another checkout that happens to be on sys.path.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    import sr as _sr  # noqa: F401
except ImportError:
    sys.path.insert(0, str(REPO_ROOT))

from helpers import Captured


@pytest.fixture(autouse=True)
def not_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend to be an ordinary user.

    sr refuses mutating runs with euid 0 unless ``--allow-root`` is given.  The
    guard is tested explicitly in ``TestGuards``; every other test wants the
    normal path, and CI runners are frequently root.
    """
    if hasattr(os, "geteuid"):
        monkeypatch.setattr(os, "geteuid", lambda: 1000)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect HOME/XDG, silence colours and disable the update check."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("SR_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("SR_CONFIG", raising=False)
    for name in list(os.environ):
        if name.startswith("SR_") and name != "SR_NO_UPDATE_CHECK":
            monkeypatch.delenv(name, raising=False)
    yield home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A small, deterministic project tree with the usual noise around it."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()

    (root / "src" / "app.py").write_text('NAME = "old"\nprint(NAME)\n', encoding="utf-8")
    (root / "src" / "util.py").write_text("# old helper\nold = 1\n", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("# Old Guide\n\nold text here\n", encoding="utf-8")
    (root / "README.md").write_text("old readme\n", encoding="utf-8")
    (root / "notes.txt").write_text("keep old\n", encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text("old\n", encoding="utf-8")
    (root / ".git" / "config").write_text("old\n", encoding="utf-8")
    return root


@pytest.fixture
def captured() -> Captured:
    """An in-memory console for asserting on sr's output."""
    return Captured()

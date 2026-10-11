"""CLI helpers: run ``sr`` in-process with captured stdout/stderr.

Going through :func:`sr.cli.main` (rather than calling command functions
directly) exercises preprocessing, argparse, config merging, the console and the
exit codes in one shot — which is exactly the surface a user touches.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Run:
    """Result of one in-process CLI invocation."""

    argv: list[str]
    code: int
    out: str
    err: str
    cwd: Path

    @property
    def stdout_lines(self) -> list[str]:
        return self.out.splitlines()

    @property
    def stderr_lines(self) -> list[str]:
        return self.err.splitlines()

    def json(self) -> Any:
        """Parse stdout as the single JSON document sr promises in --json mode."""
        return json.loads(self.out)

    def __contains__(self, needle: str) -> bool:
        return needle in self.out or needle in self.err


def sr(*argv: str, cwd: Path | str | None = None, env: dict[str, str] | None = None) -> Run:
    """Run ``sr`` with the given arguments and capture everything."""
    from sr.cli import main

    working = Path(cwd) if cwd else Path.cwd()
    saved_cwd = Path.cwd()
    saved_env = dict(os.environ)
    out, err = io.StringIO(), io.StringIO()
    os.chdir(working)
    if env:
        os.environ.update(env)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
    finally:
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
    return Run(argv=list(argv), code=int(code), out=out.getvalue(), err=err.getvalue(), cwd=working)


def sr_json(*argv: str, cwd: Path | str | None = None, env: dict[str, str] | None = None) -> tuple[Any, Run]:
    """Run ``sr … --json`` and return ``(document, run)``."""
    arguments = list(argv)
    if "--json" not in arguments:
        arguments.append("--json")
    run = sr(*arguments, cwd=cwd, env=env)
    return run.json(), run


def write(root: Path, name: str, content: str | bytes = "old\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def tree(root: Path) -> list[str]:
    """Sorted, root-relative listing of every file (for assertions)."""
    return sorted(
        str(path.relative_to(root)).replace(os.sep, "/")
        for path in root.rglob("*")
        if path.is_file()
    )

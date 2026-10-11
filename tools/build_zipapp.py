#!/usr/bin/env python3
"""Build ``build/sr.pyz`` — the single-file release asset.

A zipapp is the right distribution shape for sr: it is one executable file, it
needs no installation step, it starts with the stdlib ``zipimport`` machinery,
and ``sr update`` can replace it atomically.  The archive carries

* the ``sr`` package (byte-compiled for a faster cold start),
* a ``__main__.py`` entry point,
* ``LICENSE`` and ``VERSION`` for provenance,

and nothing else — no tests, no docs, no build scripts.

Usage::

    python3 tools/build_zipapp.py                 # → build/sr.pyz
    python3 tools/build_zipapp.py --check         # verify an existing build
    python3 tools/build_zipapp.py --output /tmp/x # custom path

The script also writes ``build/sr.pyz.sha256`` in ``sha256sum`` format, which is
what ``sr update`` verifies against.
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "sr"
DEFAULT_OUTPUT = ROOT / "build" / "sr.pyz"

#: Files from the repository root that are bundled for provenance.
EXTRA_FILES = ("LICENSE",)

MAIN_TEMPLATE = '''\
"""Entry point of the sr zipapp.

Kept trivial on purpose: everything lives in the ``sr`` package so that the
zipapp and an installed distribution run exactly the same code.
"""

import sys

from sr.cli import main

if __name__ == "__main__":
    sys.exit(main())
'''


def _clean_staging(staging: Path) -> None:
    """Copy the package into the staging area, without dev noise."""
    target = staging / "sr"
    shutil.copytree(
        PACKAGE,
        target,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".mypy_cache", ".pytest_cache", ".ruff_cache"
        ),
        symlinks=False,
    )
    (staging / "__main__.py").write_text(MAIN_TEMPLATE, encoding="utf-8")
    for name in EXTRA_FILES:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, staging / name)
    version = _read_version()
    (staging / "VERSION").write_text(version + "\n", encoding="utf-8")


def _read_version() -> str:
    sys.path.insert(0, str(ROOT))
    try:
        from sr.version import __version__

        return __version__
    finally:
        sys.path.pop(0)


def _byte_compile(staging: Path, python: str) -> int:
    """Byte-compile the staged package; returns the number of files compiled.

    ``UNCHECKED_HASH`` invalidation is required for a zipapp: the .pyc must not
    try to stat a source file that is not on disk next to it.
    """
    from py_compile import PycInvalidationMode

    ok = compileall.compile_dir(
        str(staging / "sr"),
        ddir="sr",
        quiet=1,
        force=True,
        invalidation_mode=PycInvalidationMode.UNCHECKED_HASH,
    )
    if not ok:  # pragma: no cover - a syntax error would fail the tests first
        raise SystemExit("byte-compilation failed")
    return len(list((staging / "sr").rglob("*.pyc")))


def build(output: Path, *, interpreter: str = "/usr/bin/env python3", compress: bool = True,
          bytecode: bool = True, python: str | None = None) -> Path:
    """Create the zipapp at ``output`` and return its path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    python = python or sys.executable
    with tempfile.TemporaryDirectory(prefix="sr-zipapp-") as workspace:
        staging = Path(workspace) / "stage"
        staging.mkdir()
        _clean_staging(staging)
        compiled = _byte_compile(staging, python) if bytecode else 0
        if not bytecode:
            for stale in staging.rglob("__pycache__"):
                shutil.rmtree(stale, ignore_errors=True)
        zipapp.create_archive(
            staging,
            target=output,
            interpreter=interpreter,
            main=None,
            compressed=compress,
            filter=_archive_filter,
        )
        # zipapp only sets the user execute bit; a release asset must be
        # runnable by whoever installs it, regardless of the builder's umask.
        output.chmod(0o755)
    _write_checksum(output)
    size = output.stat().st_size
    print(f"built {output} ({size / 1024:.0f} KiB, {compiled} pre-compiled modules)")
    return output


def _archive_filter(path: Path) -> bool:
    """Exclude source files that have a compiled counterpart, and dev noise."""
    name = path.name
    if name in {".DS_Store"} or name.endswith((".pyo",)):
        return False
    if "__pycache__" in path.parts and name.endswith(".pyc"):
        return True
    return True


def _write_checksum(target: Path) -> Path:
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum_path = target.with_suffix(target.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    print(f"wrote {checksum_path}")
    return checksum_path


def verify(target: Path) -> int:
    """Smoke test the built archive: version, selftest, and a real replace."""
    python = sys.executable
    checks = [
        ([python, str(target), "--version"], "version"),
        ([python, str(target), "selftest"], "selftest"),
        ([python, str(target), "help", "topics"], "help"),
        ([python, str(target), "completions", "bash"], "completions"),
    ]
    failures = 0
    for argv, label in checks:
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        status = "ok" if completed.returncode == 0 else f"FAILED ({completed.returncode})"
        print(f"  {label:<12} {status}")
        if completed.returncode != 0:
            failures += 1
            print(completed.stderr[:2000])
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT, help="Where to write the archive.")
    parser.add_argument("--interpreter", default="/usr/bin/env python3", help="Shebang interpreter.")
    parser.add_argument("--no-compress", action="store_true", help="Store entries uncompressed (faster start).")
    parser.add_argument("--no-bytecode", action="store_true", help="Ship sources only, no .pyc files.")
    parser.add_argument("--check", action="store_true", help="Verify an existing archive instead of building.")
    parser.add_argument("--python", default=None, help="Interpreter used for byte-compilation.")
    args = parser.parse_args(argv)

    if args.check:
        if not args.output.is_file():
            print(f"{args.output} does not exist; build it first", file=sys.stderr)
            return 2
        return verify(args.output)

    build(
        args.output,
        interpreter=args.interpreter,
        compress=not args.no_compress,
        bytecode=not args.no_bytecode,
        python=args.python,
    )
    return verify(args.output)


if __name__ == "__main__":
    raise SystemExit(main())

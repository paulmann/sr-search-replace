#!/usr/bin/env python3
"""Keep every version string in the repository in sync with one source.

The source of truth is ``pyproject.toml``.  Running this tool rewrites

* ``sr/version.py``          (``__version__``)
* ``npm/package.json``       (``version``)
* ``CHANGELOG.md``           (adds an "Unreleased" heading when missing)

and, with ``--check``, verifies them without writing — which is what CI runs so
a release can never ship with a stale version.

Usage::

    python3 scripts/sync_version.py 7.1.0      # bump everything
    python3 scripts/sync_version.py --check     # verify consistency
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = ROOT / "pyproject.toml"
VERSION_PY = ROOT / "sr" / "version.py"
NPM_PACKAGE = ROOT / "npm" / "package.json"
CHANGELOG = ROOT / "CHANGELOG.md"

PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)
VERSION_PY_LINE = re.compile(r'^__version__\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$"
)


def read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = PYPROJECT_VERSION.search(text)
    if not match:
        raise SystemExit(f"{PYPROJECT}: no `version = \"…\"` line found")
    return match.group("version")


def validate(version: str) -> None:
    if not VERSION_RE.match(version):
        raise SystemExit(
            f"{version!r} is not a valid semantic version (expected MAJOR.MINOR.PATCH[-PRERELEASE])"
        )


def current_versions() -> dict[str, str]:
    found: dict[str, str] = {"pyproject.toml": read_pyproject_version()}
    text = VERSION_PY.read_text(encoding="utf-8")
    match = VERSION_PY_LINE.search(text)
    found["sr/version.py"] = match.group("version") if match else "(missing)"
    if NPM_PACKAGE.is_file():
        payload = json.loads(NPM_PACKAGE.read_text(encoding="utf-8"))
        found["npm/package.json"] = str(payload.get("version", "(missing)"))
    changelog = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.is_file() else ""
    heading = re.search(r"^##\s+\[?(?P<version>\d+\.\d+\.\d+[^]\s]*)", changelog, re.MULTILINE)
    found["CHANGELOG.md (latest)"] = heading.group("version") if heading else "(none)"
    return found


def write_version_py(version: str) -> bool:
    text = VERSION_PY.read_text(encoding="utf-8")
    updated, count = VERSION_PY_LINE.subn(f'__version__ = "{version}"', text, count=1)
    if count == 0:
        raise SystemExit(f"{VERSION_PY}: no __version__ assignment found")
    if updated != text:
        VERSION_PY.write_text(updated, encoding="utf-8")
        return True
    return False


def write_npm_package(version: str) -> bool:
    if not NPM_PACKAGE.is_file():
        return False
    payload = json.loads(NPM_PACKAGE.read_text(encoding="utf-8"))
    if payload.get("version") == version:
        return False
    payload["version"] = version
    NPM_PACKAGE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def ensure_changelog_heading(version: str) -> bool:
    if not CHANGELOG.is_file():
        return False
    text = CHANGELOG.read_text(encoding="utf-8")
    if re.search(rf"^##\s+\[?{re.escape(version)}\b", text, re.MULTILINE):
        return False
    marker = re.search(r"^##\s+", text, re.MULTILINE)
    heading = f"## [{version}] - unreleased\n\n- See `sr help compat` and the commit log.\n\n"
    if marker:
        text = text[: marker.start()] + heading + text[marker.start() :]
    else:
        text = text.rstrip() + "\n\n" + heading
    CHANGELOG.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", help="New version to write everywhere.")
    parser.add_argument("--check", action="store_true", help="Verify consistency without writing.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    versions = current_versions()
    canonical = versions["pyproject.toml"]

    if args.check:
        problems = [
            f"{name} says {value}, pyproject.toml says {canonical}"
            for name, value in versions.items()
            if name != "pyproject.toml" and value not in {canonical, "(none)"}
        ]
        if problems:
            for problem in problems:
                print(f"version mismatch: {problem}", file=sys.stderr)
            print("fix with: python3 scripts/sync_version.py --apply", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"all version strings agree on {canonical}")
        return 0

    if args.version:
        validate(args.version)
        canonical = args.version
        text = PYPROJECT.read_text(encoding="utf-8")
        updated = PYPROJECT_VERSION.sub(f'version = "{canonical}"', text, count=1)
        if updated != text:
            PYPROJECT.write_text(updated, encoding="utf-8")
            if not args.quiet:
                print(f"pyproject.toml → {canonical}")

    changed = []
    if write_version_py(canonical):
        changed.append("sr/version.py")
    if write_npm_package(canonical):
        changed.append("npm/package.json")
    if ensure_changelog_heading(canonical):
        changed.append("CHANGELOG.md")
    if not args.quiet:
        print(f"version {canonical}; updated: {', '.join(changed) if changed else 'nothing (already in sync)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

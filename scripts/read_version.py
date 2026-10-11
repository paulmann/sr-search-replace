#!/usr/bin/env python3
"""Print the version declared in ``pyproject.toml``.

The single place CI and the Makefile read the version from, so that no shell
one-liner has to embed a regular expression.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r'^version\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)


def read_version(path: Path | None = None) -> str:
    target = path or (ROOT / "pyproject.toml")
    text = target.read_text(encoding="utf-8")
    match = PATTERN.search(text)
    if not match:
        raise SystemExit(f"{target}: no `version = \"…\"` line found")
    return match.group("version")


if __name__ == "__main__":
    sys.stdout.write(read_version(Path(sys.argv[1]) if len(sys.argv) > 1 else None) + "\n")

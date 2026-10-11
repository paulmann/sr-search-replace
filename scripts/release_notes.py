#!/usr/bin/env python3
"""Render the GitHub Release body from ``CHANGELOG.md``.

The release notes are the CHANGELOG section for the version being released,
plus a fixed install/verify footer.  Deriving them (instead of pasting by hand)
means the notes can never disagree with the changelog, and the ``sr update``
output that links to them is always meaningful.

Usage::

    python3 scripts/release_notes.py                    # → stdout
    python3 scripts/release_notes.py --version 7.1.0
    python3 scripts/release_notes.py --output build/RELEASE_NOTES.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `scripts/` is not a package, so it is put on sys.path directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from read_version import read_version

FOOTER = """
---

**Install**

```sh
# standalone single file (no package manager)
curl -fsSL https://raw.githubusercontent.com/paulmann/sr-search-replace/main/scripts/install.sh | bash

# or
pipx install sr-search-replace
npm install -g sr-search-replace
```

**Verify the download**

```sh
sha256sum -c sr.pyz.sha256      # must print "OK"
sr selftest                     # end-to-end smoke test
sr doctor                       # environment and installation report
```

**Update an existing standalone install**

```sh
sr update --check
sr update
```

Full manual: `sr help` · Migration from 6.x: `sr help compat`
"""


def extract_section(changelog: str, version: str) -> str | None:
    """Return the ``## [X.Y.Z]`` section of the changelog, heading included."""
    pattern = re.compile(
        rf"^(?P<heading>##\s+\[?{re.escape(version)}\b[^\n]*)\n(?P<body>.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(changelog)
    if not match:
        return None
    return f"{match.group('heading')}\n{match.group('body').strip()}\n"


def render(version: str) -> str:
    changelog_path = ROOT / "CHANGELOG.md"
    text = changelog_path.read_text(encoding="utf-8") if changelog_path.is_file() else ""
    section = extract_section(text, version)
    header = f"# sr {version}\n"
    body = section or (
        f"No CHANGELOG entry for {version}.\n\n"
        f"See the commit log: "
        f"https://github.com/paulmann/sr-search-replace/commits/v{version}\n"
    )
    return f"{header}\n{body}\n{FOOTER}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=None, help="Version to render (default: pyproject.toml).")
    parser.add_argument("--output", "-o", default=None, help="Write here instead of stdout.")
    args = parser.parse_args(argv)

    version = args.version or read_version()
    notes = render(version)

    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(notes, encoding="utf-8")
        print(f"wrote {target} ({len(notes.splitlines())} lines)")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Convenience entry point for a source checkout: ``python3 sr.py …``.

The canonical launcher is ``./sr-cli`` (POSIX sh, finds a suitable interpreter);
this file exists so that ``python3 sr.py`` and ``./sr.py`` both work while
developing, without installing anything.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sr.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

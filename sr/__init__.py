"""sr — safe, auditable search & replace for source trees.

`sr` is a production-grade command line utility for bulk text replacement.
It is built around four ideas:

1. **Predictable matching** — literal by default, opt-in regular expressions,
   one engine for counting, previewing and replacing (no grep/sed drift).
2. **Auditability** — every mutating run produces a *session* with a manifest,
   a journal and byte-for-byte backups of the original files.
3. **Reversibility** — any session can be rolled back, and the rollback itself
   is journalled, so it can be rolled back too.
4. **Automation friendliness** — stable exit codes, ``--json`` output,
   ``--yes`` for CI and a machine readable ``--help=json``.

The package deliberately depends on the Python standard library only.
"""

from __future__ import annotations

from .version import __version__, version_tuple

__all__ = ["__version__", "version_tuple"]

#: Distribution name on PyPI / npm. Kept here so tooling has one source of truth.
PACKAGE_NAME = "sr-search-replace"

#: Short program name used in help output, man page and log prefixes.
PROGRAM_NAME = "sr"

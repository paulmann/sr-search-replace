"""Single source of truth for the version string.

The build tooling (``scripts/sync_version.py``) rewrites :data:`__version__`
from ``pyproject.toml`` / ``npm/package.json`` so that the three can never
drift apart.  Nothing else in the code base should hard-code a version.
"""

from __future__ import annotations

import re

__version__ = "7.0.0"

#: Semantic version scheme used for update checks and release assets.
_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def version_tuple(version: str | None = None) -> tuple[int, int, int, str]:
    """Parse ``version`` into a sortable ``(major, minor, patch, pre)`` tuple.

    Unparsable input degrades to ``(0, 0, 0, version)`` instead of raising:
    version comparison happens on the "is there an update?" path, which must
    never crash the program.
    """
    text = (version or __version__).strip().lstrip("vV")
    match = _VERSION_RE.match(text)
    if not match:
        return (0, 0, 0, text)
    pre = match.group("pre") or ""
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        pre,
    )


def compare_versions(left: str, right: str) -> int:
    """Return ``-1``/``0``/``1`` for ``left`` </==/> ``right``.

    Pre-release identifiers sort *before* the corresponding release, matching
    semver: ``7.0.0-rc.1 < 7.0.0``.
    """
    lt, rt = version_tuple(left), version_tuple(right)

    def key(item: tuple[int, int, int, str]) -> tuple:
        major, minor, patch, pre = item
        # An empty pre-release means "final" and must sort after any pre-release.
        return (major, minor, patch, 1 if not pre else 0, pre)

    lk, rk = key(lt), key(rt)
    if lk < rk:
        return -1
    if lk > rk:
        return 1
    return 0


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly newer release than ``current``."""
    return compare_versions(candidate, current) > 0

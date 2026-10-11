"""Unified diff rendering with optional colour.

Used by ``--diff``, ``sr diff`` and the interactive confirmations.  The output
is standard unified diff so it can be piped into ``patch -p1``, ``git apply``
or a code review tool.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .console import Console

_DIFF_CONTEXT_DEFAULT = 3


@dataclass(frozen=True, slots=True)
class DiffStats:
    """Per-file diff counters."""

    added: int = 0
    removed: int = 0

    @property
    def changed(self) -> int:
        return self.added + self.removed

    def as_dict(self) -> dict[str, int]:
        return {"added": self.added, "removed": self.removed, "changed": self.changed}


def unified_diff(
    original: Sequence[str],
    modified: Sequence[str],
    *,
    from_label: str = "a",
    to_label: str = "b",
    context: int = _DIFF_CONTEXT_DEFAULT,
) -> list[str]:
    """Line-based unified diff without trailing newlines on each element."""
    return list(
        difflib.unified_diff(
            list(original),
            list(modified),
            fromfile=from_label,
            tofile=to_label,
            n=max(0, int(context)),
            lineterm="",
        )
    )


def diff_stats(lines: Iterable[str]) -> DiffStats:
    added = removed = 0
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return DiffStats(added=added, removed=removed)


def render_diff(lines: Sequence[str], console: Console | None = None) -> str:
    """Colourise a unified diff when the console allows it."""
    if console is None or not console.color:
        return "\n".join(lines)
    out: list[str] = []
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            out.append(console.paint(line, "bold"))
        elif line.startswith("@@"):
            out.append(console.paint(line, "cyan"))
        elif line.startswith("+"):
            out.append(console.paint(line, "green"))
        elif line.startswith("-"):
            out.append(console.paint(line, "red"))
        elif line.startswith("\\"):
            out.append(console.paint(line, "dim"))
        else:
            out.append(line)
    return "\n".join(out)


def diff_texts(
    original: str,
    modified: str,
    *,
    from_label: str = "a",
    to_label: str = "b",
    context: int = _DIFF_CONTEXT_DEFAULT,
) -> list[str]:
    """Convenience wrapper over whole text buffers."""
    return unified_diff(
        original.splitlines(),
        modified.splitlines(),
        from_label=from_label,
        to_label=to_label,
        context=context,
    )


def labels_for(path: Path, *, backup: Path | None = None) -> tuple[str, str]:
    """``(from, to)`` labels for a diff of ``path`` against its backup copy."""
    current = str(path)
    previous = str(backup) if backup else f"{current}.orig"
    return previous, current

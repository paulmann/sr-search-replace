"""``sr find`` — read-only search with grep-like output.

The renderer is separate from the replace engine on purpose: search output has
its own requirements (context lines, highlighting, ``-l``/``-c`` modes, JSON
documents with per-match coordinates) that would only complicate the write
path.  Matching, decoding and discovery are shared, so ``find`` and ``replace``
can never disagree about what matches.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import discovery, textio
from .console import Console, Verbosity
from .pattern import Matcher
from .replace import ReplaceOptions
from .util import format_duration, format_size, plural

__all__ = ["FindFileResult", "FindMatch", "FindOptions", "FindResult", "run_find"]

#: Default context lines when ``-A``/``-B`` are not given.
DEFAULT_CONTEXT = 0


@dataclass(slots=True)
class FindOptions:
    """How a search should be rendered."""

    replace_options: ReplaceOptions
    count_only: bool = False
    list_files: bool = False
    before_context: int | None = None
    after_context: int | None = None
    highlight: bool | None = None
    max_matches_per_file: int | None = None


@dataclass(slots=True)
class FindMatch:
    """One match with its coordinates."""

    line: int
    column: int
    text: str
    replacement: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"line": self.line, "column": self.column, "text": self.text}


@dataclass(slots=True)
class FindFileResult:
    """All matches in one file."""

    path: Path
    rel_path: str
    matches: list[FindMatch] = field(default_factory=list)
    skipped: str = ""
    error: str = ""
    size: int = 0
    encoding: str = ""
    #: Decoded lines, kept for context rendering.  Not part of ``to_dict``.
    lines: list[str] = field(default_factory=list, repr=False)

    @property
    def count(self) -> int:
        return len(self.matches)

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "path": str(self.path),
            "rel_path": self.rel_path,
            "matches": self.count,
            "size": self.size,
            "encoding": self.encoding,
        }
        if self.skipped:
            data["skipped"] = self.skipped
        if self.error:
            data["error"] = self.error
        if self.matches:
            data["hits"] = [match.to_dict() for match in self.matches]
        return data


@dataclass(slots=True)
class FindResult:
    """Aggregate search result."""

    files: list[FindFileResult] = field(default_factory=list)
    scanned: int = 0
    matched_files: int = 0
    matches: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0
    root: Path = field(default_factory=Path)
    search: str = ""
    pattern_kind: str = "fixed"

    def to_dict(self, *, include_files: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "root": str(self.root),
            "search": self.search,
            "pattern_kind": self.pattern_kind,
            "duration_seconds": round(self.duration, 3),
            "totals": {
                "scanned": self.scanned,
                "matched_files": self.matched_files,
                "matches": self.matches,
                "skipped": self.skipped,
                "errors": len(self.errors),
            },
            "errors": self.errors,
            "warnings": self.warnings,
        }
        if include_files:
            data["files"] = [item.to_dict() for item in self.files if item.count or item.error]
        return data


def run_find(options: FindOptions, console: Console | None = None) -> FindResult:
    """Search and render.  Returns the aggregate result for JSON/exit-code use."""
    started = time.monotonic()
    replace_options = options.replace_options
    matcher = Matcher(replace_options.spec)
    root = replace_options.root
    result = FindResult(
        root=root,
        search=replace_options.spec.effective_search,
        pattern_kind=replace_options.spec.kind.value,
    )

    found = discovery.discover(replace_options.discovery_options)
    result.scanned = found.count
    if console:
        console.debug(
            f"discovery: {found.count} candidate(s), {len(found.skipped)} filtered, "
            f"{found.elapsed * 1000:.1f}ms"
        )

    highlight = _should_highlight(console, options)
    before = _context_lines(options.before_context, replace_options.diff_context, 0)
    after = _context_lines(options.after_context, replace_options.diff_context, 0)

    for path in found.files:
        file_result = _scan_file(path, root, matcher, replace_options, result)
        result.files.append(file_result)
        if file_result.error:
            result.errors.append(f"{file_result.rel_path}: {file_result.error}")
            continue
        if file_result.skipped:
            result.skipped += 1
            if console and console.verbosity >= Verbosity.VERBOSE:
                console.verbose(f"skipped {file_result.rel_path}: {file_result.skipped}")
            continue
        if not file_result.matches:
            continue
        result.matched_files += 1
        result.matches += file_result.count
        _render_file(file_result, console, options, highlight=highlight, before=before, after=after)

    for skipped in found.skipped:
        if console and console.verbosity >= Verbosity.DEBUG:
            console.debug(f"filtered {skipped.path}: {skipped.reason}")

    result.duration = time.monotonic() - started
    _render_totals(result, console, options)
    return result


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _scan_file(
    path: Path,
    root: Path,
    matcher: Matcher,
    options: ReplaceOptions,
    result: FindResult,
) -> FindFileResult:
    rel = _rel(path, root)
    file_result = FindFileResult(path=path, rel_path=rel)
    try:
        decoded = textio.read_text_file(
            path,
            encoding=options.encoding,
            errors=options.encoding_errors,  # type: ignore[arg-type]
        )
    except textio.UndecodableFileError as exc:
        file_result.skipped = str(exc)
        result.warnings.append(f"{rel}: {exc}")
        return file_result
    except OSError as exc:
        file_result.error = exc.strerror or str(exc)
        return file_result

    file_result.size = decoded.size
    file_result.encoding = decoded.encoding
    matches = matcher.find(decoded.text)
    limit = options.spec.max_count
    file_result.lines = decoded.text.split("\n")
    for match in matches.matches:
        file_result.matches.append(
            FindMatch(line=match.line, column=match.column, text=match.text, replacement=match.replacement)
        )
        if limit and len(file_result.matches) >= limit:
            break
    return file_result


def _rel(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(str(path), str(root))
    except ValueError:  # pragma: no cover
        return str(path)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _should_highlight(console: Console | None, options: FindOptions) -> bool:
    if options.highlight is not None:
        return bool(options.highlight) and console is not None and console.color
    return bool(console and console.color and not console.machine_readable)


def _context_lines(explicit: int | None, configured: int, fallback: int) -> int:
    if explicit is not None:
        return max(0, int(explicit))
    if configured and configured != 3:
        return max(0, int(configured))
    return fallback


def _render_file(
    file_result: FindFileResult,
    console: Console | None,
    options: FindOptions,
    *,
    highlight: bool,
    before: int,
    after: int,
) -> None:
    if console is None or console.json_mode:
        return
    if options.list_files:
        console.write(file_result.rel_path)
        return
    if options.count_only:
        console.write(f"{file_result.rel_path}:{file_result.count}")
        return

    lines: Sequence[str] = file_result.lines
    matcher = Matcher(options.replace_options.spec)
    match_lines: dict[int, int] = {}
    for match in file_result.matches:
        match_lines.setdefault(match.line, match.column)

    wanted: dict[int, bool] = {}
    for line_no in match_lines:
        wanted[line_no] = True
        for context_line in range(max(1, line_no - before), min(len(lines), line_no + after) + 1):
            wanted.setdefault(context_line, False)

    if before or after:
        console.write(console.paint("--", "dim"))
    previous = None
    for line_no in sorted(wanted):
        if previous is not None and line_no > previous + 1 and (before or after):
            console.write(console.paint("--", "dim"))
        previous = line_no
        text = lines[line_no - 1] if line_no - 1 < len(lines) else ""
        is_match = wanted[line_no]
        if is_match and highlight:
            text = matcher.highlight(text)
        separator = ":" if is_match else "-"
        column = match_lines.get(line_no, 1)
        console.write(
            f"{console.paint(file_result.rel_path, 'magenta')}"
            f"{console.paint(':', 'dim')}"
            f"{console.paint(str(line_no), 'green')}"
            f"{console.paint(':', 'dim')}"
            f"{console.paint(str(column), 'yellow')}"
            f"{separator} {text}"
        )


def _render_totals(result: FindResult, console: Console | None, options: FindOptions) -> None:
    if console is None or console.machine_readable:
        return
    if console.verbosity < Verbosity.NORMAL:
        return
    if options.count_only:
        console.write(f"total:{result.matches}")
        return
    if options.list_files:
        console.write("")
        console.info(
            f"{plural(result.matched_files, 'file')} with matches, "
            f"{plural(result.matches, 'match')} in {format_duration(result.duration)}"
        )
        return
    console.write("")
    console.info(
        f"{plural(result.matches, 'match')} in {plural(result.matched_files, 'file')} "
        f"(scanned {result.scanned}, skipped {result.skipped}) in {format_duration(result.duration)}"
    )
    if result.scanned and console.verbosity >= Verbosity.VERBOSE:
        console.verbose(f"searched {format_size(sum(item.size for item in result.files))} of text")

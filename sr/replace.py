"""The replace engine: scan → confirm → write → record.

This module is the heart of sr.  It knows nothing about ``argparse``: it
receives a fully resolved :class:`ReplaceOptions` and returns a
:class:`ReplaceSummary`.  That separation is what makes the engine testable
without spawning processes, and lets ``find``, ``diff`` and ``replace`` share
one code path.

The run happens in two phases:

**scan** — every candidate file is read, decoded and matched.  Nothing is
written.  This produces the authoritative change count, which is what the
``--confirm-threshold`` prompt, the progress bar and the summary report.

**write** — the scanned changes are applied, journaled into the session and
(optionally) diffed.  Keeping the phases apart means a crash or a Ctrl-C during
the write phase can never leave a half-scanned, half-reported run behind.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable

from . import discovery, textio
from .atomic import atomic_write_bytes
from .console import Console, ProgressBar, Verbosity
from .diffview import DiffStats, diff_stats, diff_texts, render_diff
from .errors import UsageError
from .exit_codes import ExitCode
from .pattern import Matcher, PatternKind, PatternSpec, build_spec
from .session import Session, SessionInfo, prune_sessions, store_for
from .util import format_size, relative_to_cwd

__all__ = [
    "FileOutcome",
    "Hunk",
    "RenameAction",
    "ReplaceOptions",
    "ReplaceSummary",
    "build_matcher",
    "run_replace",
    "scan_only",
]

#: ``(path, hunks) -> one boolean per hunk``; used by ``--interactive``.
HunkFilter = Callable[[Path, Sequence["Hunk"]], Sequence[bool]]
#: ``(files_that_would_change, total_replacements) -> proceed?``
ConfirmFn = Callable[[int, int], bool]
#: ``(index, total, outcome) -> None``; called after each file is written.
ProgressFn = Callable[[int, int, "FileOutcome"], None]


@dataclass(slots=True)
class Hunk:
    """Matches that are close enough to be reviewed as one unit.

    ``before``/``after`` cover only the *changed* lines (``first_line`` to
    ``last_line``); the surrounding context lives in ``context_before`` and
    ``context_after`` so a renderer can show it without guessing offsets.
    """

    first_line: int
    last_line: int
    match_indexes: list[int] = field(default_factory=list)
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)
    replacements: int = 0

    # Backwards-compatible aliases: the window bounds.
    @property
    def start_line(self) -> int:
        return self.first_line

    @property
    def end_line(self) -> int:
        return self.last_line


@dataclass(slots=True)
class FileOutcome:
    """What happened (or would happen) to one file."""

    path: Path
    rel_path: str = ""
    status: str = "unchanged"  # changed | unchanged | skipped | error | dry-run
    matches: int = 0
    replacements: int = 0
    reason: str = ""
    error: str = ""
    encoding: str = ""
    size_before: int = 0
    size_after: int = 0
    diff: list[str] = field(default_factory=list)
    diff_stats: DiffStats = field(default_factory=DiffStats)
    written_to: Path | None = None
    renamed_to: Path | None = None
    binary: bool = False
    # Filled during the scan phase, consumed by the write phase.
    _decoded: textio.DecodedFile | None = field(default=None, repr=False)
    _new_text: str | None = field(default=None, repr=False)
    _new_bytes: bytes | None = field(default=None, repr=False)
    _old_bytes: bytes | None = field(default=None, repr=False)

    @property
    def changed(self) -> bool:
        return self.status in {"changed", "dry-run"}

    def to_dict(self, *, include_diff: bool = False) -> dict[str, object]:
        data: dict[str, object] = {
            "path": str(self.path),
            "rel_path": self.rel_path,
            "status": self.status,
            "matches": self.matches,
            "replacements": self.replacements,
            "encoding": self.encoding,
            "size_before": self.size_before,
            "size_after": self.size_after,
        }
        if self.reason:
            data["reason"] = self.reason
        if self.error:
            data["error"] = self.error
        if self.written_to:
            data["written_to"] = str(self.written_to)
        if self.renamed_to:
            data["renamed_to"] = str(self.renamed_to)
        if self.binary:
            data["binary"] = True
        if self.diff_stats.changed:
            data["diff"] = self.diff_stats.as_dict()
        if include_diff and self.diff:
            data["patch"] = "\n".join(self.diff)
        return data


@dataclass(slots=True)
class RenameAction:
    """One planned or executed rename."""

    source: Path
    target: Path
    rel_path: str = ""
    executed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "from": str(self.source),
            "to": str(self.target),
            "executed": self.executed,
            "error": self.error,
        }


@dataclass(slots=True)
class ReplaceOptions:
    """Everything the engine needs, fully resolved before the call."""

    spec: PatternSpec
    discovery_options: discovery.DiscoveryOptions
    root: Path = field(default_factory=Path)

    apply_changes: bool = True
    write_mode: str = "in-place"  # in-place | stdout | dir
    output_dir: Path | None = None
    suffix: str = ""

    backup: bool = True
    backup_dir: Path | None = None
    keep: int | None = 20
    keep_age: float | None = None
    lock: bool = True

    encoding: str | None = None
    encoding_errors: str = "strict"
    line_endings: str = "preserve"
    preserve_mtime: bool = False
    atomic: bool = True

    show_diff: bool = False
    diff_context: int = 3
    interactive: bool = False
    hunk_filter: HunkFilter | None = None
    confirm: ConfirmFn | None = None
    confirm_threshold: int = 0
    progress: ProgressFn | None = None

    rename: bool = False
    rename_only: bool = False
    rename_directories: bool = False
    force: bool = False

    command: Sequence[str] = field(default_factory=tuple)
    version: str = ""
    console: Console | None = None
    max_read_bytes: int | None = None

    def validate(self) -> None:
        if self.write_mode == "dir" and not self.output_dir:
            raise UsageError(
                "--write=dir requires --output-dir",
                hint="example: sr replace foo bar --write=dir --output-dir=out",
            )
        if self.output_dir and self.write_mode != "dir":
            raise UsageError(
                "--output-dir requires --write=dir",
                hint="or drop --output-dir to edit files in place",
            )
        if self.rename_only and self.write_mode == "stdout":
            raise UsageError("--rename-only cannot be combined with --write=stdout")
        if self.interactive and not self.apply_changes:
            raise UsageError("--interactive has no effect on a read-only run")
        if self.write_mode == "stdout":
            self.backup = False
        if self.rename_only:
            self.backup = self.backup  # renames are journalled too
        if self.rename_only:
            # Renaming is about *names*; the content of the file is irrelevant,
            # so binary detection must not filter the candidate out.
            self.discovery_options.text_only = False
        if not self.root:
            roots = self.discovery_options.roots
            self.root = Path(roots[0]) if roots else Path.cwd()


@dataclass(slots=True)
class ReplaceSummary:
    """Aggregate result of a run — the single object commands report on."""

    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    duration: float = 0.0
    scanned: int = 0
    changed: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    matches: int = 0
    replacements: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    dry_run: bool = False
    session: SessionInfo | None = None
    outcomes: list[FileOutcome] = field(default_factory=list)
    skipped_files: list[discovery.SkippedFile] = field(default_factory=list)
    renames: list[RenameAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    root: Path = field(default_factory=Path)
    search: str = ""
    replacement: str = ""
    pattern_kind: str = PatternKind.FIXED.value
    exit_hint: int = 0
    version: str = ""

    @property
    def totals(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
            "matches": self.matches,
            "replacements": self.replacements,
            "renamed": sum(1 for item in self.renames if item.executed),
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
        }

    def to_dict(self, *, include_files: bool = False, include_diff: bool = False) -> dict[str, object]:
        data: dict[str, object] = {
            "version": self.version,
            "dry_run": self.dry_run,
            "root": str(self.root),
            "search": self.search,
            "replacement": self.replacement,
            "pattern_kind": self.pattern_kind,
            "duration_seconds": round(self.duration, 3),
            "totals": self.totals,
            "session": self.session.to_dict() if self.session else None,
            "renames": [item.to_dict() for item in self.renames],
            "errors": self.errors,
            "warnings": self.warnings,
            "exit_code": self.exit_hint,
        }
        if include_files:
            data["files"] = [outcome.to_dict(include_diff=include_diff) for outcome in self.outcomes]
        return data


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def scan_only(options: ReplaceOptions) -> ReplaceSummary:
    """Run the pipeline read-only (used by ``sr find`` and ``sr diff``)."""
    clone = ReplaceOptions(**{item.name: getattr(options, item.name) for item in fields(options)})
    clone.apply_changes = False
    clone.backup = False
    return run_replace(clone)


def run_replace(options: ReplaceOptions) -> ReplaceSummary:
    """Execute a search/replace (and optional rename) run."""
    options.validate()
    started = time.monotonic()
    console = options.console
    matcher = Matcher(options.spec)
    summary = ReplaceSummary(
        dry_run=not options.apply_changes,
        root=options.root,
        search=options.spec.effective_search,
        replacement=options.spec.effective_replacement,
        pattern_kind=options.spec.kind.value,
        version=options.version,
    )

    found = discovery.discover(options.discovery_options)
    summary.scanned = found.count
    summary.skipped_files = list(found.skipped)
    if console:
        console.debug(
            f"discovery: {found.count} candidate(s), {len(found.skipped)} skipped, "
            f"{found.directories_scanned} directories in {found.elapsed * 1000:.1f}ms"
        )
    wants_rename = options.rename or options.rename_only
    if found.count == 0 and not wants_rename:
        summary.errors.append(
            "no files matched the target selection"
            + (f"; {len(found.skipped)} candidate(s) were filtered out" if found.skipped else "")
        )
        return _finish(summary, options, started, ExitCode.NO_MATCH)

    # -- phase 1: scan -------------------------------------------------------
    outcomes = [
        _scan_file(path, matcher, options, summary) for path in found.files
    ]
    pending = [item for item in outcomes if item.status == "dry-run"]
    summary.outcomes = outcomes
    for item in outcomes:
        summary.matches += item.matches
    summary.replacements = sum(item.replacements for item in pending)

    if console and console.verbosity >= Verbosity.DEBUG:
        for item in outcomes:
            console.debug(f"scan {item.rel_path}: {item.status} ({item.replacements})")

    # -- confirmation --------------------------------------------------------
    if (
        options.apply_changes
        and options.confirm_threshold
        and options.confirm is not None
        and len(pending) > options.confirm_threshold
    ):
        if not options.confirm(len(pending), summary.replacements):
            summary.errors.append("aborted by the user at the confirmation prompt")
            summary.changed = 0
            summary.replacements = 0
            return _finish(summary, options, started, ExitCode.ERROR)

    # -- phase 2: write ------------------------------------------------------
    # A session is only meaningful when something reversible happens: an
    # in-place write, or a rename.  `--write=dir` and `--write=stdout` leave
    # every original untouched, so creating a store for them would be noise.
    session: Session | None = None
    journalled = options.write_mode == "in-place" or options.rename_only
    if options.apply_changes and options.backup and journalled and (pending or options.rename_only):
        store = store_for(options.root, options.backup_dir)
        session = store.create(
            kind="rename" if options.rename_only else "replace",
            root=options.root,
            command=list(options.command),
            search=options.spec.effective_search,
            replacement=options.spec.effective_replacement,
            pattern_kind=options.spec.kind.value,
            options=_serialisable_options(options),
            version=options.version,
        )
        session.open(lock=options.lock)

    bar: ProgressBar | None = None
    if console and options.progress is None and not console.machine_readable:
        bar = ProgressBar(console, len(pending), label="writing")

    try:
        for index, outcome in enumerate(pending, start=1):
            if not options.apply_changes:
                outcome.status = "dry-run"
            else:
                _write_outcome(outcome, options, summary, session)
            if bar:
                bar.update(1, suffix=f"{outcome.replacements} repl")
            if options.progress:
                options.progress(index, len(pending), outcome)
            _report_file(console, outcome, options)
    except KeyboardInterrupt:
        summary.errors.append("interrupted by the user")
        if session is not None:
            session.abort()
            session = None
        raise
    finally:
        if bar:
            bar.close()

    if options.rename or options.rename_only:
        candidates: list[Path] = list(found.files)
        if options.rename_directories and options.discovery_options.collect_dirs:
            candidates.extend(found.directories)
        summary.renames = _plan_renames(candidates, matcher, options)
        _execute_renames(summary.renames, options, session, summary)

    for outcome in outcomes:
        _tally(summary, outcome)

    if session is not None:
        try:
            summary.session = session.close(complete=not summary.errors)
        except OSError as exc:  # pragma: no cover - disk full
            summary.errors.append(f"cannot finalise the session: {exc}")
        _prune(options, console, summary)

    hint = ExitCode.NO_MATCH
    if summary.failed or summary.errors:
        hint = ExitCode.ERROR
    elif summary.changed or any(item.executed for item in summary.renames) or (not options.apply_changes and summary.replacements):
        hint = ExitCode.OK
    return _finish(summary, options, started, hint)


def _finish(summary: ReplaceSummary, options: ReplaceOptions, started: float, code: ExitCode) -> ReplaceSummary:
    summary.duration = time.monotonic() - started
    summary.finished_at = time.time()
    summary.exit_hint = int(code)
    return summary


# ---------------------------------------------------------------------------
# Phase 1 — scanning
# ---------------------------------------------------------------------------


def _scan_file(
    path: Path, matcher: Matcher, options: ReplaceOptions, summary: ReplaceSummary
) -> FileOutcome:
    rel_path = _rel(path, options.root)
    outcome = FileOutcome(path=path, rel_path=rel_path)

    if options.rename_only:
        outcome.reason = "rename-only"
        return outcome

    try:
        raw = path.read_bytes()
    except OSError as exc:
        outcome.status = "error"
        outcome.error = f"cannot read: {exc.strerror or exc}"
        summary.errors.append(f"{rel_path}: {outcome.error}")
        return outcome

    outcome.size_before = len(raw)
    summary.bytes_read += len(raw)

    # A file that sniffs as binary is skipped unless the user asked for binary
    # processing.  The sniff runs on the bytes we already read, so this costs
    # nothing extra and — importantly — makes the engine's behaviour identical
    # to discovery's, whichever way the file list was produced.
    #
    # An explicit --encoding is a stronger statement than the sniff: the user is
    # telling us how to interpret the bytes, so we honour it and let the decoder
    # decide (it will still refuse rather than write mojibake).
    if textio.sniff_binary(raw):
        outcome.binary = True
        if not options.discovery_options.text_only and not options.encoding:
            outcome.status = "skipped"
            outcome.reason = "binary content (use --binary to force)"
            return outcome

    try:
        decoded = textio.read_text_file(
            path,
            encoding=options.encoding,
            errors=options.encoding_errors,  # type: ignore[arg-type]
        )
    except textio.UndecodableFileError as exc:
        outcome.status = "skipped"
        outcome.reason = str(exc)
        summary.warnings.append(f"{rel_path}: skipped ({exc})")
        return outcome
    except OSError as exc:
        outcome.status = "error"
        outcome.error = exc.strerror or str(exc)
        summary.errors.append(f"{rel_path}: {outcome.error}")
        return outcome

    outcome.encoding = decoded.encoding
    original_text = decoded.text
    result = matcher.find(original_text)
    outcome.matches = result.count + result.skipped_out_of_range
    if not result.matches:
        return outcome

    matches = list(result.matches)
    if options.interactive and options.hunk_filter is not None and options.apply_changes:
        hunks = build_hunks(original_text, matches, options.diff_context)
        decisions = [bool(item) for item in options.hunk_filter(path, hunks)]
        keep = _expand_decisions(hunks, decisions, len(matches))
        matches = [match for match, flag in zip(matches, keep) if flag]
        if not matches:
            outcome.reason = "all hunks rejected"
            return outcome

    new_text = _apply_matches(original_text, matches)
    if new_text == original_text:
        outcome.reason = "replacement is identical to the match"
        return outcome

    if options.line_endings == "lf":
        decoded.line_ending = "\n"
    elif options.line_endings == "crlf":
        decoded.line_ending = "\r\n"

    old_bytes = _encode_original(decoded, original_text, raw)
    new_bytes = decoded.encode(new_text)
    if new_bytes == old_bytes:
        outcome.reason = "byte-identical after encoding"
        return outcome

    outcome.replacements = len(matches)
    outcome.status = "dry-run"  # provisional; becomes "changed" when written
    outcome._decoded = decoded
    outcome._new_text = new_text
    outcome._new_bytes = new_bytes
    outcome._old_bytes = old_bytes

    if options.show_diff or options.interactive or not options.apply_changes:
        outcome.diff = diff_texts(
            original_text,
            new_text,
            from_label=f"a/{rel_path}",
            to_label=f"b/{rel_path}",
            context=options.diff_context,
        )
        outcome.diff_stats = diff_stats(outcome.diff)
    return outcome


def _encode_original(decoded: textio.DecodedFile, original_text: str, raw: bytes) -> bytes:
    """Bytes to store in the session.

    When the line-ending mode is ``preserve`` the file we read *is* the backup,
    so ``raw`` is used directly and the round trip is exact even for encodings
    with quirks.
    """
    if decoded.line_ending == textio.detect_line_ending(raw)[0] and not decoded.bom:
        return raw
    return decoded.encode(original_text)


def _apply_matches(text: str, matches: Sequence) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        pieces.append(text[cursor : match.start])
        pieces.append(match.replacement)
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def build_hunks(text: str, matches: Sequence, context: int) -> list[Hunk]:
    """Group matches into reviewable hunks and render their before/after.

    Two matches belong to the same hunk when they are at most ``context`` lines
    apart, which is exactly how ``diff`` decides to merge hunks — so the
    interactive view and ``--diff`` output agree.
    """
    lines = text.split("\n")
    hunks: list[Hunk] = []
    current: Hunk | None = None

    for index, match in enumerate(matches):
        line_no = match.line
        if current is None or line_no > current.last_line + max(1, context):
            if current is not None:
                hunks.append(current)
            current = Hunk(first_line=line_no, last_line=line_no, match_indexes=[index], replacements=1)
        else:
            current.match_indexes.append(index)
            current.last_line = max(current.last_line, line_no)
            current.replacements += 1
    if current is not None:
        hunks.append(current)

    for hunk in hunks:
        lo = max(1, hunk.first_line - context)
        hi = min(len(lines), hunk.last_line + context)
        hunk.context_before = lines[lo - 1 : hunk.first_line - 1]
        hunk.context_after = lines[hunk.last_line : hi]
        hunk.before = lines[hunk.first_line - 1 : hunk.last_line]
        hunk.after = _render_after(lines, matches, hunk)
    return hunks


def _render_after(lines: Sequence[str], matches: Sequence, hunk: Hunk) -> list[str]:
    """Apply only this hunk's matches to its own line window.

    ``MatchInfo`` offsets are absolute within the whole buffer, so they are
    converted to line-relative offsets here — otherwise the rendered "after"
    text would splice at the wrong column.
    """
    window = list(lines[hunk.first_line - 1 : hunk.last_line])
    line_start = 0
    for line in lines[: hunk.first_line - 1]:
        line_start += len(line) + 1

    by_line: dict[int, list] = {}
    for index in hunk.match_indexes:
        match = matches[index]
        by_line.setdefault(match.line, []).append(match)

    cursor_offset = line_start
    for offset, line in enumerate(window):
        line_matches = by_line.get(hunk.first_line + offset)
        if line_matches:
            pieces: list[str] = []
            cursor = 0
            for match in sorted(line_matches, key=lambda item: item.start):
                start = match.start - cursor_offset
                end = match.end - cursor_offset
                if start < cursor:
                    continue
                pieces.append(line[cursor:start])
                pieces.append(match.replacement)
                cursor = max(cursor, end)
            pieces.append(line[cursor:])
            window[offset] = "".join(pieces)
        cursor_offset += len(line) + 1
    return window


def _expand_decisions(hunks: Sequence[Hunk], decisions: Sequence[bool], total: int) -> list[bool]:
    keep = [False] * total
    for hunk, decision in zip(hunks, decisions):
        for index in hunk.match_indexes:
            if index < total:
                keep[index] = decision
    return keep


# ---------------------------------------------------------------------------
# Phase 2 — writing
# ---------------------------------------------------------------------------


def _write_outcome(
    outcome: FileOutcome,
    options: ReplaceOptions,
    summary: ReplaceSummary,
    session: Session | None,
) -> None:
    new_bytes = outcome._new_bytes
    old_bytes = outcome._old_bytes
    decoded = outcome._decoded
    if new_bytes is None or old_bytes is None or decoded is None:  # pragma: no cover
        outcome.status = "error"
        outcome.error = "internal: scan phase produced no payload"
        return

    mode = options.write_mode
    try:
        if mode == "stdout":
            _write_stdout(outcome, new_bytes, decoded, options)
            target = None
        elif mode == "dir":
            target = _write_to_dir(outcome, new_bytes, decoded, options)
        else:
            target = _write_in_place(outcome, new_bytes, decoded, options)
    except OSError as exc:
        outcome.status = "error"
        outcome.error = exc.strerror or str(exc)
        summary.errors.append(f"{outcome.rel_path}: {outcome.error}")
        if session is not None:
            session.record_error(outcome.path, outcome.rel_path, outcome.error)
        return

    outcome.status = "changed"
    outcome.size_after = len(new_bytes)
    outcome.written_to = target
    summary.bytes_written += len(new_bytes)

    if session is not None and mode == "in-place":
        diff_text = "\n".join(outcome.diff)
        if not diff_text:
            diff_text = "\n".join(
                diff_texts(
                    decoded.text,
                    outcome._new_text or "",
                    from_label=f"a/{outcome.rel_path}",
                    to_label=f"b/{outcome.rel_path}",
                    context=0,
                )
            )
        try:
            session.record_replace(
                path=outcome.path,
                rel_path=outcome.rel_path,
                original_bytes=old_bytes,
                new_bytes=new_bytes,
                replacements=outcome.replacements,
                matches=outcome.matches,
                encoding=decoded.encoding,
                diff_text=diff_text,
            )
        except OSError as exc:  # pragma: no cover
            summary.errors.append(f"cannot journal {outcome.rel_path}: {exc}")


def _write_stdout(
    outcome: FileOutcome, new_bytes: bytes, decoded: textio.DecodedFile, options: ReplaceOptions
) -> None:
    console = options.console
    text = new_bytes.decode(decoded.encoding, errors="replace")
    if console is None:
        print(f"==> {outcome.rel_path} <==")
        print(text, end="" if text.endswith("\n") else "\n")
        return
    console.write_raw(f"==> {outcome.rel_path} <==\n")
    console.write_raw(text if text.endswith("\n") else text + "\n")


def _write_to_dir(
    outcome: FileOutcome, new_bytes: bytes, decoded: textio.DecodedFile, options: ReplaceOptions
) -> Path:
    assert options.output_dir is not None  # guaranteed by validate()
    rel = Path(outcome.rel_path)
    target = options.output_dir / (f"{rel}{options.suffix}" if options.suffix else rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(new_bytes)
    if options.preserve_mtime:
        stamp = int(decoded.mtime * 1e9)
        os.utime(target, ns=(stamp, stamp))
    return target


def _write_in_place(
    outcome: FileOutcome, new_bytes: bytes, decoded: textio.DecodedFile, options: ReplaceOptions
) -> Path:
    if not os.access(outcome.path, os.W_OK):
        raise PermissionError(13, "Permission denied", str(outcome.path))
    atomic_write_bytes(
        outcome.path,
        new_bytes,
        preserve_inode=not options.atomic,
        preserve_metadata=not options.preserve_mtime,
    )
    if options.preserve_mtime:
        stamp = int(decoded.mtime * 1e9)
        os.utime(outcome.path, ns=(stamp, stamp))
    return outcome.path


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------


def _plan_renames(files: Sequence[Path], matcher: Matcher, options: ReplaceOptions) -> list[RenameAction]:
    """Compute the rename plan without touching the filesystem.

    Two passes, because a rename is only safe if *nothing else* claims the same
    new name:

    1. compute the candidate new name for every source and reject the ones that
       are unsafe by construction (empty, ``.``/``..``, or containing a path
       separator — a rename must never move a file to another directory);
    2. reject every source whose target is claimed by more than one source, and
       every source whose target already exists (unless ``--force``).

    Refusing *all* colliding sources rather than letting the first one win is
    deliberate: a bulk rename that silently loses a file is far worse than one
    that stops and asks.
    """
    template = options.spec.effective_replacement
    literal = options.spec.kind is PatternKind.FIXED
    search = options.spec.effective_search

    planned: list[tuple[Path, Path, str]] = []
    actions: list[RenameAction] = []

    for path in sorted(files, key=lambda item: (len(item.parts), str(item)), reverse=True):
        rel = _rel(path, options.root)
        if literal:
            new_name = path.name.replace(search, template)
        else:
            new_name = matcher.pattern.sub(lambda match: match.expand(template), path.name)

        if new_name == path.name:
            continue
        if not new_name.strip() or new_name in {".", ".."}:
            actions.append(RenameAction(source=path, target=path, rel_path=rel, error="new name is empty"))
            continue
        if os.sep in new_name or (os.altsep and os.altsep in new_name):
            actions.append(
                RenameAction(
                    source=path,
                    target=path,
                    rel_path=rel,
                    error="new name contains a path separator",
                )
            )
            continue
        planned.append((path, path.with_name(new_name), rel))

    claims: dict[str, list[Path]] = {}
    for source, target, _rel_path in planned:
        claims.setdefault(str(target), []).append(source)

    for source, target, rel in planned:
        key = str(target)
        rivals = claims.get(key, [])
        if len(rivals) > 1:
            others = ", ".join(_rel(item, options.root) for item in rivals if item != source)
            actions.append(
                RenameAction(
                    source=source,
                    target=target,
                    rel_path=rel,
                    error=f"conflicts with {others} (both would become {target.name})",
                )
            )
            continue
        if target.exists() and not options.force:
            actions.append(
                RenameAction(source=source, target=target, rel_path=rel, error="target already exists")
            )
            continue
        actions.append(RenameAction(source=source, target=target, rel_path=rel))
    return actions


def _execute_renames(
    actions: Sequence[RenameAction],
    options: ReplaceOptions,
    session: Session | None,
    summary: ReplaceSummary,
) -> None:
    """Perform (or, in a dry run, only report) the planned renames.

    Planned renames are surfaced through the console, not through
    ``summary.warnings``: a dry run that lists what it *would* do is a success,
    and mixing that into warnings makes the exit code and the summary lie.
    """
    console = options.console
    for action in actions:
        if action.error:
            summary.warnings.append(
                f"rename skipped: {action.rel_path or action.source.name} ({action.error})"
            )
            continue
        if not options.apply_changes:
            _console_info(console, f"would rename {action.rel_path} → {action.target.name}")
            continue
        try:
            action.target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(action.source, action.target)
            action.executed = True
            _console_info(console, f"renamed {action.rel_path} → {action.target.name}")
            if session is not None:
                session.record_rename(
                    source=action.source, target=action.target, rel_path=action.rel_path
                )
        except OSError as exc:
            action.error = exc.strerror or str(exc)
            summary.failed += 1
            summary.errors.append(f"cannot rename {action.rel_path}: {action.error}")


def _console_info(console: Console | None, message: str) -> None:
    if console is not None:
        console.info(message)


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _tally(summary: ReplaceSummary, outcome: FileOutcome) -> None:
    if outcome.status == "changed" or outcome.status == "dry-run":
        summary.changed += 1
    elif outcome.status == "skipped":
        summary.skipped += 1
    elif outcome.status == "error":
        summary.failed += 1
    else:
        summary.unchanged += 1


def _report_file(console: Console | None, outcome: FileOutcome, options: ReplaceOptions) -> None:
    if console is None:
        return
    if console.porcelain_mode:
        if outcome.replacements:
            console.record(
                outcome.rel_path, 1, 1, f"{outcome.replacements} replacement(s)", kind=outcome.status
            )
        return
    if outcome.status == "changed":
        console.info(f"{console.paint(outcome.rel_path, 'green')}: {outcome.replacements} replacement(s)")
    elif outcome.status == "dry-run":
        console.info(f"{console.paint(outcome.rel_path, 'yellow')}: would replace {outcome.replacements}")
    elif outcome.status == "skipped" and console.verbosity >= Verbosity.VERBOSE:
        console.verbose(f"skipped {outcome.rel_path}: {outcome.reason}")
    # Diffs of a read-only run are rendered by the command layer (`sr diff`),
    # which owns their ordering; printing them here too would duplicate output.
    if options.show_diff and outcome.diff and options.apply_changes:
        console.write(render_diff(outcome.diff, console))


def _prune(options: ReplaceOptions, console: Console | None, summary: ReplaceSummary) -> None:
    """Apply the retention policy after a mutating run.

    ``keep`` of ``None`` or ``0`` means "no count limit" — pruning then happens
    only when ``--keep-age`` is configured.  This is the documented meaning of
    ``--keep 0`` and it must never delete the session that was just written.
    """
    if not options.keep and options.keep_age is None:
        return
    store = store_for(options.root, options.backup_dir)
    removed = prune_sessions(store, keep=options.keep, keep_age=options.keep_age)
    if not removed:
        return
    if console:
        console.verbose(f"pruned {len(removed)} old session(s), keeping {options.keep}")
    names = ", ".join(info.short_id for info in removed[:5])
    summary.warnings.append(f"pruned {len(removed)} session(s): {names}{'…' if len(removed) > 5 else ''}")


def _rel(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(str(path), str(root))
    except ValueError:  # pragma: no cover - different drives on Windows
        return relative_to_cwd(path)


def _serialisable_options(options: ReplaceOptions) -> dict[str, object]:
    return {
        "write_mode": options.write_mode,
        "output_dir": str(options.output_dir or ""),
        "suffix": options.suffix,
        "backup": options.backup,
        "backup_dir": str(options.backup_dir or ""),
        "atomic": options.atomic,
        "lock": options.lock,
        "encoding": options.encoding or "auto",
        "encoding_errors": options.encoding_errors,
        "line_endings": options.line_endings,
        "preserve_mtime": options.preserve_mtime,
        "interactive": options.interactive,
        "rename": options.rename,
        "rename_only": options.rename_only,
        "pattern": options.spec.describe(),
        "discovery": {
            "patterns": list(options.discovery_options.patterns),
            "include": list(options.discovery_options.include),
            "exclude": list(options.discovery_options.exclude),
            "recursive": options.discovery_options.recursive,
            "gitignore": options.discovery_options.gitignore,
            "hidden": options.discovery_options.hidden,
            "max_depth": options.discovery_options.max_depth,
        },
    }


def build_matcher(
    search: str,
    replacement: str = "",
    *,
    kind: PatternKind | str = PatternKind.FIXED,
    ignore_case: bool = False,
    word_boundary: bool = False,
    multiline: bool = False,
    process_escapes: bool = True,
    global_replace: bool = True,
    max_count: int | None = None,
    line_range: str | None = None,
) -> Matcher:
    """Convenience wrapper so callers need not build a :class:`PatternSpec`."""
    spec = build_spec(
        search,
        replacement,
        kind=kind,
        ignore_case=ignore_case,
        word_boundary=word_boundary,
        multiline=multiline,
        process_escapes=process_escapes,
        global_replace=global_replace,
        max_count=max_count,
        line_range=line_range,
    )
    return Matcher(spec)


def human_size(num: float) -> str:
    """Public helper for report rendering."""
    return format_size(num)

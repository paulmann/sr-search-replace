"""Shared plumbing for the command implementations.

Translates a parsed :class:`~sr.context.Context` into the engine's typed
options, and renders the summaries that ``replace``, ``find`` and ``diff``
have in common.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from .. import discovery
from ..console import Console, Verbosity
from ..context import Context
from ..diffview import render_diff
from ..errors import UsageError
from ..exit_codes import ExitCode
from ..options import BY_NAME
from ..pattern import PatternKind, PatternSpec, build_spec
from ..replace import (
    Hunk,
    ReplaceOptions,
    ReplaceSummary,
)
from ..util import dedupe_preserve_order as dedupe
from ..util import format_duration, format_size, plural

#: Directories that must never be the root of a mutating run without --force.
FORBIDDEN_ROOTS = frozenset(
    {
        "/",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
        "/var",
        "/root",
    }
)


def resolve_target(context: Context) -> Path:
    """The directory the command operates on."""
    raw = context.value("target", ".")
    return Path(os.path.expanduser(str(raw or "."))).resolve()


def build_spec_from_context(context: Context, search: str, replacement: str = "") -> PatternSpec:
    """Turn CLI/config values into a validated :class:`PatternSpec`."""
    kind = _resolve_pattern_kind(context)
    return build_spec(
        search,
        replacement,
        kind=kind,
        ignore_case=bool(context.value("ignore_case", False)),
        word_boundary=bool(context.value("word_boundary", False)),
        multiline=bool(context.value("multiline", False)),
        process_escapes=not context.value("no_escapes", False),
        global_replace=not context.value("no_global", False),
        max_count=_optional_int(context.value("max_count")),
        line_range=context.value("line_range"),
    )


def _resolve_pattern_kind(context: Context) -> PatternKind:
    explicit = [name for name in ("regex", "ere", "pcre") if context.value(name, False)]
    kind_option = context.value("pattern-kind")
    if kind_option and kind_option != PatternKind.FIXED.value:
        explicit.append(f"pattern-kind={kind_option}")
    if len(explicit) > 1:
        raise UsageError(
            f"conflicting pattern kinds: {', '.join(explicit)}",
            hint="choose exactly one of --regex, --ere, --pcre or --pattern-kind",
        )
    if context.flag("regex"):
        return PatternKind.REGEX
    if context.flag("ere"):
        return PatternKind.ERE
    if context.flag("pcre"):
        return PatternKind.PCRE
    if kind_option:
        return PatternKind.parse(str(kind_option))
    return PatternKind.FIXED


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def build_discovery_options(
    context: Context,
    root: Path,
    *,
    patterns: Sequence[str] = (),
    explicit_files: Sequence[str | os.PathLike[str]] = (),
    text_only: bool | None = None,
) -> discovery.DiscoveryOptions:
    """Collect every target-selection flag into :class:`DiscoveryOptions`.

    ``patterns`` are the positional globs; ``-p/--pattern`` values are merged in
    (and de-duplicated) so both spellings work and can be combined.
    """
    files_from = context.value("files-from")
    listed_files = _read_files_from(files_from) if files_from else []
    all_patterns = dedupe([str(item) for item in (*patterns, *context.list_value("pattern")) if item])

    default_excludes = not context.value("no_default_excludes", False)
    exclude_dirs = list(context.list_value("exclude-dir"))
    if not default_excludes:
        base_dirs: list[str] = []
        base_files: list[str] = []
    else:
        base_dirs = list(discovery.DEFAULT_EXCLUDE_DIRS)
        base_files = list(discovery.DEFAULT_EXCLUDE_FILES)

    # When the user named files explicitly (--file / --files-from) and asked for
    # neither a pattern nor a target directory, the tree walk is skipped:
    # scanning "." as well would silently widen the target set, which is the
    # opposite of what naming files promises.
    explicit_only = bool(explicit_files or listed_files or context.list_value("file")) and not (
        all_patterns or context.list_value("include") or "target" in context.explicit
    )
    options = discovery.DiscoveryOptions(
        roots=[] if explicit_only else [root],
        patterns=all_patterns,
        explicit_files=[Path(item) for item in (*explicit_files, *listed_files, *context.list_value("file"))],
        include=list(context.list_value("include")),
        exclude=list(context.list_value("exclude")),
        exclude_dirs=base_dirs + [item for item in exclude_dirs if item not in base_dirs],
        exclude_files=base_files,
        recursive=not context.value("no_recursive", False),
        max_depth=_optional_int(context.value("max-depth")),
        hidden=bool(context.value("hidden", False)),
        symlinks=bool(context.value("symlinks", False)),
        follow_symlinked_dirs=bool(context.value("symlinks", False)),
        gitignore=bool(context.value("gitignore", False)),
        max_size=_optional_int(context.value("max-size")),
        min_size=_optional_int(context.value("min-size")),
        newer_than=_age_bound(context.value("newer-than")),
        older_than=_age_bound(context.value("older-than"), older=True),
        text_only=not context.value("binary", False) if text_only is None else text_only,
        encoding=context.value("encoding"),
        collect_dirs=directories_enabled(context),
    )
    options.normalise()
    return options


def _age_bound(value: Any, *, older: bool = False) -> float | None:
    if value in (None, ""):
        return None
    import time

    seconds = float(value)
    reference = time.time()
    # --newer-than 7d  → only files with mtime newer than (now - 7d)
    # --older-than 7d  → only files with mtime older than (now - 7d)
    return reference - seconds


def _read_files_from(source: str) -> list[str]:
    text = sys.stdin.read() if str(source).strip() == "-" else Path(os.path.expanduser(str(source))).read_text(
        encoding="utf-8", errors="replace"
    )
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def build_replace_options(
    context: Context,
    *,
    spec: PatternSpec,
    root: Path,
    discovery_options: discovery.DiscoveryOptions,
    apply_changes: bool,
    command: Sequence[str] = (),
) -> ReplaceOptions:
    """Assemble the engine options for a replace/find/diff run."""
    from ..version import __version__

    write_mode = str(context.value("write", "in-place") or "in-place")
    output_dir = context.path("output-dir")
    if output_dir and write_mode == "in-place":
        write_mode = "dir"
    options = ReplaceOptions(
        spec=spec,
        discovery_options=discovery_options,
        root=root,
        apply_changes=apply_changes,
        write_mode=write_mode,
        output_dir=output_dir.resolve() if output_dir else None,
        suffix=str(context.value("suffix", "") or ""),
        backup=bool(context.value("backup", True)) and apply_changes,
        backup_dir=context.path("backup-dir"),
        keep=_optional_int(context.value("keep")),
        keep_age=_optional_float(context.value("keep-age")),
        lock=bool(context.value("lock", True)),
        encoding=context.value("encoding"),
        encoding_errors=str(context.value("encoding_error", "strict") or "strict"),
        line_endings=str(context.value("line_endings", "preserve") or "preserve"),
        preserve_mtime=bool(context.value("preserve_mtime", False)),
        atomic=bool(context.value("atomic", True)),
        show_diff=bool(context.value("diff", False)),
        diff_context=int(context.value("context", 3) or 3),
        interactive=bool(context.value("interactive", False)),
        confirm_threshold=int(context.value("confirm-threshold", 0) or 0),
        rename=bool(context.value("rename", False)),
        rename_only=bool(context.value("rename_only", False)),
        rename_directories=directories_enabled(context),
        force=bool(context.value("force", False)),
        command=list(command or context.full_command()),
        version=__version__,
        console=context.console,
    )
    if options.interactive:
        options.hunk_filter = make_hunk_filter(context)
    if options.confirm_threshold and not context.assume_yes:
        options.confirm = make_confirm(context)
    return options


def directories_enabled(context: Context) -> bool:
    """Whether directory names take part in a rename.

    ``sr rename`` renames directories by default (that is the point of the
    command); ``sr replace --rename`` does not, unless asked, because renaming
    folders as a side effect of a content edit is surprising.
    """
    raw = context.value("directories", None)
    if raw is not None:
        return bool(raw)
    return context.command == "rename"


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------


def make_confirm(context: Context):
    """Build the "N files will change, continue?" callback."""

    def confirm(files: int, replacements: int) -> bool:
        question = (
            f"{plural(files, 'file')} would change "
            f"({plural(replacements, 'replacement')}). Continue?"
        )
        return context.confirm(question, default=False)

    return confirm


def make_hunk_filter(context: Context):
    """Build the per-hunk ``[y]es/[n]o/[a]ll/[q]uit`` prompt."""
    console = context.console

    def filter_hunks(path: Path, hunks: Sequence[Hunk]) -> list[bool]:
        decisions: list[bool] = []
        state = {"all": False, "quit": False}
        for index, hunk in enumerate(hunks, start=1):
            if state["quit"]:
                decisions.append(False)
                continue
            if state["all"]:
                decisions.append(True)
                continue
            console.header(f"{path} — hunk {index}/{len(hunks)} ({hunk.replacements} change(s))")
            for line in _render_hunk(hunk, console):
                console.write(line)
            answer = _ask_hunk(console, context)
            if answer == "all":
                state["all"] = True
                decisions.append(True)
            elif answer == "quit":
                state["quit"] = True
                decisions.append(False)
            else:
                decisions.append(answer == "yes")
        return decisions

    return filter_hunks


def _render_hunk(hunk: Hunk, console: Console) -> Iterable[str]:
    """Render a hunk: dim context, then red/green for every changed line."""
    for line in hunk.context_before:
        yield console.paint(f"      {line}", "dim")
    for offset, (before, after) in enumerate(zip(hunk.before, hunk.after, strict=False)):
        number = hunk.first_line + offset
        if before == after:
            yield console.paint(f"{number:>5}   {before}", "dim")
        else:
            yield console.paint(f"{number:>5} - {before}", "red")
            yield console.paint(f"{number:>5} + {after}", "green")
    # A multiline match can add or remove lines; show the remainder either way.
    for extra in hunk.after[len(hunk.before) :]:
        yield console.paint(f"      + {extra}", "green")
    for extra in hunk.before[len(hunk.after) :]:
        yield console.paint(f"      - {extra}", "red")
    for line in hunk.context_after:
        yield console.paint(f"      {line}", "dim")


def _ask_hunk(console: Console, context: Context) -> str:
    prompt = "Apply this hunk? [y]es / [n]o / [a]ll / [d]iff / [q]uit: "
    while True:
        if context.assume_yes:
            return "yes"
        if not context.stdin_is_tty:
            console.warn("not a terminal — skipping the remaining hunks", hint="pass --yes to apply all")
            return "quit"
        try:
            console.write_raw(console.paint(prompt, "bold"), stream=sys.stderr)
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.write_raw("\n", stream=sys.stderr)
            return "quit"
        if answer in {"", "y", "yes"}:
            return "yes"
        if answer in {"n", "no"}:
            return "no"
        if answer in {"a", "all"}:
            return "all"
        if answer in {"q", "quit", "exit"}:
            return "quit"
        if answer in {"d", "diff"}:
            continue
        console.warn(f"unknown answer {answer!r}")


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------


def guard_root(context: Context, root: Path, *, mutating: bool) -> None:
    """Refuse to rewrite system directories or the whole filesystem by accident."""
    text = str(root)
    home = str(Path.home())
    dangerous = text in FORBIDDEN_ROOTS or text == home
    if not dangerous:
        return
    if not mutating or context.dry_run or context.value("force", False):
        return
    raise UsageError(
        f"refusing to modify files directly under {text}",
        hint="pass -C <project-dir>, or --force if you really mean the whole tree",
    )


def guard_writable(context: Context, root: Path) -> None:
    if not os.access(root, os.W_OK):
        raise UsageError(
            f"{root} is not writable",
            hint="check the permissions, or use --write=dir --output-dir=<path>",
        )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report_summary(context: Context, summary: ReplaceSummary, *, spec: PatternSpec) -> None:
    """Print the human summary (or emit the JSON document)."""
    console = context.console
    if console.json_mode:
        document = summary.to_dict(include_files=True, include_diff=bool(context.value("diff", False)))
        document["options"] = _effective_options(context)
        console.emit_json(document)
        return

    if console.verbosity <= Verbosity.QUIET:
        return
    if console.porcelain_mode:
        return

    console.header("Summary")
    console.kv("Search", _quote(summary.search))
    console.kv("Replace", _quote(summary.replacement))
    console.kv("Pattern", spec.describe())
    console.kv("Root", str(summary.root))
    console.kv("Files scanned", summary.scanned)
    console.kv("Files changed", f"{summary.changed}" + (" (dry run)" if summary.dry_run else ""))
    console.kv("Replacements", summary.replacements)
    if summary.matches != summary.replacements:
        console.kv("Matches seen", summary.matches)
    if summary.unchanged:
        console.kv("Unchanged", summary.unchanged)
    if summary.skipped:
        console.kv("Skipped", summary.skipped)
    if summary.failed:
        console.kv("Failed", console.paint(str(summary.failed), "red"))
    if summary.bytes_read:
        console.kv("Read", format_size(summary.bytes_read))
    if summary.bytes_written:
        console.kv("Written", format_size(summary.bytes_written))
    console.kv("Elapsed", format_duration(summary.duration))
    if summary.renames:
        executed = sum(1 for item in summary.renames if item.executed)
        console.kv("Renames", f"{executed} executed, {len(summary.renames) - executed} skipped")
    if summary.session is not None:
        console.kv("Session", summary.session.id)
        console.kv("Rollback", console.paint(f"sr rollback {summary.session.short_id}", "cyan"))

    if console.verbosity >= Verbosity.VERBOSE and summary.skipped_files:
        console.write("")
        console.info(f"Skipped {len(summary.skipped_files)} candidate(s):")
        for item in summary.skipped_files[:25]:
            console.write(f"  {item.path} — {item.reason}")
        if len(summary.skipped_files) > 25:
            console.write(f"  … and {len(summary.skipped_files) - 25} more")

    for warning in summary.warnings[:10]:
        console.warn(warning)
    if len(summary.warnings) > 10:
        console.warn(f"… and {len(summary.warnings) - 10} more warnings")
    for error in summary.errors[:10]:
        console.error(error)
    if len(summary.errors) > 10:
        console.error(f"… and {len(summary.errors) - 10} more errors")

    if summary.dry_run:
        console.write("")
        console.warn("dry run — no files were modified")


def _effective_options(context: Context) -> dict[str, Any]:
    values = {key: context.value(key) for key in BY_NAME}
    return {key: value for key, value in values.items() if value not in (None, "", (), [])}


def _quote(text: str) -> str:
    return repr(text)


def report_diff_summary(context: Context, summary: ReplaceSummary) -> None:
    """Print only the diffs (used by ``sr diff``)."""
    console = context.console
    if console.json_mode:
        document = summary.to_dict(include_files=True, include_diff=True)
        console.emit_json(document)
        return
    for outcome in summary.outcomes:
        if outcome.diff:
            console.write(render_diff(outcome.diff, console))
    if console.verbosity >= Verbosity.NORMAL and not console.machine_readable:
        console.write("")
        console.info(
            f"{plural(summary.changed, 'file')} would change, "
            f"{plural(summary.replacements, 'replacement')}"
        )


def exit_code_for(summary: ReplaceSummary, *, mutating: bool) -> int:
    """Map a summary onto a documented exit code.

    ``0`` means "there was something to do and it was done (or would be, in a
    dry run)"; ``3`` means "nothing matched".  Errors always win.
    """
    if summary.failed or summary.errors:
        return int(ExitCode.ERROR)
    planned_renames = sum(1 for item in summary.renames if not item.error)
    if not mutating:
        return int(ExitCode.OK) if (summary.replacements or planned_renames) else int(ExitCode.NO_MATCH)
    if summary.changed or summary.replacements or any(item.executed for item in summary.renames):
        return int(ExitCode.OK)
    return int(ExitCode.NO_MATCH)

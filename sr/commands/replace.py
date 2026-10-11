"""``sr replace`` — the main command."""

from __future__ import annotations

from collections.abc import Sequence

from ..console import Verbosity
from ..context import Context
from ..errors import UsageError
from ..exit_codes import ExitCode
from ..replace import run_replace, scan_only
from ..util import format_duration
from . import common


def resolve_positionals(
    positionals: Sequence[str] | None,
    *,
    glob_option: Sequence[str] | None = None,
    require_replacement: bool = True,
) -> tuple[list[str], str, str]:
    """Interpret the flexible positional grammar.

    Accepted forms (``require_replacement=True``)::

        GLOB... SEARCH REPLACE
        SEARCH REPLACE

    With ``require_replacement=False`` (``sr find``)::

        GLOB... SEARCH
        SEARCH

    The disambiguation rule is deliberately boring and predictable: when the
    first token names an existing file or directory, it is a target, not a
    search string.  Everything else is a glob.  ``--`` always ends option
    parsing, so ``sr replace -- "weird name" "a" "b"`` is unambiguous.
    """
    items = list(positionals or [])
    globs: list[str] = list(glob_option or [])
    needed = 3 if require_replacement else 2

    if not items:
        if globs:
            raise UsageError(
                "missing SEARCH and REPLACE",
                hint="example: sr replace --glob '*.py' \"old\" \"new\"",
            )
        raise UsageError(
            "missing arguments",
            hint="example: sr replace \"*.py\" \"old\" \"new\"  —  see `sr help replace`",
        )

    if len(items) == 1:
        if globs:
            return globs, items[0], ""
        if require_replacement:
            raise UsageError(
                "missing REPLACE argument",
                hint=f"you gave one argument ({items[0]!r}); example: sr replace \"old\" \"new\"",
            )
        return globs, items[0], ""

    if len(items) == needed - 1 and not globs:
        # SEARCH REPLACE (no glob).
        if require_replacement:
            return globs, items[0], items[1]
        return globs, items[0], ""

    if len(items) >= needed:
        extra = items[:-2] if require_replacement else items[:-1]
        tail = items[-2:] if require_replacement else [items[-1], ""]
        globs.extend(extra)
        return globs, tail[0], tail[1]

    raise UsageError(
        f"cannot interpret {len(items)} positional arguments",
        hint="expected: sr replace [GLOB...] SEARCH REPLACE",
    )


def cmd_replace(context: Context) -> int:
    """``sr replace [GLOB...] SEARCH REPLACE``."""
    args = context.args
    globs, search, replacement = resolve_positionals(
        getattr(args, "positionals", None),
        glob_option=getattr(args, "glob_option", None),
    )
    if not search:
        raise UsageError("the search pattern must not be empty")

    root = common.resolve_target(context)
    if not root.exists():
        raise UsageError(f"target does not exist: {root}")
    common.guard_root(context, root, mutating=True)
    if not context.value("dry_run", False):
        context.refuse_root_user()
        common.guard_writable(context, root)

    spec = common.build_spec_from_context(context, search, replacement)
    discovery_options = common.build_discovery_options(context, root, patterns=globs)
    options = common.build_replace_options(
        context,
        spec=spec,
        root=root,
        discovery_options=discovery_options,
        apply_changes=not context.value("dry_run", False),
    )

    console = context.console
    if console.verbosity >= Verbosity.NORMAL and not console.machine_readable:
        console.info(
            f"replace {_quote(search)} → {_quote(replacement)} "
            f"in {root} [{spec.describe()}]"
        )

    summary = run_replace(options)
    common.report_summary(context, summary, spec=spec)
    return common.exit_code_for(summary, mutating=not context.value("dry_run", False))


def cmd_find(context: Context) -> int:
    """``sr find [GLOB...] SEARCH`` — read-only search with previews."""
    args = context.args
    globs, search, replacement = resolve_positionals(
        getattr(args, "positionals", None),
        glob_option=getattr(args, "glob_option", None),
        require_replacement=False,
    )
    if not search:
        raise UsageError("the search pattern must not be empty")

    root = common.resolve_target(context)
    if not root.exists():
        raise UsageError(f"target does not exist: {root}")
    spec = common.build_spec_from_context(context, search, replacement or search)
    discovery_options = common.build_discovery_options(context, root, patterns=globs)
    options = common.build_replace_options(
        context,
        spec=spec,
        root=root,
        discovery_options=discovery_options,
        apply_changes=False,
    )
    options.backup = False
    options.show_diff = bool(context.value("diff", False))

    from ..find import FindOptions, run_find

    find_options = FindOptions(
        replace_options=options,
        count_only=bool(getattr(args, "count_only", False)),
        list_files=bool(getattr(args, "list_files", False)),
        before_context=getattr(args, "before_context", None),
        after_context=getattr(args, "after_context", None),
        highlight=getattr(args, "highlight", None),
    )
    result = run_find(find_options, context.console)
    if context.console.json_mode:
        context.console.emit_json(result.to_dict(include_files=True))
    return int(ExitCode.OK) if result.matches else int(ExitCode.NO_MATCH)


def cmd_diff(context: Context) -> int:
    """``sr diff [GLOB...] SEARCH REPLACE`` — preview as a unified diff."""
    args = context.args
    session_reference = getattr(args, "session", None)
    if session_reference:
        from .sessions import show_session_diff

        return show_session_diff(context, session_reference)

    globs, search, replacement = resolve_positionals(
        getattr(args, "positionals", None),
        glob_option=None,
    )
    if not search:
        raise UsageError("the search pattern must not be empty")
    root = common.resolve_target(context)
    if not root.exists():
        raise UsageError(f"target does not exist: {root}")
    spec = common.build_spec_from_context(context, search, replacement)
    discovery_options = common.build_discovery_options(context, root, patterns=globs)
    options = common.build_replace_options(
        context,
        spec=spec,
        root=root,
        discovery_options=discovery_options,
        apply_changes=False,
    )
    options.backup = False
    options.show_diff = True
    summary = scan_only(options)
    common.report_diff_summary(context, summary)
    return int(ExitCode.OK) if summary.replacements else int(ExitCode.NO_MATCH)


def cmd_rename(context: Context) -> int:
    """``sr rename [GLOB...] SEARCH REPLACE`` — rename by pattern."""
    args = context.args
    globs, search, replacement = resolve_positionals(
        getattr(args, "positionals", None),
        glob_option=None,
    )
    if not search:
        raise UsageError("the search pattern must not be empty")
    root = common.resolve_target(context)
    if not root.exists():
        raise UsageError(f"target does not exist: {root}")
    common.guard_root(context, root, mutating=True)
    if not context.value("dry_run", False):
        context.refuse_root_user()
        common.guard_writable(context, root)

    spec = common.build_spec_from_context(context, search, replacement)
    discovery_options = common.build_discovery_options(context, root, patterns=globs)
    # Renaming works on names, so binary files are perfectly valid targets.
    discovery_options.text_only = False
    options = common.build_replace_options(
        context,
        spec=spec,
        root=root,
        discovery_options=discovery_options,
        apply_changes=not context.value("dry_run", False),
    )
    options.rename_only = True
    if getattr(args, "directories", None) is False:
        options.rename = False

    summary = run_replace(options)
    _report_renames(context, summary)
    return common.exit_code_for(summary, mutating=not context.value("dry_run", False))


def _report_renames(context: Context, summary) -> None:
    console = context.console
    if console.json_mode:
        console.emit_json(summary.to_dict(include_files=False))
        return
    if console.verbosity <= Verbosity.QUIET:
        return
    executed = [item for item in summary.renames if item.executed]
    planned = [item for item in summary.renames if not item.error]
    console.write("")
    console.header("Summary")
    console.kv(
        "Renames",
        f"{len(planned)} planned (dry run)" if summary.dry_run else f"{len(executed)} executed",
    )
    console.kv("Elapsed", format_duration(summary.duration))
    if summary.session is not None:
        console.kv("Session", summary.session.id)
        console.kv("Rollback", console.paint(f"sr rollback {summary.session.short_id}", "cyan"))
    for warning in summary.warnings[:10]:
        console.warn(warning)
    for error in summary.errors[:10]:
        console.error(error)


def _quote(text: str) -> str:
    return repr(text)

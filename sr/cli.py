"""Command line surface: preprocessing, dispatch and error handling.

Grammar
-------
``sr`` uses a subcommand grammar (``sr replace …``, ``sr find …``) plus one
ergonomic shortcut: when the first token is not a known subcommand the whole
line is treated as ``sr replace``.  Both of these are equivalent::

    sr replace "*.py" "old" "new"
    sr "*.py" "old" "new"

The shortcut is resolved *before* argparse sees the arguments by
:func:`preprocess`, which keeps leading global flags in place and moves the
leading positionals behind the injected ``replace``.

Precedence
----------
Every option is declared once in :mod:`sr.options`.  The parser records which
destinations the user actually typed (:class:`~sr.cli_support.ExplicitStore`),
which is what lets :class:`~sr.context.Context` implement
``CLI > env > project config > user config > defaults`` without having to guess
whether a value came from argparse's default or from the user.
"""

from __future__ import annotations

import argparse as _argparse
import os
import sys
from argparse import Namespace as _Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .cli_support import SrArgumentParser, add_option_group, converter, int_type  # noqa: F401 (re-exported)
from .context import Context
from .errors import SrError, UsageError
from .exit_codes import DESCRIPTIONS, ExitCode
from .options import BY_FLAG
from .registry import (  # noqa: F401  (re-exported for tooling)
    PROG,
    SUBCOMMAND_ALIASES,
    SUBCOMMAND_NAMES,
    SUBCOMMANDS,
    normalize_alias,
    summary_of,
    usage_of,
)
from .version import __version__

#: Options accepted before the subcommand.
GLOBAL_FLAGS = (
    "-h", "--help", "--help-all", "-V", "--version",
    "-v", "--verbose", "-q", "--quiet", "--debug",
    "--json", "--porcelain", "--color", "--no-color",
    "--no-update-check", "--config", "--config-file",
)

#: Global flags that consume the next token as their value.
#:
#: This is an explicit list, not a lookup in the option schema: ``--no-color``
#: is an alias of ``--color`` and shares its schema entry, so asking the schema
#: "does this take a value?" would answer *yes* and swallow the following
#: positional (``sr --no-color '*.py' a b`` lost its glob that way).
GLOBAL_VALUE_FLAGS = frozenset({"--color", "--verbosity", "--config", "--config-file"})

_PARSER_DESCRIPTION = """\
sr — safe, auditable search & replace for source trees.

Quick start:
  sr replace "old" "new" -p '*.py'      edit in place, with a rollback session
  sr find "old" -p '*.py'               where does it occur?
  sr diff "old" "new"                   what exactly would change?
  sr rollback                           undo the last run
  sr help                               the manual
"""

_PARSER_EPILOG = """\
Every mutating run is journalled under <target>/.sr, so `sr rollback` undoes
it.  Use `sr help topics` for the full manual and `sr doctor` to verify the
installation.
"""


def build_parser() -> Any:
    """Construct the complete argument parser."""

    from . import commands  # imported lazily: commands → help_system → cli

    parser = SrArgumentParser(
        prog=PROG,
        description=_PARSER_DESCRIPTION,
        epilog=_PARSER_EPILOG,
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        add_help=False,
        allow_abbrev=True,
    )
    _add_global_options(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND", required=False)
    for name, aliases, summary in SUBCOMMANDS:
        builder = getattr(commands.parsers, f"build_{name}_parser")
        builder(subparsers, name, aliases, summary)
    return parser


def _add_global_options(parser: Any) -> None:
    from .cli_support import AppendExplicit, CountExplicit, ExplicitStore

    group = parser.add_argument_group("Global options")
    group.add_argument("-h", "--help", action="store_true", dest="help",
                       help="Show help and exit.")
    group.add_argument("--help-all", action="store_true", dest="help_all",
                       help="Show help for every subcommand and option.")
    group.add_argument("-V", "--version", action="store_true", dest="version",
                       help="Print version, license and update information.")
    group.add_argument("-v", "--verbose", action=CountExplicit, nargs=0, default=False,
                       dest="verbose", help="More output; repeat (-vv) for debug level.")
    group.add_argument("-q", "--quiet", action=CountExplicit, nargs=0, default=False,
                       dest="quiet", help="Only errors; repeat (-qq) to print data only.")
    group.add_argument("--debug", action=ExplicitStore, nargs=0, default=False, dest="debug",
                       help="Debug logging (same as -vv).")
    group.add_argument("--verbosity", action=ExplicitStore, default=None, metavar="LEVEL",
                       choices=("quiet", "normal", "verbose", "debug"),
                       help="Set the log level explicitly.")
    group.add_argument("--color", action=ExplicitStore, default=None, metavar="WHEN",
                       choices=("auto", "always", "never"), help="Colourise output (default: auto).")
    group.add_argument("--no-color", action=ExplicitStore, nargs=0, default=None, dest="color",
                       const="never", help="Same as --color=never.")
    group.add_argument("--json", action=ExplicitStore, nargs=0, default=False, dest="json",
                       help="Emit a single JSON document on stdout.")
    group.add_argument("--porcelain", action=ExplicitStore, nargs=0, default=False, dest="porcelain",
                       help="Emit stable machine-readable records.")
    group.add_argument("--no-update-check", action=ExplicitStore, nargs=0, default=False,
                       dest="no_update_check", help="Do not check for a newer release.")
    group.add_argument("--config", action=AppendExplicit, default=None, metavar="KEY=VALUE",
                       help="Override one configuration value (repeatable).")
    group.add_argument("--config-file", action=AppendExplicit, default=None, metavar="FILE",
                       help="Read an extra configuration file (repeatable).")


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


def preprocess(argv: Sequence[str]) -> tuple[list[str], bool]:
    """Rewrite a bare invocation into ``replace …``.

    Returns ``(tokens, was_rewritten)``.

    The bare form accepts flags in *any* position::

        sr "*.py" "old" "new" -i          →  sr replace -i "*.py" "old" "new"
        sr -i "*.py" "old" "new"          →  sr replace -i "*.py" "old" "new"

    which removes the "options must come first" trap that sr 6.x had.  The rule
    for telling an option from a positional is the usual one: a token that
    starts with ``-`` and is longer than one character is an option, and
    everything after a bare ``--`` is positional.  That means a search string
    that genuinely starts with a dash must be written after ``--``.
    """
    tokens = list(argv)
    if not tokens:
        return tokens, False
    first = tokens[0]
    if first in SUBCOMMAND_NAMES:
        return tokens, False
    if first in SUBCOMMAND_ALIASES:
        return [normalize_alias(first), *tokens[1:]], True
    if first in {"-h", "--help", "--help-all", "-V", "--version"} or first.startswith("--help="):
        return tokens, False

    global_flags: list[str] = []
    command_flags: list[str] = []
    positionals: list[str] = []
    index = 0
    after_separator = False
    while index < len(tokens):
        word = tokens[index]
        if after_separator:
            positionals.append(word)
            index += 1
            continue
        if word == "--":
            positionals.append(word)
            after_separator = True
            index += 1
            continue
        if len(word) > 1 and word.startswith("-"):
            target = global_flags if _is_global_flag(word) else command_flags
            target.append(word)
            if "=" not in word and _takes_a_value(word) and index + 1 < len(tokens):
                target.append(tokens[index + 1])
                index += 2
                continue
            index += 1
            continue
        positionals.append(word)
        index += 1

    # A known subcommand name among the positionals *is* the subcommand, even
    # when global flags separated it from position 0 (`sr --json find x`).
    # Documented consequence for the bare form: `sr help a b` invokes the help
    # command rather than replacing "help" with "b"; use `sr replace help a b`
    # (or `sr -- help a b`) for that.
    # Everything after a literal `--` is data, never a command name.
    separator_at = positionals.index("--") if "--" in positionals else len(positionals)
    for position, word in enumerate(positionals[:separator_at]):
        if word in SUBCOMMAND_NAMES or word in SUBCOMMAND_ALIASES:
            command = normalize_alias(word)
            return [
                *global_flags,
                command,
                *command_flags,
                *positionals[:position],
                *positionals[position + 1 :],
            ], True

    # The parser only accepts a *global* flag before the subcommand name, so
    # the two buckets are re-assembled around the injected `replace`.
    return [*global_flags, "replace", *command_flags, *positionals], True


def _is_global_flag(token: str) -> bool:
    """True when ``token`` is understood by the top-level parser.

    Anything else is placed after the subcommand, where the subparser can
    report it properly ("unrecognized argument" for a typo, or a real match for
    a command-specific flag such as ``--allow-root``).
    """
    base = token.split("=", 1)[0]
    if base in GLOBAL_FLAGS:
        return True
    spec = BY_FLAG.get(base)
    if spec is None:
        return False
    return not spec.commands


def _rewrite_help_topic(tokens: Sequence[str]) -> list[str]:
    """Turn ``sr help --max-count`` into ``sr help --option max-count``.

    argparse cannot accept an option-looking token as a positional value, and
    ``sr help --<flag>`` is exactly the invocation the manual promises.  The
    rewrite happens before parsing so the rest of the pipeline stays ordinary.
    The same applies to ``sr config show --<flag>``, which documents one option.
    """
    result: list[str] = []
    index = 0
    while index < len(tokens):
        item = tokens[index]
        result.append(item)
        if item in _TOPIC_COMMANDS:
            index += 1
            while index < len(tokens):
                candidate = tokens[index]
                if "=" in candidate or candidate in _HELP_FLAGS or not _is_topic_flag(candidate):
                    result.append(candidate)
                elif candidate.startswith("--"):
                    result.extend(["--option", candidate[2:]])
                else:
                    # A short flag: look its long name up in the schema.
                    spec = BY_FLAG.get(candidate)
                    result.extend(["--option", spec.name if spec else candidate.lstrip("-")])
                index += 1
            break
        index += 1
    return result


#: Subcommands whose first positional may be written as a flag.
_TOPIC_COMMANDS = frozenset({"help", "config"})

#: Flags those subcommands really own; anything else is a topic name.
_HELP_FLAGS = frozenset(
    {
        "--all", "--json", "--no-pager", "--option", "-h", "--help",
        "--show-origin", "--force", "-f", "--file", "--target", "-C",
        "--color", "--config", "--config-file", "--verbosity", "--debug",
        "--porcelain", "--no-update-check", "--no-color",
    }
)


def _is_topic_flag(candidate: str) -> bool:
    """True when ``candidate`` looks like an option name rather than a flag.

    Only tokens that sr actually knows (or that start with a double dash) are
    rewritten, so ``config validate --config-file x`` keeps working.
    """
    if not candidate.startswith("-") or candidate == "-":
        return False
    return candidate not in _HELP_FLAGS


def _takes_a_value(token: str) -> bool:
    """True when ``token`` consumes the next argument as its value.

    Alias flags (``--no-color``) are excluded even though they share a
    value-taking schema entry with ``--color``; see :data:`GLOBAL_VALUE_FLAGS`.
    """
    if token in GLOBAL_VALUE_FLAGS:
        return True
    spec = BY_FLAG.get(token)
    if spec is None or not spec.takes_value:
        return False
    return spec.alias_flag != token


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, *, program: str = PROG) -> int:
    """Run sr and return a process exit code.  Never raises."""
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(raw, program=program)
    except SrError as exc:
        _report_error(exc)
        return int(exc.exit_code)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else (0 if code is None else 1)
    except KeyboardInterrupt:
        sys.stderr.write("\nsr: interrupted\n")
        return int(ExitCode.INTERRUPTED)
    except BrokenPipeError:  # `sr … | head` closes the pipe early
        _silence_pipe_errors()
        return int(ExitCode.OK)
    except Exception as exc:
        return _crash(exc)


def _dispatch(raw: Sequence[str], *, program: str) -> int:
    from . import commands  # lazy: breaks the cli ↔ help_system import cycle

    tokens, _rewritten = preprocess(raw)
    tokens = _rewrite_help_topic(tokens)
    parser = build_parser()
    # Only the tokens *before* the subcommand name are globals; after the
    # rewrite the subcommand is always present (bare invocations get `replace`
    # injected), so the split point is unambiguous.
    cut = next(
        (index for index, token in enumerate(tokens) if token in SUBCOMMAND_NAMES),
        len(tokens),
    )
    head = tokens[:cut]
    args = parser.parse_args(tokens, namespace=_preglobal(head))
    _restore_globals(args, head)

    explicit: set[str] = set(getattr(args, "_explicit", set()) or set())
    if getattr(args, "debug", False):
        explicit.add("verbosity")
        args.verbosity = "debug"
    if getattr(args, "verbosity_count", 0):
        explicit.add("verbosity")

    command = str(getattr(args, "command", "") or "")

    if getattr(args, "version", False):
        commands.print_version(sys.stdout)
        return int(ExitCode.OK)
    if getattr(args, "help_all", False):
        commands.help_all(parser, sys.stdout)
        return int(ExitCode.OK)
    if getattr(args, "help", False) and not command:
        commands.show_help(topic=None, parser=parser, subcommand=None, stream=sys.stdout)
        return int(ExitCode.OK)
    if not command:
        commands.show_help(topic=None, parser=parser, subcommand=None, stream=sys.stdout)
        return int(ExitCode.USAGE)

    handler = getattr(commands, f"cmd_{command}", None)
    if handler is None:  # pragma: no cover - the parser guarantees a command
        raise UsageError(f"unknown command {command!r}", hint="run `sr help` for the list")

    target_dir = str(getattr(args, "target", None) or ".")
    context = Context.create(
        argv=list(raw),
        args=args,
        explicit=explicit,
        command=command,
        program=program,
        target=target_dir,
    )
    config_files = list(getattr(args, "config_file", None) or [])
    if config_files:
        from .config import build_config

        context.config = build_config(
            target=target_dir,
            command=command,
            cli_config=list(getattr(args, "config", None) or []),
            explicit_files=config_files,
        )
    commands.maybe_check_for_update(context)
    return int(handler(context) or 0)


#: Global flags whose value a subparser default could otherwise overwrite.
_GLOBAL_DESTS = {
    "--no-color": ("color", "never"),
    "--json": ("json", True),
    "--porcelain": ("porcelain", True),
    "--debug": ("debug", True),
    "--no-update-check": ("no_update_check", True),
    "--help-all": ("help_all", True),
    "--help": ("help", True),
    "-h": ("help", True),
    "--version": ("version", True),
    "-V": ("version", True),
    "-v": ("verbose", True),
    "--verbose": ("verbose", True),
    "-q": ("quiet", True),
    "--quiet": ("quiet", True),
}


def _restore_globals(args: Any, head: Sequence[str]) -> None:
    """Re-apply leading global flags after the subparser has run.

    argparse applies a subparser's defaults over attributes the parent parser
    already set, so ``sr --json find x`` would lose ``--json``.  Restoring from
    the raw leading tokens is exact and needs no second parser.
    """
    explicit = set(getattr(args, "_explicit", set()) or set())

    pending_value_for: str | None = None
    for flag in head:
        if pending_value_for is not None:
            setattr(args, pending_value_for, flag)
            explicit.add(pending_value_for)
            pending_value_for = None
            continue
        if "=" in flag:
            name, _, value = flag.partition("=")
            key = name.lstrip("-").replace("-", "_")
            if key in {"color", "verbosity"}:
                setattr(args, key, value)
                explicit.add(key)
            continue
        if flag in GLOBAL_VALUE_FLAGS:
            pending_value_for = flag.lstrip("-").replace("-", "_")
            continue
        mapped = _GLOBAL_DESTS.get(flag)
        if mapped is None:
            continue
        # Distinct name: `value` above is the str half of a `--opt=value` pair,
        # while a mapped global carries a bool or a fixed string.
        destination, flag_value = mapped
        setattr(args, destination, flag_value)
        explicit.add(destination)

    # -v/-q are counters: `-vv` means debug, `-qq` means data only.
    delta = sum(1 for flag in head if flag == "-v") - sum(1 for flag in head if flag == "-q")
    if delta:
        args.verbosity_count = delta
        explicit.add("verbosity")

    args._explicit = explicit


def _preglobal(head: Sequence[str]) -> _argparse.Namespace:
    """Pre-parse the leading global flags into the namespace argparse fills.

    ``head`` is the slice of argv that precedes the subcommand name.

    argparse lets a subparser overwrite an attribute the parent parser already
    set, so ``sr --no-color find x`` would lose the flag the moment the ``find``
    subparser applied its own default.  Parsing the globals first and handing
    the result to ``parse_args(namespace=…)`` preserves them, and marks them
    explicit so the config-precedence logic still sees a user value.
    """
    if not head:
        return _Namespace()

    probe = _argparse.ArgumentParser(add_help=False)
    _add_global_options(probe)
    namespace = _Namespace()
    try:
        # `parse_known_args` because the head may legitimately contain a
        # positional (the bare form) that only the subparser understands.
        probe.parse_known_args(list(head), namespace=namespace)
    except SystemExit:  # pragma: no cover - the real parse reports it
        pass
    except Exception:
        pass
    explicit = set(getattr(namespace, "_explicit", set()) or set())
    values = {key: value for key, value in vars(namespace).items() if key != "_explicit"}
    return _Namespace(_explicit=explicit, **values)


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------

_RED, _BOLD, _DIM, _RESET = "\x1b[31m", "\x1b[1m", "\x1b[2m", "\x1b[0m"


def _colorize_stderr() -> bool:
    from .console import detect_color

    return detect_color(None, sys.stderr)


def _report_error(exc: SrError) -> None:
    color = _colorize_stderr()
    prefix = f"{_RED}{_BOLD}error:{_RESET}" if color else "error:"
    sys.stderr.write(f"{prefix} {exc.message}\n")
    if exc.hint:
        pad = " " * (len("error:") + 1)
        hint = f"{_DIM}{exc.hint}{_RESET}" if color else exc.hint
        sys.stderr.write(f"{pad}{hint}\n")


def _crash(exc: BaseException) -> int:
    """Report an unexpected exception; full traceback only when asked for."""
    import traceback

    debug = os.environ.get("SR_DEBUG", "").lower() in {"1", "true", "yes"} or "--debug" in sys.argv
    color = _colorize_stderr()
    head = f"{_RED}{_BOLD}sr crashed:{_RESET}" if color else "sr crashed:"
    sys.stderr.write(f"{head} {type(exc).__name__}: {exc}\n")
    if debug:
        traceback.print_exc()
    else:
        sys.stderr.write(
            "       this is a bug — re-run with SR_DEBUG=1 for a traceback and report it at\n"
            "       https://github.com/paulmann/sr-search-replace/issues\n"
        )
    return int(ExitCode.ERROR)


def _silence_pipe_errors() -> None:
    try:
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        pass
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Introspection helpers (used by help, completions and the man page)
# ---------------------------------------------------------------------------


def exit_code_reference() -> list[str]:
    """``code — description`` lines for the help system and the man page."""
    return [f"{int(code)}\t{DESCRIPTIONS[int(code)]}" for code in ExitCode]


def cli_flags(parser: Any = None) -> set[str]:
    """Every option string the real parser accepts, main parser included.

    Used by ``scripts/gen_options_table.py --check`` as a gate: the documented
    option table must cover the whole CLI surface, so a flag added to a
    subparser by hand (or dropped from the schema) fails the build instead of
    silently disappearing from README, docs/OPTIONS.md and the man page.
    """
    parser = parser or build_parser()
    flags: set[str] = set()

    def collect(target: Any) -> None:
        for action in getattr(target, "_actions", []):
            flags.update(action.option_strings)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for sub in choices.values():
                    collect(sub)

    collect(parser)
    return flags


def known_subcommands() -> list[tuple[str, str]]:
    """``(name, summary)`` for every documented subcommand."""
    return [(name, summary) for name, _aliases, summary in SUBCOMMANDS]


def subcommand_aliases(name: str) -> tuple[str, ...]:
    for item, aliases, _summary in SUBCOMMANDS:
        if item == name:
            return aliases
    return ()


def installed_root() -> Path:
    """Directory holding the ``sr`` package (for update/doctor output)."""
    return Path(__file__).resolve().parent


def version_string() -> str:
    return __version__

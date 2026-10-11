"""Parser plumbing shared by :mod:`sr.cli` and :mod:`sr.commands.parsers`.

Lives in its own module because the two would otherwise import each other:
``cli`` builds the parser, ``commands.parsers`` fills in the subcommands, and
both need the same custom argparse actions.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from .options import GROUPS, OPTIONS, OptionKind, OptionSpec


class ExplicitStore(argparse.Action):
    """Records that the user typed this option (drives config precedence)."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        mark_explicit(namespace, self.dest)
        # `nargs=0` hands the action an empty list; the declared `const` is the
        # real value (True/False), which is what makes --no-<flag> work.
        if isinstance(values, list) and not values:
            values = self.const
        setattr(namespace, self.dest, values)


class AppendExplicit(argparse.Action):
    """Repeatable option: accumulates values and marks the dest explicit."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        mark_explicit(namespace, self.dest)
        current = getattr(namespace, self.dest, None)
        if not isinstance(current, list):
            current = []
        if isinstance(values, (list, tuple)):
            current.extend(str(item) for item in values)
        else:
            current.append(str(values))
        setattr(namespace, self.dest, current)


class CountExplicit(argparse.Action):
    """``-v``/``-q`` counter that also records explicitness."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        mark_explicit(namespace, self.dest)
        delta = -1 if option_string and "q" in option_string else 1
        namespace.verbosity_count = (getattr(namespace, "verbosity_count", 0) or 0) + delta
        setattr(namespace, self.dest, True)


def mark_explicit(namespace: argparse.Namespace, dest: str) -> None:
    seen = getattr(namespace, "_explicit", None)
    if seen is None:
        seen = set()
        namespace._explicit = seen
    seen.add(dest)


class SrArgumentParser(argparse.ArgumentParser):
    """Parser that turns argparse errors into :class:`~sr.errors.UsageError`."""

    def error(self, message: str) -> Any:
        from .errors import UsageError

        raise UsageError(
            message,
            hint="run `sr help` for the overview, `sr help options` for every flag",
        )

    def exit(self, status: int = 0, message: str | None = None) -> Any:
        if message:
            (sys.stderr if status else sys.stdout).write(message)
        raise SystemExit(status)


def add_subcommand(
    subparsers: Any,
    name: str,
    aliases: Sequence[str],
    summary: str,
    description: str,
) -> argparse.ArgumentParser:
    """Create one subparser with a consistent help/epilog setup."""
    parser: argparse.ArgumentParser = subparsers.add_parser(
        name,
        aliases=list(aliases),
        help=summary,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run `sr help` for the manual and `sr help examples` for recipes.",
    )
    return parser


def add_option_group(parser: argparse.ArgumentParser, group_name: str, command: str) -> argparse._ArgumentGroup:
    """Create a titled argument group holding every option of ``group_name``."""
    titles = dict(GROUPS)
    group = parser.add_argument_group(titles.get(group_name, group_name.title()))
    for spec in OPTIONS:
        if spec.hidden or spec.group != group_name:
            continue
        if spec.commands and command not in spec.commands:
            continue
        add_spec(group, spec, parser=parser)
    return group


def add_spec(target: Any, spec: OptionSpec, *, parser: argparse.ArgumentParser | None = None) -> None:
    """Add one declared option to a parser or argument group.

    ``parser`` is the owning parser: when given, options that are already
    registered (for example the global ``-y/--yes`` added by
    :func:`add_global_group`) are skipped instead of raising a conflict.
    """
    names = list(spec.flag_names())
    if parser is not None and _has_option(parser, *names):
        return
    base_help = spec.help + (f" [env: {spec.env_var}]" if spec.env_var else "")
    metavar = spec.metavar or None

    if spec.kind == OptionKind.FLAG:
        kwargs: dict[str, Any] = {
            "dest": spec.config_key,
            "help": base_help,
            "action": ExplicitStore,
            "nargs": 0,
            "default": None,
            "const": True,
        }
        # For negatable flags `names` already ends with `--no-<name>`; that one
        # is registered separately below so it can carry `const=False`.
        positive = names[:-1] if spec.negatable else names
        target.add_argument(*positive, **kwargs)
        if spec.negatable:
            negative = dict(kwargs)
            negative["const"] = False
            negative["help"] = f"Disable --{spec.name}."
            target.add_argument(f"--no-{spec.name}", **negative)
        return

    kwargs = {"dest": spec.config_key, "help": base_help, "default": None}
    # An alias flag (`--no-color`) is registered separately below: it shares the
    # destination but carries a fixed value instead of taking one.
    if spec.alias_flag:
        names = [name for name in names if name != spec.alias_flag]
    if spec.kind == OptionKind.LIST:
        kwargs.update(action=AppendExplicit, metavar=spec.metavar or "VALUE")
    elif spec.kind == OptionKind.CHOICE:
        kwargs.update(action=ExplicitStore, choices=spec.choices, metavar=metavar or "VALUE")
    elif spec.kind == OptionKind.INT:
        kwargs.update(action=ExplicitStore, type=int_type(spec), metavar=metavar or "N")
    elif spec.kind in {OptionKind.SIZE, OptionKind.DURATION}:
        kwargs.update(action=ExplicitStore, type=converter(spec), metavar=metavar or "VALUE")
    else:
        kwargs.update(action=ExplicitStore, metavar=metavar or "VALUE")
    target.add_argument(*names, **kwargs)

    if spec.alias_flag:
        alias_kwargs = dict(kwargs)
        alias_kwargs.pop("choices", None)
        alias_kwargs.pop("metavar", None)
        alias_kwargs.pop("type", None)
        alias_kwargs.update(
            action=ExplicitStore,
            nargs=0,
            const=_alias_value(spec),
            help=f"Same as {spec.long_flag}={_alias_value(spec)}.",
        )
        target.add_argument(spec.alias_flag, **alias_kwargs)


def _alias_value(spec: OptionSpec) -> str:
    """The value an alias flag stands for."""
    return {"--no-color": "never"}.get(spec.alias_flag, "never")


def int_type(spec: OptionSpec):
    """``type=`` callable producing good errors for integer options."""

    def convert(text: str) -> int:
        try:
            return int(str(text).strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"--{spec.name} expects an integer, got {text!r}"
            ) from exc

    return convert


def converter(spec: OptionSpec):
    """``type=`` callable delegating to the schema's own coercion."""

    def convert(text: str) -> Any:
        try:
            return spec.coerce(text)
        except (ValueError, TypeError) as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return convert


def _has_option(parser: argparse.ArgumentParser, *names: str) -> bool:
    """True when any of ``names`` is already registered on ``parser``."""
    existing = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    return any(name in existing for name in names)


def add_global_group(parser: argparse.ArgumentParser) -> None:
    """Register the cross-command flags on a subparser.

    argparse does not inherit the parent parser's options into a subcommand, so
    without this `sr config validate --config-file X` would be rejected even
    though `--config-file` is documented as global.  Registration is skipped for
    any flag the subcommand already defines itself, which keeps hand-written
    parsers (``rollback``, ``sessions``, …) authoritative.
    """
    from .cli_support import AppendExplicit, CountExplicit

    group = parser.add_argument_group("Global options")

    def add(*names: str, **kwargs) -> None:
        if _has_option(parser, *names):
            return
        group.add_argument(*names, **kwargs)

    add("-v", "--verbose", action=CountExplicit, nargs=0, default=False, dest="verbose",
        help="More output; repeat (-vv) for debug level.")
    add("-q", "--quiet", action=CountExplicit, nargs=0, default=False, dest="quiet",
        help="Only errors.")
    add("--debug", action=ExplicitStore, nargs=0, default=False, dest="debug",
        help="Debug logging (same as -vv).")
    add("--verbosity", action=ExplicitStore, default=None, metavar="LEVEL",
        choices=("quiet", "normal", "verbose", "debug"), help="Set the log level explicitly.")
    add("--color", action=ExplicitStore, default=None, metavar="WHEN",
        choices=("auto", "always", "never"), help="Colourise output (default: auto).")
    add("--no-color", action=ExplicitStore, nargs=0, default=None, dest="color",
        const="never", help="Same as --color=never.")
    add("--json", action=ExplicitStore, nargs=0, const=True, default=None, dest="json",
        help="Emit a single JSON document on stdout.")
    add("--porcelain", action=ExplicitStore, nargs=0, const=True, default=None, dest="porcelain",
        help="Emit stable machine-readable records.")
    add("-y", "--yes", action=ExplicitStore, nargs=0, const=True, default=None, dest="yes",
        help="Answer 'yes' to every prompt.")
    add("--no-update-check", action=ExplicitStore, nargs=0, default=False, dest="no_update_check",
        help="Do not check for a newer release.")
    add("--config", action=AppendExplicit, default=None, metavar="KEY=VALUE",
        help="Override one configuration value (repeatable).")
    add("--config-file", action=AppendExplicit, default=None, metavar="FILE",
        help="Read an extra configuration file (repeatable).")


def add_common_reporting(parser: argparse.ArgumentParser) -> None:
    """Backwards-compatible alias for :func:`add_global_group`."""
    add_global_group(parser)


def option_specs_for(command: str) -> list[OptionSpec]:
    """Every option that applies to ``command``, grouped for help output."""
    result: list[OptionSpec] = []
    for group_name, _title in GROUPS:
        for spec in OPTIONS:
            if spec.group == group_name and (not spec.commands or command in spec.commands):
                result.append(spec)
    return result


def all_flags() -> Iterable[str]:
    """Every flag sr accepts, sorted (used by completions and `help options`)."""
    from .cli import BY_FLAG  # local import: avoids a cycle at module load

    return sorted(BY_FLAG)

"""Runtime context shared by every command.

:class:`Context` is built once in :func:`sr.cli.main` and threaded into the
command implementations.  It owns the console, the merged configuration, the
"which options did the user actually type on the command line" bookkeeping and
a couple of process-wide guards (root check, update check).  Commands stay
pure: they read the context, do their work and return an exit code.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, build_config
from .console import Console, OutputFormat, Verbosity, make_console
from .errors import UsageError
from .options import BY_NAME, OptionSpec
from .version import __version__


@dataclass(slots=True)
class Context:
    """Everything a command needs to run."""

    argv: Sequence[str] = ()
    command: str = ""
    args: Any = None
    config: Config = field(default_factory=Config)
    console: Console = field(default_factory=Console)
    explicit: set[str] = field(default_factory=set)
    cwd: Path = field(default_factory=Path.cwd)
    assume_yes: bool = False
    stdin_is_tty: bool = False
    stdout_is_tty: bool = False
    program: str = "sr"

    # ------------------------------------------------------------ construction
    @classmethod
    def create(
        cls,
        *,
        argv: Sequence[str],
        args: Any,
        explicit: Iterable[str],
        command: str,
        program: str = "sr",
        target: str = ".",
    ) -> Context:
        explicit_set = {name for name in explicit if name}
        config = build_config(target=target, command=command)
        verbosity = _resolve_verbosity(args, explicit_set, config)
        color_option = _value(args, explicit_set, config, "color") or "auto"
        fmt = OutputFormat.JSON if _flag(args, explicit_set, config, "json") else (
            OutputFormat.PORCELAIN if _flag(args, explicit_set, config, "porcelain") else OutputFormat.TEXT
        )
        console = make_console(verbosity=verbosity, color=color_option, fmt=fmt)
        return cls(
            argv=list(argv),
            command=command,
            args=args,
            config=config,
            console=console,
            explicit=explicit_set,
            cwd=Path.cwd(),
            assume_yes=bool(_flag(args, explicit_set, config, "yes")),
            stdin_is_tty=_is_tty(sys.stdin),
            stdout_is_tty=_is_tty(sys.stdout),
            program=program,
        )

    # ----------------------------------------------------------------- values
    def value(self, name: str, default: Any = None) -> Any:
        """Effective value of an option: CLI → env/config → default.

        Values coming from a config file or an environment variable are raw
        strings, so they are coerced through the option's own schema here.  CLI
        values have already been converted by argparse, and the coercion is
        idempotent.
        """
        raw = _value(self.args, self.explicit, self.config, name, default)
        return coerce(name, raw)

    def flag(self, name: str) -> bool:
        return bool(_value(self.args, self.explicit, self.config, name, False))

    def path(self, name: str) -> Path | None:
        raw = self.value(name)
        if raw in (None, ""):
            return None
        return Path(os.path.expanduser(str(raw)))

    def list_value(self, name: str) -> list[str]:
        raw = self.value(name)
        if not raw:
            return []
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw if str(item)]
        return [item for item in str(raw).replace(",", " ").split() if item]

    def spec(self, name: str) -> OptionSpec:
        if name not in BY_NAME:
            raise UsageError(f"internal error: unknown option {name!r}")
        return BY_NAME[name]

    # ---------------------------------------------------------------- prompts
    def confirm(self, question: str, *, default: bool = False) -> bool:
        """Interactive yes/no with ``--yes`` and non-TTY handling."""
        if self.assume_yes:
            return True
        if not self.stdin_is_tty:
            self.console.warn(
                f"{question} — not a terminal, assuming 'no'",
                hint="pass --yes to run unattended",
            )
            return False
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            self.console.write_raw(self.console.paint(question + suffix, "bold"), stream=sys.stderr)
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            self.console.write_raw("\n", stream=sys.stderr)
            return False
        if not answer:
            return default
        if answer in {"y", "yes", "д", "да", "ok", "1", "true"}:
            return True
        if answer in {"n", "no", "н", "нет", "0", "false"}:
            return False
        return default

    # ------------------------------------------------------------------ guards
    def require_writable_root(self, root: Path) -> None:
        if not root.exists():
            raise UsageError(f"target directory does not exist: {root}")
        if not root.is_dir():
            raise UsageError(f"target is not a directory: {root} (use --file for a single file)")

    #: Commands that never modify a file, whatever their flags say.
    READ_ONLY_COMMANDS = frozenset(
        {"find", "diff", "sessions", "doctor", "config", "help", "completions", "selftest", "update"}
    )

    @property
    def dry_run(self) -> bool:
        """True when this run cannot modify any file.

        Covers three cases that must all bypass the destructive-run guards:
        an explicit ``--dry-run``, a read-only command, and a write mode that
        leaves the originals alone (``--write=stdout`` / ``--write=dir``).
        """
        if getattr(self.args, "dry_run", False):
            return True
        if self.command in self.READ_ONLY_COMMANDS:
            return True
        write_mode = str(getattr(self.args, "write", "") or "")
        return write_mode in {"stdout", "dir"}

    def refuse_root_user(self) -> None:
        """A mutating run as root is almost always a mistake."""
        if self.value("allow-root", False):
            return
        if self.dry_run:
            return
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise UsageError(
                "refusing to modify files as root",
                hint="re-run as the file owner, or pass --allow-root if you really mean it",
            )

    def full_command(self) -> list[str]:
        """The command line as invoked, for the session manifest."""
        return [self.program, *self.argv]


def _is_tty(stream: Any) -> bool:
    try:
        return bool(stream and stream.isatty())
    except Exception:  # pragma: no cover
        return False


def _flag(args: Any, explicit: set[str], config: Config, name: str) -> bool:
    return bool(_value(args, explicit, config, name, False))


def _key(name: str) -> str:
    """Normalise an option name to its namespace attribute / config key."""
    return name.replace("-", "_")


def _resolve_verbosity(args: Any, explicit: set[str], config: Config) -> Verbosity:
    level = _value(args, explicit, config, "verbosity", "normal")
    resolved = Verbosity.from_option(level)
    if "verbose" in explicit and getattr(args, "verbose", False):
        resolved = Verbosity.VERBOSE
    if "quiet" in explicit and getattr(args, "quiet", False):
        resolved = Verbosity.QUIET
    # -vv / -qq from the counting action
    count = getattr(args, "verbosity_count", 0) or 0
    if count:
        base = int(Verbosity.NORMAL) + count
        resolved = Verbosity(max(int(Verbosity.QUIET), min(int(Verbosity.DEBUG), base)))
    return resolved


def coerce(name: str, value: Any) -> Any:
    """Bring a raw value into the type the schema declares for ``name``."""
    spec = BY_NAME.get(name.replace("_", "-"))
    if spec is None or value is None or value == "":
        return value
    from .options import OptionKind
    from .util import parse_duration, parse_size

    try:
        if spec.kind == OptionKind.INT:
            return value if isinstance(value, int) else int(str(value).strip())
        if spec.kind == OptionKind.SIZE:
            if isinstance(value, int):
                return value
            size = parse_size(str(value))
            return size if size is not None else value
        if spec.kind == OptionKind.DURATION:
            if isinstance(value, (int, float)):
                return float(value)
            seconds = parse_duration(str(value))
            return seconds if seconds is not None else value
        if spec.kind == OptionKind.FLAG:
            return value if isinstance(value, bool) else str(value).strip().lower() in {
                "1", "true", "yes", "y", "on",
            }
        if spec.kind == OptionKind.LIST and not isinstance(value, (list, tuple)):
            return [item for item in str(value).replace(",", " ").split() if item]
    except (ValueError, TypeError):
        return value
    return value


def _value(args: Any, explicit: set[str], config: Config, name: str, default: Any = None) -> Any:
    """Resolve one option: command line → env/config → schema default.

    Three details make this less trivial than it looks:

    * A flag the user actually typed is in ``explicit``.  For booleans we must
      trust the namespace even when the value is ``False`` — that is how
      ``--no-backup`` can beat ``backup = true`` in a config file.
    * A *repeating* option (``-p``/``--exclude``/``--file``) has ``None`` as its
      argparse default and a list once given.  Config may also supply a list, so
      the namespace is consulted first whenever it holds a non-``None`` value,
      even if the dest is not in ``explicit``.
    * Empty containers are treated as "not set" so that a config file cannot be
      shadowed by an argparse default of ``[]``.
    """
    key = _key(name)
    spec = BY_NAME.get(key) or BY_NAME.get(name)
    is_flag = spec is None or spec.kind == "flag"

    if args is not None:
        raw = getattr(args, key, None)
        if key in explicit:
            if raw is not None:
                return raw
            if is_flag:
                return bool(raw)
        elif raw is not None and raw != () and raw != []:
            return raw

    configured = config.get(key)
    if configured is not None and configured != () and configured != []:
        return configured
    if default is not None:
        return default
    if spec is not None:
        return spec.default
    return configured


def version_banner() -> str:
    return f"sr {__version__}"


def environment_snapshot() -> dict[str, str]:
    """``SR_*``/``NO_COLOR`` variables, for ``doctor`` and ``--json`` output."""
    keys = [key for key in os.environ if key.startswith("SR_")]
    keys += [key for key in ("NO_COLOR", "CLICOLOR_FORCE", "TERM", "EDITOR") if key in os.environ]
    return {key: os.environ[key] for key in sorted(keys)}

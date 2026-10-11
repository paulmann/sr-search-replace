"""Command implementations.

Each subcommand lives in its own module and exposes ``cmd_<name>(context) ->
int``.  This package re-exports them so :mod:`sr.cli` can dispatch with
``getattr(commands, f"cmd_{name}")`` without importing every module by hand.
"""

from __future__ import annotations

from . import parsers
from .completions import cmd_completions, detect_shell, generate, install
from .config import cmd_config
from .doctor import cmd_doctor
from .replace import cmd_diff, cmd_find, cmd_rename, cmd_replace, resolve_positionals
from .selftest import cmd_selftest
from .sessions import cmd_rollback, cmd_sessions, show_session_diff
from .update import cmd_update, detect_installation, maybe_check_for_update

__all__ = [
    # Command entry points, dispatched by sr.cli as `cmd_<name>`.
    "cmd_completions",
    "cmd_config",
    "cmd_diff",
    "cmd_doctor",
    "cmd_find",
    "cmd_rename",
    "cmd_replace",
    "cmd_rollback",
    "cmd_selftest",
    "cmd_sessions",
    "cmd_update",
    # Helpers other layers reach through this package.
    "detect_installation",
    "detect_shell",
    "generate",
    "install",
    "maybe_check_for_update",
    "parsers",
    "resolve_positionals",
    "show_session_diff",
]


def __getattr__(name: str):
    """Lazily expose the help-system entry points.

    ``sr.help_system`` imports ``sr.cli``, so importing it at module scope here
    would create a cycle.  Resolving it on first attribute access keeps the
    dispatch table uniform without the cycle.
    """
    if name in {"cmd_help", "show_help", "help_all", "print_version", "render_topic", "overview"}:
        from .. import help_system

        return getattr(help_system, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

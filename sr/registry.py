"""The subcommand registry.

Kept separate from :mod:`sr.cli` because the help system, the man page and the
completion generator all need the command list, and importing the parser from
those modules would create an import cycle (``cli → commands → help_system →
cli``).  Data has no dependencies; code does.
"""

from __future__ import annotations

PROG = "sr"

#: ``(name, aliases, one-line summary)`` for every subcommand, in help order.
SUBCOMMANDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("replace", ("r",), "Search and replace text in files."),
    ("find", ("search", "grep"), "Show matches without changing anything."),
    ("diff", (), "Preview changes as a unified diff."),
    ("rename", ("mv",), "Rename files and directories by pattern."),
    ("rollback", ("undo", "restore"), "Restore files from a session backup."),
    ("sessions", ("backups",), "List, inspect and prune sessions."),
    ("update", ("upgrade",), "Check for and install a newer sr release."),
    ("doctor", ("diagnose",), "Check the installation and environment."),
    ("completions", (), "Print shell completion scripts."),
    ("config", (), "Inspect and manage configuration files."),
    ("selftest", ("smoke",), "Run the built-in smoke test."),
    ("help", (), "Show the help system (topics, options, examples)."),
)

SUBCOMMAND_NAMES: frozenset[str] = frozenset(name for name, _, _ in SUBCOMMANDS)

SUBCOMMAND_ALIASES: dict[str, str] = {
    alias: name for name, aliases, _ in SUBCOMMANDS for alias in aliases
}

#: Canonical usage line per subcommand (used by help and the man page).
USAGE: dict[str, str] = {
    "replace": f"{PROG} replace [GLOB...] SEARCH REPLACE [options]",
    "find": f"{PROG} find [GLOB...] SEARCH [options]",
    "diff": f"{PROG} diff [GLOB...] SEARCH REPLACE | --session ID [options]",
    "rename": f"{PROG} rename [GLOB...] SEARCH REPLACE [options]",
    "rollback": f"{PROG} rollback [SESSION] [--file PATH] [--dry-run] [--force]",
    "sessions": f"{PROG} sessions [list|show|delete|prune|size] [SESSION]",
    "update": f"{PROG} update [--check] [--version X] [--channel stable|prerelease]",
    "doctor": f"{PROG} doctor [--json] [--offline]",
    "completions": f"{PROG} completions [bash|zsh|fish] [--install]",
    "config": f"{PROG} config [list|show|init|path|validate] [KEY]",
    "selftest": f"{PROG} selftest [--quick] [--json]",
    "help": f"{PROG} help [TOPIC|COMMAND|--OPTION]",
}


def normalize_alias(name: str) -> str:
    """Map an alias onto its canonical subcommand name."""
    return SUBCOMMAND_ALIASES.get(name, name)


def summary_of(name: str) -> str:
    for item, _aliases, summary in SUBCOMMANDS:
        if item == name:
            return summary
    return ""


def usage_of(name: str) -> str:
    return USAGE.get(name, f"{PROG} {name} [options]")

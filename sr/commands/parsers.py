"""Argument parsers for every subcommand.

Each ``build_<command>_parser`` function is called from
:func:`sr.cli.build_parser`.  Options come from the single schema in
:mod:`sr.options`, so a new flag automatically appears in the parser, in the
config file layer, in completions and in the man page.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from typing import Any

from ..cli_support import (
    ExplicitStore,
    add_common_reporting,
    add_global_group,
    add_option_group,
    add_subcommand,
)

#: Option groups added to the mutating/searching commands, in display order.
SEARCH_GROUPS: tuple[str, ...] = ("targets", "matching", "action", "safety", "reporting")


def _add_groups(parser: argparse.ArgumentParser, command: str, groups: Iterable[str] = SEARCH_GROUPS) -> None:
    add_global_group(parser)
    for group in groups:
        add_option_group(parser, group, command)


def _add_positionals(parser: argparse.ArgumentParser, spec: Sequence[tuple[str, str, str]]) -> None:
    """Add the flexible positional argument(s).

    ``dest`` is set explicitly: argparse would otherwise derive it from the
    metavar (``<positionals>``), which is not a valid attribute name.
    """
    for metavar, nargs, help_text in spec:
        parser.add_argument(
            "positionals",
            nargs=nargs,
            default=None,
            help=help_text,
            metavar=metavar.strip("<>") or "ARGS",
        )


def build_replace_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _REPLACE_DESCRIPTION)
    _add_positionals(
        parser,
        [("<positionals>", "*", "GLOB SEARCH REPLACE, or SEARCH REPLACE (GLOB defaults to '*').")],
    )
    _add_groups(parser, "replace")
    parser.add_argument(
        "--glob", action="append", default=None, dest="glob_option", metavar="GLOB",
        help="File name pattern (repeatable); alternative to the positional GLOB.",
    )
    return parser


def build_find_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _FIND_DESCRIPTION)
    _add_positionals(parser, [("<positionals>", "*", "SEARCH, or GLOB SEARCH.")])
    _add_groups(parser, "find", ("targets", "matching", "reporting"))
    parser.add_argument(
        "--glob", action="append", default=None, dest="glob_option", metavar="GLOB",
        help="File name pattern (repeatable).",
    )
    parser.add_argument(
        "-c", "--count", action=ExplicitStore, nargs=0, const=True, default=None, dest="count_only",
        help="Print only the number of matches per file (and a total).",
    )
    parser.add_argument(
        "-l", "--files-with-matches", action=ExplicitStore, nargs=0, const=True, default=None, dest="list_files",
        help="Print only the names of files that contain a match.",
    )
    parser.add_argument(
        "-A", "--after-context", type=int, default=None, metavar="N",
        help="Print N lines after each match.",
    )
    parser.add_argument(
        "-B", "--before-context", type=int, default=None, metavar="N",
        help="Print N lines before each match.",
    )
    parser.add_argument(
        "--highlight", action=ExplicitStore, nargs=0, const=True, default=None, dest="highlight",
        help="Force match highlighting even when writing to a file.",
    )
    return parser


def build_diff_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _DIFF_DESCRIPTION)
    _add_positionals(parser, [("<positionals>", "*", "SEARCH REPLACE, or GLOB SEARCH REPLACE.")])
    _add_groups(parser, "diff", ("targets", "matching", "reporting"))
    parser.add_argument(
        "--session", default=None, metavar="SESSION",
        help="Instead of computing a diff, show the diff stored in a session.",
    )
    return parser


def build_rename_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _RENAME_DESCRIPTION)
    _add_positionals(parser, [("<positionals>", "*", "SEARCH REPLACE, or GLOB SEARCH REPLACE.")])
    _add_groups(parser, "rename", ("targets", "matching", "action", "safety", "reporting"))
    parser.add_argument(
        "--directories", action=ExplicitStore, nargs=0, const=True, default=None,
        help="Also rename directories that match (default: files only).",
    )
    return parser


def build_rollback_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _ROLLBACK_DESCRIPTION)
    parser.add_argument("session", nargs="?", default=None, metavar="SESSION",
                        help="Session id, unique suffix, ordinal (1 = newest) or 'latest' (default).")
    parser.add_argument("--file", action="append", default=None, dest="file_filter", metavar="PATH",
                        help="Restore only files whose path contains PATH (repeatable).")
    parser.add_argument("-C", "--target", default=".", metavar="DIR", help="Project root (default: cwd).")
    parser.add_argument("--backup-dir", default=None, metavar="DIR", help="Where sessions live (default <target>/.sr).")
    parser.add_argument("-n", "--dry-run", action=ExplicitStore, nargs=0, const=True, default=None,
                        help="Show what would be restored without writing.")
    parser.add_argument("-f", "--force", action=ExplicitStore, nargs=0, const=True, default=None,
                        help="Restore even files that changed after the session.")
    parser.add_argument("--no-safety-snapshot", action=ExplicitStore, nargs=0, const=False,
                        default=None, dest="safety_snapshot",
                        help="Do not record the pre-rollback state (not recommended).")
    add_common_reporting(parser)
    return parser


def build_sessions_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _SESSIONS_DESCRIPTION)
    parser.add_argument("action", nargs="?", default="list",
                        choices=("list", "show", "delete", "prune", "size"),
                        help="What to do (default: list).")
    parser.add_argument("session", nargs="?", default=None, metavar="SESSION",
                        help="Session id for show/delete.")
    parser.add_argument("-C", "--target", default=".", metavar="DIR", help="Project root (default: cwd).")
    parser.add_argument("--backup-dir", default=None, metavar="DIR", help="Where sessions live.")
    parser.add_argument("--limit", type=int, default=20, metavar="N", help="List at most N sessions (default 20).")
    parser.add_argument("--files", action=ExplicitStore, nargs=0, const=True, default=None, help="Include the per-file list in `show`.")
    parser.add_argument("--keep", type=int, default=None, metavar="N", help="For prune: keep the newest N sessions.")
    parser.add_argument("--keep-age", default=None, metavar="AGE", help="For prune: drop sessions older than AGE.")
    add_common_reporting(parser)
    return parser


def build_update_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _UPDATE_DESCRIPTION)
    parser.add_argument("--check", action=ExplicitStore, nargs=0, const=True, default=None, help="Only report whether an update exists.")
    parser.add_argument("--version", default=None, metavar="VERSION", help="Install a specific version (e.g. 7.1.0).")
    parser.add_argument("--channel", default="stable", choices=("stable", "prerelease"),
                        help="Release channel (default: stable).")
    parser.add_argument("--source", default="github", choices=("github", "npm", "pypi"),
                        help="Where to fetch release information (default: github).")
    parser.add_argument("--repo", default=None, metavar="OWNER/NAME", help="Override the GitHub repository.")
    parser.add_argument("--timeout", type=float, default=20.0, metavar="SECONDS", help="Network timeout (default 20).")
    parser.add_argument("--path", default=None, metavar="FILE", help="Standalone file to update (default: auto-detect).")
    parser.add_argument("--no-verify", action=ExplicitStore, nargs=0, const=True, default=None,
                        help="Install without SHA-256 verification. Verification is "
                             "fail-closed by default, so this is the only way past a "
                             "missing checksum — use it only for a mirror you trust.")
    parser.add_argument("-f", "--force", action=ExplicitStore, nargs=0, const=True, default=None,
                        help="Reinstall even when the version is already current.")
    add_common_reporting(parser)
    return parser


def build_doctor_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _DOCTOR_DESCRIPTION)
    parser.add_argument("-C", "--target", default=".", metavar="DIR", help="Project root to check (default: cwd).")
    parser.add_argument("--offline", action=ExplicitStore, nargs=0, const=True, default=None, help="Skip the network reachability probe.")
    add_common_reporting(parser)
    return parser


def build_completions_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _COMPLETIONS_DESCRIPTION)
    parser.add_argument("shell", nargs="?", default=None, choices=("bash", "zsh", "fish"),
                        help="Shell to generate for (default: auto-detect from $SHELL).")
    parser.add_argument("--install", action=ExplicitStore, nargs=0, const=True, default=None,
                        help="Write the script into the shell's completion directory.")
    add_common_reporting(parser)
    return parser


def build_config_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _CONFIG_DESCRIPTION)
    parser.add_argument("action", nargs="?", default="list",
                        choices=("list", "show", "init", "path", "validate"),
                        help="What to do (default: list).")
    parser.add_argument("key", nargs="?", default=None, metavar="KEY",
                        help="Option name for `show` (also accepted as --<name>).")
    parser.add_argument("--option", default=None, metavar="NAME",
                        help="Option name for `show`, e.g. `sr config show --option max-size`.")
    parser.add_argument("-C", "--target", default=".", metavar="DIR", help="Project root (default: cwd).")
    parser.add_argument("--file", default=None, metavar="PATH", help="Config file for `init` (default: user config).")
    parser.add_argument("--show-origin", action=ExplicitStore, nargs=0, const=True, default=None, help="Show where each value comes from.")
    parser.add_argument("-f", "--force", action=ExplicitStore, nargs=0, const=True, default=None, help="Overwrite an existing file in `init`.")
    add_common_reporting(parser)
    return parser


def build_selftest_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _SELFTEST_DESCRIPTION)
    parser.add_argument("--quick", action=ExplicitStore, nargs=0, const=True, default=None, help="Run only the essential checks.")
    parser.add_argument("--keep", action=ExplicitStore, nargs=0, const=True, default=None, help="Keep the temporary workspace for inspection.")
    add_common_reporting(parser)
    return parser


def build_help_parser(subparsers: Any, name: str, aliases: Sequence[str], summary: str) -> argparse.ArgumentParser:
    parser = add_subcommand(subparsers, name, aliases, summary, _HELP_DESCRIPTION)
    parser.add_argument("topic", nargs="?", default=None, metavar="TOPIC",
                        help="Help topic, subcommand name, or an option written "
                             "without its leading dashes (see `sr help topics`).")
    # `sr help --max-count` must explain the option, not be parsed as one.
    # sr.cli rewrites a leading `--flag` topic into `--option flag`; see
    # `_rewrite_help_topic`.
    parser.add_argument("--option", default=None, metavar="NAME",
                        help="Explain one option, e.g. `sr help --option max-count`.")
    parser.add_argument("--all", action=ExplicitStore, nargs=0, const=True, default=None, help="Same as `sr --help-all`.")
    parser.add_argument("--no-pager", action=ExplicitStore, nargs=0, const=True, default=None, help="Never page the output.")
    parser.add_argument("--json", action=ExplicitStore, nargs=0, const=True, default=None,
                        dest="json", help="Emit the help document as JSON.")
    return parser


# ---------------------------------------------------------------------------
# Long descriptions (shown by `sr <command> --help`)
# ---------------------------------------------------------------------------

_REPLACE_DESCRIPTION = """\
Replace SEARCH with REPLACE in the selected files.

Positional arguments are flexible:
  sr replace SEARCH REPLACE              every text file below the target
  sr replace GLOB SEARCH REPLACE         only files whose name matches GLOB
  sr replace GLOB... -- SEARCH REPLACE   several globs, unambiguously

Nothing is written until the scan phase has counted every change, so the
confirmation prompt and the summary always report the real numbers.  Every
mutating run creates a session under <target>/.sr that can be restored with
`sr rollback`.

Examples:
  sr replace "TODO" "FIXME" -p '*.py'
  sr replace 'foo\\(bar\\)' 'foo(bar)' --regex -p '*.js'
  sr replace old new -C ~/project --diff --dry-run
  sr replace 'a' 'b' --interactive
"""

_FIND_DESCRIPTION = """\
Show where SEARCH occurs, without modifying anything.

Output modes:
  default      file:line:col and the matching line, with the match highlighted
  --count      the number of matches per file, then a total
  -l           only the names of files that match
  --json       a machine-readable document
  --porcelain  stable `path:line:col:match:text` records

Examples:
  sr find "deprecated_api" -p '*.py'
  sr find 'version\\s*=\\s*["\\']1' --regex -l
  sr find old --count -C src
"""

_DIFF_DESCRIPTION = """\
Show the changes a replace would make, as a unified diff.

Equivalent to `sr replace --dry-run --diff`, with one addition: `--session ID`
prints the diff that a previous run recorded, which is how you review what a
colleague's (or your own) session actually did.

Examples:
  sr diff "foo" "bar" -p '*.md'
  sr diff --session latest
  sr diff --session 2 -C ~/project
"""

_RENAME_DESCRIPTION = """\
Rename files (and with --directories also folders) by replacing SEARCH with
REPLACE in their names.  Content is not touched.

Renames are planned deepest-path-first so that renaming a directory does not
invalidate the paths of its children, and they are journalled in a session so
`sr rollback` undoes them.

Examples:
  sr rename ".txt" ".md" -p '*.txt'
  sr rename "IMG_" "photo-" -p 'IMG_*'
  sr rename --regex "^(.*)\\.jpeg$" "\\\\1.jpg"
"""

_ROLLBACK_DESCRIPTION = """\
Restore files from a session backup.

SESSION may be a full id (20260131T120530Z-9f3a1c), its unique suffix
(9f3a1c), an ordinal (1 = newest, 2 = the one before), or 'latest' (default).

Safety:
  * files whose content changed *after* the session are reported as "drifted"
    and skipped unless --force is given;
  * unless --no-safety-snapshot is passed, the pre-rollback state is itself
    recorded in a new session, so an erroneous rollback can be rolled back;
  * renames recorded in the session are undone in reverse order.

Examples:
  sr rollback
  sr rollback 9f3a1c --dry-run
  sr rollback latest --file src/app.py --yes
"""

_SESSIONS_DESCRIPTION = """\
List, inspect and prune the sessions stored under <target>/.sr.

  sr sessions                  newest first, one line each
  sr sessions show latest      full manifest, including per-file records
  sr sessions size             total disk usage of the session store
  sr sessions prune --keep 10  delete everything but the newest 10
  sr sessions delete <id> -y   remove one session
"""

_UPDATE_DESCRIPTION = """\
Check for a newer sr release and install it.

How the update is applied depends on the installation type, which sr detects
automatically (`sr doctor` shows it):

  standalone file   the release asset is downloaded, its SHA-256 verified
                    against sr.pyz.sha256, the download smoke-tested in a
                    subprocess, the previous copy kept as <file>.bak, and only
                    then the file replaced atomically. Verification is
                    fail-closed: a missing or mismatched checksum aborts the
                    update and installs nothing (--no-verify is the only,
                    loudly warned, way past that)
  pip / pipx        sr prints the exact upgrade command and exits
  npm               sr prints `npm install -g sr-search-replace@latest`

A quiet background check runs at most once per 24h; disable it with
--no-update-check or SR_NO_UPDATE_CHECK=1.

Examples:
  sr update --check
  sr update
  sr update --version 7.1.0 --channel prerelease
"""

_DOCTOR_DESCRIPTION = """\
Verify that this sr installation can actually do its job.

Checks: Python version, installation type and writability, session store
health and disk usage, configuration files (parsed and validated), the
effective option values, filesystem capabilities (atomic rename, symlink
support, inode preservation) and — unless --offline — whether the update
endpoints are reachable.
"""

_COMPLETIONS_DESCRIPTION = """\
Print (or install) a shell completion script.

  sr completions bash >> ~/.bash_completion
  sr completions zsh  > "${fpath[1]}/_sr"
  sr completions fish > ~/.config/fish/completions/sr.fish
  sr completions --install        # writes to the detected location

The scripts are generated from the live option schema, so they never drift
out of sync with the parser.
"""

_CONFIG_DESCRIPTION = """\
Inspect and manage sr configuration files.

  sr config list --show-origin    every option, its value and where it came from
  sr config show ignore-case      one option in detail
  sr config init                  write a documented template to ~/.config/sr/sr.conf
  sr config path                  print the files sr would read, in priority order
  sr config validate              parse every config file and report errors

Precedence: CLI > SR_* environment > project .srrc > user config > /etc/sr/sr.conf.
"""

_SELFTEST_DESCRIPTION = """\
Run a built-in smoke test against a temporary directory.

It exercises discovery, literal and regex matching, in-place writing, session
creation and rollback, then verifies the bytes on disk.  Use it after
installing or upgrading sr: exit status 0 means the tool works.
"""

_HELP_DESCRIPTION = """\
Show the sr manual.

  sr help                 command overview and quick start
  sr help topics          every available topic
  sr help <topic>         one topic (options, matching, backup, update, …)
  sr help <command>       full help for one subcommand
  sr help --<option>      explain a single option
  sr help --all           everything, in one document
  sr help --json          the same content as machine-readable JSON
"""

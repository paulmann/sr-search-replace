"""The option schema: one declaration driving CLI, config files, env vars and docs.

Every tunable is declared once in :data:`OPTIONS`.  From that single table the
package derives

* the ``argparse`` definition (:mod:`sr.cli`),
* config-file keys and their validation (:mod:`sr.config`),
* environment variable names,
* ``sr help options`` / ``sr config list`` output,
* the ``OPTIONS`` section of the man page,
* shell completions.

Adding an option is therefore a one-line change plus (optionally) a use of the
value in a command — there is no fourth place that can drift out of sync.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .pattern import PatternKind


class OptionKind:
    FLAG = "flag"
    STRING = "string"
    INT = "int"
    SIZE = "size"
    DURATION = "duration"
    CHOICE = "choice"
    LIST = "list"
    PATH = "path"


@dataclass(frozen=True, slots=True)
class OptionSpec:
    """Declarative description of one tunable."""

    name: str
    kind: str = OptionKind.FLAG
    short: str = ""
    default: Any = None
    choices: tuple[str, ...] = ()
    env: str = ""
    group: str = "general"
    help: str = ""
    metavar: str = ""
    negatable: bool = False
    #: An extra long flag that is accepted as a synonym (``--no-color`` for
    #: ``--color never``).  Declared here so the flag is part of the schema and
    #: therefore shows up in help, the man page, the completions and the docs —
    #: a hand-written alias in the parser used to be invisible to all of them.
    alias_flag: str = ""
    repeatable: bool = False
    commands: tuple[str, ...] = ()
    hidden: bool = False
    #: False for options that only make sense per invocation.  They stay in the
    #: CLI, help and completions, but are not readable from a config file or an
    #: SR_* variable — `json = true` in a config file would turn every command
    #: into a JSON emitter, which is never what somebody meant.
    configurable: bool = True
    parse: Callable[[str], Any] | None = None

    @property
    def long_flag(self) -> str:
        return f"--{self.name}"

    @property
    def config_key(self) -> str:
        return self.name.replace("-", "_")

    @property
    def env_var(self) -> str:
        return self.env or f"SR_{self.name.replace('-', '_').upper()}"

    @property
    def takes_value(self) -> bool:
        return self.kind != OptionKind.FLAG

    def flag_names(self) -> tuple[str, ...]:
        names = [self.long_flag]
        if self.short:
            names.insert(0, f"-{self.short}")
        if self.negatable and self.kind == OptionKind.FLAG:
            names.append(f"--no-{self.name}")
        if self.alias_flag and self.alias_flag not in names:
            names.append(self.alias_flag)
        return tuple(names)

    def coerce(self, raw: Any) -> Any:
        """Validate/convert a value coming from a config file or env var."""
        if raw is None:
            return self.default
        if self.kind == OptionKind.FLAG:
            return parse_bool(raw)
        if self.kind == OptionKind.LIST:
            if isinstance(raw, (list, tuple)):
                return [str(item) for item in raw]
            return split_list(str(raw))
        if self.kind == OptionKind.INT:
            return int(str(raw).strip())
        if self.kind == OptionKind.SIZE:
            from .util import parse_size

            text = str(raw).strip()
            # A bare number means megabytes here, exactly like on the command
            # line: nobody writes `max-size = 104857600` in a config file
            # deliberately, and the two spellings must not disagree.
            if text.isdigit():
                return int(text) * 1024 * 1024
            size = parse_size(text)
            if size is None:
                raise ValueError(f"{self.name}: invalid size {raw!r}")
            return size
        if self.kind == OptionKind.DURATION:
            from .util import parse_duration

            seconds = parse_duration(str(raw))
            if seconds is None:
                raise ValueError(f"{self.name}: invalid duration {raw!r}")
            return seconds
        if self.kind == OptionKind.CHOICE:
            text = str(raw).strip().lower()
            if self.choices and text not in self.choices:
                raise ValueError(
                    f"{self.name}: {raw!r} is not one of {', '.join(self.choices)}"
                )
            return text
        return str(raw)


def parse_bool(raw: Any) -> bool:
    """Config/env boolean parsing (``yes``, ``1``, ``on``, ``true`` …)."""
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disable", "disabled", ""}:
        return False
    raise ValueError(f"invalid boolean {raw!r} (use true/false)")


def split_list(raw: str) -> list[str]:
    """Split a comma- or whitespace-separated list, dropping empties."""
    text = raw.replace(",", " ")
    return [item for item in text.split() if item]


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------

#: Commands that select files from a tree.
_SEARCH_COMMANDS = ("replace", "find", "diff", "rename")

#: Commands that interpret a search pattern (all of them).
_MATCH_COMMANDS = ("replace", "find", "diff", "rename")

#: Kept for readability in the schema below.
_REPLACE_COMMANDS = _SEARCH_COMMANDS

OPTIONS: tuple[OptionSpec, ...] = (
    # -- targets ---------------------------------------------------------
    OptionSpec(
        "target",
        OptionKind.PATH,
        short="C",
        default=".",
        group="targets",
        metavar="DIR",
        commands=_SEARCH_COMMANDS,
        help="Directory to operate on (default: the current directory).",
    ),
    OptionSpec(
        "pattern",
        OptionKind.LIST,
        short="p",
        default=(),
        group="targets",
        metavar="GLOB",
        repeatable=True,
        commands=_SEARCH_COMMANDS,
        help="Only files whose name matches GLOB (repeatable). Default: every text file.",
    ),
    OptionSpec(
        "file",
        OptionKind.LIST,
        default=(),
        group="targets",
        metavar="PATH",
        repeatable=True,
        commands=_SEARCH_COMMANDS,
        help="Explicit file to process, bypassing pattern matching (repeatable).",
    ),
    OptionSpec(
        "files-from",
        OptionKind.PATH,
        default=None,
        group="targets",
        metavar="FILE",
        commands=_SEARCH_COMMANDS,
        help="Read the list of target files from FILE, one per line ('-' for stdin).",
    ),
    OptionSpec(
        "include",
        OptionKind.LIST,
        default=(),
        group="targets",
        metavar="GLOB",
        repeatable=True,
        commands=_SEARCH_COMMANDS,
        help="Additional include glob, may contain '/' and '**' (repeatable).",
    ),
    OptionSpec(
        "exclude",
        OptionKind.LIST,
        default=(),
        group="targets",
        metavar="GLOB",
        repeatable=True,
        commands=_SEARCH_COMMANDS,
        help="Exclude glob; wins over --include and --pattern (repeatable).",
    ),
    OptionSpec(
        "exclude-dir",
        OptionKind.LIST,
        default=(),
        group="targets",
        metavar="NAME",
        repeatable=True,
        commands=_SEARCH_COMMANDS,
        help="Prune a directory by name (repeatable; adds to the built-in list).",
    ),
    OptionSpec(
        "no-default-excludes",
        OptionKind.FLAG,
        group="targets",
        commands=_SEARCH_COMMANDS,
        help="Do not apply the built-in exclude lists (node_modules, dist, *.bak …).",
    ),
    OptionSpec(
        "no-recursive",
        OptionKind.FLAG,
        short="1",
        # Not negatable: `--no-no-recursive` would be absurd.  The positive
        # form is the default (recursive), so this flag is the only switch.
        negatable=False,
        group="targets",
        commands=_SEARCH_COMMANDS,
        help="Only the target directory itself, no subdirectories.",
    ),
    OptionSpec(
        "max-depth",
        OptionKind.INT,
        default=None,
        group="targets",
        metavar="N",
        commands=_SEARCH_COMMANDS,
        help="Descend at most N levels below the target directory.",
    ),
    OptionSpec(
        "hidden",
        OptionKind.FLAG,
        group="targets",
        commands=_SEARCH_COMMANDS,
        help="Also process dotfiles and dot-directories.",
    ),
    OptionSpec(
        "symlinks",
        OptionKind.FLAG,
        group="targets",
        commands=_SEARCH_COMMANDS,
        help="Follow symlinked files (they are skipped by default).",
    ),
    OptionSpec(
        "gitignore",
        OptionKind.FLAG,
        group="targets",
        commands=_SEARCH_COMMANDS,
        help="Honour .gitignore files (including the global excludesfile).",
    ),
    OptionSpec(
        "max-size",
        OptionKind.SIZE,
        default=100 * 1024 * 1024,
        group="targets",
        metavar="SIZE",
        commands=_SEARCH_COMMANDS,
        help="Skip files larger than SIZE (default 100MB; a bare number means megabytes).",
    ),
    OptionSpec(
        "min-size",
        OptionKind.SIZE,
        default=None,
        group="targets",
        metavar="SIZE",
        commands=_SEARCH_COMMANDS,
        help="Skip files smaller than SIZE.",
    ),
    OptionSpec(
        "newer-than",
        OptionKind.DURATION,
        default=None,
        group="targets",
        metavar="AGE",
        commands=_SEARCH_COMMANDS,
        help="Only files modified within AGE (e.g. 24h, 7d, 30m).",
    ),
    OptionSpec(
        "older-than",
        OptionKind.DURATION,
        default=None,
        group="targets",
        metavar="AGE",
        commands=_SEARCH_COMMANDS,
        help="Only files not modified within AGE.",
    ),
    OptionSpec(
        "binary",
        OptionKind.FLAG,
        group="targets",
        commands=_SEARCH_COMMANDS,
        help="Process files that look binary instead of skipping them.",
    ),
    # -- matching ---------------------------------------------------------
    OptionSpec(
        "regex",
        OptionKind.FLAG,
        short="R",
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Treat PATTERN as a Python regular expression.",
    ),
    OptionSpec(
        "ere",
        OptionKind.FLAG,
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Treat PATTERN as a POSIX Extended Regular Expression (grep -E syntax).",
    ),
    OptionSpec(
        "pcre",
        OptionKind.FLAG,
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Treat PATTERN as a Perl-compatible regular expression.",
    ),
    OptionSpec(
        "pattern-kind",
        OptionKind.CHOICE,
        choices=tuple(kind.value for kind in PatternKind),
        default=PatternKind.FIXED.value,
        group="matching",
        metavar="KIND",
        commands=_MATCH_COMMANDS,
        help="Explicit pattern kind: fixed, regex, ere or pcre.",
    ),
    OptionSpec(
        "ignore-case",
        OptionKind.FLAG,
        short="i",
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Case-insensitive matching.",
    ),
    OptionSpec(
        "word-boundary",
        OptionKind.FLAG,
        short="w",
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Match whole words only.",
    ),
    OptionSpec(
        "multiline",
        OptionKind.FLAG,
        short="m",
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Allow the pattern to span line boundaries ('.' matches newline).",
    ),
    OptionSpec(
        "no-escapes",
        OptionKind.FLAG,
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Do not interpret \\n, \\t, \\xNN in fixed patterns/replacements.",
    ),
    OptionSpec(
        "line-range",
        OptionKind.STRING,
        short="L",
        default=None,
        group="matching",
        metavar="RANGE",
        commands=_REPLACE_COMMANDS,
        help="Restrict edits to a line range: 10, 10-20, 10- or -20.",
    ),
    OptionSpec(
        "max-count",
        OptionKind.INT,
        short="M",
        default=None,
        group="matching",
        metavar="N",
        commands=_REPLACE_COMMANDS,
        help="Replace at most N occurrences per file.",
    ),
    OptionSpec(
        "no-global",
        OptionKind.FLAG,
        group="matching",
        commands=_MATCH_COMMANDS,
        help="Replace only the first occurrence on each line.",
    ),
    OptionSpec(
        "context",
        OptionKind.INT,
        default=3,
        group="matching",
        metavar="N",
        commands=_REPLACE_COMMANDS,
        help="Lines of context around a match in preview/diff output (default 3).",
    ),
    # -- action -----------------------------------------------------------
    OptionSpec(
        "dry-run",
        OptionKind.FLAG,
        short="n",
        group="action",
        commands=("replace", "find", "diff", "rename"),
        help="Report what would change; never write, never create a session.",
    ),
    OptionSpec(
        "diff",
        OptionKind.FLAG,
        group="action",
        commands=_REPLACE_COMMANDS,
        help="Show a unified diff of every change (implies a preview).",
    ),
    OptionSpec(
        "interactive",
        OptionKind.FLAG,
        short="I",
        group="action",
        commands=_REPLACE_COMMANDS,
        help="Confirm every hunk before it is written.",
    ),
    OptionSpec(
        "write",
        OptionKind.CHOICE,
        choices=("in-place", "stdout", "dir"),
        default="in-place",
        group="action",
        metavar="MODE",
        commands=("replace",),
        help="Where the result goes: in-place (default), stdout, or --output-dir.",
    ),
    OptionSpec(
        "output-dir",
        OptionKind.PATH,
        short="o",
        default=None,
        group="action",
        metavar="DIR",
        commands=("replace",),
        help="Write modified copies under DIR, mirroring the tree (needs --write=dir).",
    ),
    OptionSpec(
        "suffix",
        OptionKind.STRING,
        default="",
        group="action",
        metavar="EXT",
        commands=("replace",),
        help="When writing copies, append EXT to the file name instead of mirroring.",
    ),
    OptionSpec(
        "rename",
        OptionKind.FLAG,
        group="action",
        commands=("replace",),
        help="Also apply the replacement to file and directory names.",
    ),
    OptionSpec(
        "rename-only",
        OptionKind.FLAG,
        group="action",
        commands=("replace",),
        help="Rename matching files/directories without touching their content.",
    ),
    OptionSpec(
        "encoding",
        OptionKind.STRING,
        short="E",
        default=None,
        group="action",
        metavar="NAME",
        commands=_REPLACE_COMMANDS,
        help="Force an encoding (utf-8, cp1251, latin-1 …); default is auto-detect.",
    ),
    OptionSpec(
        "encoding-error",
        OptionKind.CHOICE,
        choices=("strict", "replace", "ignore"),
        default="strict",
        group="action",
        metavar="POLICY",
        commands=_REPLACE_COMMANDS,
        help="What to do with undecodable bytes: strict (skip), replace, ignore.",
    ),
    OptionSpec(
        "line-endings",
        OptionKind.CHOICE,
        choices=("preserve", "lf", "crlf"),
        default="preserve",
        group="action",
        metavar="MODE",
        commands=("replace",),
        help="Keep the file's line endings (default) or normalise to LF/CRLF.",
    ),
    OptionSpec(
        "preserve-mtime",
        OptionKind.FLAG,
        group="action",
        commands=("replace",),
        help="Restore the original modification time after writing.",
    ),
    # -- safety -----------------------------------------------------------
    OptionSpec(
        "backup",
        OptionKind.FLAG,
        default=True,
        negatable=True,
        group="safety",
        commands=("replace", "rename"),
        help="Store originals in a session so the run can be rolled back (default on).",
    ),
    OptionSpec(
        "backup-dir",
        OptionKind.PATH,
        default=None,
        group="safety",
        metavar="DIR",
        commands=("replace", "rename"),
        help="Where to keep sessions (default <target>/.sr). Use an absolute path to keep them out of the tree.",
    ),
    OptionSpec(
        "keep",
        OptionKind.INT,
        default=20,
        group="safety",
        metavar="N",
        commands=("replace", "rename", "sessions"),
        help="Keep at most N sessions; older ones are pruned after a run (default 20, 0 disables pruning).",
    ),
    OptionSpec(
        "keep-age",
        OptionKind.DURATION,
        default=None,
        group="safety",
        metavar="AGE",
        commands=("replace", "rename", "sessions"),
        help="Also drop sessions older than AGE (e.g. 30d).",
    ),
    OptionSpec(
        "atomic",
        OptionKind.FLAG,
        default=True,
        negatable=True,
        group="safety",
        commands=("replace",),
        help="Write via a temporary file + rename (default) or rewrite the inode in place.",
    ),
    OptionSpec(
        "lock",
        OptionKind.FLAG,
        default=True,
        negatable=True,
        group="safety",
        commands=("replace", "rename"),
        help="Take an advisory lock so two sr runs cannot interleave (default on).",
    ),
    OptionSpec(
        "confirm-threshold",
        OptionKind.INT,
        default=50,
        group="safety",
        metavar="N",
        commands=("replace", "rename"),
        help="Ask for confirmation when more than N files would change (0 disables).",
    ),
    OptionSpec(
        "allow-root",
        OptionKind.FLAG,
        group="safety",
        commands=("replace", "rename"),
        help="Permit a mutating run with euid 0 (refused by default).",
    ),
    OptionSpec(
        "force",
        OptionKind.FLAG,
        short="f",
        group="safety",
        commands=("replace", "rename", "rollback"),
        help="Override safety guards (drifted files, read-only warnings, existing targets).",
    ),
    # -- reporting --------------------------------------------------------
    OptionSpec(
        "verbose",
        OptionKind.FLAG,
        short="v",
        group="reporting",
        configurable=False,
        commands=(),
        help="Show per-file results and the reason skipped files were skipped.",
    ),
    OptionSpec(
        "quiet",
        OptionKind.FLAG,
        short="q",
        group="reporting",
        configurable=False,
        commands=(),
        help="Only errors; suitable for scripts.",
    ),
    OptionSpec(
        "verbosity",
        OptionKind.CHOICE,
        choices=("quiet", "normal", "verbose", "debug"),
        default="normal",
        group="reporting",
        configurable=False,
        metavar="LEVEL",
        help="Set the log level explicitly (overrides -v/-q).",
    ),
    OptionSpec(
        "color",
        OptionKind.CHOICE,
        choices=("auto", "always", "never"),
        default="auto",
        group="reporting",
        configurable=False,
        metavar="WHEN",
        alias_flag="--no-color",
        help="Colourise output. Honours NO_COLOR and CLICOLOR_FORCE.",
    ),
    OptionSpec(
        "json",
        OptionKind.FLAG,
        group="reporting",
        configurable=False,
        help="Emit a single JSON document on stdout; diagnostics go to stderr.",
    ),
    OptionSpec(
        "yes",
        OptionKind.FLAG,
        short="y",
        group="action",
        commands=("replace", "rename", "rollback", "sessions", "update"),
        help="Answer 'yes' to every prompt; required for unattended runs in CI.",
    ),
    OptionSpec(
        "porcelain",
        OptionKind.FLAG,
        group="reporting",
        configurable=False,
        commands=_REPLACE_COMMANDS,
        help="Emit stable 'path:line:col:text' records, one per match.",
    ),
    OptionSpec(
        "progress",
        OptionKind.FLAG,
        default=True,
        negatable=True,
        group="reporting",
        configurable=False,
        commands=("replace", "find", "diff", "rename"),
        help="Show a progress bar on a terminal (default on).",
    ),
    OptionSpec(
        "summary",
        OptionKind.FLAG,
        default=True,
        negatable=True,
        group="reporting",
        configurable=False,
        commands=("replace", "find", "diff", "rename"),
        help="Print the run summary (default on).",
    ),
)

#: Options that belong to no single command: they are parsed before the
#: subcommand name and apply to every one of them.  They live in the schema for
#: the same reason everything else does — so that help, the man page, the
#: completions and `scripts/gen_options_table.py --check` see the *whole* CLI
#: surface instead of only the per-command part.
GLOBAL_OPTIONS: tuple[OptionSpec, ...] = (
    OptionSpec(
        "help",
        OptionKind.FLAG,
        short="h",
        group="global",
        help="Show help and exit.",
    ),
    OptionSpec(
        "help-all",
        OptionKind.FLAG,
        group="global",
        help="Show help for every subcommand and option.",
    ),
    OptionSpec(
        "version",
        OptionKind.FLAG,
        short="V",
        group="global",
        help="Print version, installation and update information.",
    ),
    OptionSpec(
        "debug",
        OptionKind.FLAG,
        group="global",
        help="Debug logging (same as -vv).",
    ),
    OptionSpec(
        "no-update-check",
        OptionKind.FLAG,
        group="global",
        env="SR_NO_UPDATE_CHECK",
        help="Do not check for a newer release in the background.",
    ),
    OptionSpec(
        "config",
        OptionKind.LIST,
        group="global",
        metavar="KEY=VALUE",
        repeatable=True,
        help="Override one configuration value (repeatable).",
    ),
    OptionSpec(
        "config-file",
        OptionKind.PATH,
        group="global",
        metavar="FILE",
        help="Read an extra configuration file (repeatable).",
    ),
)

#: Every declared option, per-command ones first (this is the table the docs
#: and the man page render) followed by the global ones.
ALL_OPTIONS: tuple[OptionSpec, ...] = OPTIONS + GLOBAL_OPTIONS

#: Fast lookups.
#:
#: ``BY_NAME`` and ``BY_FLAG`` cover *everything* the CLI accepts, including the
#: global flags.  ``BY_CONFIG_KEY`` and ``BY_ENV`` deliberately cover only the
#: configurable options: a global flag is per-invocation by design, and letting
#: ``json = true`` sit in a config file would make every command emit JSON.
BY_NAME: dict[str, OptionSpec] = {spec.name: spec for spec in ALL_OPTIONS}
#: Configurable options only — see :attr:`OptionSpec.configurable`.
CONFIGURABLE: tuple[OptionSpec, ...] = tuple(spec for spec in OPTIONS if spec.configurable)

BY_CONFIG_KEY: dict[str, OptionSpec] = {spec.config_key: spec for spec in CONFIGURABLE}
BY_ENV: dict[str, OptionSpec] = {spec.env_var: spec for spec in CONFIGURABLE if spec.env_var}
BY_FLAG: dict[str, OptionSpec] = {}
for _spec in ALL_OPTIONS:
    for _flag in _spec.flag_names():
        BY_FLAG[_flag] = _spec

#: Group titles, in display order.
GROUPS: tuple[tuple[str, str], ...] = (
    ("targets", "Target selection"),
    ("matching", "Matching"),
    ("action", "Action"),
    ("safety", "Safety & backups"),
    ("reporting", "Output & reporting"),
    ("global", "Global (before or after the subcommand)"),
)


def group_of(name: str) -> str:
    return BY_NAME[name].group if name in BY_NAME else "general"


def options_for(command: str) -> list[OptionSpec]:
    """Options that apply to ``command`` (empty ``commands`` means global)."""
    return [
        spec
        for spec in OPTIONS
        if not spec.commands or command in spec.commands
    ]


def specs_in_group(group: str, command: str | None = None) -> list[OptionSpec]:
    result = [spec for spec in ALL_OPTIONS if spec.group == group and not spec.hidden]
    if command:
        result = [spec for spec in result if not spec.commands or command in spec.commands]
    return result


def defaults() -> dict[str, Any]:
    """Every per-command option's default, keyed by config key.

    Global flags are excluded: they are parsed before the subcommand and are
    never read from a config file.
    """
    return {spec.config_key: spec.default for spec in OPTIONS}


def describe(spec: OptionSpec) -> str:
    """One-line human description used by help and the man page."""
    flags = ", ".join(spec.flag_names())
    if spec.takes_value:
        flags += f" {spec.metavar or spec.kind.upper()}"
    default = spec.default
    if default in (None, "", (), False):
        default_text = ""
    elif default is True:
        default_text = " (default: on)"
    else:
        default_text = f" (default: {default})"
    return f"{flags}\n{spec.help}{default_text}"

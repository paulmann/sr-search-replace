"""The help system: topics, the option reference, examples and the man page.

``sr help`` is a real manual, not a flag dump.  It is organised in *topics*
(``sr help topics`` lists them), each topic is either

* **static prose** — written once here, wrapped to the terminal width, or
* **generated** — the option reference, environment variables, exit codes and
  the command list are rendered from :mod:`sr.options` and
  :mod:`sr.exit_codes`, so they can never contradict the parser.

Everything is also available as JSON (``sr help --json``), as a man page
(``tools/build_manpage.py`` uses the same renderer) and as ``--help-all``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cli import exit_code_reference
from .console import Console, Verbosity, make_console
from .context import Context
from .errors import UsageError
from .exit_codes import DESCRIPTIONS, ExitCode
from .options import BY_FLAG, BY_NAME, GROUPS, OPTIONS, OptionKind, OptionSpec
from .registry import PROG, SUBCOMMAND_ALIASES, SUBCOMMANDS, usage_of
from .version import __version__

#: Topic registry: ``name → (title, renderer)``.  Order defines the index.
TOPIC_ORDER: tuple[str, ...] = (
    "intro",
    "quickstart",
    "install",
    "commands",
    "options",
    "matching",
    "targets",
    "backup",
    "rollback",
    "sessions",
    "rename",
    "interactive",
    "json",
    "env",
    "config",
    "update",
    "shell",
    "exit-codes",
    "performance",
    "troubleshooting",
    "examples",
    "recipes",
    "architecture",
    "development",
    "security",
    "compat",
    "license",
)


@dataclass(slots=True)
class Topic:
    """One help topic."""

    name: str
    title: str
    summary: str
    body: Callable[..., str] = field(default_factory=lambda: (lambda *_args: ""))
    aliases: tuple[str, ...] = ()


def _topic_registry() -> dict[str, Topic]:
    registry: dict[str, Topic] = {}
    for name, title, summary, renderer, aliases in _TOPIC_SPECS:
        registry[name] = Topic(name=name, title=title, summary=summary, body=renderer, aliases=aliases)
        for alias in aliases:
            registry[alias] = registry[name]
    return registry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def show_help(
    *,
    topic: str | None,
    parser: Any = None,
    subcommand: str | None = None,
    stream: Any = None,
    console: Console | None = None,
    as_json: bool = False,
    no_pager: bool = False,
) -> int:
    """Render one topic (or the overview) to ``stream``."""
    out = stream if stream is not None else sys.stdout
    view = console or make_console(verbosity=Verbosity.NORMAL, color=None, fmt="text", out=out, err=sys.stderr)

    if topic is None and subcommand:
        text = command_help(subcommand, parser=parser)
    elif topic is None:
        text = overview()
    else:
        text = render_topic(topic, parser=parser)

    if as_json:
        view.emit_json(help_document(topic or subcommand or "overview", text))
        return int(ExitCode.OK)

    _emit(text, view, use_pager=not no_pager)
    return int(ExitCode.OK)


def cmd_help(context: Context) -> int:
    """``sr help [TOPIC|--option|command]``."""
    from .cli import build_parser  # lazy: avoids the cli ↔ help_system cycle

    args = context.args
    parser = build_parser()
    topic = getattr(args, "topic", None)
    # `sr help --max-count` arrives as `--option max-count` (see cli._rewrite_help_topic)
    option_topic = getattr(args, "option", None)
    if option_topic:
        topic = f"--{str(option_topic).lstrip('-')}"
    as_json = bool(getattr(args, "json", False))
    no_pager = bool(getattr(args, "no_pager", False)) or context.console.machine_readable

    if getattr(args, "all", False):
        text = help_all_text(parser)
        if as_json:
            context.console.emit_json(help_document("all", text))
        else:
            _emit(text, context.console, use_pager=not no_pager)
        return int(ExitCode.OK)

    if topic in (None, "", "topics"):
        text = topics_index() if topic == "topics" else overview()
        if as_json:
            context.console.emit_json(help_document(topic or "overview", text, index=True))
        else:
            _emit(text, context.console, use_pager=not no_pager)
        return int(ExitCode.OK)

    text = render_topic(str(topic), parser=parser)
    if as_json:
        context.console.emit_json(help_document(str(topic), text))
    else:
        _emit(text, context.console, use_pager=not no_pager)
    return int(ExitCode.OK)


def render_topic(topic: str, *, parser: Any = None) -> str:
    """Render one topic by name, command name or ``--flag``."""
    normalized = str(topic).strip()
    registry = _topic_registry()

    if normalized.startswith("-"):
        return option_help(normalized)
    if normalized in registry:
        entry = registry[normalized]
        return _format_topic(entry)
    for name, _aliases, _summary in SUBCOMMANDS:
        if normalized == name or (normalized in SUBCOMMAND_ALIASES and SUBCOMMAND_ALIASES[normalized] == name):
            return command_help(name, parser=parser)
    if normalized.replace("-", "_") in BY_NAME:
        return option_help(f"--{normalized.replace('_', '-')}")

    known = ", ".join(TOPIC_ORDER)
    raise UsageError(
        f"unknown help topic {topic!r}",
        hint=f"run `sr help topics`; available: {known}",
    )


def help_document(topic: str, text: str, *, index: bool = False) -> dict[str, Any]:
    """Machine-readable help payload for ``--json`` consumers."""
    document: dict[str, Any] = {
        "program": PROG,
        "version": __version__,
        "topic": topic,
        "text": text,
    }
    if index:
        document["topics"] = [
            {"name": name, "title": _title(name), "summary": _summary(name)} for name in TOPIC_ORDER
        ]
        document["commands"] = [
            {"name": name, "aliases": list(aliases), "summary": summary}
            for name, aliases, summary in SUBCOMMANDS
        ]
        document["options"] = [
            {
                "name": spec.name,
                "flags": list(spec.flag_names()),
                "kind": spec.kind,
                "group": spec.group,
                "default": spec.default,
                "env": spec.env_var,
                "help": spec.help,
                "choices": list(spec.choices),
                "commands": list(spec.commands),
            }
            for spec in OPTIONS
        ]
        document["exit_codes"] = {str(int(code)): DESCRIPTIONS[int(code)] for code in ExitCode}
    return document


def help_all(parser: Any, stream: Any) -> int:
    """Print the whole manual (``sr --help-all``)."""
    text = help_all_text(parser)
    console = make_console(verbosity=Verbosity.NORMAL, color=None, fmt="text", out=stream, err=sys.stderr)
    _emit(text, console, use_pager=False)
    return int(ExitCode.OK)


def help_all_text(parser: Any = None) -> str:
    """The whole manual as one document (``sr --help-all``, man page source)."""
    parts = [overview(), topics_index(), commands_reference(), options_reference(), exit_codes_section()]
    for name in TOPIC_ORDER:
        if name in {"intro", "commands", "options"}:
            continue
        entry = _topic_registry().get(name)
        if entry is None:
            continue
        parts.append(_format_topic(entry))
    return "\n\n".join(parts).rstrip() + "\n"


def print_version(stream: Any) -> None:
    """``sr --version`` — version plus provenance and update hints."""
    from .commands.update import DEFAULT_REPO, detect_installation

    installation = detect_installation()
    lines = [
        f"{PROG} {__version__}",
        "",
        f"Python     : {sys.version.split()[0]} ({sys.executable})",
        f"Platform   : {sys.platform} {os.uname().machine if hasattr(os, 'uname') else ''}".rstrip(),
        f"Install    : {installation.kind} — {installation.details or installation.executable}",
        f"Package    : {installed_root()}",
        f"Repository : https://github.com/{DEFAULT_REPO}",
        "License    : MIT",
        "",
        "Run `sr update --check` to look for a newer release,",
        "`sr doctor` to verify the installation, `sr help` for the manual.",
    ]
    stream.write("\n".join(lines) + "\n")


def overview() -> str:
    """The screen a new user sees first."""
    width = _width()
    body = f"""{PROG} {__version__} — safe, auditable search & replace for source trees

{PROG} rewrites text across many files and keeps a journal of everything it
did, so any run can be inspected, diffed and undone.  Matching is literal by
default, one engine does the counting and the replacing, and nothing is written
until the whole tree has been scanned.

QUICK START
  {PROG} replace "old" "new" -p '*.py'     edit in place (creates a session)
  {PROG} find "old" -p '*.py'              where does it occur?
  {PROG} diff "old" "new" -p '*.py'        what exactly would change?
  {PROG} replace "old" "new" --dry-run     count and preview, write nothing
  {PROG} rollback                          undo the last run
  {PROG} sessions                          list the recorded runs
  {PROG} rename ".txt" ".md" -p '*.txt'    rename files by pattern
  {PROG} update --check                    is there a newer release?
  {PROG} doctor                            verify this installation

COMMANDS
"""
    for name, aliases, summary in SUBCOMMANDS:
        alias_text = f" ({', '.join(aliases)})" if aliases else ""
        body += f"  {name:<11}{alias_text:<16} {summary}\n"
    body += f"""
Every command takes -h/--help: `{PROG} replace --help`.
Bare invocations are shortcuts for replace: `{PROG} "*.py" "old" "new"`.

THE HELP SYSTEM
  {PROG} help                 this overview
  {PROG} help topics          every topic, one line each
  {PROG} help <topic>         one topic (options, matching, backup, update …)
  {PROG} help <command>       full help for a subcommand
  {PROG} help --<option>      explain a single option
  {PROG} help --all           the whole manual in one document
  {PROG} help --json          the same content, machine readable

MOST USEFUL TOPICS
  matching        literal vs --regex/--ere/--pcre, word boundaries, multiline
  targets         globs, --include/--exclude, .gitignore, size and age filters
  backup          what a session stores, where, and how big it gets
  rollback        undoing a run, drift detection, undoing a rollback
  json            the --json document, exit codes and CI usage
  update          how self-update works and how to pin a version
  examples        ~40 ready-to-run command lines
  troubleshooting what to do when something looks wrong

Safety defaults: literal matching, binary files skipped, .git/node_modules
pruned, a session written for every mutating run, root refused, and a
confirmation prompt when more than 50 files would change.
"""
    return _wrap_block(body, width)


def topics_index() -> str:
    lines = [f"{PROG} help topics — everything the manual covers", ""]
    for name in TOPIC_ORDER:
        lines.append(f"  {name:<16}{_summary(name)}")
    lines.append("")
    lines.append("Also: `sr help <command>` and `sr help --<option>`.")
    return "\n".join(lines)


def commands_reference() -> str:
    lines = [f"{PROG} commands", ""]
    for name, aliases, summary in SUBCOMMANDS:
        lines.append(f"{name}" + (f" ({', '.join(aliases)})" if aliases else ""))
        lines.append(f"    {summary}")
        lines.append(f"    usage: {command_usage(name)}")
        lines.append("")
    return "\n".join(lines)


def command_usage(name: str) -> str:
    """Canonical usage line for a subcommand."""
    return usage_of(name)


def installed_root() -> Path:
    """Directory holding the ``sr`` package (used to locate LICENSE)."""
    return Path(__file__).resolve().parent


def exit_codes_section() -> str:
    """Exit-code table rendered from the frozen contract in :mod:`sr.exit_codes`."""
    lines = ["Exit codes", "----------", ""]
    for entry in exit_code_reference():
        code, _, description = entry.partition("\t")
        lines.append(f"  {code:<5}{description}")
    lines.append("")
    lines.append("See `sr help exit-codes` for the conventions behind them.")
    return "\n".join(lines)


def options_reference(command: str | None = None) -> str:
    """Every option, grouped, with defaults and environment variables."""
    lines = [f"{PROG} options" + (f" for `{command}`" if command else ""), ""]
    lines.append("Precedence: command line > SR_* environment > project .srrc >")
    lines.append("            user config > /etc/sr/sr.conf > built-in default.")
    lines.append("")
    for group_name, title in GROUPS:
        specs = [
            spec
            for spec in OPTIONS
            if spec.group == group_name and not spec.hidden
            and (not spec.commands or not command or command in spec.commands)
        ]
        if not specs:
            continue
        lines.append(title.upper())
        for spec in specs:
            lines.extend(_option_lines(spec))
        lines.append("")
    lines.append("GLOBAL")
    for flag, help_text in (
        ("-h, --help", "Show help and exit."),
        ("--help-all", "Show help for every subcommand and option."),
        ("-V, --version", "Print version, installation and update information."),
        ("-v, --verbose", "More output; repeat (-vv) for debug level."),
        ("-q, --quiet", "Only errors; repeat (-qq) to print data only."),
        ("--debug", "Debug logging (same as -vv)."),
        ("--json", "Emit a single JSON document on stdout."),
        ("--porcelain", "Emit stable path:line:col records."),
        ("--color WHEN", "auto (default), always or never. Honours NO_COLOR."),
        ("--no-update-check", "Do not check for a newer release."),
        ("--config KEY=VALUE", "Override one configuration value (repeatable)."),
        ("--config-file FILE", "Read an extra configuration file (repeatable)."),
    ):
        lines.append(f"  {flag:<32}{help_text}")
    lines.append("")
    lines.append(f"{len(OPTIONS)} tunables in total; every one is also a config key and an SR_* variable.")
    return "\n".join(lines)


def _option_lines(spec: OptionSpec) -> list[str]:
    flags = ", ".join(spec.flag_names())
    if spec.takes_value:
        flags += f" {spec.metavar or spec.kind.upper()}"
    rendered = f"  {flags:<32}{spec.help}"
    extras: list[str] = []
    if spec.kind == OptionKind.CHOICE and spec.choices:
        extras.append("one of: " + ", ".join(spec.choices))
    default = spec.default
    if default not in (None, "", (), False):
        extras.append(f"default: {default}")
    elif default is False:
        extras.append("default: off")
    extras.append(f"env: {spec.env_var}")
    extras.append(f"config: {spec.config_key}")
    second = " " * 34 + ", ".join(extras)
    return [rendered, second]


def option_help(flag: str) -> str:
    """Deep help for a single option."""
    spec = BY_FLAG.get(flag)
    if spec is None:
        base = flag.lstrip("-").replace("_", "-")
        spec = BY_NAME.get(base)
    if spec is None:
        raise UsageError(
            f"unknown option {flag!r}",
            hint="run `sr help options` for the full list",
        )
    lines = [
        f"{flag} — {spec.help}",
        "",
        f"  Flags        : {', '.join(spec.flag_names())}",
        f"  Kind         : {spec.kind}" + (f" ({', '.join(spec.choices)})" if spec.choices else ""),
        f"  Default      : {_render_default(spec.default)}",
        f"  Environment  : {spec.env_var}",
        f"  Config key   : {spec.config_key}",
        f"  Applies to   : {', '.join(spec.commands) if spec.commands else 'every command'}",
    ]
    extra = _option_extra_help(spec.name)
    if extra:
        lines.append("")
        lines.extend(textwrap.indent(_wrap_text(extra, _width() - 2), "  ").splitlines())
    lines.append("")
    lines.append("  Example:")
    lines.extend(textwrap.indent(_wrap_text(_option_example(spec), _width() - 4), "    ").splitlines())
    return "\n".join(lines)


def _render_default(value: Any) -> str:
    if value is None:
        return "(none)"
    if value is False:
        return "off"
    if value is True:
        return "on"
    if value == ():
        return "(empty)"
    return str(value)


def _option_extra_help(name: str) -> str:
    return _OPTION_EXTRA.get(name, "")


_OPTION_EXTRA: dict[str, str] = {
    "max-count": (
        "Caps the number of replacements per file, not per line. Combine with "
        "--no-global to change only the first occurrence on each line."
    ),
    "line-range": (
        "Line numbers are 1-based and inclusive. `10` means line 10 only, "
        "`10-20` a window, `10-` everything from line 10, `-20` the first 20 "
        "lines. Matches outside the window are counted but not replaced."
    ),
    "gitignore": (
        "Reads .gitignore files from the target directory upwards, stops at the "
        "repository boundary, and also honours core.excludesFile. Negated rules "
        "(`!pattern`) and directory-only rules (`dir/`) are supported."
    ),
    "backup": (
        "Sessions are stored under <target>/.sr/sessions/<id> with a "
        "content-addressed blob store, a manifest and an append-only journal. "
        "Add `.sr/` to .gitignore. Use --backup-dir to keep them outside the tree."
    ),
    "atomic": (
        "With --atomic (the default) the file is written to a temporary sibling "
        "and renamed onto the target: a crash can never leave a truncated file, "
        "but the inode changes. With --no-atomic the inode is preserved (hard "
        "links and open descriptors stay valid) at the cost of that guarantee."
    ),
    "encoding": (
        "Without --encoding sr detects a BOM, then tries strict UTF-8. Files "
        "that fail to decode are skipped and reported, never mangled. Pass "
        "--encoding-error=replace to force a lossy decode instead."
    ),
    "confirm-threshold": (
        "The scan phase runs before anything is written, so the prompt states "
        "the real number of affected files. Set 0 to never ask."
    ),
    "write": (
        "in-place edits the files; stdout prints the modified content with a "
        "`==> path <==` header per file (handy for pipes); dir writes copies "
        "under --output-dir, mirroring the directory structure."
    ),
}


def _option_example(spec: OptionSpec) -> str:
    examples = {
        "pattern": "sr replace \"old\" \"new\" -p '*.py' -p '*.pyx'",
        "exclude": "sr replace \"old\" \"new\" --exclude '*.min.js' --exclude 'vendor/*'",
        "gitignore": "sr replace \"old\" \"new\" --gitignore",
        "regex": "sr replace 'v(\\d+)\\.(\\d+)' 'version \\1.\\2' --regex",
        "ere": "sr replace '[[:digit:]]{4}' 'YEAR' --ere",
        "multiline": "sr replace 'a.c' 'X' --multiline --regex",
        "line-range": "sr replace \"old\" \"new\" --line-range 1-20 -p 'header*'",
        "max-count": "sr replace \"TODO\" \"DONE\" --max-count 1",
        "interactive": "sr replace \"old\" \"new\" --interactive",
        "dry-run": "sr replace \"old\" \"new\" --dry-run",
        "diff": "sr replace \"old\" \"new\" --diff --dry-run",
        "backup-dir": "sr replace \"old\" \"new\" --backup-dir ~/.sr-backups/myproject",
        "keep": "sr replace \"old\" \"new\" --keep 50",
        "json": "sr replace \"old\" \"new\" --json | jq '.totals'",
        "porcelain": "sr find \"old\" --porcelain",
        "encoding": "sr replace \"old\" \"new\" --encoding cp1251",
        "write": "sr replace \"old\" \"new\" --write=dir --output-dir out",
        "rename": "sr replace \".txt\" \".md\" --rename-only -p '*.txt'",
        "target": "sr replace \"old\" \"new\" -C ~/projects/api",
        "max-size": "sr replace \"old\" \"new\" --max-size 1MB",
        "newer-than": "sr replace \"old\" \"new\" --newer-than 7d",
        "no-recursive": "sr replace \"old\" \"new\" --no-recursive",
        "hidden": "sr replace \"old\" \"new\" --hidden -p '.env*'",
        "symlinks": "sr replace \"old\" \"new\" --symlinks",
        "confirm-threshold": "sr replace \"old\" \"new\" --confirm-threshold 500",
        "word-boundary": "sr replace \"cat\" \"dog\" -w",
        "ignore-case": "sr replace \"warning\" \"notice\" -i",
        "no-global": "sr replace \"a\" \"b\" --no-global",
        "context": "sr find \"old\" --context 2",
        "preserve-mtime": "sr replace \"old\" \"new\" --preserve-mtime",
        "line-endings": "sr replace \"old\" \"new\" --line-endings lf",
        "files-from": "git ls-files -z | tr '\\0' '\\n' > files.txt && sr replace a b --files-from files.txt",
        "file": "sr replace \"old\" \"new\" --file src/a.py --file src/b.py",
        "include": "sr replace \"old\" \"new\" --include 'src/**/*.py'",
        "max-depth": "sr replace \"old\" \"new\" --max-depth 2",
        "no-default-excludes": "sr replace \"old\" \"new\" --no-default-excludes -p '*.bak'",
        "binary": "sr replace \"old\" \"new\" --binary -p '*.dat'",
        "lock": "sr replace \"old\" \"new\" --no-lock",
        "allow-root": "sudo sr replace \"old\" \"new\" --allow-root",
        "force": "sr rollback latest --force",
        "exclude-dir": "sr replace \"old\" \"new\" --exclude-dir tmp --exclude-dir .venv",
        "min-size": "sr replace \"old\" \"new\" --min-size 1KB",
        "older-than": "sr replace \"old\" \"new\" --older-than 30d",
        "no-escapes": "sr replace 'C:\\temp' 'D:\\data' --no-escapes",
        "pattern-kind": "sr replace 'a.c' 'X' --pattern-kind regex",
        "pcre": "sr replace '(?<=foo)bar' 'baz' --pcre",
        "output-dir": "sr replace \"old\" \"new\" --write=dir --output-dir ./out",
        "suffix": "sr replace \"old\" \"new\" --write=dir --output-dir ./out --suffix .new",
        "keep-age": "sr sessions prune --keep-age 30d",
        "encoding-error": "sr replace \"old\" \"new\" --encoding-error replace",
        "summary": "sr replace \"old\" \"new\" --no-summary -q",
        "progress": "sr replace \"old\" \"new\" --no-progress",
        "no-update-check": "sr replace \"old\" \"new\" --no-update-check",
        "verbosity": "sr replace \"old\" \"new\" --verbosity debug",
        "color": "sr replace \"old\" \"new\" --color always | less -R",
    }
    return examples.get(spec.name, f"sr replace \"old\" \"new\" --{spec.name}")


def command_help(name: str, *, parser: Any = None) -> str:
    """Full help for one subcommand."""
    if parser is None:
        # Imported lazily: sr.cli imports sr.commands, which imports this module,
        # so a module-level import would be circular.
        from .cli import build_parser

        parser = build_parser()
    subparser = _find_subparser(parser, name)
    usage = command_usage(name)
    lines = [f"{PROG} {name} — {_subcommand_summary(name)}", "", "USAGE", f"  {usage}", ""]
    description = getattr(subparser, "description", "") if subparser else ""
    if description:
        lines.append("DESCRIPTION")
        lines.extend(textwrap.indent(_wrap_text(description.strip(), _width() - 2), "  ").splitlines())
        lines.append("")
    lines.append(options_reference(name))
    lines.append("")
    lines.append("SEE ALSO")
    lines.extend(
        textwrap.indent(
            _wrap_text(f"sr help {_related_topics(name)}", _width() - 2), "  "
        ).splitlines()
    )
    return "\n".join(lines)


def _related_topics(name: str) -> str:
    mapping = {
        "replace": "matching targets backup rollback json examples",
        "find": "matching targets json examples",
        "diff": "matching rollback sessions",
        "rename": "rename targets rollback",
        "rollback": "rollback sessions backup",
        "sessions": "sessions backup rollback",
        "update": "update install",
        "doctor": "troubleshooting install",
        "completions": "shell",
        "config": "config env",
        "selftest": "troubleshooting development",
        "help": "topics",
    }
    return " ".join(f"`sr help {item}`" for item in mapping.get(name, "options").split())


def _subcommand_summary(name: str) -> str:
    for item, _aliases, summary in SUBCOMMANDS:
        if item == name:
            return summary
    return ""


def _find_subparser(parser: Any, name: str) -> Any:
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            if name in action.choices:
                return action.choices[name]
    return None


# ---------------------------------------------------------------------------
# Static topics
# ---------------------------------------------------------------------------


def _t_intro() -> str:
    return f"""What {PROG} does
----------------
{PROG} rewrites text across a whole tree of files, and keeps an audit trail of
every change so you can review and undo it.  It is meant for the jobs where
`sed -i` is too blunt and an editor's project-wide replace is too manual:

  * renaming an API across a repository
  * bumping versions in dozens of manifests
  * migrating a config key or an import path
  * normalising whitespace, encoding or line endings
  * bulk renaming files

Design rules that follow from that:

  1. Literal by default.  A search string is a search string; `.` does not
     suddenly mean "any character".  Regular expressions are opt-in.
  2. One engine.  Counting, previewing and replacing all use the same matcher,
     so the number sr reports is the number of edits it makes.
  3. Nothing is written before the whole tree is scanned.  The confirmation
     prompt and the summary therefore state real numbers, not estimates.
  4. Every mutating run is reversible.  Originals are stored content-addressed
     under <target>/.sr with a manifest, a journal and a diff per file.
  5. Fail loudly, never silently.  Undecodable files are skipped and reported;
     binary files are skipped unless asked for; drift after a session is
     detected before a rollback overwrites anything.
  6. Machine friendly.  Stable exit codes, --json, --porcelain, --yes and a
     lock so concurrent runs cannot interleave.
"""


def _t_quickstart() -> str:
    return f"""Quick start
-----------
1. Look before you leap:

     {PROG} find "old_api" -p '*.py'
     {PROG} diff "old_api" "new_api" -p '*.py'

   `find` shows file:line:col for every occurrence, `diff` shows the exact
   unified diff a replace would produce.  Neither writes anything.

2. Do it:

     {PROG} replace "old_api" "new_api" -p '*.py'

   The scan phase counts the changes first; if more than 50 files would change
   you are asked to confirm.  Originals are stored in a session under .sr/.

3. Check and, if needed, undo:

     git diff                       # or: {PROG} diff --session latest
     {PROG} rollback                # restores every file of the last session

4. Useful variants:

     {PROG} replace "old" "new" --dry-run          # count only
     {PROG} replace "old" "new" --interactive      # confirm every hunk
     {PROG} replace "old" "new" --write=dir -o out # write copies, touch nothing
     {PROG} replace "old" "new" --json | jq .      # machine readable
     {PROG} replace "old" "new" -C ~/project       # another directory

5. Files are skipped for a reason.  Add -v to see why:

     {PROG} replace "old" "new" -v

   You will see "binary content", "ignored by .gitignore", "excluded
   directory (node_modules)" and so on.
"""


def _t_install() -> str:
    return f"""Installation
------------
Pick one; every method installs the same `sr` command.

A) Standalone single file (no package manager, no dependencies beyond Python 3.9+)

     curl -fsSL https://raw.githubusercontent.com/paulmann/sr-search-replace/main/scripts/install.sh | bash
     # or, explicitly:
     curl -fsSLO https://github.com/paulmann/sr-search-replace/releases/latest/download/sr.pyz
     curl -fsSLO https://github.com/paulmann/sr-search-replace/releases/latest/download/sr.pyz.sha256
     sha256sum -c sr.pyz.sha256
     install -m 0755 sr.pyz /usr/local/bin/sr

B) pipx (isolated, recommended for Python users)

     pipx install sr-search-replace
     pipx upgrade sr-search-replace

C) pip

     python3 -m pip install --user sr-search-replace

D) npm (wrapper that locates python3 and runs the bundled app)

     npm install -g sr-search-replace

E) From source

     git clone https://github.com/paulmann/sr-search-replace
     cd sr-search-replace
     make test          # runs the full suite
     make install       # installs the zipapp to PREFIX/bin (default /usr/local)

Verify the installation:

     {PROG} --version
     {PROG} doctor          # environment, filesystem, config, network
     {PROG} selftest        # end-to-end smoke test in a temporary directory

Shell completion (optional):

     {PROG} completions --install

Updating:

     {PROG} update --check     # is there a newer release?
     {PROG} update             # standalone installs: download, verify, replace
                              # pip/pipx/npm: prints the exact upgrade command

Requirements: Python 3.9 or newer.  No third-party packages.  Linux and macOS;
on Windows use WSL2 (the filesystem code assumes POSIX rename semantics).
"""


def _t_matching() -> str:
    return f"""Matching
--------
Four pattern kinds, selected by a flag (literal is the default):

  (default)   literal text.  `.` is a dot, `(` is a parenthesis.
              Escape sequences \\n \\t \\r \\0 \\xNN are interpreted, so
              `{PROG} replace 'a\\tb' 'a b'` matches a real tab.
              The replacement is inserted verbatim — `$1`, `\\1` and `&`
              stay literal, which makes config files safe to edit.
              Use --no-escapes to disable escape processing.

  --regex     Python regular expressions.  The replacement is a template:
              \\1, \\g<name>, \\n and \\\\ work as in re.sub.
                {PROG} replace 'version = "(\\d+)"' 'version = "\\1.dev0"' --regex

  --ere       POSIX Extended Regular Expressions (grep -E / sed -E syntax),
              translated to Python: [[:digit:]], \\<, \\> and {{m,n}} work.
              Useful for patterns copied from shell scripts.
                {PROG} replace '[[:digit:]]{{4}}-[[:digit:]]{{4}}' 'XXXX-XXXX' --ere

  --pcre      Perl syntax: \\A, \\z, (?<name>...), atomic groups and
              possessive quantifiers (Python 3.11+).
                {PROG} replace '(?<=user=)[^&]+' 'admin' --pcre

Modifiers (they apply to every kind):

  -i, --ignore-case      case-insensitive
  -w, --word-boundary    whole words only (wraps the pattern in \\b)
  -m, --multiline        the pattern may span lines ('.' matches newline)
  -L, --line-range R     restrict edits to lines R (10, 10-20, 10-, -20)
  -M, --max-count N      at most N replacements per file
  --no-global            only the first occurrence on each line
  --context N            context lines in previews and diffs (default 3)

Counting rules
--------------
`matches` counts occurrences, not lines: a line with three hits counts three.
That is what the summary reports and what --max-count limits.  Matches outside
--line-range are reported separately and never replaced.

Because one engine does both the counting and the replacing, `-i` and `-w`
cannot disagree between the two — a class of bug that made the reported number
of replacements wrong in grep+sed based tools.

Escaping cheat sheet
--------------------
  literal dot            {PROG} replace "1.2.3" "1.2.4"
  regex dot              {PROG} replace '1\\.2\\.3' '1.2.4' --regex
  literal backslash      {PROG} replace 'C:\\\\temp' 'D:\\\\data' --no-escapes
  newline in the pattern {PROG} replace 'a\\nb' 'a b' --multiline
  tab                    {PROG} replace '\\t' '    '
  hex byte               {PROG} replace '\\x00' '' --binary
"""


def _t_targets() -> str:
    return f"""Choosing target files
---------------------
The positional GLOB matches the file *name*:

     {PROG} replace "a" "b" '*.py'          # every .py below the target
     {PROG} replace "a" "b"                 # every text file (GLOB defaults to '*')

A GLOB containing `/` matches the path relative to the target and supports `**`:

     {PROG} replace "a" "b" --include 'src/**/*.py'

Selection flags:

  -p, --pattern GLOB     repeatable; several patterns are OR-ed
  --file PATH            explicit file, bypasses pattern matching (repeatable)
  --files-from FILE      read the file list from FILE ('-' for stdin)
  --include GLOB         additional include filter (repeatable)
  --exclude GLOB         exclusion; wins over --include and -p (repeatable)
  --exclude-dir NAME     prune a directory by name (repeatable)
  -C, --target DIR       the root to operate on (default: cwd)
  --no-recursive         only the target directory itself
  --max-depth N          descend at most N levels
  --hidden               include dotfiles and dot-directories
  --symlinks             follow symlinked files (skipped by default)
  --gitignore            honour .gitignore and core.excludesFile
  --max-size SIZE        skip files larger than SIZE (default 100MB)
  --min-size SIZE        skip files smaller than SIZE
  --newer-than AGE       only files modified within AGE (24h, 7d, 30m)
  --older-than AGE       only files not modified within AGE
  --binary               process files that look binary
  --no-default-excludes  do not apply the built-in noise lists

Defaults, and why
-----------------
Excluded directories: .git .hg .svn node_modules vendor dist build target
  __pycache__ .venv .cache .gradle .terraform .idea .vscode …
Excluded file names: .DS_Store Thumbs.db *.orig *.rej *.bak *.backup *.swp *~
Hidden files: excluded (pass --hidden).
Symlinks: skipped (pass --symlinks) so a link cannot cause the same file to be
  edited twice or escape the target tree.
Binary files: skipped by content sniffing (NUL byte or >30% non-text bytes in
  the first 8 KiB), not by extension alone.
.sr/ (the session store) is always pruned, which keeps `rollback` idempotent.

Feed the file list from another tool
------------------------------------
     git ls-files '*.py' | {PROG} replace "old" "new" --files-from -
     {PROG} find "TODO" --porcelain | cut -d: -f1 | sort -u > files
     {PROG} replace "old" "new" --files-from files

Why `--files-from -` is safer than a glob: the list is exactly what git tracks,
so generated files, vendored copies and ignored paths cannot slip in.
"""


def _t_backup() -> str:
    return f"""Backups and sessions
--------------------
Every mutating run creates a *session*:

  <target>/.sr/sessions/<id>/
    manifest.json    who, what, when; per-file hashes, sizes, mode, uid/gid
    journal.jsonl    append-only op log (survives a crash mid-run)
    files/<xx>/<sha256>.dat   the original bytes, content addressed
    diffs/<path>.diff         the unified diff of the change
    .lock                     advisory lock, held for the duration of the run

Content addressing means identical originals are stored once, so a run that
touches 10 000 files sharing the same header costs one blob.  Every blob is
verified on read: a corrupt or truncated backup is reported instead of
silently restoring garbage.

Where sessions live
-------------------
Default: <target>/.sr.  Add `.sr/` to .gitignore.
Elsewhere: --backup-dir ~/sr-backups/myproject (also useful when the target is
read-only or when you do not want to touch the repository at all).

Retention
---------
  --keep N           keep the newest N sessions (default 20, 0 = never prune)
  --keep-age AGE     also drop sessions older than AGE (e.g. 30d)
  {PROG} sessions size     how much disk the store uses
  {PROG} sessions prune --keep 10 --keep-age 30d

Pruning runs automatically at the end of a mutating run, so the store cannot
grow without bound.

Disable backups
---------------
  --no-backup   faster and no .sr/ directory, but `rollback` will not work.
                The right choice inside a clean git working tree, where
                `git checkout -- .` is your rollback.

What is *not* in a session
--------------------------
Renames are journalled (as from/to pairs) but the file content is not copied
again — the content is unchanged.  --write=dir and --write=stdout runs create
no session because no original is modified.
"""


def _t_rollback() -> str:
    return f"""Rollback
--------
  {PROG} rollback                        # newest session
  {PROG} rollback latest
  {PROG} rollback 9f3a1c                 # unique id suffix
  {PROG} rollback 20260131T120530Z-9f3a1c
  {PROG} rollback 2                      # the second newest (ordinal)
  {PROG} rollback latest --dry-run       # show what would be restored
  {PROG} rollback latest --file src/app.py
  {PROG} rollback latest --force --yes   # unattended, overwrite drifted files

How a session is referenced
---------------------------
full id → unique suffix → ordinal (1 = newest) → `latest`.  Ambiguous suffixes
are reported with the candidate list instead of guessing.

Drift detection
---------------
For every file the manifest stores the SHA-256 of the content *after* the run.
Before restoring, sr hashes the current file:

  * unchanged  → restored silently
  * changed    → reported as drifted and SKIPPED, unless --force
  * missing    → restored (the session recreates the directory)

That is what makes `rollback` safe to run hours later: it will not silently
throw away edits you made in the meantime.

The rollback is itself reversible
---------------------------------
Unless --no-safety-snapshot is given, sr records the pre-rollback content in a
new session of kind `rollback`.  So:

  {PROG} rollback                # oops, wrong session
  {PROG} sessions                # the top entry is the safety snapshot
  {PROG} rollback <snapshot-id>  # back to the state before the rollback

Renames recorded in the session are undone in reverse order (deepest first), so
renaming a directory back cannot be blocked by its own children.

Exit codes
----------
  0  everything restored
  4  at least one file could not be restored (permissions, missing blob)

Rollback refuses to touch paths outside the session root, paths containing
control characters, and anything under /proc, /sys or /dev.
"""


def _t_sessions() -> str:
    return f"""Sessions
--------
  {PROG} sessions                    list, newest first
  {PROG} sessions show latest        full manifest
  {PROG} sessions show latest --files
  {PROG} sessions size               disk usage of the store
  {PROG} sessions prune --keep 10
  {PROG} sessions delete <id> --yes
  {PROG} sessions --json | jq '.sessions[].id'
  {PROG} diff --session latest       the diffs that run recorded

The list shows, per session: ordinal, id, file count, replacement count and a
one-line description of the change.  `→` marks the newest, `(incomplete)` marks
a run that was interrupted (its journal is still usable for recovery), and
`(dry run)` marks a run that wrote nothing.

Recovery from a crashed run
---------------------------
`manifest.json` is written only at the end of a run.  If sr is killed halfway,
the session has a journal but no manifest; sr rebuilds enough of the session
from `journal.jsonl` to list it and roll it back.  That is why the journal is
fsync-ed after every file rather than buffered.

Inspecting a session by hand
----------------------------
  cat .sr/sessions/<id>/manifest.json | jq .
  cat .sr/sessions/<id>/journal.jsonl | jq -c 'select(.kind=="replace")'
  ls .sr/sessions/<id>/diffs

Everything is plain JSON plus raw file blobs — no proprietary format, no
database, readable with any tool.
"""


def _t_rename() -> str:
    return f"""Renaming files
--------------
  {PROG} rename ".txt" ".md" -p '*.txt'
  {PROG} rename "IMG_" "photo-" -p 'IMG_*'
  {PROG} rename --regex '^(.*)\\.jpeg$' '\\1.jpg'
  {PROG} replace "old" "new" --rename -p '*.py'      # content *and* names
  {PROG} rename "a" "b" --dry-run                    # plan only

Semantics
---------
* The replacement is applied to the file *name* only, never to the directory
  part of the path, so a rename can never move a file to another directory.
* Directories are renamed too (deepest path first, so children keep valid
  paths).  With `sr replace --rename` directories are left alone unless
  --directories is given, because renaming folders as a side effect of a
  content edit is surprising.
* New names containing a path separator are refused.
* Collisions are detected before anything is executed: if two files would get
  the same new name, both are skipped and reported instead of one overwriting
  the other.  An existing target is also refused unless --force.
* Renames are journalled, so `sr rollback` undoes them in reverse order.

Rename + content in one pass
----------------------------
  {PROG} replace "MyClass" "YourClass" --rename -p '*.py'

The content pass runs first, then the rename pass, both inside one session —
so a single `sr rollback` restores the old content *and* the old names.
"""


def _t_interactive() -> str:
    return f"""Interactive mode
----------------
  {PROG} replace "old" "new" --interactive
  {PROG} replace "old" "new" -I -p '*.py' --context 5

For every file, changes are grouped into hunks (matches within --context lines
of each other).  Each hunk is rendered as a diff and you answer:

  y / enter   apply this hunk
  n           skip it
  a           apply this hunk and every remaining one in this file
  d           show the diff again
  q           skip the rest of the file

Hunks you reject are not written; the file is rewritten once, with only the
accepted hunks applied.  The session records the actual result, so `rollback`
restores the true original.

Non-interactive contexts
------------------------
When stdin is not a terminal, --interactive degrades to "skip the remaining
hunks" with a warning rather than blocking forever — pass --yes to apply
everything unattended.  `--yes` also answers the "N files will change"
confirmation, which is what CI needs.

Related safety valves
---------------------
  --dry-run            count and preview, write nothing
  --diff               show the diff of every change
  --confirm-threshold N  ask before writing when > N files would change
  --write=dir -o out   write copies instead of touching the originals
"""


def _t_json() -> str:
    return f"""Machine-readable output
-----------------------
--json
  One JSON document on stdout.  Every diagnostic (progress, warnings, errors)
  goes to stderr, so `sr replace ... --json | jq` is always safe.

  {{
    "version": "{__version__}",
    "dry_run": false,
    "root": "/home/me/project",
    "search": "old", "replacement": "new", "pattern_kind": "fixed",
    "duration_seconds": 0.412,
    "totals": {{
      "scanned": 128, "changed": 7, "unchanged": 121, "skipped": 2,
      "failed": 0, "matches": 19, "replacements": 19, "renamed": 0,
      "bytes_read": 421887, "bytes_written": 20144
    }},
    "session": {{ "id": "20260131T120530Z-9f3a1c", "directory": "...", ... }},
    "files": [
      {{ "path": "...", "rel_path": "src/app.py", "status": "changed",
         "matches": 3, "replacements": 3, "encoding": "utf-8",
         "size_before": 4012, "size_after": 4015,
         "diff": {{ "added": 3, "removed": 3, "changed": 6 }} }}
    ],
    "errors": [], "warnings": [], "exit_code": 0
  }}

  Add --diff to include the patch text of every file as "patch".

--porcelain
  Stable `path:line:col:kind:text` records, one per line, designed to be cut
  and piped:

     {PROG} find "old" --porcelain | cut -d: -f1 | sort -u

Exit codes (see `sr help exit-codes`)
  0 success / at least one match      3 no match
  1 runtime error                     4 rollback failed
  2 usage error                       5 update failed
                                      6 selftest failed
                                    130 interrupted

CI recipe
---------
  set -euo pipefail
  {PROG} replace "$OLD" "$NEW" -p '*.py' --yes --json --no-update-check > sr.json
  jq -e '.totals.failed == 0' sr.json
  jq -r '.files[].rel_path' sr.json

The `--no-update-check` flag keeps the network out of CI, and `--yes` avoids a
prompt that would otherwise hang the job (sr refuses to prompt when stdin is
not a terminal and exits non-zero instead).
"""


def _t_env() -> str:
    lines = [
        "Environment variables",
        "---------------------",
        "Every option has an SR_* variable: the option name, upper case, with",
        "'-' replaced by '_'.  A variable overrides config files but is",
        "overridden by the command line.",
        "",
    ]
    for spec in OPTIONS:
        lines.append(f"  {spec.env_var:<28}--{spec.name}  ({spec.help})")
    lines += [
        "",
        "Non-option variables:",
        "  SR_NO_UPDATE_CHECK=1     never check for a newer release",
        "  SR_UPDATE_REPO=o/n       override the GitHub repository used by `update`",
        "  SR_GITHUB_TOKEN          bearer token for the GitHub API (higher rate limit)",
        "  GITHUB_TOKEN / GH_TOKEN  also honoured by `update`",
        "  SR_CONFIG=path           extra configuration file to read",
        "  SR_DEBUG=1               print a traceback when sr crashes",
        "  SR_OFFLINE=1             skip the network probe in `doctor`",
        "  SR_COMPLETION_SHELL      force the shell for `completions`",
        "  NO_COLOR                 disable colours (https://no-color.org)",
        "  CLICOLOR_FORCE=1         force colours even when not a terminal",
        "  COLUMNS                  terminal width used for wrapping",
        "",
        "Examples:",
        f"  SR_IGNORE_CASE=true {PROG} replace \"old\" \"new\"",
        f"  SR_KEEP=50 SR_BACKUP=false {PROG} replace \"old\" \"new\"",
        f"  export SR_EXCLUDE='*.min.js vendor/*'   # then run {PROG} normally",
    ]
    return "\n".join(lines)


def _t_config() -> str:
    return f"""Configuration files
-------------------
Precedence, lowest to highest:

  1. built-in defaults
  2. /etc/sr/sr.conf                     system wide
  3. $XDG_CONFIG_HOME/sr/sr.conf         user (default ~/.config/sr/sr.conf)
  4. <repo root>/.srrc, <target>/.srrc,  project (walked upwards, stops at
     <target>/.sr/sr.conf                the repository boundary)
  5. $SR_CONFIG                          an extra file
  6. --config-file FILE                  an extra file, explicitly
  7. SR_* environment variables
  8. --config KEY=VALUE
  9. command line flags

Syntax — a small, forgiving INI dialect:

    # a comment
    ignore-case = true          # applies to every command
    exclude = *.min.js, dist    # comma or whitespace separated list

    [replace]                   # applies to `sr replace` only
    backup = false
    confirm-threshold = 500

    [find]
    context = 1

Rules:
  * keys are the option names ('-' and '_' are interchangeable)
  * `key` alone means `key = true`
  * booleans accept true/false/yes/no/on/off/1/0
  * sizes accept 512, 4KB, 10MB, 1.5GiB; durations accept 30s, 5m, 7d
  * repeating a key builds a list
  * an unknown key or an invalid value is a hard error with file:line —
    a silently ignored typo is exactly what a bulk-edit tool must not have

Commands:

  {PROG} config list --show-origin    every option, its value, where it came from
  {PROG} config show confirm-threshold
  {PROG} config init                  write a documented template
  {PROG} config init --file .srrc     …into the project instead
  {PROG} config path                  the files sr would read, in order
  {PROG} config validate              parse every file and report errors

A project .srrc is worth committing: it makes the team's exclusion list,
confirmation threshold and backup policy part of the repository instead of
tribal knowledge.
"""


def _t_update() -> str:
    return f"""Self-update
-----------
  {PROG} update --check          is there a newer release?
  {PROG} update                  install it
  {PROG} update --version 7.1.0  install a specific version
  {PROG} update --channel prerelease
  {PROG} update --source npm     ask the npm registry instead of GitHub
  {PROG} update --repo owner/name --path /usr/local/bin/sr

What sr detects
---------------
`sr doctor` prints the installation type.  It decides how an update is applied:

  standalone   a single executable file (zipapp or installed script).  sr can
               update itself: download → verify → smoke test → replace.
  pip / pipx   the environment owns the files, so sr prints the exact upgrade
               command instead of touching site-packages.
  npm          prints `npm install -g sr-search-replace@latest`.
  source       a git checkout — updating is refused, use `git pull && make`.

The update sequence for a standalone install
--------------------------------------------
  1. ask the GitHub releases API for the newest release on the channel
     (SR_GITHUB_TOKEN/GITHUB_TOKEN raises the rate limit)
  2. compare versions with semver rules; stop if not newer (unless --version)
  3. download the `sr.pyz` asset to a temporary directory
  4. verify its SHA-256 against the published `sr.pyz.sha256`
     — a mismatch aborts the update and installs nothing
  5. smoke test: run `python3 <downloaded> --version` in a subprocess
  6. copy the current file to `<file>.bak`
  7. atomically replace the file (write sibling + rename) and keep its mode

Every step either succeeds or leaves the current installation untouched.

Background check
----------------
At most once per 24h, sr looks for a newer release on a daemon thread with a
2.5s budget and caches the answer under $XDG_CACHE_HOME/sr/update-check.json.
It never blocks a run: if the network is slow or absent, nothing is printed and
the cache is used next time.  Turn it off with --no-update-check or
SR_NO_UPDATE_CHECK=1 (a good idea in CI and on air-gapped machines).

Pinning
-------
Install a specific version and disable the check to pin:

     {PROG} update --version 7.0.0
     export SR_NO_UPDATE_CHECK=1

Offline / air-gapped
--------------------
Download the two release assets on a connected machine, copy them over, then:

     sha256sum -c sr.pyz.sha256
     install -m 0755 sr.pyz /usr/local/bin/sr
"""


def _t_shell() -> str:
    return f"""Shell integration
-----------------
Completion
----------
  {PROG} completions bash --install
  {PROG} completions zsh --install
  {PROG} completions fish --install
  {PROG} completions bash > ~/.bash_completion      # manual

The scripts are generated from the live option schema, so a new flag is
completed without regenerating anything by hand.  `sr doctor` reports whether
completion is installed for your shell.

Aliases worth having
--------------------
  alias srd='{PROG} replace --dry-run'          # count first
  alias srf='{PROG} find'
  alias sru='{PROG} rollback'
  # safe wrapper: always preview, then ask
  srs() {{ {PROG} diff "$@" && {PROG} replace "$@"; }}

Pager
-----
`sr help` pipes long output through $PAGER (or `less -R`) when stdout is a
terminal.  Use --no-pager to disable it, e.g. inside scripts.

Pipes and SIGPIPE
-----------------
`{PROG} find ... | head` closes the pipe early.  sr catches BrokenPipeError and
exits 0 instead of dumping a traceback, so pipelines behave like grep's.

Colour
------
Colours are used when stdout is a terminal, and disabled otherwise — plus
NO_COLOR is honoured and CLICOLOR_FORCE overrides.  `--color always | less -R`
keeps colours in a pager.

Editor integration
------------------
`--porcelain` prints `path:line:col:kind:text`, which most editors can open as
a quickfix/error list:

  {PROG} find "TODO" --porcelain > /tmp/sr.qf      # vim: :cfile /tmp/sr.qf
"""


def _t_exit_codes() -> str:
    lines = [
        "Exit codes",
        "----------",
        "The codes are part of sr's contract: scripts branch on them, so they are",
        "frozen and documented here.",
        "",
    ]
    for entry in exit_code_reference():
        code, _, description = entry.partition("\t")
        lines.append(f"  {code:<4}{description}")
    lines += [
        "",
        "The same table is published in docs/EXIT_CODES.md, generated from",
        "sr/exit_codes.py by `make docs`.",
        "",
        "Notes:",
        "  * `find` and `replace` follow the grep/diff convention: 0 means",
        "    \"something matched\", 3 means \"nothing matched\".  That lets you",
        "    write `sr find X || echo missing` without shell gymnastics.",
        "  * A dry run that finds matches exits 0.",
        "  * 2 is reserved for \"you asked for something impossible\" (bad flag,",
        "    unreadable config, missing directory) as opposed to 1 \"something",
        "    went wrong while working\".",
        "  * 130 is the conventional 128+SIGINT so shells see a normal Ctrl-C.",
        "",
        "Example:",
        "  if sr replace \"$OLD\" \"$NEW\" --yes -q; then",
        "      echo changed",
        "  elif [ $? -eq 3 ]; then",
        "      echo 'nothing to do'",
        "  else",
        "      echo failed >&2; exit 1",
        "  fi",
    ]
    return "\n".join(lines)


def _t_performance() -> str:
    return """Performance
-----------
Where the time goes, in order of impact:

  1. Discovery.  Pruning is the whole game: sr prunes excluded directories
     *before* descending, so a tree with node_modules costs nothing.  Narrow
     the target with -C and -p instead of scanning everything.
  2. Reading and decoding files.  Every candidate is fully read once.  Use
     --max-size, --min-size, --newer-than and --include to cut the set.
  3. Matching.  A literal pattern is compiled to a regex once and reused for
     every file, so per-file cost is one search over the buffer.
  4. Writing.  Each changed file is written once, with fsync of the file and
     (for atomic writes) of the directory.

Practical numbers
-----------------
On a laptop SSD, a literal replacement over ~10 000 text files (≈120 MB) takes
a few seconds.  The scan phase doubles the read cost of a mutating run — that
is the price of an accurate confirmation prompt and summary, and it is what
makes `--dry-run` free of side effects.

Tips
----
  * Prefer --files-from with a list from git/ripgrep over scanning the tree.
  * Use --no-progress and -q in scripts; the progress bar is a TTY-only cost.
  * --no-backup skips the blob store entirely (use it inside a clean git tree).
  * --keep 5 keeps the session store small on long-lived projects.
  * Split huge runs per directory: sessions are per-target, so a rollback then
    affects only that directory.
  * --no-lock removes one lock file per run; only relevant for thousands of
    tiny invocations.

What sr deliberately does not do
--------------------------------
No parallelism by default.  A bulk rewrite is I/O bound, and parallel writers
make the ordering of the journal (and therefore rollback) harder to reason
about.  If you need throughput, shard by directory with -C and run the shards
yourself — each shard gets its own session and can be rolled back independently.
"""


def _t_troubleshooting() -> str:
    return f"""Troubleshooting
---------------
Start here: `{PROG} doctor` checks the interpreter, the installation type, the
config files, the session store, filesystem capabilities (atomic rename, inode
preservation, symlinks, permissions, unicode names), locking, disk space and
the network.  Then `{PROG} selftest` proves the whole pipeline works end to end.

"no files matched the target selection"
  * Is the glob quoted?  `{PROG} replace a b *.py` lets the *shell* expand
    `*.py`; quote it: `'*.py'`.
  * Are the files hidden or inside an excluded directory?  Add -v to see every
    skipped candidate and the reason.
  * Is --gitignore hiding them?  Check with `{PROG} find x --gitignore -v`.
  * Wrong root?  `-C <dir>` instead of relying on the cwd.

"skipped: binary content"
  sr sniffs the first 8 KiB: a NUL byte or >30% non-text bytes means binary.
  UTF-16 files without a BOM look binary.  Fix with --encoding utf-16 (then the
  file is decoded, not sniffed) or --binary (forces processing).

"cannot decode as utf-8"
  The file is not UTF-8.  Use --encoding cp1251 (or whatever it really is), or
  --encoding-error replace to force a lossy decode.  sr never guesses silently:
  a wrong guess would corrupt the file.

Line endings changed
  They are preserved by default (per file, LF/CRLF/CR).  --line-endings lf
  normalises on purpose.  Mixed endings inside one file keep the dominant one.

Rollback says "file changed after the session"
  Correct behaviour: the file was edited after the session was recorded, so
  restoring would throw those edits away.  Review with
  `{PROG} diff --session <id>`, then either accept the loss with --force or
  restore the single file with --file <path> --force.

"another sr process is using <session>"
  A lock file is held.  If no sr is running, the previous run was killed;
  sr breaks locks whose owner process is gone, otherwise delete
  `.sr/sessions/<id>/.lock`.

Everything is slower than expected
  * -v shows the discovery statistics (candidates, filtered, directories, time)
  * check that node_modules/dist/vendor are being pruned
  * `--no-backup` removes the blob writes if you have git as a safety net

sr crashed
  Re-run with SR_DEBUG=1 for a traceback and open an issue with the output of
  `{PROG} doctor --json` attached.

Getting help
  {PROG} help topics        the manual's index
  {PROG} help --<option>    one option in depth, with an example
  {PROG} <command> --help   one command in full
"""


def _t_examples() -> str:
    return f"""Examples
--------
Basics
  {PROG} replace "old_api" "new_api" -p '*.py'
  {PROG} replace "old" "new"                        # every text file below cwd
  {PROG} replace "old" "new" -C ~/projects/api      # another directory
  {PROG} replace "old" "new" --file src/a.py --file src/b.py
  git ls-files '*.py' | {PROG} replace "old" "new" --files-from -

Looking before leaping
  {PROG} find "TODO" -p '*.py'
  {PROG} find "TODO" --count
  {PROG} find "TODO" -l                             # file names only
  {PROG} find "TODO" -A 2 -B 2                      # with context
  {PROG} diff "old" "new" -p '*.md'
  {PROG} replace "old" "new" --dry-run
  {PROG} replace "old" "new" --dry-run --json | jq '.totals'

Regular expressions
  {PROG} replace 'version = "(\\d+\\.\\d+)"' 'version = "\\1.post0"' --regex -p 'setup.py'
  {PROG} replace 'import old\\b' 'import new' --regex -p '*.py'
  {PROG} replace '[[:digit:]]{{3}}-[[:digit:]]{{4}}' 'XXX-XXXX' --ere
  {PROG} replace '(?<=password=)\\S+' 'changeme' --pcre -p '*.env' --hidden
  {PROG} replace "cat" "dog" -w                     # whole words only
  {PROG} replace "Warning" "Notice" -i

Scoped edits
  {PROG} replace "old" "new" --line-range 1-30 -p 'header*'
  {PROG} replace "TODO" "DONE" --max-count 1
  {PROG} replace "a" "b" --no-global                # first per line
  {PROG} replace "a" "b" --include 'src/**/*.py' --exclude 'src/vendor/*'
  {PROG} replace "a" "b" --max-depth 2 --hidden
  {PROG} replace "a" "b" --newer-than 7d

Safety and review
  {PROG} replace "old" "new" --interactive
  {PROG} replace "old" "new" --write=dir --output-dir out
  {PROG} replace "old" "new" --confirm-threshold 5
  {PROG} sessions
  {PROG} diff --session latest
  {PROG} rollback latest --dry-run
  {PROG} rollback 9f3a1c --file src/app.py
  {PROG} sessions prune --keep 10 --keep-age 30d

Renaming
  {PROG} rename ".txt" ".md" -p '*.txt'
  {PROG} rename --regex '^(.*)\\.jpeg$' '\\1.jpg'
  {PROG} replace "MyClass" "YourClass" --rename -p '*.py'

Encodings and line endings
  {PROG} replace "old" "new" --encoding cp1251 -p '*.txt'
  {PROG} replace "old" "new" --encoding utf-16 --hidden
  {PROG} replace "\\r\\n" "\\n" --multiline -p '*.sh'   # CRLF → LF
  {PROG} replace "old" "new" --line-endings lf -p '*.py'

Automation
  {PROG} replace "old" "new" --yes --json --no-update-check > report.json
  {PROG} find "old" --porcelain | cut -d: -f1 | sort -u
  {PROG} replace "1.2.3" "1.2.4" -p 'package.json' -p 'pyproject.toml' --no-backup
  SR_IGNORE_CASE=true {PROG} replace "warning" "notice" -p '*.md'

Real-world migrations
  # bump a version everywhere it is declared
  {PROG} replace '"version": "6.1.0"' '"version": "7.0.0"' -p package.json
  # move an import path across a monorepo
  {PROG} replace "from app.utils import" "from core.utils import" --gitignore
  # retire a deprecated API, then review what is left
  {PROG} replace "legacy_call(" "modern_call(" -p '*.py' && {PROG} find "legacy_call"
"""


def _t_recipes() -> str:
    return rf"""Recipes
-------
1. Version bump across every manifest, verified

     {PROG} find "6.1.0" -p 'package.json' -p 'pyproject.toml' -p '*.md'
     {PROG} diff "6.1.0" "7.0.0" -p 'package.json' -p 'pyproject.toml'
     {PROG} replace "6.1.0" "7.0.0" -p 'package.json' -p 'pyproject.toml'
     git diff --stat

2. Retire an identifier safely (word boundary + review)

     {PROG} replace "OldClient" "NewClient" -w -p '*.py' --interactive

3. Normalise line endings in shell scripts only

     {PROG} replace "" "" --line-endings lf -p '*.sh' --max-count 0 2>/dev/null || \\
     {PROG} replace "\\r" "" -p '*.sh' --multiline --no-escapes

4. Redact secrets from logs before sharing

     {PROG} replace --regex '(?i)(api[_-]?key|token|secret)\\s*[=:]\\s*\\S+' \\
         '\\1=REDACTED' -p '*.log' --no-backup

5. Rewrite only the header block of generated files

     {PROG} replace "GENERATED BY v1" "GENERATED BY v2" --line-range 1-10

6. Migrate a config key without touching vendored copies

     {PROG} replace "old_key:" "new_key:" -p '*.yml' --exclude 'vendor/*' --gitignore

7. Dry-run report for a pull request

     {PROG} replace "old" "new" --dry-run --json > changes.json
     jq -r '.files[] | "\(.rel_path): \(.replacements)"' changes.json

8. Undo only part of a run

     {PROG} rollback latest --file src/app.py --force

9. Bulk rename with a preview

     {PROG} rename "IMG_" "photo-" -p 'IMG_*' --dry-run
     {PROG} rename "IMG_" "photo-" -p 'IMG_*'

10. Scheduled cleanup of the session store (cron)

     0 3 * * 0 {PROG} sessions prune --keep 5 --keep-age 14d -C /srv/project --yes
"""


def _t_architecture() -> str:
    return """Architecture
------------
  bin/sr                  launcher (finds python3, runs the app)
  sr/
    cli.py                preprocessing, dispatch, error handling
    cli_support.py        argparse actions, schema → parser plumbing
    options.py            THE option schema (single source of truth)
    config.py             config files, env vars, precedence
    context.py            per-run context: console, config, prompts, guards
    console.py            colour, verbosity, text/json/porcelain output
    pattern.py            the matcher: literal/regex/ERE/PCRE, ranges, counts
    discovery.py          walking, pruning, globs, .gitignore, filters
    textio.py             encoding, BOM, line endings, binary sniffing
    replace.py            the engine: scan phase → confirm → write → record
    find.py               read-only search rendering (grep-like output)
    diffview.py           unified diffs and their statistics
    atomic.py             atomic, permission-preserving writes
    session.py            sessions: manifest, journal, blob store, restore
    errors.py             exception hierarchy → exit codes
    exit_codes.py         the frozen exit-code contract
    version.py            semver parsing and comparison
    util.py               sizes, durations, hashes, ids, paths
    commands/             one module per subcommand (+ shared helpers)

Data flow of `sr replace`
-------------------------
  argv → preprocess → argparse → Context (config merge, console)
       → PatternSpec (validated once)
       → DiscoveryOptions → discover() → candidate list
       → scan phase: read → decode → match → diff        (nothing written)
       → confirm (accurate numbers)
       → write phase: atomic write → session.record_replace()
       → close session (manifest) → prune → report → exit code

Invariants worth knowing
------------------------
  * The scan phase never writes, so --dry-run has literally no side effects —
    not even a .sr/ directory.
  * Counting and replacing share one Matcher, so the reported number of
    replacements equals the number of edits made.
  * The journal is fsync-ed per file and the manifest only at the end, so an
    interrupted run is still recoverable from the journal.
  * Blobs are content addressed and re-hashed on read: a corrupt backup is
    detected instead of silently restored.
  * Paths in a session are recorded both absolute and relative, and a restore
    refuses to write outside the session root.
"""


def _t_development() -> str:
    return """Development
-----------
  git clone https://github.com/paulmann/sr-search-replace
  cd sr-search-replace
  python3 -m venv .venv && . .venv/bin/activate
  make install-dev        # editable install plus dev dependencies
  make test               # the full suite
  make lint               # ruff + (optionally) mypy
  make dist               # build/sr.pyz (the release asset)
  make man                # docs/man/sr.1 from the live schema
  make completions        # regenerate completions/*.
  make check              # lint + test + dist + man + selftest

Layout
  sr/            the package (stdlib only)
  tests/         pytest suite (unit + end-to-end, no network)
  tools/         build helpers: zipapp, man page, version sync
  scripts/       install.sh and release scripts
  npm/           the npm package (wrapper + postinstall)
  docs/          the manual that `sr help` renders, plus man page

Conventions
  * Every new option goes into sr/options.py and nowhere else; the parser,
    config layer, completions, man page and help text are all derived.
  * Every command module exposes `cmd_<name>(context) -> int`.
  * Errors are SrError subclasses with an exit_code and a hint.
  * No third-party runtime dependencies, ever.  Dev tools are optional.
  * Tests must not touch the network or the user's home directory.

Running a single test
  python3 -m pytest tests/test_pattern.py -k multiline -q

Release checklist (maintainers)
  1. bump the version in pyproject.toml
  2. `make sync-version` (rewrites sr/version.py and npm/package.json)
  3. update CHANGELOG.md
  4. `make check`
  5. commit, tag `v<version>`, push --tags
  6. CI publishes to PyPI and npm, builds sr.pyz, and attaches
     sr.pyz + sr.pyz.sha256 to the GitHub release — which is what `sr update`
     downloads and verifies.
"""


def _t_security() -> str:
    return """Security and safety model
-------------------------
Threat model: sr runs with your own privileges over your own files, so the
risks are accidental destruction, unintended scope, and supply-chain attacks
on the update path — not remote exploitation.

Accidental destruction
  * literal matching by default: a pattern is never silently a regex
  * binary files are skipped unless --binary is given
  * undecodable files are skipped and reported, never rewritten with a guessed
    encoding
  * the scan phase completes before the first write, so the confirmation prompt
    states real numbers
  * a confirmation prompt above --confirm-threshold (default 50 files)
  * VCS directories, build output and .sr/ are pruned by default
  * symlinks are skipped by default, so a link cannot smuggle a file outside
    the target tree into the edit set
  * running as root is refused unless --allow-root
  * rewriting / , /etc, /usr or $HOME directly is refused unless --force
  * every mutating run is journalled and reversible; rollbacks are reversible
    too (safety snapshot)
  * rollback refuses to overwrite files that drifted after the session

Unintended scope
  * -v prints every skipped candidate with the reason, so an empty result is
    explainable rather than mysterious
  * --dry-run and --diff make the blast radius visible before committing
  * --write=dir writes copies and touches nothing

Update path (supply chain)
  * HTTPS only; no plaintext fallback
  * the downloaded asset's SHA-256 is verified against the published
    sr.pyz.sha256; a mismatch aborts and installs nothing
  * the download is smoke-tested in a subprocess before it replaces anything
  * the previous file is kept as <file>.bak
  * --no-verify exists for mirrors without checksums and warns loudly
  * the background check is read-only, cached, non-blocking and can be disabled
    entirely (SR_NO_UPDATE_CHECK=1)

Data handling
  * file content never leaves the machine; the only network calls are the
    release lookup and the release download
  * sessions store original bytes under <target>/.sr — treat that directory
    with the same care as the files themselves (it is not encrypted), and add
    it to .gitignore so backups are never committed
  * no telemetry, no analytics, no crash reporting: a crash prints a traceback
    locally and suggests opening an issue

Reporting a vulnerability
  See SECURITY.md.  Please do not open a public issue for something that could
  destroy data; use the contact address listed there.
"""


def _t_compat() -> str:
    return """Compatibility
-------------
Python
  Requires 3.9+.  Developed and CI-tested on 3.9, 3.11, 3.12 and 3.13.
  No third-party runtime dependencies.

Operating systems
  Linux and macOS are supported.  Windows works under WSL2; native Windows is
  not supported because sr relies on POSIX rename semantics, `os.chown`,
  advisory locks and inode preservation.

Relationship to sr 6.x (the bash implementation)
------------------------------------------------
sr 7 is a rewrite in Python with a redesigned command line.  The 6.x flags are
not accepted.  Migration table:

  6.x                              7.x
  ------------------------------   -----------------------------------------
  sr "*.py" "a" "b"                sr replace "a" "b" -p '*.py'
  sr -i / --ignore-case            sr replace -i …            (unchanged)
  sr -E / --extended-regex         sr replace --ere …
  sr -w / --word-boundary          sr replace -w …            (unchanged)
  sr -m / --multiline              sr replace -m …            (unchanged)
  sr -nr / --no-recursive          sr replace --no-recursive
  sr -nb / --no-backup             sr replace --no-backup
  sr -nbf / --no-backup-folder     sr replace --backup-dir <dir>
  sr -fb / --force-backup          (backups are on by default)
  sr -xs / --max-size MB           sr replace --max-size 10MB
  sr -xp / --exclude-patterns      sr replace --exclude GLOB (repeatable)
  sr -xd / --exclude-dirs          sr replace --exclude-dir NAME (repeatable)
  sr -sd / --search-dir DIR        sr replace -C DIR
  sr -od / --output-dir DIR        sr replace --write=dir -o DIR
  sr -mode copy                    sr replace --write=dir -o DIR
  sr -mode backup_only             (covered by --dry-run + sessions)
  sr --dry-run                     sr replace --dry-run       (unchanged)
  sr --rollback                    sr rollback
  sr --rollback=DIR                sr rollback <session-id>
  sr --rollback-list               sr sessions
  sr --binary                      sr replace --binary        (unchanged)
  sr --binary-method=…             (single robust sniffer; no knob)
  sr --find-opts/--sed-opts/…      removed: flags were injected into a shell
                                   command line, which was unsafe.  Use the
                                   typed equivalents (--include, --max-depth,
                                   --newer-than, …).
  sr --no-global                   sr replace --no-global     (unchanged)
  sr --delimiter CHAR              removed: no shell quoting is involved, so
                                   no delimiter can collide any more.
  SR_* environment variables       SR_* still exist, one per option, but the
                                   names follow the new option names.

Behavioural changes worth knowing
---------------------------------
  * Patterns are literal by default.  6.x escaped the search string for sed,
    which also meant `-E` was effectively broken; 7.x separates literal and
    regex cleanly.
  * Replacement counts are occurrences, not matching lines.
  * `--dry-run` writes nothing at all (6.x created a backup directory).
  * Sessions live in `.sr/` instead of `sr.backup.<timestamp>/` in the project
    root, and old 6.x backup directories are not read.
  * Exit codes are documented and stable; "no match" is 3, not 1.

6.x sessions
------------
`sr.backup.*` directories from 6.x are left untouched.  Restore from them with
the 6.x script (still downloadable from the v6.1.0 release assets) or copy the
files back by hand — the layout is a plain directory mirror.
"""


def _t_license() -> str:
    path = installed_root().parent / "LICENSE"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:  # pragma: no cover - LICENSE is always shipped
        text = "The LICENSE file was not found next to the sr package."
    return (
        "License\n"
        "-------\n"
        "sr — safe, auditable search & replace for source trees.\n"
        f"Copyright (c) Mikhail Deynekin.  Distributed under the MIT License\n"
        f"(source: {path}).\n\n"
        f"{text}\n"
    )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _width() -> int:
    try:
        import shutil as _shutil

        return max(60, min(100, _shutil.get_terminal_size((88, 24)).columns))
    except Exception:  # pragma: no cover
        return 88


def _wrap_text(text: str, width: int) -> str:
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        indent = paragraph[: len(paragraph) - len(paragraph.lstrip())]
        if len(paragraph) <= width or _looks_like_code(paragraph):
            out.append(paragraph)
            continue
        wrapped = textwrap.fill(
            paragraph.strip(),
            width=max(40, width - len(indent)),
            initial_indent=indent,
            subsequent_indent=indent + "  ",
            break_long_words=False,
            break_on_hyphens=False,
        )
        out.append(wrapped)
    return "\n".join(out)


def _looks_like_code(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith(("$", "#", "sr ", "{", "}", "//", "|")) or "  " in line[:4]


def _wrap_block(text: str, width: int) -> str:
    return "\n".join(_wrap_text(chunk, width) for chunk in text.split("\n\n")).rstrip() + "\n"


def _format_topic(topic: Topic) -> str:
    body = topic.body().strip("\n")
    header = f"{topic.title}\n{'=' * max(3, min(len(topic.title), 72))}\n"
    return f"{header}\n{_wrap_text(body, _width())}\n"


def _title(name: str) -> str:
    registry = _topic_registry()
    entry = registry.get(name)
    return entry.title if entry else name.replace("-", " ").title()


def _summary(name: str) -> str:
    registry = _topic_registry()
    entry = registry.get(name)
    return entry.summary if entry else ""


def _emit(text: str, console: Console, *, use_pager: bool) -> None:
    if use_pager and _pager_enabled():
        if _pipe_through_pager(text):
            return
    for line in text.rstrip("\n").split("\n"):
        console.write(line)


def _pager_enabled() -> bool:
    if os.environ.get("SR_NO_PAGER", "").strip():
        return False
    if not sys.stdout.isatty():
        return False
    return bool(os.environ.get("PAGER") or _which("less"))


def _which(name: str) -> str | None:
    import shutil as _shutil

    return _shutil.which(name)


def _pipe_through_pager(text: str) -> bool:
    pager = os.environ.get("PAGER") or "less -R"
    try:
        process = subprocess.Popen(
            pager,
            shell=True,
            stdin=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return False
    try:
        assert process.stdin is not None
        process.stdin.write(text)
        process.stdin.close()
        process.wait()
        return True
    except (BrokenPipeError, KeyboardInterrupt, OSError):
        try:
            process.kill()
        except OSError:  # pragma: no cover
            pass
        return False


# ---------------------------------------------------------------------------
# Topic table (kept at the bottom: it references the renderers above)
# ---------------------------------------------------------------------------

_TOPIC_SPECS: tuple[tuple[str, str, str, Callable[..., str], tuple[str, ...]], ...] = (
    ("intro", "What sr does", "Purpose and design rules", _t_intro, ("about", "overview")),
    ("quickstart", "Quick start", "Five steps from preview to rollback", _t_quickstart, ("start", "getting-started", "tutorial")),
    ("install", "Installation", "Every installation method, verified", _t_install, ("installation", "setup", "download")),
    ("commands", "Commands", "All subcommands with their usage line", commands_reference, ("command", "subcommands")),
    ("options", "Options", "Every option, grouped, with defaults and env vars", options_reference, ("flags", "opts")),
    ("matching", "Matching", "Literal vs regex/ERE/PCRE, ranges, counts", _t_matching, ("regex", "patterns", "pattern")),
    ("targets", "Target selection", "Globs, exclusions, .gitignore, filters", _t_targets, ("files", "glob", "exclude")),
    ("backup", "Backups & sessions", "What is stored, where, and for how long", _t_backup, ("backups", "session-store")),
    ("rollback", "Rollback", "Undoing a run, drift detection, undoing a rollback", _t_rollback, ("undo", "restore")),
    ("sessions", "Sessions", "Listing, inspecting and pruning the audit trail", _t_sessions, ("journal", "audit")),
    ("rename", "Renaming", "Bulk file renaming with collision detection", _t_rename, ("mv", "renaming")),
    ("interactive", "Interactive mode", "Per-hunk confirmation", _t_interactive, ("prompt", "confirm")),
    ("json", "JSON & automation", "The --json document, exit codes, CI recipes", _t_json, ("automation", "ci", "porcelain", "machine")),
    ("env", "Environment variables", "Every SR_* variable", _t_env, ("environment", "variables")),
    ("config", "Configuration files", "Precedence, syntax, templates", _t_config, ("configuration", "srrc", "conf")),
    ("update", "Self-update", "How updates are fetched, verified and installed", _t_update, ("upgrade", "auto-update", "autoupdate")),
    ("shell", "Shell integration", "Completion, aliases, pager, pipes", _t_shell, ("completion", "completions", "bash", "zsh", "fish")),
    ("exit-codes", "Exit codes", "The frozen exit-code contract", _t_exit_codes, ("exitcodes", "codes", "status")),
    ("performance", "Performance", "Where the time goes and how to cut it", _t_performance, ("speed", "fast", "slow")),
    ("troubleshooting", "Troubleshooting", "Symptom → cause → fix", _t_troubleshooting, ("faq", "problems", "errors", "help-me")),
    ("examples", "Examples", "Ready-to-run command lines", _t_examples, ("example", "usage")),
    ("recipes", "Recipes", "Complete workflows for common jobs", _t_recipes, ("cookbook", "howto")),
    ("architecture", "Architecture", "Module map and data flow", _t_architecture, ("internals", "design", "code")),
    ("development", "Development", "Building, testing and releasing sr", _t_development, ("contributing", "hacking", "build")),
    ("security", "Security model", "Guards, update chain, data handling", _t_security, ("safety", "vulnerability")),
    ("compat", "Compatibility & migration", "From sr 6.x (bash) to sr 7", _t_compat, ("migration", "compatibility", "changelog-lite", "upgrade-from-6")),
    ("license", "License", "MIT license text", _t_license, ("licence", "mit")),
)

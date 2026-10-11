#!/usr/bin/env python3
"""Generate ``docs/man/sr.1`` from the live option schema and help topics.

The man page is *derived*, never hand-edited: every flag, default, environment
variable and exit code comes from :mod:`sr.options` and :mod:`sr.exit_codes`,
and the prose sections come from :mod:`sr.help_system`.  That is what keeps
``man sr``, ``sr help`` and ``sr <cmd> --help`` from disagreeing.

Usage::

    python3 tools/build_manpage.py                 # → docs/man/sr.1
    python3 tools/build_manpage.py --check         # fail when out of date
    python3 tools/build_manpage.py --output -      # stdout
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sr.exit_codes import DESCRIPTIONS, ExitCode
from sr.help_system import render_topic
from sr.options import GLOBAL_OPTIONS, GROUPS, OPTIONS, OptionKind
from sr.registry import PROG, SUBCOMMANDS, usage_of
from sr.version import __version__

DEFAULT_OUTPUT = ROOT / "docs" / "man" / f"{PROG}.1"

#: Help topics rendered as man sections, in order, with their section headings.
MAN_SECTIONS: tuple[tuple[str, str], ...] = (
    ("intro", "DESCRIPTION"),
    ("quickstart", "QUICK START"),
    ("matching", "PATTERN MATCHING"),
    ("targets", "TARGET SELECTION"),
    ("backup", "BACKUPS AND SESSIONS"),
    ("rollback", "ROLLBACK"),
    ("rename", "RENAMING FILES"),
    ("interactive", "INTERACTIVE MODE"),
    ("json", "MACHINE-READABLE OUTPUT"),
    ("config", "CONFIGURATION FILES"),
    ("update", "SELF-UPDATE"),
    ("performance", "PERFORMANCE"),
    ("security", "SECURITY MODEL"),
    ("compat", "COMPATIBILITY AND MIGRATION"),
    ("troubleshooting", "TROUBLESHOOTING"),
    ("examples", "EXAMPLES"),
)


def strip_titles(text: str) -> str:
    """Drop leading "Title" + underline blocks.

    Topics carry a title of their own (for terminal rendering) and the registry
    adds a second one; in the man page the ``.SH`` heading replaces both.
    """
    lines = text.splitlines()
    index = 0
    removed = 0
    while index < len(lines) and removed < 2:
        if not lines[index].strip():
            index += 1
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        is_rule = bool(following.strip()) and set(following.strip()) <= set("=-~")
        if is_rule:
            index += 2
            removed += 1
            continue
        break
    return "\n".join(lines[index:]).strip("\n")


def esc(text: str) -> str:
    """Escape a string for roff output."""
    return (
        text.replace("\\", "\\(rs")
        .replace("-", "\\-")
        .replace("'", "\\(aq")
        .replace("`", "\\(ga")
    )


def paragraph(text: str, *, indent: str = "") -> list[str]:
    """Convert a help topic's plain text into roff paragraphs.

    The topics are written for a terminal, so the conversion is mechanical:
    indented lines become literal (``.nf``/``.fi``) blocks, blank lines become
    paragraph breaks, and everything else is re-wrapped by ``.PP``.
    """
    out: list[str] = []
    buffer: list[str] = []
    literal: list[str] = []

    def flush_text() -> None:
        if buffer:
            out.append(".PP")
            out.append(" ".join(buffer))
            buffer.clear()

    def flush_literal() -> None:
        if literal:
            out.append(".PP")
            if indent:
                out.append(indent)
            out.append(".nf")
            out.extend(esc(line) for line in literal)
            out.append(".fi")
            literal.clear()

    for line in text.splitlines():
        if line.startswith("    ") or line.startswith("\t") or line.startswith("  "):
            flush_text()
            literal.append(line.strip("\n"))
        elif not line.strip() or (line.startswith(("=", "-")) and set(line.strip()) <= {"=", "-"}):
            flush_text()
            flush_literal()
        else:
            flush_literal()
            buffer.append(line.strip())
    flush_text()
    flush_literal()
    return out


def header() -> list[str]:
    today = _datetime.date.today().strftime("%Y-%m-%d")
    return [
        f'.TH "{PROG.upper()}" "1" "{today}" "sr {__version__}" "User Commands"',
        ".ds Pg sr-search-replace",
        ".hy 0",
    ]


def name_section() -> list[str]:
    return [
        ".SH NAME",
        f"{esc(PROG)} \\- safe, auditable search and replace for source trees",
    ]


def synopsis_section() -> list[str]:
    lines = [".SH SYNOPSIS", ".nf"]
    lines.append(f"{esc(PROG)} [GLOBAL OPTIONS] <command> [OPTIONS] [ARGS...]")
    lines.append(f"{esc(PROG)} [GLOBAL OPTIONS] [GLOB...] SEARCH REPLACE")
    lines.append(".fi")
    lines.append(".PP")
    lines.append(
        "A bare invocation (no known command as the first word) is a shortcut for "
        f"{esc(PROG)} replace. Flags may appear in any position."
    )
    return lines


def commands_section() -> list[str]:
    lines = [".SH COMMANDS"]
    for name, aliases, summary in SUBCOMMANDS:
        alias_text = f" (aliases: {', '.join(aliases)})" if aliases else ""
        lines.append(".TP")
        lines.append(f".B {esc(name)}{esc(alias_text)}")
        lines.append(esc(summary))
        lines.append(".br")
        lines.append(f"Usage: {esc(usage_of(name))}")
    return lines


def options_section() -> list[str]:
    lines = [".SH OPTIONS"]
    lines.append("Options are declared once in the source and drive the parser, the")
    lines.append("configuration files, the shell completions and this page.")
    for group_name, title in GROUPS:
        specs = [spec for spec in OPTIONS if spec.group == group_name and not spec.hidden]
        if not specs:
            continue
        lines.append(".SS " + esc(title.title()))
        for spec in specs:
            lines.extend(option_entry(spec))
    # Globals come from the schema too, so `--no-color` (an alias declared on
    # the `color` spec) cannot go missing the way it did when this list was
    # hand-written.
    lines.append(".SS " + esc("Global (before or after the subcommand)"))
    for spec in GLOBAL_OPTIONS:
        lines.extend(option_entry(spec))
    # The reporting flags (`--json`, `--porcelain`, `-v`, `-q`, `--color`,
    # `--no-color`, …) are per-invocation and therefore not configurable, but
    # they are absolutely part of the documented CLI surface.
    for spec in OPTIONS:
        if spec.group == "reporting":
            lines.extend(option_entry(spec))
    lines.extend(command_local_entries())
    return lines


def command_local_entries() -> list[str]:
    """The hand-written, command-local flags, taken from the live parser.

    Generated for the same reason the schema options are: a flag that exists in
    the parser but not in the manual is how `--no-color` disappeared from the
    documentation while still being accepted on the command line.
    """
    import argparse as _argparse

    from sr.cli import build_parser

    schema_flags = {flag for spec in OPTIONS for flag in spec.flag_names()}
    schema_flags |= {flag for spec in GLOBAL_OPTIONS for flag in spec.flag_names()}
    parser = build_parser()

    lines: list[str] = []

    def harvest(target, title: str) -> None:
        entries: list[tuple[str, str]] = []
        for action in target._actions:
            names = [name for name in action.option_strings if name]
            if not names or any(name in schema_flags for name in names):
                continue
            if set(names) <= {"-h", "--help"} or action.help == _argparse.SUPPRESS:
                continue
            label = ", ".join(names)
            if action.metavar:
                label += f" {action.metavar}"
            entries.append((label, (action.help or "").strip() or "—"))
        if not entries:
            return
        lines.append(".SS " + esc(title))
        for label, help_text in entries:
            lines.append(".TP")
            lines.append(f".B {esc(label)}")
            lines.append(esc(help_text))

    for name, _aliases, _summary in SUBCOMMANDS:
        sub = _subparser(parser, name)
        if sub is not None:
            harvest(sub, f"{name} only")
    return lines


def _subparser(parser, name: str):
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and name in choices:
            return choices[name]
    return None


def option_entry(spec) -> list[str]:
    flags = ", ".join(spec.flag_names())
    if spec.takes_value:
        flags += f" {spec.metavar or spec.kind.upper()}"
    lines = [".TP", f".B {esc(flags)}"]
    body = spec.help
    if spec.kind == OptionKind.CHOICE and spec.choices:
        body += " One of: " + ", ".join(spec.choices) + "."
    default = spec.default
    if default not in (None, "", (), False):
        body += f" Default: {default}."
    elif default is False:
        body += " Default: off."
    lines.append(esc(body))
    applies = ", ".join(spec.commands) if spec.commands else "every command"
    lines.append(".br")
    lines.append(esc(f"Applies to: {applies}. Config key: {spec.config_key}. Env: {spec.env_var}."))
    return lines


def exit_codes_section() -> list[str]:
    lines = [".SH EXIT STATUS"]
    for code in ExitCode:
        lines.append(".TP")
        lines.append(f".B {int(code)}")
        lines.append(esc(DESCRIPTIONS[int(code)]))
    return lines


def env_section() -> list[str]:
    lines = [
        ".SH ENVIRONMENT",
        "Every option has an SR_* variable: the option name in upper case with",
        "'-' replaced by '_'. A variable overrides configuration files but is",
        "overridden by the command line.",
    ]
    for spec in OPTIONS:
        lines.append(".TP")
        lines.append(f".B {esc(spec.env_var)}")
        lines.append(esc(f"Same as --{spec.name}. {spec.help}"))
    for name, description in (
        ("SR_NO_UPDATE_CHECK", "Set to 1 to disable the background update check."),
        ("SR_UPDATE_REPO", "Override the GitHub repository used by `sr update`."),
        ("SR_GITHUB_TOKEN", "Bearer token for the GitHub API (raises the rate limit)."),
        ("SR_CONFIG", "Path to an extra configuration file."),
        ("SR_DEBUG", "Set to 1 to print a traceback when sr crashes."),
        ("SR_OFFLINE", "Set to 1 to skip the network probe in `sr doctor`."),
        ("SR_COMPLETION_SHELL", "Force the shell for `sr completions`."),
        ("NO_COLOR", "Disable colours (https://no-color.org)."),
        ("CLICOLOR_FORCE", "Force colours even when stdout is not a terminal."),
        ("COLUMNS", "Terminal width used for wrapping help output."),
    ):
        lines.append(".TP")
        lines.append(f".B {esc(name)}")
        lines.append(esc(description))
    return lines


def files_section() -> list[str]:
    return [
        ".SH FILES",
        ".TP",
        ".B <target>/.sr/sessions/<id>/",
        "Session store: manifest.json, journal.jsonl, a content-addressed blob",
        "store under files/, and per-file diffs. Created by every mutating run.",
        "Add .sr/ to .gitignore.",
        ".TP",
        ".B /etc/sr/sr.conf",
        "System-wide configuration (lowest priority).",
        ".TP",
        ".B $XDG_CONFIG_HOME/sr/sr.conf",
        "User configuration; ~/.config/sr/sr.conf by default.",
        ".TP",
        ".B <project>/.srrc",
        "Project configuration, found by walking up to the repository root.",
        ".TP",
        ".B $XDG_CACHE_HOME/sr/update-check.json",
        "Cache for the background update check (24h TTL).",
    ]


def see_also() -> list[str]:
    lines = [".SH SEE ALSO"]
    lines.append(
        esc(
            "sr help (the built-in manual), sr help topics, sr doctor, sr selftest, "
            "grep(1), sed(1), diff(1), patch(1)"
        )
    )
    lines.append(".PP")
    lines.append(
        "Full documentation: https://github.com/paulmann/sr-search-replace"
    )
    return lines


def build_manpage() -> str:
    """Render the complete man page."""
    lines: list[str] = []
    lines.extend(header())
    lines.extend(name_section())
    lines.extend(synopsis_section())

    for topic, heading in MAN_SECTIONS:
        lines.append(".SH " + esc(heading))
        body = strip_titles(render_topic(topic))
        lines.extend(paragraph(body))

    lines.extend(commands_section())
    lines.extend(options_section())
    lines.extend(exit_codes_section())
    lines.extend(env_section())
    lines.extend(files_section())
    lines.append(".SH AUTHOR")
    lines.append(esc("Mikhail Deynekin <mid1977@gmail.com> — https://deynekin.com"))
    lines.extend(see_also())
    return "\n".join(lines) + "\n"


def lint(path: Path) -> list[str]:
    """Run the groff linter when it is available; otherwise skip quietly."""
    import shutil

    if not shutil.which("mandoc"):
        return []
    completed = subprocess.run(
        ["mandoc", "-T", "lint", str(path)], capture_output=True, text=True, check=False
    )
    output = (completed.stdout + completed.stderr).strip()
    return [line for line in output.splitlines() if line.strip()] if completed.returncode else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "-o", default=str(DEFAULT_OUTPUT), help="Output path ('-' for stdout).")
    parser.add_argument("--check", action="store_true", help="Fail when the committed page is out of date.")
    parser.add_argument("--lint", action="store_true", help="Run mandoc -Tlint when available.")
    args = parser.parse_args(argv)

    text = build_manpage()

    if args.output == "-":
        sys.stdout.write(text)
        return 0

    target = Path(args.output)
    if args.check:
        if not target.is_file():
            print(f"{target} is missing; run `make man`", file=sys.stderr)
            return 1
        current = target.read_text(encoding="utf-8")
        if _without_date(current) != _without_date(text):
            print(f"{target} is out of date; run `make man`", file=sys.stderr)
            return 1
        print(f"{target} is up to date")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o644)
    print(f"wrote {target} ({len(text.splitlines())} lines)")

    if args.lint:
        problems = lint(target)
        for problem in problems:
            print(f"  mandoc: {problem}")
    return 0


def _without_date(text: str) -> str:
    """Ignore the .TH date line so --check is not tripped by the calendar."""
    return "\n".join(line for line in text.splitlines() if not line.startswith(".TH"))


if __name__ == "__main__":
    raise SystemExit(main())

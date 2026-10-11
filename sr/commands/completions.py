"""``sr completions`` — shell completion generated from the live option schema.

The scripts are *derived* from :data:`sr.options.OPTIONS` and
:data:`sr.cli.SUBCOMMANDS`, so they cannot drift out of sync with the parser:
adding an option to the schema automatically adds it to bash, zsh and fish
completion.

Install locations
-----------------
``bash``
    ``$XDG_DATA_HOME/bash-completion/completions/sr`` (default
    ``~/.local/share/…``), falling back to ``~/.bash_completion``.
``zsh``
    the first writable directory in ``$fpath``, else ``~/.zfunc/_sr``
    (``sr`` prints the ``fpath`` line you need to add to ``.zshrc``).
``fish``
    ``~/.config/fish/completions/sr.fish``.
"""

from __future__ import annotations

import os
import shlex
import stat
from collections.abc import Iterable
from pathlib import Path

from ..cli import SUBCOMMAND_ALIASES, SUBCOMMANDS
from ..context import Context
from ..errors import UsageError
from ..exit_codes import ExitCode
from ..options import OPTIONS, OptionKind, OptionSpec

SHELLS = ("bash", "zsh", "fish")


def cmd_completions(context: Context) -> int:
    """Print or install a completion script."""
    args = context.args
    shell = getattr(args, "shell", None) or detect_shell()
    if shell not in SHELLS:
        raise UsageError(
            f"unsupported shell {shell!r}",
            hint=f"supported: {', '.join(SHELLS)}",
        )
    script = generate(shell)
    if getattr(args, "install", False):
        target = install(shell, script, console=context.console)
        context.console.success(f"{shell} completion installed at {target}")
        _print_install_notes(shell, target, context)
    else:
        context.console.write_raw(script)
    return int(ExitCode.OK)


def detect_shell() -> str:
    """Best-effort detection of the user's shell."""
    shell = os.environ.get("SR_COMPLETION_SHELL", "").strip().lower()
    if shell in SHELLS:
        return shell
    parent = os.environ.get("SHELL", "")
    name = Path(parent).name.lower() if parent else ""
    if name in SHELLS:
        return name
    # Fall back to the parent process name (works when $SHELL is a login shell).
    try:
        import subprocess

        output = subprocess.run(
            ["ps", "-p", str(os.getppid()), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip().lower()
    except Exception:
        output = ""
    for candidate in SHELLS:
        if candidate in output:
            return candidate
    return "bash"


def generate(shell: str) -> str:
    """Render the completion script for ``shell``."""
    if shell == "bash":
        return _bash_script()
    if shell == "zsh":
        return _zsh_script()
    if shell == "fish":
        return _fish_script()
    raise UsageError(f"unsupported shell {shell!r}", hint=f"supported: {', '.join(SHELLS)}")


# ---------------------------------------------------------------------------
# Data derived from the schema
# ---------------------------------------------------------------------------


def subcommand_names() -> list[str]:
    return [name for name, _aliases, _summary in SUBCOMMANDS]


def subcommand_aliases() -> list[str]:
    return sorted(SUBCOMMAND_ALIASES)


def flags_for(command: str | None = None) -> list[str]:
    """Long flags that apply to ``command`` (or the global ones)."""
    result: list[str] = []
    for spec in OPTIONS:
        if spec.hidden:
            continue
        if command is None:
            if spec.commands:
                continue
        elif spec.commands and command not in spec.commands:
            continue
        result.append(f"--{spec.name}")
        if spec.negatable:
            result.append(f"--no-{spec.name}")
    return sorted(set(result))


def short_flags_for(command: str | None = None) -> list[str]:
    result = [f"-{spec.short}" for spec in OPTIONS if spec.short and _applies(spec, command)]
    return sorted(set(result))


def _applies(spec: OptionSpec, command: str | None) -> bool:
    """True when ``spec`` is meaningful for ``command`` (``None`` = global)."""
    if not spec.commands:
        return True
    return bool(command and command in spec.commands)


def value_hints(spec: OptionSpec) -> list[str]:
    if spec.kind == OptionKind.CHOICE:
        return list(spec.choices)
    if spec.name == "target":
        return []
    return []


def specs_by_command(command: str) -> Iterable[OptionSpec]:
    for spec in OPTIONS:
        if spec.hidden:
            continue
        if not spec.commands or command in spec.commands:
            yield spec


# ---------------------------------------------------------------------------
# bash
# ---------------------------------------------------------------------------


def _bash_script() -> str:
    commands = " ".join(subcommand_names())
    aliases = " ".join(subcommand_aliases())
    global_flags = " ".join([*flags_for(None), "--help", "--help-all", "--version", "--json", "--porcelain",
                             "--no-color", "--no-update-check", "--config", "--config-file", "--debug"])
    per_command = {name: " ".join([*flags_for(name), *short_flags_for(name)]) for name in subcommand_names()}
    per_command["replace"] += " --help"
    body = "\n".join(
        f'    {name})\n      opts="{flags}"\n      ;;' for name, flags in per_command.items()
    )
    choices = " ".join(
        sorted({choice for spec in OPTIONS for choice in value_hints(spec)})
    )
    return f'''# bash completion for sr — generated by `sr completions bash`
# Install: sr completions bash --install
_sr_completion() {{
  local cur prev words cword
  COMPREPLY=()
  cur="${{COMP_WORDS[COMP_CWORD]}}"
  prev="${{COMP_WORDS[COMP_CWORD-1]}}"

  # Options that take a value: complete the value, not another flag.
  case "$prev" in
    --color|--verbosity|--encoding-error|--line-endings|--write|--pattern-kind|--channel|--source)
      COMPREPLY=( $(compgen -W "{choices} auto" -- "$cur") )
      return 0
      ;;
    --target|-C|--output-dir|-o|--backup-dir|--files-from|--config-file)
      COMPREPLY=( $(compgen -d -f -- "$cur") )
      return 0
      ;;
    --help)
      COMPREPLY=( $(compgen -W "topics options examples matching backup rollback sessions update config json exit-codes env install" -- "$cur") )
      return 0
      ;;
  esac

  local cmd=""
  local i=1
  while [ $i -lt $COMP_CWORD ]; do
    local word="${{COMP_WORDS[i]}}"
    case "$word" in
      -*) ;;
      *) cmd="$word"; break ;;
    esac
    i=$((i+1))
  done

  if [ -z "$cmd" ]; then
    if [[ "$cur" == -* ]]; then
      COMPREPLY=( $(compgen -W "{global_flags}" -- "$cur") )
    else
      COMPREPLY=( $(compgen -W "{commands} {aliases}" -- "$cur") $(compgen -d -f -- "$cur") )
    fi
    return 0
  fi

  local opts=""
  case "$cmd" in
{body}
    *)
      opts="{global_flags}"
      ;;
  esac

  if [[ "$cur" == -* ]]; then
    COMPREPLY=( $(compgen -W "$opts {global_flags}" -- "$cur") )
  else
    COMPREPLY=( $(compgen -d -f -- "$cur") )
  fi
  return 0
}}
complete -F _sr_completion sr
'''


# ---------------------------------------------------------------------------
# zsh
# ---------------------------------------------------------------------------


def _zsh_script() -> str:
    lines: list[str] = ["#compdef sr", "# zsh completion for sr — generated by `sr completions zsh`", "_sr() {", "  local -a commands", "  commands=("]
    for name, _aliases, summary in SUBCOMMANDS:
        lines.append(f'    "{name}:{_zsh_escape(summary)}"')
    lines.append("  )")
    lines.append("  local -a global_opts")
    lines.append("  global_opts=(")
    for spec in OPTIONS:
        if spec.commands or spec.hidden:
            continue
        lines.append(f'    "{_zsh_option(spec)}"')
    for flag, help_text in (
        ("--help", "Show help and exit"),
        ("--help-all", "Show help for every subcommand and option"),
        ("--version", "Print version information"),
        ("--json", "Emit a JSON document"),
        ("--porcelain", "Emit machine-readable records"),
        ("--no-color", "Disable colours"),
        ("--debug", "Debug logging"),
    ):
        lines.append(f'    "{flag}[{help_text}]"')
    lines.append("  )")
    lines.append("  if (( CURRENT == 2 )); then")
    lines.append("    _describe -t commands 'sr command' commands")
    lines.append("    _files")
    lines.append("    return")
    lines.append("  fi")
    lines.append("  local cmd=\"${words[2]}\"")
    lines.append("  case \"$cmd\" in")
    for name in subcommand_names():
        entries = " ".join(
            f'"{_zsh_option(spec)}"' for spec in specs_by_command(name) if spec.commands
        )
        lines.append(f"    {name})")
        lines.append(f"      _arguments -s : $global_opts {entries} '*:file:_files'")
        lines.append("      ;;")
    lines.append("    *)")
    lines.append("      _arguments -s : $global_opts '*:file:_files'")
    lines.append("      ;;")
    lines.append("  esac")
    lines.append("}")
    lines.append("_sr \"$@\"")
    return "\n".join(lines) + "\n"


def _zsh_option(spec: OptionSpec) -> str:
    """Render one option in zsh's ``_arguments`` spec syntax."""
    help_text = _zsh_escape(spec.help)
    long_flag = f"--{spec.name}"
    short_flag = f"-{spec.short}" if spec.short else ""

    if spec.kind == OptionKind.FLAG:
        body = f"[{help_text}]"
    else:
        metavar = _zsh_escape(spec.metavar or "VALUE")
        if spec.kind == OptionKind.CHOICE and spec.choices:
            action = f"({'|'.join(spec.choices)})"
        elif spec.kind in {OptionKind.PATH, OptionKind.LIST}:
            action = "_files"
        else:
            action = " "
        body = f"=[{help_text}]:{metavar}:{action}"

    if short_flag:
        return f"({short_flag} {long_flag}){{{short_flag},{long_flag}}}{body}"
    return f"{long_flag}{body}"


def _zsh_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace(":", "\\:")


# ---------------------------------------------------------------------------
# fish
# ---------------------------------------------------------------------------


def _fish_script() -> str:
    lines: list[str] = ["# fish completion for sr — generated by `sr completions fish`"]
    lines.append("complete -c sr -e")
    for name, _aliases, summary in SUBCOMMANDS:
        lines.append(
            f"complete -c sr -f -n '__fish_use_subcommand' -a {name} -d {_fish_quote(summary)}"
        )
    for alias, canonical in sorted(SUBCOMMAND_ALIASES.items()):
        lines.append(
            f"complete -c sr -f -n '__fish_use_subcommand' -a {alias} -d {_fish_quote(f'alias for {canonical}')}"
        )
    for spec in OPTIONS:
        if spec.hidden:
            continue
        # Quote the condition: fish's `-n` takes a single shell-quoted string,
        # and an unquoted space would break the directive.
        condition = (
            "__fish_use_subcommand"
            if not spec.commands
            else f"__fish_seen_subcommand_from {' '.join(spec.commands)}"
        )
        parts = [f"complete -c sr -f -n {_fish_quote(condition)}"]
        if spec.short:
            parts.append(f"-s {spec.short}")
        parts.append(f"-l {spec.name}")
        if spec.kind != OptionKind.FLAG:
            parts.append("-r")
            if spec.kind == OptionKind.CHOICE and spec.choices:
                parts.append(f"-a {_fish_quote(' '.join(spec.choices))}")
        parts.append(f"-d {_fish_quote(spec.help)}")
        lines.append(" ".join(parts))
    for flag, help_text in (
        ("help", "Show help and exit"),
        ("version", "Print version information"),
        ("json", "Emit a JSON document"),
        ("porcelain", "Emit machine-readable records"),
        ("no-color", "Disable colours"),
        ("debug", "Debug logging"),
    ):
        lines.append(f"complete -c sr -f -l {flag} -d {_fish_quote(help_text)}")
    return "\n".join(lines) + "\n"


def _fish_quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install_locations(shell: str) -> list[Path]:
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", "") or (home / ".local" / "share"))
    if shell == "bash":
        return [
            data_home / "bash-completion" / "completions" / "sr",
            home / ".bash_completion",
        ]
    if shell == "zsh":
        return [
            home / ".zfunc" / "_sr",
            home / ".oh-my-zsh" / "completions" / "_sr",
            data_home / "zsh" / "site-functions" / "_sr",
        ]
    return [home / ".config" / "fish" / "completions" / "sr.fish"]


def install(shell: str, script: str, *, console=None) -> Path:
    """Write ``script`` to the first writable completion location."""
    candidates = install_locations(shell)
    last_error: Exception | None = None
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(script, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            return path
        except OSError as exc:
            last_error = exc
            continue
    raise UsageError(
        f"cannot install the {shell} completion",
        hint=str(last_error) if last_error else f"tried: {', '.join(str(item) for item in candidates)}",
    )


def _print_install_notes(shell: str, target: Path, context: Context) -> None:
    console = context.console
    if shell == "zsh":
        console.info("make sure ~/.zfunc is on your fpath, in .zshrc *before* compinit:")
        console.write('  fpath=(~/.zfunc $fpath)')
        console.write("  autoload -Uz compinit && compinit")
    elif shell == "bash":
        if target.name == ".bash_completion":
            console.info("add this to ~/.bashrc if it is not there already:")
            console.write("  [ -f ~/.bash_completion ] && . ~/.bash_completion")
        else:
            console.info("bash-completion loads this file automatically in a new shell")
    else:
        console.info("fish picks the file up in a new shell")
    console.info("then start a new shell, or run:")
    console.write(f"  {shlex.join(['source', str(target)]) if shell != 'fish' else 'exec fish'}")

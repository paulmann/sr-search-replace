"""``sr config`` — inspect and manage configuration files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import config as config_module
from ..context import Context
from ..errors import ConfigError, UsageError
from ..exit_codes import ExitCode
from ..options import BY_CONFIG_KEY, BY_NAME, GROUPS, OPTIONS


def cmd_config(context: Context) -> int:
    """``sr config [list|show|init|path|validate] [KEY]``."""
    args = context.args
    action = str(getattr(args, "action", "list") or "list")
    root = Path(os.path.expanduser(str(getattr(args, "target", ".") or "."))).resolve()

    if action == "path":
        return _action_path(context, root)
    if action == "init":
        return _action_init(context, root)
    if action == "validate":
        return _action_validate(context, root)
    if action == "show":
        return _action_show(context, root)
    return _action_list(context, root)


def _action_list(context: Context, root: Path) -> int:
    console = context.console
    show_origin = bool(getattr(context.args, "show_origin", False))
    lines = config_module.render_config(
        context.config, show_origin=show_origin, only=()
    )
    if console.json_mode:
        console.emit_json(
            {
                "values": context.config.as_dict(),
                "files": [str(path) for path in context.config.files_loaded],
                "warnings": context.config.warnings,
                "options": [
                    {
                        "key": spec.config_key,
                        "flag": f"--{spec.name}",
                        "group": spec.group,
                        "env": spec.env_var,
                        "value": context.config.get(spec.config_key),
                        "default": spec.default,
                        "origin": context.config.origin_of(spec.config_key),
                    }
                    for spec in OPTIONS
                ],
            }
        )
        return int(ExitCode.OK)

    current_group = ""
    titles = dict(GROUPS)
    lookup = {spec.config_key: spec for spec in OPTIONS}
    for line in lines:
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        spec = lookup.get(key)
        group = spec.group if spec else "general"
        if group != current_group:
            current_group = group
            console.write("")
            console.write(console.paint(f"# {titles.get(group, group).upper()}", "bold"))
        if not value:
            value = console.paint("(unset)", "dim")
        console.write(f"  {key.ljust(22)} = {value}")
    console.write("")
    for warning in context.config.warnings:
        console.warn(warning)
    if context.config.files_loaded:
        console.info("loaded: " + ", ".join(str(path) for path in context.config.files_loaded))
    else:
        console.info("no config files found — every value is a built-in default")
    console.write("")
    console.write("Edit with: sr config init    ·    Inspect one: sr config show <key>")
    return int(ExitCode.OK)


def _action_show(context: Context, root: Path) -> int:
    key = str(getattr(context.args, "key", "") or getattr(context.args, "option", "") or "")
    key = key.strip().lstrip("-")
    if not key:
        raise UsageError("config show needs an option name", hint="example: sr config show confirm-threshold")
    normalized = key.replace("-", "_")
    spec = BY_CONFIG_KEY.get(normalized) or BY_NAME.get(key.replace("_", "-"))
    if spec is None:
        raise UsageError(
            f"unknown option {key!r}",
            hint="run `sr config list` to see every valid key",
        )
    value = context.config.get(spec.config_key)
    if context.console.json_mode:
        context.console.emit_json(
            {
                "key": spec.config_key,
                "flag": f"--{spec.name}",
                "flags": list(spec.flag_names()),
                "kind": spec.kind,
                "choices": list(spec.choices),
                "group": spec.group,
                "env": spec.env_var,
                "value": value,
                "default": spec.default,
                "origin": context.config.origin_of(spec.config_key),
                "help": spec.help,
                "commands": list(spec.commands),
            }
        )
        return int(ExitCode.OK)
    console = context.console
    console.header(f"--{spec.name}")
    console.kv("Value", _render(value))
    console.kv("Default", _render(spec.default))
    console.kv("Origin", console.paint(context.config.origin_of(spec.config_key), "cyan"))
    console.kv("Kind", spec.kind + (f" ({', '.join(spec.choices)})" if spec.choices else ""))
    console.kv("Flags", ", ".join(spec.flag_names()))
    console.kv("Environment", spec.env_var)
    console.kv("Config key", spec.config_key)
    console.kv("Applies to", ", ".join(spec.commands) if spec.commands else "every command")
    console.write("")
    console.write(spec.help)
    return int(ExitCode.OK)


def _action_path(context: Context, root: Path) -> int:
    paths = config_module.config_search_path(root)
    if context.console.json_mode:
        context.console.emit_json(
            {
                "search_path": [
                    {"path": str(path), "exists": path.is_file(), "readable": os.access(path, os.R_OK)}
                    for path in paths
                ]
            }
        )
        return int(ExitCode.OK)
    console = context.console
    console.header("Configuration search path (lowest priority first)")
    for index, path in enumerate(paths, start=1):
        state = "present" if path.is_file() else "absent"
        color = "green" if path.is_file() else "dim"
        console.write(f"  {index}. {console.paint(str(path), color)}  [{state}]")
    console.write("")
    console.info("Command line flags beat every file; SR_* variables beat project files.")
    return int(ExitCode.OK)


def _action_init(context: Context, root: Path) -> int:
    console = context.console
    explicit = getattr(context.args, "file", None)
    target = Path(os.path.expanduser(str(explicit))) if explicit else config_module.user_config_path()
    force = bool(getattr(context.args, "force", False))
    path = config_module.write_config_template(target, force=force)
    console.success(f"wrote {path}")
    console.info("every key maps 1:1 to a command line flag; uncomment what you need")
    console.write("")
    console.write("Validate with: sr config validate")
    console.write("Inspect with : sr config list --show-origin")
    return int(ExitCode.OK)


def _action_validate(context: Context, root: Path) -> int:
    console = context.console
    paths = [path for path in config_module.config_search_path(root) if path.is_file()]
    problems: list[str] = []
    if not paths:
        console.info("no configuration files to validate")
        return int(ExitCode.OK)
    for path in paths:
        warnings: list[str] = []
        try:
            config_module.load_file(path, warnings)
        except ConfigError as exc:
            problems.append(f"{path}: {exc}")
            continue
        problems.extend(warnings)
    if context.console.json_mode:
        console.emit_json({"files": [str(item) for item in paths], "problems": problems, "ok": not problems})
        return int(ExitCode.OK) if not problems else int(ExitCode.USAGE)
    for path in paths:
        console.info(f"parsed {path}")
    if problems:
        console.write("")
        for problem in problems:
            console.error(problem)
        return int(ExitCode.USAGE)
    console.success(f"{len(paths)} file(s) are valid")
    return int(ExitCode.OK)


def _render(value: Any) -> str:
    if value is None:
        return "(not set)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "(empty)"
    return json.dumps(value) if isinstance(value, dict) else str(value)

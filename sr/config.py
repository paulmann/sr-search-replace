"""Configuration files and environment variables.

Resolution order (later wins)::

    built-in defaults
      → /etc/sr/sr.conf                      (system)
      → $XDG_CONFIG_HOME/sr/sr.conf          (user, default ~/.config/sr/sr.conf)
      → <target>/.srrc  and  <target>/.sr/sr.conf   (project)
      → $SR_CONFIG                           (explicit file)
      → SR_* environment variables
      → --config key=value on the command line
      → command line flags

File syntax is a small, forgiving INI dialect::

    # comment
    ignore-case = true            # applies to every command
    exclude = target, *.min.js    # comma or whitespace separated list

    [replace]                     # applies to `sr replace` only
    backup = false
    write = stdout

    [find]
    context = 1

Unknown keys and invalid values are reported as :class:`~sr.errors.ConfigError`
with the file name and line number, because a silently ignored typo in a
config file is exactly the kind of surprise a bulk-edit tool must not have.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .options import BY_CONFIG_KEY, BY_ENV, BY_NAME, OPTIONS

SECTION_RE = re.compile(r"^\[(?P<name>[A-Za-z0-9_.:-]+)\]$")
ASSIGN_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)\s*(?P<op>[:=])\s*(?P<value>.*)$")
BARE_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)$")

#: Environment variable that points at an extra config file.
ENV_CONFIG_FILE = "SR_CONFIG"

SYSTEM_CONFIG = Path("/etc/sr/sr.conf")


def user_config_path() -> Path:
    """XDG-compliant user config location."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "sr" / "sr.conf"


#: Files that mark the root of a project.  The upward search for a project
#: ``.srrc`` stops at the first one, so a nested checkout never inherits
#: configuration from an unrelated parent directory.
PROJECT_ROOT_MARKERS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "setup.py",
        ".project",
    }
)


def find_project_root(start: Path) -> Path | None:
    """Nearest ancestor of ``start`` that looks like a project root."""
    resolved = Path(start).resolve()
    if any((resolved / marker).exists() for marker in PROJECT_ROOT_MARKERS):
        return resolved
    for parent in resolved.parents:
        if any((parent / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return parent
        if parent == parent.parent:
            break
    return None


def project_config_paths(target: Path) -> list[Path]:
    """Project-level config candidates, in increasing priority.

    Only two places are consulted: the project root (so one ``.srrc`` can serve
    a whole repository) and the target directory itself (so a sub-project can
    override it).  Walking every ancestor would pick up unrelated directories
    and make the effective configuration impossible to predict.
    """
    resolved = Path(target).resolve()
    root = find_project_root(resolved)
    candidates: list[Path] = []
    if root is not None and root != resolved:
        candidates.append(root / ".srrc")
    candidates.append(resolved / ".sr" / "sr.conf")
    candidates.append(resolved / ".srrc")

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


@dataclass(slots=True)
class ConfigSource:
    """Where a value came from — used by ``sr config list --show-origin``."""

    name: str
    kind: str  # default | file | env | cli-config | flag
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Config:
    """Merged configuration plus its provenance."""

    values: dict[str, Any] = field(default_factory=dict)
    sources: list[ConfigSource] = field(default_factory=list)
    files_loaded: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        spec = BY_NAME.get(key) or BY_CONFIG_KEY.get(key)
        lookup = spec.config_key if spec else key.replace("-", "_")
        if lookup in self.values:
            return self.values[lookup]
        return default if default is not None else (spec.default if spec else None)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        spec = BY_NAME.get(key) or BY_CONFIG_KEY.get(key)
        lookup = spec.config_key if spec else key
        return lookup in self.values

    def origin_of(self, key: str) -> str:
        """Where the effective value came from: ``file:<path>``, ``env``, …"""
        spec = BY_NAME.get(key) or BY_CONFIG_KEY.get(key)
        lookup = spec.config_key if spec else key
        for source in reversed(self.sources):
            if lookup in source.values:
                if source.kind == "default":
                    return "default"
                return f"{source.kind}:{source.name}"
        return "default"

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)

    def section(self, command: str) -> Config:
        """View of the config with ``[command]`` overrides already applied."""
        return self


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` or ``;`` comment that is not inside quotes."""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in {"#", ";"}:
            return line[:index].rstrip()
    return line.rstrip()


def parse_config_text(text: str, *, origin: str) -> dict[str, dict[str, Any]]:
    """Parse the INI dialect into ``{section: {key: raw_value}}``.

    ``section`` is ``""`` for the implicit global section.  Raises
    :class:`ConfigError` with a precise ``file:line`` location.
    """
    sections: dict[str, dict[str, Any]] = {"": {}}
    current = ""
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = SECTION_RE.match(line)
        if section_match:
            current = section_match.group("name").strip().lower()
            sections.setdefault(current, {})
            continue
        assign_match = ASSIGN_RE.match(line)
        if assign_match:
            key = assign_match.group("key").strip().lower().replace("-", "_")
            value = _strip_comment(assign_match.group("value")).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            bucket = sections.setdefault(current, {})
            if key in bucket:
                existing = bucket[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    bucket[key] = [existing, value]
            else:
                bucket[key] = value
            continue
        bare_match = BARE_RE.match(line)
        if bare_match:
            # `ignore-case` on its own means `ignore-case = true`.
            key = bare_match.group("key").strip().lower().replace("-", "_")
            sections.setdefault(current, {})[key] = "true"
            continue
        raise ConfigError(
            f"{origin}:{lineno}: cannot parse {raw_line!r}",
            hint="expected 'key = value', '[section]' or '# comment'",
        )
    return sections


def _coerce_section(
    section_name: str,
    raw: Mapping[str, Any],
    origin: str,
    warnings: list[str],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in raw.items():
        spec = BY_CONFIG_KEY.get(key)
        if spec is None:
            warnings.append(f"{origin}: unknown option '{key}' in [{section_name or 'global'}] (ignored)")
            continue
        try:
            if isinstance(value, list):
                if spec.repeatable:
                    merged: list[Any] = []
                    for item in value:
                        coerced = spec.coerce(item)
                        if isinstance(coerced, list):
                            merged.extend(coerced)
                        else:
                            merged.append(coerced)
                    values[key] = merged
                else:
                    values[key] = spec.coerce(value[-1])
            else:
                values[key] = spec.coerce(value)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"{origin}: invalid value for '{key}': {exc}") from exc
    return values


def load_file(path: Path, warnings: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load one config file; returns ``(global_values, per_command_values)``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror or exc}") from exc
    sections = parse_config_text(text, origin=str(path))
    per_command: dict[str, dict[str, Any]] = {}
    for name, raw in sections.items():
        if not name:
            continue
        base, _, command = name.partition(":")
        if base and base not in {"sr", "default"} and not command:
            # `[replace]`, `[find]`, … → command specific.
            per_command[name] = _coerce_section(name, raw, str(path), warnings)
            continue
        target_command = command or "default"
        bucket = per_command.setdefault(target_command, {})
        bucket.update(_coerce_section(name, raw, str(path), warnings))
    global_values = _coerce_section("", sections.get("", {}), str(path), warnings)
    return global_values, per_command


def load_environment(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read ``SR_*`` variables into config values."""
    environment = os.environ if env is None else env
    warnings: list[str] = []
    values: dict[str, Any] = {}
    for name, raw in environment.items():
        if not name.startswith("SR_"):
            continue
        if name == ENV_CONFIG_FILE:
            continue
        spec = BY_ENV.get(name)
        if spec is None:
            # Tolerate unrelated SR_* variables (e.g. SR_HOME) but mention the
            # ones that look like a typo of a real option.
            guess = name[3:].lower().replace("_", "-")
            if guess in BY_NAME:
                warnings.append(f"environment variable {name} is not used; did you mean --{guess}?")
            continue
        try:
            values[spec.config_key] = spec.coerce(raw)
        except (ValueError, TypeError) as exc:
            warnings.append(f"ignoring {name}: {exc}")
    return values


def build_config(
    *,
    target: Path | str = ".",
    env: Mapping[str, str] | None = None,
    command: str = "",
    cli_config: Sequence[str] = (),
    explicit_files: Sequence[str | os.PathLike[str]] = (),
    load_files: bool = True,
) -> Config:
    """Merge every configuration layer for ``command``."""
    config = Config()
    config.values.update({spec.config_key: spec.default for spec in OPTIONS})
    config.sources.append(ConfigSource(name="defaults", kind="default", values=dict(config.values)))

    if load_files:
        candidates: list[Path] = []
        if SYSTEM_CONFIG.is_file():
            candidates.append(SYSTEM_CONFIG)
        user_path = user_config_path()
        if user_path.is_file():
            candidates.append(user_path)
        candidates.extend(path for path in project_config_paths(Path(target)) if path.is_file())
        for item in explicit_files:
            candidates.append(Path(os.path.expanduser(str(item))))
        env_file = (env or os.environ).get(ENV_CONFIG_FILE, "").strip()
        if env_file:
            candidates.append(Path(os.path.expanduser(env_file)))

        for path in candidates:
            if not path.is_file():
                if path in {Path(os.path.expanduser(str(item))) for item in explicit_files} or (
                    env_file and path == Path(os.path.expanduser(env_file))
                ):
                    raise ConfigError(
                        f"config file not found: {path}",
                        hint="check --config-file / $SR_CONFIG",
                    )
                continue
            global_values, per_command = load_file(path, config.warnings)
            config.files_loaded.append(path)
            layer: dict[str, Any] = {}
            layer.update(global_values)
            layer.update(per_command.get("default", {}))
            if command:
                layer.update(per_command.get(command, {}))
            if layer:
                config.values.update(layer)
                config.sources.append(ConfigSource(name=str(path), kind="file", values=dict(layer)))

    env_values = load_environment(env)
    if env_values:
        config.values.update(env_values)
        config.sources.append(ConfigSource(name="environment", kind="env", values=dict(env_values)))

    if cli_config:
        layer = parse_cli_config(cli_config)
        config.values.update(layer)
        config.sources.append(ConfigSource(name="--config", kind="cli-config", values=dict(layer)))

    return config


def parse_cli_config(pairs: Iterable[str]) -> dict[str, Any]:
    """Parse repeated ``--config key=value`` arguments."""
    values: dict[str, Any] = {}
    for pair in pairs:
        text = str(pair).strip()
        if not text:
            continue
        key, sep, raw_value = text.partition("=")
        key = key.strip().lower().replace("-", "_")
        if not sep:
            raw_value = "true"
        spec = BY_CONFIG_KEY.get(key)
        if spec is None:
            raise ConfigError(
                f"--config: unknown option {key!r}",
                hint="run `sr config list` to see the valid keys",
            )
        try:
            values[key] = spec.coerce(raw_value)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"--config {key}={raw_value!r}: {exc}") from exc
    return values


def render_config(config: Config, *, show_origin: bool = False, only: Sequence[str] = ()) -> list[str]:
    """Render the effective configuration for ``sr config list``.

    Per-invocation flags (``--json``, ``--color``, ``-v`` …) are excluded: they
    are not readable from a config file, so listing them here would advertise a
    knob that does nothing.
    """
    from .options import CONFIGURABLE

    lines: list[str] = []
    for spec in CONFIGURABLE:
        if spec.hidden:
            continue
        if only and spec.name not in only and spec.config_key not in only:
            continue
        value = config.values.get(spec.config_key, spec.default)
        rendered = _render_value(value)
        if show_origin:
            origin = config.origin_of(spec.config_key)
            lines.append(f"{spec.config_key} = {rendered}    # {origin}")
        else:
            lines.append(f"{spec.config_key} = {rendered}")
    return lines


def _render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def write_config_template(path: Path, *, force: bool = False) -> Path:
    """Create a documented starter config (``sr config init``)."""
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists", hint="pass --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# sr configuration file",
        "# Generated by `sr config init`. Every key maps 1:1 to a command line",
        "# option: `ignore-case` here == `--ignore-case` on the command line.",
        "# Values may also be set per command in a [replace] / [find] section.",
        "",
    ]
    current_group = ""
    from .options import CONFIGURABLE, GROUPS

    group_titles = dict(GROUPS)
    for spec in CONFIGURABLE:
        if spec.hidden:
            continue
        if spec.group != current_group:
            current_group = spec.group
            lines.append("")
            lines.append(f"# ── {group_titles.get(current_group, current_group)} " + "─" * 10)
        default = _render_value(spec.default)
        comment = f"# {spec.help}"
        lines.append(comment)
        lines.append(f"# {spec.config_key} = {default}" if default == "" else f"{spec.config_key} = {default}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def config_search_path(target: Path | str = ".") -> list[Path]:
    """All config file locations sr looks at, existing or not (for ``doctor``)."""
    paths = [SYSTEM_CONFIG, user_config_path()]
    paths.extend(project_config_paths(Path(target)))
    env_file = os.environ.get(ENV_CONFIG_FILE, "").strip()
    if env_file:
        paths.append(Path(os.path.expanduser(env_file)))
    return paths

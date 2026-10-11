"""Small, dependency-free helpers shared across the package."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

_SIZE_UNITS: tuple[tuple[str, int], ...] = (
    ("pb", 1024**5),
    ("tb", 1024**4),
    ("gb", 1024**3),
    ("mb", 1024**2),
    ("kb", 1024),
    ("b", 1),
)

_SIZE_RE = re.compile(
    r"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>[kmgtpe]?i?b?)\s*$", re.IGNORECASE
)

_DURATION_RE = re.compile(
    r"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>ms|s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)?\s*$",
    re.IGNORECASE,
)


def parse_size(text: str | int | None, *, default: int | None = None) -> int | None:
    """Parse a human size such as ``10MB``, ``1.5 GiB``, ``512`` into bytes.

    Bare numbers are bytes; a bare number *with* the legacy ``--max-size``
    semantics (megabytes) is handled by the caller, not here.  Returns
    ``default`` for ``None``/empty input and raises :class:`ValueError` for
    anything unparsable so the CLI can turn it into a usage error.
    """
    if text is None:
        return default
    if isinstance(text, int):
        return text
    raw = str(text).strip()
    if not raw:
        return default
    match = _SIZE_RE.match(raw)
    if not match:
        raise ValueError(f"invalid size: {text!r} (try 512, 4KB, 10MB, 1.5GiB)")
    number = float(match.group("num").replace(",", "."))
    unit = match.group("unit").lower().rstrip("b") or "b"
    if unit.endswith("i"):
        unit = unit[:-1]
    multiplier = 1
    for suffix, value in _SIZE_UNITS:
        if suffix.startswith(unit) or unit == suffix.rstrip("b"):
            multiplier = value
            break
    else:
        raise ValueError(f"unknown size unit in {text!r}")
    if unit in {"", "b"}:
        multiplier = 1
    result = int(number * multiplier)
    if result < 0:
        raise ValueError(f"size must not be negative: {text!r}")
    return result


def format_size(num_bytes: float | int, *, precision: int = 1) -> str:
    """Render a byte count as ``1.4 MB`` / ``812 B`` (binary prefixes)."""
    value = float(num_bytes)
    for suffix, unit in _SIZE_UNITS:
        if value >= unit or suffix == "b":
            scaled = value / unit
            if suffix == "b":
                return f"{int(scaled)} B"
            label = suffix.upper().replace("I", "i")
            return f"{scaled:.{precision}f} {label}"
    return f"{value:.0f} B"  # pragma: no cover - unreachable


def parse_duration(text: str | float | None, *, default: float | None = None) -> float | None:
    """Parse ``30s``, ``5m``, ``1h``, ``500ms`` or a bare number of seconds."""
    if text is None:
        return default
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip()
    if not raw:
        return default
    match = _DURATION_RE.match(raw)
    if not match:
        raise ValueError(f"invalid duration: {text!r} (try 30s, 5m, 1h)")
    number = float(match.group("num").replace(",", "."))
    unit = (match.group("unit") or "s").lower()
    factors = {
        "ms": 0.001,
        "s": 1.0,
        "sec": 1.0,
        "secs": 1.0,
        "second": 1.0,
        "seconds": 1.0,
        "m": 60.0,
        "min": 60.0,
        "mins": 60.0,
        "minute": 60.0,
        "minutes": 60.0,
        "h": 3600.0,
        "hr": 3600.0,
        "hrs": 3600.0,
        "hour": 3600.0,
        "hours": 3600.0,
        "d": 86400.0,
        "day": 86400.0,
        "days": 86400.0,
    }
    return number * factors.get(unit, 1.0)


def format_duration(seconds: float) -> str:
    """Render a duration compactly: ``812ms``, ``4.21s``, ``1m05s``."""
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.2f}s"
    minutes, secs = divmod(round(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file, hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA-256 of an in-memory buffer, hex digest."""
    return hashlib.sha256(data).hexdigest()


def short_hash(value: str, length: int = 12) -> str:
    """Truncated hash, handy for ids and log lines."""
    return value[:length]


def file_stat(path: str | os.PathLike[str]) -> tuple[int, float, int, int]:
    """Return ``(size, mtime, uid, gid)``; missing files yield zeros."""
    try:
        info = os.stat(path)
    except OSError:
        return (0, 0.0, -1, -1)
    return (info.st_size, info.st_mtime, info.st_uid, info.st_gid)


def copy_metadata(source: str | os.PathLike[str], target: str | os.PathLike[str], *, ownership: bool = True) -> None:
    """Best-effort copy of mode, timestamps and (optionally) ownership."""
    try:
        shutil.copystat(source, target)
    except OSError:
        pass
    if not ownership:
        return
    try:
        info = os.stat(source)
        os.chown(target, info.st_uid, info.st_gid)
    except (OSError, AttributeError):
        # chown needs privileges; silently ignore, mode/timestamps are kept.
        pass


def unique_path(path: Path) -> Path:
    """Return ``path`` or the first free ``path.N`` sibling."""
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    """De-duplicate a sequence while keeping first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def chunked(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """Yield consecutive slices of ``items`` no longer than ``size``."""
    if size <= 0:
        size = len(items) or 1
    for start in range(0, len(items), size):
        yield items[start : start + size]


def terminal_width(default: int = 80) -> int:
    """Best-effort terminal width, honouring ``COLUMNS``."""
    env = os.environ.get("COLUMNS")
    if env and env.strip().isdigit():
        return max(20, int(env.strip()))
    try:
        return max(20, shutil.get_terminal_size((default, 24)).columns)
    except Exception:  # pragma: no cover - defensive
        return default


#: Irregular plurals used in user-facing messages.
_IRREGULAR_PLURALS = {
    "match": "matches",
    "entry": "entries",
    "directory": "directories",
    "file": "files",
}


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``plural(1, 'file')`` -> ``'1 file'``, ``plural(2, 'match')`` -> ``'2 matches'``."""
    if count == 1:
        return f"{count} {singular}"
    if plural_form:
        return f"{count} {plural_form}"
    return f"{count} {_IRREGULAR_PLURALS.get(singular, singular + 's')}"


def now_iso(when: float | None = None) -> str:
    """UTC timestamp in RFC-3339 form with second precision."""
    stamp = time.gmtime(when if when is not None else time.time())
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", stamp)


def session_id(when: float | None = None) -> str:
    """Sortable, collision-resistant session id: ``20260131T120530Z-a1b2c3``."""
    stamp = time.gmtime(when if when is not None else time.time())
    salt = hashlib.sha256(f"{time.time_ns()}-{os.getpid()}".encode()).hexdigest()[:6]
    return f"{time.strftime('%Y%m%dT%H%M%SZ', stamp)}-{salt}"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def supports_symlinks() -> bool:
    return not is_windows()


def expand_user(path: str) -> Path:
    """``~`` and environment variables expanded, but never resolved."""
    return Path(os.path.expanduser(os.path.expandvars(path)))


def resolve(path: str | os.PathLike[str]) -> Path:
    """Absolute, symlink-free path; tolerates non-existent targets.

    ``os.path.realpath`` is used instead of ``Path.resolve()`` because it is the
    one call that behaves identically for missing paths on every supported
    platform without raising.
    """
    return Path(os.path.realpath(os.path.abspath(str(path))))


def relative_to_cwd(path: str | os.PathLike[str]) -> str:
    """Shortest sensible display form of ``path`` relative to the cwd."""
    text = str(path)
    try:
        return str(Path(text).resolve().relative_to(Path.cwd()))
    except (ValueError, OSError):
        return text


def first_existing(*candidates: str | os.PathLike[str] | None) -> Path | None:
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))

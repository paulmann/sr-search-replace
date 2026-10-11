"""Terminal output: colour, verbosity, machine-readable modes.

The console is a single object that every command talks to.  It owns three
independent concerns that the previous implementation tangled together:

* **styling** — ANSI colours, auto-disabled when stdout is not a TTY, when
  ``NO_COLOR`` is set, or when ``--no-color``/``--color=never`` is given
  (the ``CLICOLOR_FORCE`` variable is honoured too);
* **verbosity** — ``quiet < normal < verbose < debug``;
* **format** — ``text`` (default), ``json`` (one document on stdout) and
  ``porcelain`` (stable, greppable ``path:line:col:text`` lines).

In JSON mode *nothing* but the final document is written to stdout; all
diagnostics go to stderr.  That is what makes ``sr replace … --json | jq``
safe to use in pipelines.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import IO, Any, TextIO

from .util import terminal_width


class Verbosity(IntEnum):
    """Ordered log levels; comparisons like ``v >= Verbosity.VERBOSE`` work."""

    QUIET = 0
    NORMAL = 1
    VERBOSE = 2
    DEBUG = 3

    @classmethod
    def from_option(cls, value: str | int | None) -> Verbosity:
        if value is None:
            return cls.NORMAL
        if isinstance(value, int):
            return cls(clamp_level(value))
        text = str(value).strip().lower()
        mapping = {
            "quiet": cls.QUIET,
            "q": cls.QUIET,
            "error": cls.QUIET,
            "errors": cls.QUIET,
            "silent": cls.QUIET,
            "normal": cls.NORMAL,
            "info": cls.NORMAL,
            "default": cls.NORMAL,
            "verbose": cls.VERBOSE,
            "v": cls.VERBOSE,
            "debug": cls.DEBUG,
            "trace": cls.DEBUG,
        }
        if text.lstrip("-").isdigit():
            return cls(clamp_level(int(text)))
        return mapping.get(text, cls.NORMAL)


def clamp_level(value: int) -> int:
    return max(int(Verbosity.QUIET), min(int(Verbosity.DEBUG), int(value)))


class OutputFormat:
    TEXT = "text"
    JSON = "json"
    PORCELAIN = "porcelain"

    ALL = (TEXT, JSON, PORCELAIN)


#: ANSI palette.  Names match what the help text references.
@dataclass(frozen=True)
class Palette:
    reset: str = "\x1b[0m"
    bold: str = "\x1b[1m"
    dim: str = "\x1b[2m"
    red: str = "\x1b[31m"
    green: str = "\x1b[32m"
    yellow: str = "\x1b[33m"
    blue: str = "\x1b[34m"
    magenta: str = "\x1b[35m"
    cyan: str = "\x1b[36m"
    grey: str = "\x1b[90m"
    red_bg: str = "\x1b[41;97m"
    green_bg: str = "\x1b[42;30m"

    def as_dict(self) -> dict[str, str]:
        return {
            "reset": self.reset,
            "bold": self.bold,
            "dim": self.dim,
            "red": self.red,
            "green": self.green,
            "yellow": self.yellow,
            "blue": self.blue,
            "magenta": self.magenta,
            "cyan": self.cyan,
            "grey": self.grey,
            "red_bg": self.red_bg,
            "green_bg": self.green_bg,
        }


COLOR = Palette()
NO_COLOR = Palette(**dict.fromkeys(Palette().as_dict(), ""))


def detect_color(color_option: str | bool | None, stream: IO[str] | None = None) -> bool:
    """Resolve ``--color`` / ``NO_COLOR`` / TTY detection into a boolean."""
    text = color_option
    if isinstance(text, bool):
        return text
    if isinstance(text, str):
        normalized = text.strip().lower()
        if normalized in {"always", "yes", "force", "true", "1"}:
            return True
        if normalized in {"never", "no", "none", "false", "0"}:
            return False
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("SR_NO_COLOR", "").strip():
        return False
    if os.environ.get("CLICOLOR_FORCE", "") not in {"", "0"}:
        return True
    if os.environ.get("TERM", "") == "dumb":
        return False
    target = stream or sys.stdout
    try:
        return bool(target.isatty())
    except Exception:  # pragma: no cover - exotic streams
        return False


@dataclass
class Console:
    """The single output sink for a command run."""

    verbosity: Verbosity = Verbosity.NORMAL
    color: bool = False
    fmt: str = OutputFormat.TEXT
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    width: int = 0
    prefix: str = "sr"
    _json_buffer: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.width:
            self.width = terminal_width()
        self.palette = COLOR if self.color else NO_COLOR

    # ------------------------------------------------------------------ style
    def paint(self, text: str, *attrs: str) -> str:
        """Wrap ``text`` in the named palette attributes (no-op without colour)."""
        if not self.color or not attrs or text == "":
            return text
        codes = "".join(getattr(self.palette, attr, "") for attr in attrs)
        return f"{codes}{text}{self.palette.reset}"

    # ----------------------------------------------------------------- output
    @property
    def json_mode(self) -> bool:
        return self.fmt == OutputFormat.JSON

    @property
    def porcelain_mode(self) -> bool:
        return self.fmt == OutputFormat.PORCELAIN

    @property
    def machine_readable(self) -> bool:
        return self.json_mode or self.porcelain_mode

    def write(self, text: str = "", *, stream: TextIO | None = None) -> None:
        target = stream or self.out
        if target is None:
            return
        if self.json_mode and target is self.out:
            # Keep stdout pristine for the JSON document.
            self._json_buffer.append(text)
            return
        target.write(text.rstrip("\n") + "\n" if text else "\n")
        target.flush()

    def write_raw(self, text: str, *, stream: TextIO | None = None) -> None:
        """Write without appending a newline (progress bars, prompts)."""
        target = stream or self.out
        if self.json_mode and target is self.out:
            self._json_buffer.append(text)
            return
        target.write(text)
        target.flush()

    def echo(self, message: str = "", *, level: Verbosity = Verbosity.NORMAL) -> None:
        if self.verbosity < level:
            return
        self.write(message)

    def info(self, message: str) -> None:
        if self.machine_readable:
            return
        if self.verbosity < Verbosity.NORMAL:
            return
        self.write(f"{self.paint('[info]', 'blue')} {message}")

    def success(self, message: str) -> None:
        if self.machine_readable:
            return
        if self.verbosity < Verbosity.NORMAL:
            return
        self.write(f"{self.paint('[ok]', 'green', 'bold')} {message}")

    def warn(self, message: str, *, hint: str | None = None) -> None:
        if self.verbosity < Verbosity.NORMAL:
            return
        if self.machine_readable:
            self._stderr(f"warning: {message}")
            if hint:
                self._stderr(f"         {hint}")
            return
        self._stderr(f"{self.paint('[warn]', 'yellow')} {message}")
        if hint:
            self._stderr(f"       {self.paint(hint, 'dim')}")

    def error(self, message: str, *, hint: str | None = None) -> None:
        if self.machine_readable:
            self._stderr(f"error: {message}")
            if hint:
                self._stderr(f"       {hint}")
            return
        self._stderr(f"{self.paint('[error]', 'red', 'bold')} {message}")
        if hint:
            self._stderr(f"        {self.paint(hint, 'dim')}")

    def debug(self, message: str) -> None:
        if self.verbosity < Verbosity.DEBUG:
            return
        self._stderr(f"{self.paint('[debug]', 'cyan')} {message}")

    def verbose(self, message: str) -> None:
        if self.verbosity < Verbosity.VERBOSE:
            return
        if self.machine_readable:
            return
        self._stderr(f"{self.paint('[verbose]', 'grey')} {message}")

    def header(self, title: str) -> None:
        if self.machine_readable or self.verbosity < Verbosity.NORMAL:
            return
        bar = "─" * max(8, min(self.width - 2, len(title) + 4))
        self.write(self.paint(bar, "magenta"))
        self.write(self.paint(f" {title} ", "magenta", "bold"))
        self.write(self.paint(bar, "magenta"))

    def rule(self, char: str = "─") -> None:
        if self.machine_readable:
            return
        self.write(self.paint(char * max(8, self.width - 2), "grey"))

    def kv(self, key: str, value: Any, *, indent: int = 0, width: int = 22) -> None:
        """Aligned ``key: value`` line used by summaries and ``doctor``."""
        if self.machine_readable:
            return
        pad = " " * indent
        self.write(f"{pad}{self.paint(str(key).ljust(width), 'dim')} {value}")

    def _stderr(self, message: str) -> None:
        if self.err is None:
            return
        self.err.write(message.rstrip("\n") + "\n")
        self.err.flush()

    # -------------------------------------------------------------- porcelain
    def record(
        self,
        path: str | Path,
        line: int,
        column: int,
        text: str,
        *,
        kind: str = "match",
        replacement: str | None = None,
    ) -> None:
        """Emit one machine-readable record (porcelain) or a styled match line."""
        if self.porcelain_mode:
            payload = text.replace("\n", "\\n")
            self.write(f"{path}:{line}:{column}:{kind}:{payload}")
            return
        if self.json_mode:
            return
        self.write(self.format_match_line(path, line, column, text, replacement))

    def format_match_line(
        self,
        path: str | Path,
        line: int,
        column: int,
        text: str,
        replacement: str | None = None,
    ) -> str:
        locator = self.paint(f"{path}:{line}:{column}", "magenta")
        body = text.rstrip("\n")
        if self.color:
            body = body.replace("\t", "    ")
        return f"{locator}: {body}"

    # ------------------------------------------------------------------- json
    def emit_json(self, document: Any) -> None:
        """Write the final JSON document to the *real* stdout."""
        target = self.out if self.out is not None else sys.stdout
        text = json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False, default=str)
        # Detour around the json_mode guard by writing to the raw stream.
        raw = getattr(target, "buffer", None)
        if raw is not None:
            raw.write((text + "\n").encode("utf-8"))
            raw.flush()
        else:  # pragma: no cover - StringIO in tests
            target.write(text + "\n")
            target.flush()

    def suppressed_output(self) -> list[str]:
        """Lines that were buffered instead of printed while in JSON mode."""
        return list(self._json_buffer)


def make_console(
    *,
    verbosity: Verbosity | int | str = Verbosity.NORMAL,
    color: str | bool | None = None,
    fmt: str = OutputFormat.TEXT,
    out: TextIO | None = None,
    err: TextIO | None = None,
    width: int = 0,
) -> Console:
    """Factory that resolves colour/verbosity/format in one place."""
    level = verbosity if isinstance(verbosity, Verbosity) else Verbosity.from_option(verbosity)
    normalized_fmt = (fmt or OutputFormat.TEXT).strip().lower()
    if normalized_fmt not in OutputFormat.ALL:
        normalized_fmt = OutputFormat.TEXT
    stream = out if out is not None else sys.stdout
    use_color = detect_color(color, stream) and normalized_fmt == OutputFormat.TEXT
    return Console(
        verbosity=level,
        color=use_color,
        fmt=normalized_fmt,
        out=stream,
        err=err if err is not None else sys.stderr,
        width=width or terminal_width(),
    )


class ProgressBar:
    """Minimal single-line progress indicator (TTY only)."""

    def __init__(self, console: Console, total: int, *, label: str = "processing", enabled: bool | None = None) -> None:
        self.console = console
        self.total = max(0, int(total))
        self.label = label
        self.done = 0
        self.enabled = (
            enabled
            if enabled is not None
            else (
                self.total > 0
                and not console.machine_readable
                and console.verbosity >= Verbosity.NORMAL
                and _is_tty(console.err)
            )
        )
        self._last_render = 0.0

    def update(self, amount: int = 1, *, suffix: str = "") -> None:
        self.done += amount
        if not self.enabled:
            return
        import time

        now = time.monotonic()
        if now - self._last_render < 0.08 and self.done < self.total:
            return
        self._last_render = now
        self._render(suffix)

    def _render(self, suffix: str = "") -> None:
        width = max(10, min(40, self.console.width - len(self.label) - 24))
        ratio = 0.0 if not self.total else min(1.0, self.done / self.total)
        filled = int(width * ratio)
        bar = "█" * filled + "░" * (width - filled)
        line = f"\r{self.label} {bar} {self.done}/{self.total} ({ratio * 100:3.0f}%)"
        if suffix:
            line += f" {suffix}"
        self.console.write_raw(line.ljust(min(self.console.width - 1, len(line) + 4)), stream=self.console.err)

    def close(self) -> None:
        if self.enabled:
            self.console.write_raw("\n", stream=self.console.err)
            self.enabled = False

    def __enter__(self) -> ProgressBar:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _is_tty(stream: IO[str] | None) -> bool:
    try:
        return bool(stream and stream.isatty())
    except Exception:  # pragma: no cover
        return False


def wrap_lines(text: str, width: int, *, indent: str = "") -> Iterable[str]:
    """Poor-man's word wrap that keeps indentation stable."""
    limit = max(20, width - len(indent))
    for paragraph in text.splitlines() or [""]:
        if not paragraph:
            yield ""
            continue
        words: Sequence[str] = paragraph.split(" ")
        current = ""
        for word in words:
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= limit:
                current += f" {word}"
            else:
                yield f"{indent}{current}"
                current = word
            while len(current) > limit:
                yield f"{indent}{current[:limit]}"
                current = current[limit:]
        if current:
            yield f"{indent}{current}"

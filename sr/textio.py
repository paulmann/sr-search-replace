"""Encoding-aware, line-ending-preserving file I/O.

sr never trusts the platform default encoding: a bulk rewrite that silently
transcodes files is a data-loss bug.  The rules implemented here are

* an explicit ``--encoding`` always wins;
* otherwise a BOM (UTF-8/16/32) is detected and *preserved* on write;
* otherwise UTF-8 is attempted (strict);
* otherwise ``--encoding-error=replace`` is required, else the file is
  reported as undecodable and skipped;
* the dominant line ending (LF / CRLF / CR) is detected per file and
  restored byte-for-byte on write;
* a trailing newline (or its absence) is preserved.

Everything works on ``str`` internally; :class:`DecodedFile` carries the
metadata needed to write the result back losslessly.
"""

from __future__ import annotations

import codecs
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import SrError

EncodingErrorPolicy = Literal["strict", "replace", "ignore"]

#: Byte order marks we can detect, longest first.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

#: Encodings whose payload is not text and must never be rewritten.
BINARY_SNIFF_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
        ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst",
        ".tar", ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".jar",
        ".war", ".pyc", ".pyo", ".o", ".a", ".obj", ".woff", ".woff2", ".ttf",
        ".otf", ".eot", ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".webm",
        ".sqlite", ".db", ".mdb", ".parquet", ".wasm", ".keystore", ".p12",
        ".der", ".mo",
    }
)


class UndecodableFileError(SrError):
    """A file could not be decoded with the requested policy."""


@dataclass(slots=True)
class DecodedFile:
    """Text of a file plus everything needed to write it back unchanged."""

    path: Path
    text: str
    encoding: str = "utf-8"
    bom: bytes = b""
    line_ending: str = "\n"
    had_trailing_newline: bool = True
    newline_mixed: bool = False
    size: int = 0
    mtime: float = 0.0
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def lines(self) -> list[str]:
        """Logical lines without terminators."""
        return self.text.split("\n") if self.text else []

    def encode(self, text: str | None = None) -> bytes:
        """Serialize ``text`` (default: current text) back to bytes."""
        payload = self.text if text is None else text
        if self.had_trailing_newline and payload and not payload.endswith("\n"):
            payload += "\n"
        elif not self.had_trailing_newline and payload.endswith("\n"):
            payload = payload.rstrip("\n")
        if self.line_ending != "\n":
            payload = payload.replace("\n", self.line_ending)
        codec = self.encoding
        if codec == "utf-8-sig":
            # Strip the BOM from the codec choice; we re-attach it explicitly so
            # that a decoded/encoded round trip is byte-identical.
            codec = "utf-8"
        return self.bom + payload.encode(codec, errors="replace")


def detect_line_ending(raw: bytes) -> tuple[str, bool]:
    """Return the dominant line ending and whether the file mixes endings."""
    crlf = raw.count(b"\r\n")
    total_lf = raw.count(b"\n")
    total_cr = raw.count(b"\r")
    lone_lf = total_lf - crlf
    lone_cr = total_cr - crlf
    # A file is "mixed" when more than one *terminator style* is present.  The
    # dominant style is the one used most often, and it is restored on write;
    # the flag exists so a report can mention the file instead of hiding it.
    kinds = sum(1 for count in (crlf, lone_lf, lone_cr) if count)
    mixed = kinds > 1 or (lone_lf > 0 and lone_cr > 0)
    if crlf >= lone_lf and crlf >= lone_cr and crlf:
        return "\r\n", mixed
    if lone_cr > lone_lf and lone_cr:
        return "\r", mixed
    return "\n", mixed


#: Bytes that may appear in a text file: the whitespace/format characters real
#: editors produce, ASCII printable, and the high-bit range (which covers every
#: 8-bit legacy encoding such as cp1251/latin-1, and UTF-8 continuation bytes).
#:
#: Deliberately excluded: NUL and the C0 controls (0x01-0x06, 0x0e-0x1a,
#: 0x1c-0x1f).  They essentially never occur in text, so a single one is
#: strong evidence of binary content — which is what makes UTF-16 files
#: (every other byte is NUL) get reported as "needs --encoding" instead of
#: being silently rewritten.
_TEXT_BYTES = frozenset(
    {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1B, 0x85}
    | set(range(0x20, 0x7F))
    | set(range(0x80, 0x100))
)


def sniff_binary(raw: bytes, *, sample: int = 8192) -> bool:
    """Heuristic binary test on the first ``sample`` bytes.

    A file is binary when it contains NUL bytes, or when the sampled prefix has
    a high ratio of non-text control bytes.  This mirrors the classic
    ``grep -I`` behaviour without spawning a process.
    """
    if not raw:
        return False
    chunk = raw[:sample]
    if not chunk:
        return False
    # A single NUL byte settles it: no text encoding produces one.
    if b"\x00" in chunk:
        return True
    non_text = sum(1 for byte in chunk if byte not in _TEXT_BYTES)
    return non_text / float(len(chunk)) > 0.30


def detect_bom(raw: bytes) -> tuple[bytes, str | None]:
    """Return ``(bom_bytes, codec)`` for a leading BOM, or ``(b'', None)``."""
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            return bom, codec
    return b"", None


def normalize_codec(name: str) -> str:
    """Canonicalise a user supplied encoding name.

    Accepts the aliases people actually type (``utf8``, ``UTF-8``, ``latin1``,
    ``cp1251``, ``ascii``) and validates that Python knows the codec.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return "utf-8"
    try:
        return codecs.lookup(cleaned).name
    except LookupError as exc:
        raise UndecodableFileError(
            f"unknown encoding {name!r}",
            hint="run `sr help encodings` for the accepted names",
        ) from exc


def decode_bytes(
    raw: bytes,
    *,
    encoding: str | None = None,
    errors: EncodingErrorPolicy = "strict",
) -> tuple[str, str, bytes, bool]:
    """Decode ``raw`` into ``(text, encoding_used, bom, had_trailing_newline)``.

    ``encoding=None`` triggers auto-detection (BOM, then strict UTF-8, then
    the error policy).  Raises :class:`UndecodableFileError` when strict
    decoding fails and the policy forbids lossy recovery.
    """
    bom, bom_codec = detect_bom(raw)
    body = raw[len(bom) :] if bom else raw

    if encoding:
        codec = normalize_codec(encoding)
        # A UTF-16/32 codec already handles its own BOM.
        if codec in {"utf-16", "utf-32", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"}:
            body = raw
            bom = b""
    else:
        codec = bom_codec or "utf-8"

    try:
        text = body.decode(codec, errors="strict")
    except UnicodeDecodeError as exc:
        if errors == "strict":
            raise UndecodableFileError(
                f"cannot decode as {codec}: {exc.reason} at byte {exc.start}",
                hint="pass --encoding=<name> or --encoding-error=replace to force it",
            ) from exc
        text = body.decode(codec, errors=errors)

    trailing = text.endswith("\n") or text.endswith("\r")
    return text, codec, bom, trailing


def read_text_file(
    path: str | os.PathLike[str],
    *,
    encoding: str | None = None,
    errors: EncodingErrorPolicy = "strict",
    max_bytes: int | None = None,
) -> DecodedFile:
    """Read and decode a file into a :class:`DecodedFile`.

    ``max_bytes`` guards against accidental multi-gigabyte reads; when the file
    is larger, :class:`UndecodableFileError` is raised so the caller can skip
    it with a clear message instead of exhausting memory.
    """
    target = Path(path)
    info = target.stat()
    if max_bytes is not None and info.st_size > max_bytes:
        raise UndecodableFileError(
            f"{target}: {info.st_size} bytes exceeds the {max_bytes} byte read limit"
        )
    raw = target.read_bytes()
    line_ending, mixed = detect_line_ending(raw)
    text, codec, bom, trailing = decode_bytes(raw, encoding=encoding, errors=errors)
    # Normalise to LF internally; `encode()` puts the original ending back.
    if line_ending == "\r\n":
        text = text.replace("\r\n", "\n")
    elif line_ending == "\r":
        text = text.replace("\r", "\n")
    return DecodedFile(
        path=target,
        text=text,
        encoding=codec,
        bom=bom,
        line_ending=line_ending,
        had_trailing_newline=trailing,
        newline_mixed=mixed,
        size=info.st_size,
        mtime=info.st_mtime,
    )


def looks_binary(path: str | os.PathLike[str], *, sample: int = 8192) -> bool:
    """Cheap binary test: extension blocklist, then byte sniffing."""
    target = Path(path)
    if target.suffix.lower() in BINARY_SNIFF_EXTENSIONS:
        return True
    try:
        with open(target, "rb") as handle:
            return sniff_binary(handle.read(sample), sample=sample)
    except OSError:
        return False


def is_probably_text(path: str | os.PathLike[str], *, sample: int = 8192) -> bool:
    return not looks_binary(path, sample=sample)

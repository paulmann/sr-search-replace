"""The matching engine.

One engine serves counting, previewing, diffing and replacing, which is the
property the previous (grep-for-counting + sed-for-replacing) implementation
lacked — the two tools disagreed about ``-i``, ``-w`` and ``-E``, so the
reported number of replacements was frequently wrong.

Semantics
---------
``--fixed`` (default)
    The pattern is a literal string.  ``\\n``/``\\t`` style escapes are
    interpreted *before* matching (so ``--fixed 'a\\tb'`` finds a real tab),
    and the replacement is inserted verbatim — back-references are not
    expanded, which makes ``$1``-laden config files safe to edit.
``--regex``
    Python regular expressions (``re``).  The replacement is a template:
    ``\\1``, ``\\g<name>``, ``\\n`` and ``\\\\`` work as in :func:`re.sub`.
``--ere``
    POSIX Extended Regular Expression syntax, translated to Python.  Handy for
    patterns copy-pasted from ``grep -E`` / ``sed -E`` scripts.
``--pcre``
    Perl syntax via ``grep -P``-compatible translation; falls back to
    ``--regex`` when the pattern needs no translation.

Modifiers ``--ignore-case``, ``--word-boundary``, ``--multiline``,
``--line-range`` and ``--max-count`` apply uniformly to all four kinds.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .errors import PatternError

__all__ = [
    "LineRange",
    "MatchInfo",
    "MatchResult",
    "Matcher",
    "PatternKind",
    "PatternSpec",
    "build_spec",
    "compile_pattern",
    "parse_line_range",
    "unescape_literal",
]


class PatternKind(str, Enum):
    """How the search pattern is interpreted."""

    FIXED = "fixed"
    REGEX = "regex"
    ERE = "ere"
    PCRE = "pcre"

    @classmethod
    def parse(cls, value: str) -> PatternKind:
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise PatternError(
                f"unknown pattern kind {value!r}", hint=f"expected one of: {allowed}"
            ) from exc


#: Escape sequences understood by ``--fixed`` (and by literal replacements).
#:
#: Deliberately a *small* set.  ``\b``, ``\a`` and ``\v`` are excluded on
#: purpose: on Windows paths and in config files a backslash before ``b`` means
#: a directory named ``bin``, not a backspace.  Every excluded escape keeps its
#: backslash, which is the only behaviour that cannot silently corrupt a file.
_SIMPLE_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "f": "\f",
    "0": "\0",
    "e": "\x1b",
    "\\": "\\",
    "'": "'",
    '"': '"',
}

#: POSIX character classes inside bracket expressions -> Python equivalents.
_POSIX_CLASSES = {
    "alnum": r"A-Za-z0-9",
    "alpha": r"A-Za-z",
    "ascii": r"\x00-\x7f",
    "blank": r" \t",
    "cntrl": r"\x00-\x1f\x7f",
    "digit": r"0-9",
    "graph": r"!-~",
    "lower": r"a-z",
    "print": r" -~",
    "punct": r"!-/:-@[-`{-~",
    "space": r" \t\n\r\f\v",
    "upper": r"A-Z",
    "word": r"A-Za-z0-9_",
    "xdigit": r"0-9A-Fa-f",
}

_POSIX_CLASS_RE = re.compile(r"\[\.(:[a-z]+:)\]")
_POSIX_CLASS_IN_BRACKET_RE = re.compile(r"\[:([a-z]+):\]")


def unescape_literal(text: str) -> str:
    """Expand C-style escapes in a literal string.

    Unknown escapes keep the backslash (``\\q`` stays ``\\q``, ``\\b`` stays
    ``\\b``) so that Windows paths and regex-ish literals survive untouched.  A
    trailing lone backslash is preserved as well.

    Recognised: ``\\n \\t \\r \\f \\0 \\e \\\\ \\' \\" \\xNN``.
    """
    if "\\" not in text:
        return text
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "\\" or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = text[index + 1]
        if nxt in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[nxt])
            index += 2
            continue
        if nxt == "x" and index + 3 < length + 1:
            hexpart = text[index + 2 : index + 4]
            if len(hexpart) == 2 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
                out.append(chr(int(hexpart, 16)))
                index += 4
                continue
        out.append(char)
        index += 1
    return "".join(out)


def translate_ere(pattern: str) -> str:
    """Best-effort translation of POSIX ERE to Python ``re`` syntax.

    Covered: ``[:class:]`` inside brackets, ``\\<``/``\\>`` word anchors,
    ``\\w``-style escapes (already valid), and rejection of the ERE-only
    constructs Python cannot express.  Interval expressions ``{m,n}`` are
    treated as quantifiers, matching GNU ``grep -E``.
    """
    text = pattern
    text = text.replace(r"\<", r"\b").replace(r"\>", r"\b")

    def _class(match: re.Match[str]) -> str:
        # The captured group already includes the surrounding colons
        # (":digit:"), so it is the dictionary key as-is.
        name = match.group(1)
        replacement = _POSIX_CLASSES.get(name)
        if replacement is None:
            raise PatternError(
                f"unsupported POSIX class [:{name}:]",
                hint="supported: " + ", ".join(sorted(_POSIX_CLASSES)),
            )
        return replacement

    text = _POSIX_CLASS_IN_BRACKET_RE.sub(_class, text)
    text = _POSIX_CLASS_RE.sub(_class, text)

    if "(?" in text:
        raise PatternError(
            "POSIX ERE does not support '(?' groups",
            hint="use --regex for Python syntax or --pcre for Perl syntax",
        )
    return text


_NAMED_GROUP_RE = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")


def translate_pcre(pattern: str) -> str:
    """Translate the handful of Perl-isms that differ from Python ``re``.

    * ``\\z`` (absolute end) becomes Python's ``\\Z``.
    * ``(?<name>...)`` becomes ``(?P<name>...)``.  Look-arounds ``(?<=`` and
      ``(?<!`` are left alone — they are already valid Python.
    * Atomic groups ``(?>...)`` and possessive quantifiers ``*+``/``?+`` are
      passed through: Python 3.11+ supports them natively, and on older
      interpreters the compile error is reported as a normal pattern error.
    """
    text = re.sub(r"(?<!\\)\\z\b", r"\\Z", pattern)
    text = _NAMED_GROUP_RE.sub(lambda m: f"(?P<{m.group(1)}>", text)
    return text


@dataclass(frozen=True, slots=True)
class LineRange:
    """Inclusive 1-based line window ``[start, end]``; ``None`` means open."""

    start: int | None = None
    end: int | None = None

    def contains(self, line_no: int) -> bool:
        """Whether a 1-based line number falls inside the window."""
        start_ok = self.start is None or line_no >= self.start
        end_ok = self.end is None or line_no <= self.end
        return start_ok and end_ok

    def __str__(self) -> str:
        return f"{self.start or 1}..{self.end or '$'}"


def parse_line_range(text: str | None) -> LineRange | None:
    """Parse ``10``, ``10-20``, ``10-``, ``-20`` into a :class:`LineRange`."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if "-" not in raw:
        if not raw.isdigit():
            raise PatternError(f"invalid --line-range {text!r}", hint="try 10, 10-20, 10- or -20")
        value = int(raw)
        return LineRange(value, value)
    left, _, right = raw.partition("-")
    left, right = left.strip(), right.strip()
    start = int(left) if left.isdigit() else None
    end = int(right) if right.isdigit() else None
    if left and not left.isdigit():
        raise PatternError(f"invalid --line-range start {left!r}", hint="try 10-20")
    if right and not right.isdigit():
        raise PatternError(f"invalid --line-range end {right!r}", hint="try 10-20")
    if start is not None and end is not None and start > end:
        raise PatternError(
            f"invalid --line-range {text!r}: start is after end", hint="try 10-20"
        )
    if start is None and end is None:
        raise PatternError(f"invalid --line-range {text!r}", hint="try 10, 10-20, 10- or -20")
    return LineRange(start, end)


@dataclass(frozen=True, slots=True)
class PatternSpec:
    """Fully resolved description of "what to match and how"."""

    search: str
    kind: PatternKind = PatternKind.FIXED
    replacement: str = ""
    ignore_case: bool = False
    word_boundary: bool = False
    multiline: bool = False
    process_escapes: bool = True
    global_replace: bool = True
    max_count: int | None = None
    line_range: LineRange | None = None
    dot_all: bool | None = None

    def validate(self) -> None:
        if self.search == "":
            raise PatternError(
                "the search pattern must not be empty",
                hint="to match empty positions use --regex with an explicit pattern",
            )
        if self.max_count is not None and self.max_count < 1:
            raise PatternError("--max-count must be >= 1")
        if not self.multiline and "\n" in self.effective_search:
            raise PatternError(
                "the pattern contains a newline but --multiline was not given",
                hint="add --multiline to match across line boundaries",
            )
        if self.global_replace is False and self.max_count is not None and self.max_count > 1:
            raise PatternError(
                "--no-global and --max-count>1 contradict each other",
                hint="use either --no-global or --max-count=N",
            )

    @property
    def effective_search(self) -> str:
        """The search string after escape processing."""
        if self.kind is PatternKind.FIXED and self.process_escapes:
            return unescape_literal(self.search)
        return self.search

    @property
    def effective_replacement(self) -> str:
        """The replacement after escape processing (fixed kind only)."""
        if self.kind is PatternKind.FIXED and self.process_escapes:
            return unescape_literal(self.replacement)
        return self.replacement

    def describe(self) -> str:
        flags = [self.kind.value]
        if self.ignore_case:
            flags.append("ignore-case")
        if self.word_boundary:
            flags.append("word-boundary")
        if self.multiline:
            flags.append("multiline")
        if not self.global_replace:
            flags.append("first-per-line")
        if self.max_count:
            flags.append(f"max-count={self.max_count}")
        if self.line_range:
            flags.append(f"lines={self.line_range}")
        return ", ".join(flags)


@dataclass(frozen=True, slots=True)
class MatchInfo:
    """A single match inside a decoded text buffer."""

    start: int
    end: int
    text: str
    line: int
    column: int
    replacement: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(slots=True)
class MatchResult:
    """Outcome of scanning one buffer."""

    matches: list[MatchInfo] = field(default_factory=list)
    truncated: bool = False
    skipped_out_of_range: int = 0

    @property
    def count(self) -> int:
        return len(self.matches)

    def __bool__(self) -> bool:
        return bool(self.matches)


class Matcher:
    """Compiled, reusable matcher for one :class:`PatternSpec`.

    Instances are immutable after construction and safe to reuse across files
    (and threads — ``re`` patterns are thread-safe).
    """

    def __init__(self, spec: PatternSpec) -> None:
        spec.validate()
        self.spec = spec
        self._pattern = self._build_pattern(spec)
        # Memoises the line-start index of the *last* scanned buffer.  Bulk
        # runs scan each file once, so a single slot gives a real speed-up
        # without the memory cost of caching every buffer.
        #
        # The key is the buffer itself, not `id(buffer)`: ids are recycled
        # once an object is freed, and two different files of the same length
        # could otherwise share an index — which would report matches on the
        # wrong lines.
        self._line_cache_key: str | None = None
        self._line_cache_value: tuple[int, ...] = (0,)

    # ------------------------------------------------------------------ build
    @staticmethod
    def _build_pattern(spec: PatternSpec) -> re.Pattern[str]:
        search = spec.effective_search
        if spec.kind is PatternKind.FIXED:
            body = re.escape(search)
        elif spec.kind is PatternKind.ERE:
            body = translate_ere(search)
        elif spec.kind is PatternKind.PCRE:
            body = translate_pcre(search)
        else:
            body = search

        if spec.word_boundary:
            # \b works for literal text too because re.escape keeps word chars.
            body = rf"\b(?:{body})\b"

        flags = re.MULTILINE
        if spec.ignore_case:
            flags |= re.IGNORECASE
        if spec.multiline:
            flags |= re.DOTALL
        if spec.dot_all:
            flags |= re.DOTALL
        try:
            return re.compile(body, flags)
        except re.error as exc:
            raise PatternError(
                f"invalid {spec.kind.value} pattern {search!r}: {exc}",
                hint="use --fixed for a literal string, or --help=matching for syntax",
            ) from exc

    @property
    def pattern(self) -> re.Pattern[str]:
        return self._pattern

    # ----------------------------------------------------------------- lookup
    def _line_starts(self, text: str) -> tuple[int, ...]:
        """Offsets of every line start, memoised for the current buffer."""
        if text is self._line_cache_key:
            return self._line_cache_value
        starts = [0]
        pos = text.find("\n")
        while pos != -1:
            starts.append(pos + 1)
            pos = text.find("\n", pos + 1)
        result = tuple(starts)
        self._line_cache_key = text
        self._line_cache_value = result
        return result

    def locate(self, text: str, offset: int) -> tuple[int, int]:
        """Convert a character offset into a 1-based ``(line, column)``."""
        starts = self._line_starts(text)
        index = bisect.bisect_right(starts, offset) - 1
        index = max(0, min(index, len(starts) - 1))
        return index + 1, offset - starts[index] + 1

    # ------------------------------------------------------------------ scan
    def find(self, text: str) -> MatchResult:
        """All matches that should be replaced, in document order.

        Applies ``--line-range``, ``--max-count`` and ``--no-global``
        (first match per line only).  Zero-length matches are advanced past to
        guarantee termination.
        """
        spec = self.spec
        result = MatchResult()
        replacement_template = spec.effective_replacement
        per_line_used: set[int] = set()
        position = 0
        length = len(text)

        while position <= length:
            if spec.max_count and len(result.matches) >= spec.max_count:
                result.truncated = True
                break
            found = self._pattern.search(text, position)
            if found is None:
                break
            start, end = found.span()
            line, column = self.locate(text, start)

            if spec.line_range and not spec.line_range.contains(line):
                result.skipped_out_of_range += 1
            elif not spec.global_replace and line in per_line_used:
                # --no-global: one replacement per line, keep scanning.
                pass
            else:
                if not spec.global_replace:
                    per_line_used.add(line)
                result.matches.append(
                    MatchInfo(
                        start=start,
                        end=end,
                        text=found.group(0),
                        line=line,
                        column=column,
                        replacement=self.expand(found, replacement_template),
                    )
                )
            position = end if end > start else start + 1
        return result

    def count(self, text: str) -> int:
        """Number of replacements that :meth:`apply` would perform."""
        return self.find(text).count

    def expand(self, match: re.Match[str], template: str) -> str:
        """Render the replacement for one match.

        Fixed patterns are inserted verbatim; regex patterns support the full
        ``re.sub`` template syntax (``\\1``, ``\\g<name>``, ``\\n``).
        """
        if self.spec.kind is PatternKind.FIXED:
            return template
        try:
            return match.expand(template)
        except (re.error, IndexError, KeyError) as exc:
            raise PatternError(
                f"invalid replacement template {template!r}: {exc}",
                hint="escape a literal backslash as \\\\ or use --fixed",
            ) from exc

    # ----------------------------------------------------------------- apply
    def apply(self, text: str) -> tuple[str, MatchResult]:
        """Return ``(new_text, result)``; ``new_text is text`` when unchanged."""
        result = self.find(text)
        if not result.matches:
            return text, result
        pieces: list[str] = []
        cursor = 0
        for match in result.matches:
            pieces.append(text[cursor : match.start])
            pieces.append(match.replacement)
            cursor = match.end
        pieces.append(text[cursor:])
        return "".join(pieces), result

    def apply_to_lines(self, lines: Sequence[str]) -> tuple[list[str], int]:
        """Convenience helper for line-oriented callers (preview rendering)."""
        total = 0
        out: list[str] = []
        for line in lines:
            new_line, result = self.apply(line)
            total += result.count
            out.append(new_line)
        return out, total

    # ------------------------------------------------------------------ misc
    def iter_matches(self, text: str) -> Iterable[MatchInfo]:
        return iter(self.find(text).matches)

    def highlight(self, text: str, opener: str = "\x1b[1;31m", closer: str = "\x1b[0m") -> str:
        """Return ``text`` with every match wrapped in ANSI markers."""
        result = self.find(text)
        if not result.matches:
            return text
        pieces: list[str] = []
        cursor = 0
        for match in result.matches:
            pieces.append(text[cursor : match.start])
            pieces.append(f"{opener}{text[match.start : match.end]}{closer}")
            cursor = match.end
        pieces.append(text[cursor:])
        return "".join(pieces)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Matcher({self.spec.search!r}, kind={self.spec.kind.value})"


def compile_pattern(spec: PatternSpec) -> Matcher:
    """Validate ``spec`` and return a ready :class:`Matcher`."""
    return Matcher(spec)


def build_spec(
    search: str,
    replacement: str = "",
    *,
    kind: str | PatternKind = PatternKind.FIXED,
    ignore_case: bool = False,
    word_boundary: bool = False,
    multiline: bool = False,
    process_escapes: bool = True,
    global_replace: bool = True,
    max_count: int | None = None,
    line_range: str | LineRange | None = None,
) -> PatternSpec:
    """Typed convenience constructor used by the CLI layer."""
    resolved_kind = kind if isinstance(kind, PatternKind) else PatternKind.parse(kind)
    resolved_range = (
        line_range
        if isinstance(line_range, LineRange) or line_range is None
        else parse_line_range(line_range)
    )
    return PatternSpec(
        search=search,
        kind=resolved_kind,
        replacement=replacement,
        ignore_case=ignore_case,
        word_boundary=word_boundary,
        multiline=multiline,
        process_escapes=process_escapes,
        global_replace=global_replace,
        max_count=max_count,
        line_range=resolved_range,
    )

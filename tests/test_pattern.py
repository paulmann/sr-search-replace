"""Unit tests for the matching engine (:mod:`sr.pattern`).

These are the tests that would have caught every matching bug in sr 6.x:
literal-vs-regex confusion, occurrences-vs-lines counting, ``-i``/``-w`` being
applied to grep but not to sed, and ``-E`` silently matching nothing.
"""

from __future__ import annotations

import pytest

from sr.errors import PatternError
from sr.pattern import (
    LineRange,
    Matcher,
    PatternKind,
    PatternSpec,
    build_spec,
    compile_pattern,
    parse_line_range,
    translate_ere,
    translate_pcre,
    unescape_literal,
)


def matches(spec: PatternSpec, text: str) -> list[str]:
    return [match.text for match in compile_pattern(spec).find(text).matches]


def apply(spec: PatternSpec, text: str) -> str:
    return compile_pattern(spec).apply(text)[0]


class TestLiteral:
    def test_plain_literal(self):
        spec = build_spec("old", "new")
        assert apply(spec, "old and old again") == "new and new again"

    def test_regex_metacharacters_are_literal(self):
        """The single most important default: `.` is a dot, not "any char"."""
        spec = build_spec("a.c", "X")
        assert matches(spec, "abc a.c aXc") == ["a.c"]
        assert apply(spec, "abc a.c aXc") == "abc X aXc"

    def test_dollar_and_ampersand_in_replacement_are_literal(self):
        """sed's `&` and `\\1` must not leak into a literal replacement."""
        spec = build_spec("price", "&1 \\1 $&")
        assert apply(spec, "price") == "&1 \\1 $&"

    def test_backslash_heavy_replacement(self):
        spec = build_spec("PATH", "C:\\Users\\me\\bin")
        assert apply(spec, "PATH=x") == "C:\\Users\\me\\bin=x"

    def test_count_counts_occurrences_not_lines(self):
        spec = build_spec("a", "b")
        result = compile_pattern(spec).find("aaa\na\n")
        assert result.count == 4

    def test_empty_search_is_rejected(self):
        with pytest.raises(PatternError, match="must not be empty"):
            compile_pattern(build_spec("", "x"))

    def test_newline_in_pattern_requires_multiline(self):
        with pytest.raises(PatternError, match="--multiline"):
            compile_pattern(build_spec("a\nb", "x"))


class TestEscapes:
    def test_tab_and_newline(self):
        assert unescape_literal("a\\tb") == "a\tb"
        assert unescape_literal("a\\nb") == "a\nb"

    def test_hex_escape(self):
        assert unescape_literal("\\x41\\x42") == "AB"

    def test_unknown_escape_keeps_backslash(self):
        assert unescape_literal("C:\\queue") == "C:\\queue"

    def test_trailing_backslash_preserved(self):
        assert unescape_literal("end\\") == "end\\"

    def test_no_escapes_option(self):
        spec = build_spec("a\\tb", "X", process_escapes=False)
        assert matches(spec, "a\tb a\\tb") == ["a\\tb"]

    def test_escape_processing_applies_to_replacement_too(self):
        spec = build_spec("x", "a\\tb")
        assert apply(spec, "x") == "a\tb"


class TestRegexKinds:
    def test_python_regex_with_backreference(self):
        spec = build_spec(r"v(\d+)\.(\d+)", r"v\1.\2.0", kind="regex")
        assert apply(spec, "v1.2") == "v1.2.0"

    def test_named_group(self):
        spec = build_spec(r"(?P<num>\d+)", r"[num=\g<num>]", kind="regex")
        assert apply(spec, "42") == "[num=42]"

    def test_ignore_case_applies_to_matching_and_counting(self):
        spec = build_spec("warning", "notice", ignore_case=True)
        assert apply(spec, "Warning WARNING warning") == "notice notice notice"
        assert compile_pattern(spec).count("Warning WARNING") == 2

    def test_word_boundary(self):
        spec = build_spec("cat", "dog", word_boundary=True)
        assert apply(spec, "cat catalog concat cat") == "dog catalog concat dog"

    def test_word_boundary_with_non_word_edges(self):
        """``\\b`` is a *word* boundary, so a pattern ending in ``+`` has none.

        ``grep -w`` behaves identically; pinning it here keeps the semantics
        from looking like a bug later.  In ``"c++ and c++x"`` the only place
        where both edges are word boundaries is the ``c++`` followed by ``x``.
        """
        spec = build_spec("c++", "cpp", word_boundary=True)
        assert apply(spec, "c++ and c++x") == "c++ and cppx"
        # Anchoring works as expected when the pattern starts and ends with a
        # word character.
        assert apply(build_spec("cat", "dog", word_boundary=True), "cat cats") == "dog cats"

    def test_multiline_allows_dot_to_cross_lines(self):
        spec = build_spec("a.c", "X", kind="regex", multiline=True)
        assert apply(spec, "a\nc") == "X"

    def test_invalid_regex_reports_clearly(self):
        with pytest.raises(PatternError, match="invalid regex"):
            compile_pattern(build_spec("(", "x", kind="regex"))

    def test_invalid_replacement_template_reports_clearly(self):
        spec = build_spec(r"(a)", r"\9", kind="regex")
        with pytest.raises(PatternError, match="replacement template"):
            apply(spec, "a")

    def test_ere_posix_classes(self):
        assert translate_ere("[[:digit:]]+") == "[0-9]+"
        spec = build_spec("[[:alpha:]]{3}", "X", kind="ere")
        assert apply(spec, "abc abcd") == "X Xd"

    def test_ere_word_anchors(self):
        assert translate_ere(r"\<word\>") == r"\bword\b"

    def test_ere_rejects_python_groups(self):
        with pytest.raises(PatternError, match="POSIX ERE"):
            translate_ere("(?=x)")

    def test_ere_unsupported_class(self):
        with pytest.raises(PatternError, match="unsupported POSIX class"):
            translate_ere("[[:nope:]]")

    def test_pcre_named_group_translation(self):
        assert "(?P<name>" in translate_pcre("(?<name>x)")

    def test_pcre_z_anchor(self):
        assert translate_pcre(r"x\z") == r"x\Z"

    def test_pcre_lookbehind_untouched(self):
        assert translate_pcre("(?<=a)b") == "(?<=a)b"

    def test_pattern_kind_parsing(self):
        assert PatternKind.parse("REGEX") is PatternKind.REGEX
        with pytest.raises(PatternError, match="unknown pattern kind"):
            PatternKind.parse("nope")


class TestScoping:
    def test_no_global_replaces_first_per_line(self):
        spec = build_spec("a", "b", global_replace=False)
        assert apply(spec, "a a\na a\n") == "b a\nb a\n"

    def test_max_count_caps_per_file(self):
        spec = build_spec("a", "b", max_count=2)
        assert apply(spec, "aaaa") == "bbaa"
        assert compile_pattern(spec).find("aaaa").count == 2

    def test_max_count_and_no_global_conflict(self):
        with pytest.raises(PatternError, match="contradict"):
            compile_pattern(build_spec("a", "b", global_replace=False, max_count=3))

    def test_max_count_must_be_positive(self):
        with pytest.raises(PatternError, match=">= 1"):
            compile_pattern(build_spec("a", "b", max_count=0))

    def test_line_range_window(self):
        spec = build_spec("old", "new", line_range="2-3")
        text = "old\nold\nold\nold\n"
        assert apply(spec, text) == "old\nnew\nnew\nold\n"

    def test_line_range_counts_out_of_range_separately(self):
        spec = build_spec("old", "new", line_range="2-")
        result = compile_pattern(spec).find("old\nold\n")
        assert result.count == 1
        assert result.skipped_out_of_range == 1

    def test_line_range_single_line(self):
        spec = build_spec("x", "y", line_range="1")
        assert apply(spec, "x\nx\n") == "y\nx\n"

    def test_line_range_open_start(self):
        spec = build_spec("x", "y", line_range="-1")
        assert apply(spec, "x\nx\n") == "y\nx\n"


class TestLineRangeParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("5", LineRange(5, 5)),
            ("10-20", LineRange(10, 20)),
            ("10-", LineRange(10, None)),
            ("-20", LineRange(None, 20)),
            ("", None),
        ],
    )
    def test_valid(self, text, expected):
        assert parse_line_range(text) == expected

    @pytest.mark.parametrize("text", ["a", "20-10", "-", "1-2-3", "x-y"])
    def test_invalid(self, text):
        with pytest.raises(PatternError):
            parse_line_range(text)


class TestCoordinates:
    def test_line_and_column_are_one_based(self):
        spec = build_spec("b", "X")
        found = compile_pattern(spec).find("aa\n  b\n")
        assert [(match.line, match.column) for match in found.matches] == [(2, 3)]

    def test_columns_after_a_multiline_match(self):
        spec = build_spec("a", "X", multiline=True, kind="regex")
        found = compile_pattern(spec).find("a\nb\na")
        assert [match.line for match in found.matches] == [1, 3]

    def test_zero_length_match_terminates(self):
        """A pattern that can match empty must not loop forever."""
        spec = build_spec("x*", "-", kind="regex", max_count=5)
        assert len(compile_pattern(spec).find("ab").matches) <= 5


class TestHighlight:
    def test_highlight_wraps_every_match(self):
        spec = build_spec("a", "")
        matcher = Matcher(spec)
        assert matcher.highlight("bab", "[", "]") == "b[a]b"

    def test_highlight_no_match_returns_input(self):
        matcher = Matcher(build_spec("z", ""))
        assert matcher.highlight("abc", "[", "]") == "abc"


class TestDescribe:
    def test_describe_lists_active_modifiers(self):
        spec = build_spec("a", "b", kind="regex", ignore_case=True, word_boundary=True)
        text = spec.describe()
        assert "regex" in text
        assert "ignore-case" in text
        assert "word-boundary" in text

    def test_effective_search_applies_escapes_only_for_fixed(self):
        fixed = build_spec("a\\tb")
        regex = build_spec("a\\tb", kind="regex")
        assert fixed.effective_search == "a\tb"
        assert regex.effective_search == "a\\tb"

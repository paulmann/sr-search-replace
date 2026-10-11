"""Tests for the output layer (:mod:`sr.console`) and diff rendering."""

from __future__ import annotations

import io

import pytest
from helpers import Captured

from sr.console import (
    COLOR,
    NO_COLOR,
    Console,
    OutputFormat,
    ProgressBar,
    Verbosity,
    detect_color,
    make_console,
    wrap_lines,
)
from sr.diffview import diff_stats, diff_texts, render_diff, unified_diff


class TestVerbosity:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("quiet", Verbosity.QUIET), ("q", Verbosity.QUIET), ("errors", Verbosity.QUIET),
            ("normal", Verbosity.NORMAL), ("info", Verbosity.NORMAL),
            ("verbose", Verbosity.VERBOSE), ("v", Verbosity.VERBOSE),
            ("debug", Verbosity.DEBUG), ("trace", Verbosity.DEBUG),
            ("0", Verbosity.QUIET), ("3", Verbosity.DEBUG), ("-1", Verbosity.QUIET),
            ("nonsense", Verbosity.NORMAL),
        ],
    )
    def test_from_option(self, text, expected):
        assert Verbosity.from_option(text) is expected

    def test_none_is_normal(self):
        assert Verbosity.from_option(None) is Verbosity.NORMAL

    def test_ordering(self):
        assert Verbosity.QUIET < Verbosity.NORMAL < Verbosity.VERBOSE < Verbosity.DEBUG


class TestColorDetection:
    def test_explicit_true_and_false(self):
        assert detect_color(True, io.StringIO()) is True
        assert detect_color(False, io.StringIO()) is False

    def test_string_values(self):
        stream = io.StringIO()
        assert detect_color("always", stream) is True
        assert detect_color("never", stream) is False
        assert detect_color("garbage", stream) is False

    def test_no_color_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert detect_color(None, io.StringIO()) is False

    def test_clicolor_force(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        assert detect_color(None, io.StringIO()) is True

    def test_dumb_terminal(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert detect_color(None, io.StringIO()) is False

    def test_non_tty_defaults_to_no_color(self, monkeypatch: pytest.MonkeyPatch):
        for name in ("NO_COLOR", "CLICOLOR_FORCE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("TERM", raising=False)
        assert detect_color(None, io.StringIO()) is False


class TestConsole:
    def test_json_mode_redirects_chatter(self):
        """Prose must never pollute stdout, or `sr … --json | jq` would break."""
        view = Captured(fmt=OutputFormat.JSON)
        view.console.info("this must not reach stdout")
        view.console.success("nor this")
        view.console.header("nor this")
        assert view.stdout == ""

    def test_json_document_goes_to_stdout(self):
        view = Captured(fmt=OutputFormat.JSON)
        view.console.emit_json({"a": 1})
        assert view.json() == {"a": 1}

    def test_diagnostics_still_reach_stderr_in_json_mode(self):
        view = Captured(fmt=OutputFormat.JSON)
        view.console.error("boom")
        view.console.warn("careful")
        assert view.stdout == ""
        assert "boom" in view.stderr and "careful" in view.stderr

    def test_errors_always_reach_stderr(self):
        view = Captured()
        view.console.error("boom", hint="try this")
        assert "boom" in view.stderr
        assert "try this" in view.stderr
        assert view.stdout == ""

    def test_quiet_suppresses_info(self):
        view = Captured(verbosity=Verbosity.QUIET)
        view.console.info("noise")
        view.console.error("kept")
        assert view.stdout == ""
        assert "kept" in view.stderr

    def test_verbose_requires_the_level(self):
        normal = Captured(verbosity=Verbosity.NORMAL)
        normal.console.verbose("hidden")
        assert normal.stderr == ""
        loud = Captured(verbosity=Verbosity.VERBOSE)
        loud.console.verbose("shown")
        assert "shown" in loud.stderr

    def test_paint_is_a_noop_without_color(self):
        view = Captured(color=False)
        assert view.console.paint("text", "red", "bold") == "text"

    def test_paint_wraps_with_color(self):
        console = Console(color=True, out=io.StringIO(), err=io.StringIO())
        painted = console.paint("x", "red")
        assert painted == f"{COLOR.red}x{COLOR.reset}"

    def test_no_color_palette_is_empty(self):
        assert all(value == "" for value in NO_COLOR.as_dict().values())

    def test_porcelain_records(self):
        view = Captured(fmt=OutputFormat.PORCELAIN)
        view.console.record("a.txt", 3, 7, "hello")
        assert view.stdout.strip() == "a.txt:3:7:match:hello"

    def test_porcelain_escapes_newlines(self):
        view = Captured(fmt=OutputFormat.PORCELAIN)
        view.console.record("a.txt", 1, 1, "line\nbreak")
        assert "\\n" in view.stdout

    def test_kv_alignment(self):
        view = Captured()
        view.console.kv("Key", "value")
        assert view.stdout.startswith("Key")
        assert "value" in view.stdout

    def test_header_and_rule_are_suppressed_in_json(self):
        view = Captured(fmt=OutputFormat.JSON)
        view.console.header("Title")
        view.console.rule()
        assert view.stdout == ""

    def test_make_console_disables_color_for_json(self):
        console = make_console(color="always", fmt=OutputFormat.JSON, out=io.StringIO())
        assert console.color is False


class TestProgressBar:
    def test_disabled_without_a_tty(self):
        view = Captured()
        bar = ProgressBar(view.console, 10)
        assert bar.enabled is False
        bar.update()
        bar.close()
        assert view.stdout == ""

    def test_explicit_enable_renders(self):
        view = Captured()
        bar = ProgressBar(view.console, 4, enabled=True)
        for _ in range(4):
            bar.update()
            bar._last_render = 0.0  # force every frame
        bar.close()
        assert "4/4" in view.stderr

    def test_context_manager_closes(self):
        view = Captured()
        with ProgressBar(view.console, 1, enabled=True) as bar:
            bar.update()
        assert bar.enabled is False


class TestWrap:
    def test_wraps_long_lines(self):
        text = "word " * 40
        lines = list(wrap_lines(text.strip(), 40))
        assert all(len(line) <= 40 for line in lines)

    def test_keeps_empty_lines(self):
        assert list(wrap_lines("a\n\nb", 80)) == ["a", "", "b"]


class TestDiff:
    def test_unified_diff_shape(self):
        lines = unified_diff(["a", "b"], ["a", "c"], from_label="a/f", to_label="b/f")
        assert lines[0] == "--- a/f"
        assert lines[1] == "+++ b/f"
        assert "-b" in lines and "+c" in lines

    def test_context_zero(self):
        lines = diff_texts("1\n2\n3\n4\n5\n", "1\n2\nX\n4\n5\n", context=0)
        assert not any(line.startswith(" 3") or line == " 3" for line in lines)

    def test_stats(self):
        stats = diff_stats(diff_texts("a\nb\n", "a\nc\nd\n"))
        assert stats.added == 2 and stats.removed == 1 and stats.changed == 3

    def test_render_without_color_is_identity(self):
        lines = diff_texts("a\n", "b\n")
        assert render_diff(lines, None) == "\n".join(lines)

    def test_render_with_color(self):
        console = Console(color=True, out=io.StringIO(), err=io.StringIO())
        text = render_diff(diff_texts("a\n", "b\n"), console)
        assert COLOR.red in text and COLOR.green in text

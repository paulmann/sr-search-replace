"""End-to-end CLI tests.

These are the tests that protect the user-visible contract: the argument
grammar, the exit codes, the output formats and the safety guards.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cli_helpers import sr, sr_json, tree, write

from sr.exit_codes import ExitCode


@pytest.fixture
def work(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    write(root, "a.txt", "hello world\nhello again\n")
    write(root, "b.txt", "nothing to see\n")
    write(root, "src/c.py", "hello = 1\n")
    write(root, "src/deep/d.py", "hello = 2\n")
    return root


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------


class TestGrammar:
    def test_bare_three_argument_form(self, work: Path):
        result = sr("*.txt", "hello", "bye", "--allow-root", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.txt").read_text() == "bye world\nbye again\n"

    def test_bare_two_argument_form_defaults_to_every_file(self, work: Path):
        result = sr("hello", "bye", "--allow-root", cwd=work)
        assert result.code == 0, result.err
        assert "bye" in (work / "src" / "c.py").read_text()

    def test_flags_may_precede_the_bare_form(self, work: Path):
        result = sr("-i", "*.txt", "HELLO", "bye", "--allow-root", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.txt").read_text().startswith("bye world")

    def test_leading_global_flag_does_not_swallow_a_positional(self, work: Path):
        """Regression: `--no-color` shares a schema entry with `--color`.

        Asking the schema "does this take a value?" answered *yes* for the alias,
        so `sr --no-color '*.py' a b` consumed the glob as the colour value and
        the run failed with a usage error.
        """
        result = sr("--no-color", "*.txt", "hello", "bye", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.txt").read_text().startswith("bye world")

    def test_leading_global_flag_survives_the_subparser(self, work: Path):
        """argparse applies subparser defaults over parent values; globals must win."""
        result = sr("--json", "find", "hello", cwd=work)
        assert result.code == 0, result.err
        document = result.json()
        assert document["totals"]["matches"] == 4

    def test_global_flag_after_the_subcommand(self, work: Path):
        result = sr("find", "hello", "--json", cwd=work)
        assert result.json()["totals"]["matches"] == 4

    def test_command_flag_before_the_bare_form(self, work: Path):
        result = sr("--allow-root", "*.txt", "hello", "bye", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.txt").read_text().startswith("bye world")

    def test_double_dash_protects_a_subcommand_name(self, work: Path):
        """`sr -- help a b` replaces the text "help"; it does not run `sr help`."""
        write(work, "help", "a\n")
        result = sr("--", "help", "a", "b", cwd=work)
        assert result.code == 0, result.err
        assert (work / "help").read_text() == "b\n"

    def test_no_color_disables_ansi(self, work: Path):
        result = sr("find", "hello", "--no-color", cwd=work)
        assert result.code == 0
        assert "\x1b[" not in result.out
        assert "a.txt:1:1:" in result.out

    def test_later_color_flag_wins_over_no_color(self, work: Path):
        """`--color` and `--no-color` share one destination: last one wins."""
        result = sr("find", "hello", "--no-color", "--color", "always", cwd=work)
        assert result.code == 0
        assert "\x1b[" in result.out

    def test_color_never_overrides_no_color_when_last(self, work: Path):
        result = sr("find", "hello", "--color", "always", "--no-color", cwd=work)
        assert result.code == 0
        assert "\x1b[" not in result.out

    def test_explicit_replace_subcommand(self, work: Path):
        result = sr("replace", "hello", "bye", "-p", "*.txt", "--allow-root", cwd=work)
        assert result.code == 0, result.err
        assert (work / "b.txt").read_text() == "nothing to see\n"

    def test_double_dash_ends_options(self, work: Path):
        """Everything after ``--`` is positional — including a dash-y filename."""
        target = write(work, "-weird-.txt", "hello\n")
        other = write(work, "other.txt", "hello\n")
        result = sr("replace", "--allow-root", "--", "-weird-.txt", "hello", "bye", cwd=work)
        assert result.code == 0, result.err
        assert target.read_text() == "bye\n"
        assert other.read_text() == "hello\n", "only the named file may change"

    def test_alias_r_works(self, work: Path):
        result = sr("r", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        assert result.code == 0, result.err

    def test_alias_undo_maps_to_rollback(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("undo", "--yes", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.txt").read_text().startswith("hello world")

    def test_missing_arguments_is_a_usage_error(self, work: Path):
        result = sr("replace", "only-one", cwd=work)
        assert result.code == int(ExitCode.USAGE)
        assert "REPLACE" in result.err

    def test_no_arguments_shows_help(self, work: Path):
        result = sr(cwd=work)
        assert result.code == int(ExitCode.USAGE)
        assert "QUICK START" in result.out

    def test_unknown_command_is_reported(self, work: Path):
        # An unknown first token is treated as a bare replace, so the error
        # comes from the argument count, not from "unknown command".
        result = sr("definitely-not-a-command", cwd=work)
        assert result.code == int(ExitCode.USAGE)

    def test_unknown_option_is_reported(self, work: Path):
        result = sr("replace", "a", "b", "--no-such-flag", cwd=work)
        assert result.code == int(ExitCode.USAGE)
        assert "no-such-flag" in result.err

    def test_help_flag(self, work: Path):
        result = sr("--help", cwd=work)
        assert result.code == 0
        assert "COMMANDS" in result.out

    def test_subcommand_help(self, work: Path):
        result = sr("replace", "--help", cwd=work)
        assert result.code == 0
        assert "--ignore-case" in result.out

    def test_version_flag(self, work: Path):
        result = sr("--version", cwd=work)
        assert result.code == 0
        assert "sr 7." in result.out
        assert "License" in result.out


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_zero_on_change(self, work: Path):
        assert sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work).code == 0

    def test_three_on_no_match(self, work: Path):
        result = sr("replace", "absent", "x", "-p", "*.txt", cwd=work)
        assert result.code == int(ExitCode.NO_MATCH)

    def test_three_on_no_match_for_find(self, work: Path):
        assert sr("find", "absent", cwd=work).code == int(ExitCode.NO_MATCH)

    def test_zero_for_find_with_matches(self, work: Path):
        assert sr("find", "hello", cwd=work).code == 0

    def test_zero_for_dry_run_with_matches(self, work: Path):
        assert sr("replace", "hello", "x", "--dry-run", cwd=work).code == 0

    def test_two_for_a_bad_target(self, tmp_path: Path):
        result = sr("find", "x", "-C", str(tmp_path / "missing"), cwd=tmp_path)
        assert result.code == int(ExitCode.USAGE)

    def test_two_for_a_bad_size(self, work: Path):
        assert sr("find", "x", "--max-size", "not-a-size", cwd=work).code == int(ExitCode.USAGE)

    def test_two_for_a_bad_regex(self, work: Path):
        assert sr("find", "(", "--regex", cwd=work).code == int(ExitCode.USAGE)

    def test_two_for_conflicting_pattern_kinds(self, work: Path):
        result = sr("find", "x", "--regex", "--ere", cwd=work)
        assert result.code == int(ExitCode.USAGE)
        assert "conflicting" in result.err


# ---------------------------------------------------------------------------
# Search and replace behaviour through the CLI
# ---------------------------------------------------------------------------


class TestReplace:
    def test_in_place_with_session(self, work: Path):
        result = sr("replace", "hello", "bye", "-p", "*.py", "--allow-root", cwd=work)
        assert result.code == 0
        assert (work / "src" / "c.py").read_text() == "bye = 1\n"
        assert (work / ".sr" / "sessions").is_dir()
        assert "Session" in result.out

    def test_recursive_by_default(self, work: Path):
        sr("replace", "hello", "bye", "-p", "*.py", "--allow-root", cwd=work)
        assert (work / "src" / "deep" / "d.py").read_text() == "bye = 2\n"

    def test_no_recursive(self, work: Path):
        sr("replace", "hello", "bye", "-p", "*.py", "--no-recursive", "--allow-root", cwd=work)
        assert (work / "src" / "deep" / "d.py").read_text() == "hello = 2\n"

    def test_ignore_case(self, work: Path):
        write(work, "case.txt", "Hello HELLO hello\n")
        sr("replace", "hello", "bye", "-i", "-p", "case.txt", "--allow-root", cwd=work)
        assert (work / "case.txt").read_text() == "bye bye bye\n"

    def test_regex_mode(self, work: Path):
        write(work, "re.txt", "v1.2\n")
        sr("replace", r"v(\d+)\.(\d+)", r"v\1.\2.0", "--regex", "-p", "re.txt", "--allow-root", cwd=work)
        assert (work / "re.txt").read_text() == "v1.2.0\n"

    def test_literal_is_the_default(self, work: Path):
        write(work, "lit.txt", "a.c and abc\n")
        sr("replace", "a.c", "X", "-p", "lit.txt", "--allow-root", cwd=work)
        assert (work / "lit.txt").read_text() == "X and abc\n"

    def test_exclude(self, work: Path):
        sr("replace", "hello", "bye", "--exclude", "b.txt", "--allow-root", cwd=work)
        assert (work / "b.txt").read_text() == "nothing to see\n"
        assert "bye" in (work / "a.txt").read_text()

    def test_max_depth(self, work: Path):
        sr("replace", "hello", "bye", "--max-depth", "2", "-p", "*.py", "--allow-root", cwd=work)
        assert (work / "src" / "c.py").read_text() == "bye = 1\n"
        assert (work / "src" / "deep" / "d.py").read_text() == "hello = 2\n"

    def test_dry_run_leaves_no_trace(self, work: Path):
        before = tree(work)
        result = sr("replace", "hello", "bye", "--dry-run", cwd=work)
        assert result.code == 0
        assert tree(work) == before
        assert "dry run" in result.out.lower()

    def test_diff_shows_a_unified_diff(self, work: Path):
        result = sr("diff", "hello", "bye", "-p", "a.txt", cwd=work)
        assert "-hello world" in result.out
        assert "+bye world" in result.out
        assert (work / "a.txt").read_text().startswith("hello")

    def test_write_to_dir(self, work: Path):
        out = work.parent / "out"
        sr("replace", "hello", "bye", "-p", "*.py", "--write=dir", "-o", str(out), cwd=work)
        assert (out / "src" / "c.py").read_text() == "bye = 1\n"
        assert (work / "src" / "c.py").read_text() == "hello = 1\n"

    def test_write_to_stdout(self, work: Path):
        result = sr("replace", "hello", "bye", "-p", "a.txt", "--write=stdout", cwd=work)
        assert "bye world" in result.out
        assert (work / "a.txt").read_text().startswith("hello")

    def test_no_backup_skips_the_session(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--no-backup", "--allow-root", cwd=work)
        assert not (work / ".sr").exists()

    def test_files_from_stdin_list(self, work: Path):
        write(work, "list.txt", "a.txt\nb.txt\n")
        sr("replace", "hello", "bye", "--files-from", "list.txt", "--allow-root", cwd=work)
        assert (work / "a.txt").read_text().startswith("bye")
        assert (work / "src" / "c.py").read_text() == "hello = 1\n"

    def test_explicit_file(self, work: Path):
        sr("replace", "hello", "bye", "--file", "a.txt", "--allow-root", cwd=work)
        assert (work / "a.txt").read_text().startswith("bye")
        assert (work / "src" / "c.py").read_text() == "hello = 1\n"


# ---------------------------------------------------------------------------
# Find
# ---------------------------------------------------------------------------


class TestFind:
    def test_default_output(self, work: Path):
        result = sr("find", "hello", cwd=work)
        assert "a.txt:1:1:" in result.out
        assert "4 matches in 3 files" in result.out

    def test_count(self, work: Path):
        result = sr("find", "hello", "--count", cwd=work)
        assert "a.txt:2" in result.out
        assert "total:4" in result.out

    def test_files_with_matches(self, work: Path):
        result = sr("find", "hello", "-l", cwd=work)
        assert "a.txt" in result.out
        assert ":" not in result.out.splitlines()[0]

    def test_porcelain(self, work: Path):
        result = sr("find", "hello", "--porcelain", cwd=work)
        first = result.stdout_lines[0]
        assert first.count(":") >= 3

    def test_json(self, work: Path):
        document, run = sr_json("find", "hello", cwd=work)
        assert document["totals"]["matches"] == 4
        assert document["files"]
        assert run.code == 0

    def test_context(self, work: Path):
        result = sr("find", "hello", "-A", "1", "-B", "1", cwd=work)
        assert "--" in result.out

    def test_find_never_writes(self, work: Path):
        before = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
        sr("find", "hello", cwd=work)
        after = {path: path.read_bytes() for path in work.rglob("*") if path.is_file()}
        assert before == after


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_root_user_is_refused(self, work: Path, monkeypatch: pytest.MonkeyPatch):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            pytest.skip("only meaningful when running as root")
        result = sr("replace", "hello", "bye", "-p", "a.txt", cwd=work)
        assert result.code == int(ExitCode.USAGE)
        assert "root" in result.err
        assert (work / "a.txt").read_text().startswith("hello")

    def test_allow_root_overrides(self, work: Path):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            pytest.skip("only meaningful when running as root")
        result = sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        assert result.code == 0

    def test_dry_run_is_allowed_for_root(self, work: Path):
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            pytest.skip("only meaningful when running as root")
        assert sr("replace", "hello", "bye", "--dry-run", cwd=work).code == 0

    def test_confirmation_above_threshold(self, work: Path, monkeypatch: pytest.MonkeyPatch):
        # stdin is not a TTY here, so the prompt must refuse rather than hang.
        result = sr("replace", "hello", "bye", "--confirm-threshold", "1", cwd=work)
        assert result.code == int(ExitCode.ERROR)
        assert "not a terminal" in result.err or "aborted" in result.out + result.err
        assert (work / "a.txt").read_text().startswith("hello")

    def test_yes_overrides_the_prompt(self, work: Path):
        result = sr("replace", "hello", "bye", "--confirm-threshold", "1", "--yes",
                    "--allow-root", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").read_text().startswith("bye")

    def test_binary_files_are_skipped_and_reported(self, work: Path):
        write(work, "img.bin", b"\x00\x01hello\x02")
        result = sr("find", "hello", "-v", cwd=work)
        assert "img.bin" not in result.out or "binary" in result.out


# ---------------------------------------------------------------------------
# Sessions and rollback through the CLI
# ---------------------------------------------------------------------------


class TestSessionsCLI:
    def test_list_empty(self, work: Path):
        result = sr("sessions", cwd=work)
        assert result.code == 0
        assert "no sessions" in result.out

    def test_list_after_a_run(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("sessions", cwd=work)
        assert "1 files" in result.out or "files" in result.out
        assert "hello" in result.out

    def test_show(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("sessions", "show", "latest", "--files", cwd=work)
        assert result.code == 0
        assert "a.txt" in result.out

    def test_show_json(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        document, _run = sr_json("sessions", "show", "latest", cwd=work)
        assert document["totals"]["files"] == 1
        assert document["files"][0]["rel_path"] == "a.txt"

    def test_size(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("sessions", "size", cwd=work)
        assert result.code == 0
        assert "Disk usage" in result.out

    def test_prune(self, work: Path):
        for index in range(3):
            write(work, "a.txt", f"marker{index}\n")
            sr("replace", f"marker{index}", "done", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("sessions", "prune", "--keep", "1", cwd=work)
        assert result.code == 0
        document, _run = sr_json("sessions", cwd=work)
        assert document["count"] == 1

    def test_delete(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("sessions", "delete", "latest", "--yes", cwd=work)
        assert result.code == 0
        assert "no sessions" in sr("sessions", cwd=work).out


class TestRollbackCLI:
    def test_rollback_restores(self, work: Path):
        sr("replace", "hello", "bye", "-p", "*.txt", "--allow-root", cwd=work)
        assert (work / "a.txt").read_text().startswith("bye")
        result = sr("rollback", "--yes", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.txt").read_text().startswith("hello")

    def test_rollback_by_suffix(self, work: Path):
        run = sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", "--json", cwd=work)
        session_id = run.json()["session"]["id"]
        suffix = session_id.rsplit("-", 1)[-1]
        result = sr("rollback", suffix, "--yes", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").read_text().startswith("hello")

    def test_rollback_dry_run(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("rollback", "--dry-run", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").read_text().startswith("bye")

    def test_rollback_single_file(self, work: Path):
        sr("replace", "hello", "bye", "-p", "*.txt", "--allow-root", cwd=work)
        result = sr("rollback", "--file", "a.txt", "--yes", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").read_text().startswith("hello")

    def test_rollback_creates_a_safety_snapshot(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("rollback", "--yes", cwd=work)
        assert "Undo this rollback" in result.out
        document, _run = sr_json("sessions", cwd=work)
        kinds = [item["kind"] for item in document["sessions"]]
        assert "rollback" in kinds

    def test_undo_the_rollback(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        sr("rollback", "--yes", cwd=work)
        assert (work / "a.txt").read_text().startswith("hello")
        document, _run = sr_json("sessions", cwd=work)
        snapshot = next(item["id"] for item in document["sessions"] if item["kind"] == "rollback")
        result = sr("rollback", snapshot, "--yes", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").read_text().startswith("bye")

    def test_rollback_without_sessions(self, work: Path):
        result = sr("rollback", cwd=work)
        assert result.code == int(ExitCode.ERROR)
        assert "no sessions" in result.err

    def test_drift_is_reported(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        write(work, "a.txt", "edited by hand\n")
        result = sr("rollback", "--yes", cwd=work)
        assert "changed after the session" in result.out + result.err
        assert (work / "a.txt").read_text() == "edited by hand\n"

    def test_force_overrides_drift(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        write(work, "a.txt", "edited by hand\n")
        result = sr("rollback", "--force", "--yes", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").read_text().startswith("hello")

    def test_diff_from_a_session(self, work: Path):
        sr("replace", "hello", "bye", "-p", "a.txt", "--allow-root", cwd=work)
        result = sr("diff", "--session", "latest", cwd=work)
        assert result.code == 0
        assert "-hello world" in result.out


class TestRenameCLI:
    def test_rename_files(self, work: Path):
        result = sr("rename", ".txt", ".md", "-p", "*.txt", "--allow-root", "--yes", cwd=work)
        assert result.code == 0, result.err
        assert (work / "a.md").is_file() and not (work / "a.txt").exists()

    def test_rename_dry_run(self, work: Path):
        result = sr("rename", ".txt", ".md", "-p", "*.txt", "--dry-run", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").is_file()
        assert "would rename" in result.out

    def test_rename_is_reversible(self, work: Path):
        sr("rename", ".txt", ".md", "-p", "*.txt", "--allow-root", "--yes", cwd=work)
        result = sr("rollback", "--yes", cwd=work)
        assert result.code == 0
        assert (work / "a.txt").is_file()

    def test_replace_with_rename(self, work: Path):
        write(work, "old_name.txt", "old content\n")
        sr("replace", "old", "new", "-p", "old_name.txt", "--rename", "--allow-root", "--yes", cwd=work)
        assert (work / "new_name.txt").read_text() == "new content\n"

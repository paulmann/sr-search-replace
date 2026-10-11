"""End-to-end tests of the replace engine (:mod:`sr.replace`)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from helpers import Captured, make_options, run

from sr.exit_codes import ExitCode
from sr.replace import build_matcher, run_replace, scan_only
from sr.session import store_for


class TestLiteralReplace:
    def test_replaces_in_every_matching_file(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        assert summary.replacements == 3
        assert summary.changed == 2
        assert (project / "src" / "app.py").read_text() == 'NAME = "new"\nprint(NAME)\n'

    def test_counts_occurrences_not_lines(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("x x x\nx\n")
        summary = run(tmp_path, "x", "y", patterns=["*.txt"])
        assert summary.replacements == 4
        assert target.read_text() == "y y y\ny\n"

    def test_untouched_file_keeps_its_mtime(self, project: Path):
        before = (project / "README.md").stat().st_mtime_ns
        run(project, "old", "new", patterns=["notes.txt"])
        assert (project / "README.md").stat().st_mtime_ns == before

    def test_permissions_are_preserved(self, project: Path):
        target = project / "notes.txt"
        target.chmod(0o640)
        run(project, "old", "new", patterns=["notes.txt"])
        assert target.stat().st_mode & 0o777 == 0o640

    def test_node_modules_and_git_are_never_touched(self, project: Path):
        run(project, "old", "new", patterns=["*"])
        assert (project / "node_modules" / "pkg" / "index.js").read_text() == "old\n"
        assert (project / ".git" / "config").read_text() == "old\n"

    def test_no_match_exits_3(self, project: Path):
        summary = run(project, "not-present-anywhere", "new", patterns=["*.py"])
        assert summary.replacements == 0
        assert summary.exit_hint == int(ExitCode.NO_MATCH)


class TestDryRun:
    def test_writes_nothing(self, project: Path):
        before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
        summary = run(project, "old", "new", patterns=["*.py"], apply_changes=False)
        after = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
        assert before == after
        assert summary.replacements == 3
        assert summary.dry_run is True

    def test_creates_no_session(self, project: Path):
        run(project, "old", "new", patterns=["*.py"], apply_changes=False)
        assert store_for(project, None).list() == []

    def test_does_not_change_mtimes(self, project: Path):
        before = (project / "src" / "app.py").stat().st_mtime_ns
        run(project, "old", "new", patterns=["*.py"], apply_changes=False)
        assert (project / "src" / "app.py").stat().st_mtime_ns == before

    def test_scan_only_is_a_pure_read(self, project: Path):
        options = make_options(project, "old", "new", patterns=["*.py"])
        summary = scan_only(options)
        assert summary.dry_run is True
        assert summary.session is None


class TestRegexModes:
    def test_regex_with_backreference(self, tmp_path: Path):
        target = tmp_path / "a.py"
        target.write_text('version = "1.2"\n')
        run(
            tmp_path,
            r'version = "(\d+)\.(\d+)"',
            r'version = "\1.\2.0"',
            patterns=["*.py"],
            spec_kwargs={"kind": "regex"},
        )
        assert target.read_text() == 'version = "1.2.0"\n'

    def test_ignore_case(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("Old OLD old\n")
        run(tmp_path, "old", "new", patterns=["*.txt"], spec_kwargs={"ignore_case": True})
        assert target.read_text() == "new new new\n"

    def test_word_boundary(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("cat catalog cat\n")
        run(tmp_path, "cat", "dog", patterns=["*.txt"], spec_kwargs={"word_boundary": True})
        assert target.read_text() == "dog catalog dog\n"

    def test_multiline(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("a\nb\n")
        run(tmp_path, "a\nb", "X", patterns=["*.txt"], spec_kwargs={"multiline": True})
        assert target.read_text() == "X\n"

    def test_max_count(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("aaaa\n")
        summary = run(tmp_path, "a", "b", patterns=["*.txt"], spec_kwargs={"max_count": 2})
        assert target.read_text() == "bbaa\n"
        assert summary.replacements == 2

    def test_no_global(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("a a\na a\n")
        run(tmp_path, "a", "b", patterns=["*.txt"], spec_kwargs={"global_replace": False})
        assert target.read_text() == "b a\nb a\n"

    def test_line_range(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("x\nx\nx\n")
        run(tmp_path, "x", "y", patterns=["*.txt"], spec_kwargs={"line_range": "2-2"})
        assert target.read_text() == "x\ny\nx\n"


class TestWriteModes:
    def test_write_to_dir_mirrors_the_tree(self, project: Path):
        out = project.parent / "out"
        options = make_options(project, "old", "new", patterns=["*.py"])
        options.write_mode = "dir"
        options.output_dir = out
        summary = run_replace(options)
        assert (out / "src" / "app.py").is_file()
        assert (out / "src" / "app.py").read_text().startswith('NAME = "new"')
        assert (project / "src" / "app.py").read_text().startswith('NAME = "old"')
        assert summary.session is None, "a copy run must not create a session"

    def test_write_to_dir_with_suffix(self, project: Path):
        out = project.parent / "out"
        options = make_options(project, "old", "new", patterns=["notes.txt"])
        options.write_mode = "dir"
        options.output_dir = out
        options.suffix = ".new"
        run_replace(options)
        assert (out / "notes.txt.new").is_file()

    def test_write_to_stdout_leaves_files_alone(self, project: Path):
        view = Captured()
        options = make_options(project, "old", "new", patterns=["notes.txt"], console=view.console)
        options.write_mode = "stdout"
        run_replace(options)
        assert "new" in view.stdout
        assert "==> notes.txt <==" in view.stdout
        assert (project / "notes.txt").read_text() == "keep old\n"

    def test_write_dir_requires_output_dir(self, project: Path):
        options = make_options(project, "old", "new", patterns=["*.py"])
        options.write_mode = "dir"
        options.output_dir = None
        from sr.errors import UsageError

        with pytest.raises(UsageError, match="--output-dir"):
            run_replace(options)

    def test_output_dir_requires_write_dir(self, project: Path):
        options = make_options(project, "old", "new", patterns=["*.py"])
        options.output_dir = project.parent / "out"
        from sr.errors import UsageError

        with pytest.raises(UsageError, match="--write=dir"):
            run_replace(options)


class TestEncodingAndEndings:
    def test_crlf_preserved(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_bytes(b"old\r\nold\r\n")
        run(tmp_path, "old", "new", patterns=["*.txt"])
        assert target.read_bytes() == b"new\r\nnew\r\n"

    def test_line_endings_normalised_on_request(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_bytes(b"old\r\nold\r\n")
        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.line_endings = "lf"
        run_replace(options)
        assert target.read_bytes() == b"new\nnew\n"

    def test_cp1251_with_explicit_encoding(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_bytes("привет old".encode("cp1251"))
        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.encoding = "cp1251"
        run_replace(options)
        assert target.read_bytes() == "привет new".encode("cp1251")

    def test_undecodable_file_is_skipped_not_corrupted(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        payload = b"old \xff\xfe garbage"
        target.write_bytes(payload)
        summary = run(tmp_path, "old", "new", patterns=["*.txt"])
        assert target.read_bytes() == payload
        assert summary.skipped == 1
        assert summary.warnings

    def test_utf8_bom_preserved(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_bytes(b"\xef\xbb\xbfold\n")
        run(tmp_path, "old", "new", patterns=["*.txt"])
        assert target.read_bytes() == b"\xef\xbb\xbfnew\n"


class TestBinarySafety:
    def test_binary_skipped_by_default(self, tmp_path: Path):
        target = tmp_path / "a.bin"
        payload = b"\x00\x01old\x02"
        target.write_bytes(payload)
        options = make_options(tmp_path, "old", "new", patterns=["*.bin"])
        options.discovery_options.text_only = False
        summary = run_replace(options)
        assert target.read_bytes() == payload
        assert summary.replacements == 0

    def test_binary_editable_with_explicit_encoding(self, tmp_path: Path):
        target = tmp_path / "a.bin"
        target.write_bytes(b"\x00\x01old\x02")
        options = make_options(tmp_path, "old", "new", patterns=["*.bin"])
        options.discovery_options.text_only = False
        options.encoding = "latin-1"
        run_replace(options)
        assert target.read_bytes() == b"\x00\x01new\x02"


class TestSessions:
    def test_session_created_with_records(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        assert summary.session is not None
        assert summary.session.files == 2
        assert summary.session.complete is True
        assert {record.rel_path for record in summary.session.records} == {"src/app.py", "src/util.py"}

    def test_no_backup_flag_skips_the_session(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"], backup=False)
        assert summary.session is None
        assert not (project / ".sr").exists()

    def test_session_stores_original_bytes(self, project: Path):
        summary = run(project, "old", "new", patterns=["notes.txt"])
        info = summary.session
        record = info.records[0]
        blob = info.directory / "files" / record.blob[:2] / f"{record.blob}.dat"
        assert blob.read_bytes() == b"keep old\n"

    def test_session_stores_a_diff(self, project: Path):
        summary = run(project, "old", "new", patterns=["notes.txt"])
        record = summary.session.records[0]
        assert record.diff_path
        text = (summary.session.directory / record.diff_path).read_text()
        assert "-keep old" in text and "+keep new" in text

    def test_journal_is_append_only_json(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        journal = summary.session.directory / "journal.jsonl"
        entries = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        kinds = [entry.get("type") for entry in entries]
        assert kinds[0] == "meta"
        assert kinds[-1] == "finish"
        assert kinds.count("op") == 2

    def test_pruning_keeps_the_newest(self, project: Path):
        target = project / "notes.txt"
        for index in range(4):
            target.write_text(f"marker{index}\n")
            run_replace(make_options(project, f"marker{index}", "done", patterns=["notes.txt"], keep=2))
        sessions = store_for(project, None).list()
        assert len(sessions) == 2, f"expected exactly 2 sessions, got {len(sessions)}"

    def test_keep_zero_never_prunes(self, project: Path):
        target = project / "notes.txt"
        for index in range(3):
            target.write_text(f"marker{index}\n")
            run_replace(make_options(project, f"marker{index}", "done", patterns=["notes.txt"], keep=0))
        assert len(store_for(project, None).list()) == 3

    def test_backup_dir_outside_the_tree(self, project: Path):
        outside = project.parent / "backups"
        options = make_options(project, "old", "new", patterns=["notes.txt"])
        options.backup_dir = outside
        run_replace(options)
        assert outside.is_dir()
        assert not (project / ".sr").exists()
        assert list((outside / "sessions").iterdir())


class TestRename:
    def test_rename_only_changes_names(self, tmp_path: Path):
        (tmp_path / "a-old.txt").write_text("content\n")
        options = make_options(tmp_path, "-old", "-new", patterns=["*.txt"])
        options.rename_only = True
        summary = run_replace(options)
        assert not (tmp_path / "a-old.txt").exists()
        assert (tmp_path / "a-new.txt").read_text() == "content\n"
        assert summary.renames[0].executed is True

    def test_rename_dry_run_touches_nothing(self, tmp_path: Path):
        (tmp_path / "a-old.txt").write_text("content\n")
        options = make_options(tmp_path, "-old", "-new", patterns=["*.txt"], apply_changes=False)
        options.rename_only = True
        summary = run_replace(options)
        assert (tmp_path / "a-old.txt").exists()
        assert not (tmp_path / "a-new.txt").exists()
        assert summary.renames and summary.renames[0].executed is False

    def test_rename_collision_is_refused(self, tmp_path: Path):
        """Two sources mapping onto one target must both be skipped."""
        (tmp_path / "a-x.txt").write_text("one\n")
        (tmp_path / "a_x.txt").write_text("two\n")
        options = make_options(
            tmp_path,
            "[-_]x",
            "",
            patterns=["a*.txt"],
            spec_kwargs={"kind": "regex"},
        )
        options.rename_only = True
        summary = run_replace(options)
        assert len(summary.renames) == 2
        assert all(item.error for item in summary.renames), "both sources must be refused"
        assert any("conflicts" in item.error for item in summary.renames)
        assert (tmp_path / "a-x.txt").exists() and (tmp_path / "a_x.txt").exists()
        assert not (tmp_path / "a.txt").exists()

    def test_rename_with_regex_template(self, tmp_path: Path):
        (tmp_path / "photo-0001.jpeg").write_text("x\n")
        options = make_options(
            tmp_path,
            r"^photo-(\d+)\.jpeg$",
            r"img_\1.jpg",
            patterns=["*.jpeg"],
            spec_kwargs={"kind": "regex"},
        )
        options.rename_only = True
        run_replace(options)
        assert (tmp_path / "img_0001.jpg").is_file()

    def test_rename_onto_existing_file_is_refused(self, tmp_path: Path):
        (tmp_path / "a-old.txt").write_text("one\n")
        (tmp_path / "a.txt").write_text("taken\n")
        options = make_options(tmp_path, "-old", "", patterns=["a-old.txt"])
        options.rename_only = True
        summary = run_replace(options)
        assert any("already exists" in (item.error or "") for item in summary.renames)
        assert (tmp_path / "a.txt").read_text() == "taken\n"

    def test_rename_force_overwrites(self, tmp_path: Path):
        (tmp_path / "a-old.txt").write_text("one\n")
        (tmp_path / "a.txt").write_text("taken\n")
        options = make_options(tmp_path, "-old", "", patterns=["a-old.txt"])
        options.rename_only = True
        options.force = True
        run_replace(options)
        assert (tmp_path / "a.txt").read_text() == "one\n"

    def test_rename_never_introduces_a_separator(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x\n")
        options = make_options(tmp_path, "a.txt", "../evil.txt", patterns=["*.txt"])
        options.rename_only = True
        summary = run_replace(options)
        assert (tmp_path / "a.txt").exists()
        assert any("separator" in (item.error or "") for item in summary.renames)

    def test_rename_is_journaled(self, tmp_path: Path):
        (tmp_path / "a-old.txt").write_text("content\n")
        options = make_options(tmp_path, "-old", "-new", patterns=["*.txt"])
        options.rename_only = True
        summary = run_replace(options)
        kinds = {record.kind for record in summary.session.records}
        assert "rename" in kinds


class TestInteractive:
    def test_hunk_filter_can_reject(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old one\ntwo old\n")
        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.interactive = True
        options.hunk_filter = lambda path, hunks: [False] * len(hunks)
        summary = run_replace(options)
        assert target.read_text() == "old one\ntwo old\n"
        assert summary.replacements == 0

    def test_hunk_filter_can_accept(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old one\ntwo old\n")
        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.interactive = True
        options.hunk_filter = lambda path, hunks: [True] * len(hunks)
        run_replace(options)
        assert target.read_text() == "new one\ntwo new\n"

    def test_hunks_carry_before_and_after(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("one\ntwo old\nthree\n")
        seen = {}

        def capture(path, hunks):
            seen["hunks"] = hunks
            return [True] * len(hunks)

        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.interactive = True
        options.hunk_filter = capture
        options.diff_context = 1
        run_replace(options)
        hunk = seen["hunks"][0]
        assert hunk.before == ["two old"]
        assert hunk.after == ["two new"]
        assert hunk.replacements == 1

    def test_interactive_requires_a_mutating_run(self, tmp_path: Path):
        from sr.errors import UsageError

        options = make_options(tmp_path, "a", "b", patterns=["*.txt"], apply_changes=False)
        options.interactive = True
        with pytest.raises(UsageError, match="read-only"):
            run_replace(options)


class TestConfirmation:
    def test_confirm_is_called_with_accurate_numbers(self, project: Path):
        seen = {}

        def confirm(files, replacements):
            seen["files"] = files
            seen["replacements"] = replacements
            return False

        options = make_options(project, "old", "new", patterns=["*.py"])
        options.confirm_threshold = 1
        options.confirm = confirm
        summary = run_replace(options)
        assert seen == {"files": 2, "replacements": 3}
        assert summary.changed == 0
        assert (project / "src" / "app.py").read_text().startswith('NAME = "old"')

    def test_confirm_below_threshold_is_not_called(self, project: Path):
        called = []
        options = make_options(project, "old", "new", patterns=["notes.txt"])
        options.confirm_threshold = 10
        options.confirm = lambda files, repl: called.append(files) or True
        run_replace(options)
        assert called == []

    def test_accepting_confirmation_writes(self, project: Path):
        options = make_options(project, "old", "new", patterns=["*.py"])
        options.confirm_threshold = 1
        options.confirm = lambda files, repl: True
        summary = run_replace(options)
        assert summary.changed == 2


class TestAtomicity:
    def test_inode_preserved_with_no_atomic(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old\n")
        inode = target.stat().st_ino
        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.atomic = False
        run_replace(options)
        assert target.stat().st_ino == inode

    def test_hard_link_target_is_updated_in_place(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old\n")
        link = tmp_path / "link.txt"
        os.link(target, link)
        options = make_options(tmp_path, "old", "new", patterns=["a.txt"])
        options.atomic = False
        run_replace(options)
        assert link.read_text() == "new\n"

    def test_no_temporary_files_left_behind(self, project: Path):
        run(project, "old", "new", patterns=["*.py"])
        leftovers = [path.name for path in project.rglob(".sr-tmp-*")]
        assert leftovers == []

    def test_mtime_preserved_on_request(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old\n")
        os.utime(target, (1_000_000, 1_000_000))
        options = make_options(tmp_path, "old", "new", patterns=["*.txt"])
        options.preserve_mtime = True
        run_replace(options)
        assert int(target.stat().st_mtime) == 1_000_000


class TestSummaryObject:
    def test_totals_are_consistent(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        totals = summary.totals
        assert totals["changed"] + totals["unchanged"] + totals["skipped"] + totals["failed"] == totals["scanned"]
        assert totals["replacements"] == sum(item.replacements for item in summary.outcomes)

    def test_json_document_is_serialisable(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        document = summary.to_dict(include_files=True, include_diff=True)
        text = json.dumps(document, default=str)
        assert '"totals"' in text and '"files"' in text

    def test_bytes_accounting(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        assert summary.bytes_read > 0
        assert summary.bytes_written > 0

    def test_outcomes_carry_relative_paths(self, project: Path):
        summary = run(project, "old", "new", patterns=["*.py"])
        assert all(not item.rel_path.startswith("/") for item in summary.outcomes)


class TestHelpers:
    def test_build_matcher(self):
        matcher = build_matcher("a", "b", ignore_case=True)
        assert matcher.count("A a") == 2

    def test_human_size(self):
        from sr.replace import human_size

        assert human_size(1024).endswith("KB")

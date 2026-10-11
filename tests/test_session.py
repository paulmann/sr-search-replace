"""Tests for the session store, blob integrity and locking (:mod:`sr.session`)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from helpers import make_options, run

from sr.errors import SessionError
from sr.session import (
    SCHEMA_VERSION,
    FileRecord,
    SessionLock,
    build_restore_plan,
    execute_restore,
    prune_sessions,
    read_journal,
    resolve_state_root,
    store_for,
)


@pytest.fixture
def session(project: Path):
    """Run one replace and hand back ``(root, info, store)``."""
    summary = run(project, "old", "new", patterns=["*.py"])
    return project, summary.session, store_for(project, None)


class TestLayout:
    def test_state_root_default(self, project: Path):
        assert resolve_state_root(project) == project / ".sr"

    def test_state_root_override(self, project: Path, tmp_path: Path):
        assert resolve_state_root(project, tmp_path / "elsewhere") == (tmp_path / "elsewhere").resolve()

    def test_directory_contents(self, session):
        _root, info, _store = session
        assert (info.directory / "manifest.json").is_file()
        assert (info.directory / "journal.jsonl").is_file()
        assert (info.directory / "files").is_dir()
        assert (info.directory / "diffs").is_dir()
        assert not (info.directory / ".lock").exists(), "the lock must be released"

    def test_manifest_is_valid_json_with_schema(self, session):
        _root, info, _store = session
        payload = json.loads((info.directory / "manifest.json").read_text())
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["complete"] is True
        assert payload["totals"]["files"] == 2

    def test_blobs_are_content_addressed(self, session):
        _root, info, _store = session
        for record in info.records:
            blob = info.directory / "files" / record.blob[:2] / f"{record.blob}.dat"
            assert blob.is_file()
            import hashlib

            assert hashlib.sha256(blob.read_bytes()).hexdigest() == record.blob

    def test_identical_originals_are_stored_once(self, tmp_path: Path):
        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text("same content\n")
        summary = run(tmp_path, "same", "other", patterns=["*.txt"])
        blobs = {record.blob for record in summary.session.records}
        assert len(blobs) == 1, "identical originals must share one blob"


class TestJournal:
    def test_entries_are_ordered_and_typed(self, session):
        _root, info, _store = session
        entries = list(read_journal(info.directory))
        assert entries[0]["type"] == "meta"
        assert entries[-1]["type"] == "finish"
        assert all(entry["type"] in {"meta", "op", "finish"} for entry in entries)

    def test_truncated_last_line_is_tolerated(self, session):
        _root, info, _store = session
        journal = info.directory / "journal.jsonl"
        journal.write_text(journal.read_text() + '{"type": "op", "path": "x"')
        entries = list(read_journal(info.directory))
        assert entries, "a truncated tail must not lose the earlier entries"

    def test_recovery_without_manifest(self, session):
        root, info, store = session
        (info.directory / "manifest.json").unlink()
        recovered = store.load(info.id)
        assert recovered.complete is False
        assert recovered.files == 2
        assert {record.rel_path for record in recovered.records} == {"src/app.py", "src/util.py"}


class TestResolution:
    def test_latest(self, project: Path):
        run(project, "old", "new", patterns=["notes.txt"])
        store = store_for(project, None)
        assert store.resolve("latest").id == store.list()[0].id
        assert store.resolve(None).id == store.list()[0].id

    def test_ordinal(self, project: Path):
        for index in range(3):
            (project / "notes.txt").write_text(f"marker{index}\n")
            run(project, f"marker{index}", "done", patterns=["notes.txt"])
        store = store_for(project, None)
        sessions = store.list()
        assert store.resolve("1").id == sessions[0].id
        assert store.resolve("2").id == sessions[1].id
        assert store.resolve("-1").id == sessions[-1].id

    def test_unique_suffix(self, project: Path):
        summary = run(project, "old", "new", patterns=["notes.txt"])
        store = store_for(project, None)
        assert store.resolve(summary.session.short_id).id == summary.session.id

    def test_ambiguous_suffix_is_reported(self, project: Path):
        store = store_for(project, None)
        (project / "notes.txt").write_text("a\n")
        run(project, "a", "b", patterns=["notes.txt"])
        (project / "notes.txt").write_text("c\n")
        run(project, "c", "d", patterns=["notes.txt"])
        sessions = store.list()
        assert len(sessions) == 2, "the fixture must produce two sessions"
        # Both ids start with the same date, so the year prefix is ambiguous.
        shared = sessions[0].id[:4]
        with pytest.raises(SessionError, match="ambiguous"):
            store.resolve(shared)

    def test_unknown_reference(self, project: Path):
        run(project, "old", "new", patterns=["notes.txt"])
        with pytest.raises(SessionError, match="unknown session"):
            store_for(project, None).resolve("does-not-exist")

    def test_out_of_range_ordinal(self, project: Path):
        run(project, "old", "new", patterns=["notes.txt"])
        with pytest.raises(SessionError, match="no session #"):
            store_for(project, None).resolve("99")

    def test_empty_store(self, project: Path):
        with pytest.raises(SessionError, match="no sessions found"):
            store_for(project, None).resolve("latest")

    def test_explicit_directory_path(self, project: Path):
        summary = run(project, "old", "new", patterns=["notes.txt"])
        store = store_for(project, None)
        assert store.resolve(str(summary.session.directory)).id == summary.session.id

    def test_newer_schema_is_refused(self, project: Path):
        summary = run(project, "old", "new", patterns=["notes.txt"])
        manifest = summary.session.directory / "manifest.json"
        payload = json.loads(manifest.read_text())
        payload["schema_version"] = SCHEMA_VERSION + 1
        manifest.write_text(json.dumps(payload))
        with pytest.raises(SessionError, match="newer sr"):
            store_for(project, None).load(summary.session.id)


class TestRestorePlan:
    def test_plan_covers_every_record(self, session):
        root, info, _store = session
        plan = build_restore_plan(info)
        assert len(plan) == 2
        assert all(item.path.is_absolute() for item in plan)

    def test_drift_is_detected(self, session):
        root, info, _store = session
        (root / "src" / "app.py").write_text("edited afterwards\n")
        plan = build_restore_plan(info)
        drifted = [item for item in plan if item.rel_path == "src/app.py"]
        assert drifted and drifted[0].drifted is True

    def test_missing_file_is_not_drift(self, session):
        root, info, _store = session
        (root / "src" / "app.py").unlink()
        plan = build_restore_plan(info)
        item = next(entry for entry in plan if entry.rel_path == "src/app.py")
        assert item.exists is False

    def test_only_filter(self, session):
        _root, info, _store = session
        plan = build_restore_plan(info, only=["app.py"])
        assert [item.rel_path for item in plan] == ["src/app.py"]

    def test_paths_outside_the_root_are_refused(self, session):
        """A tampered manifest must not be able to write outside the project."""
        root, info, store = session
        info.records[0].path = "/etc/passwd"
        info.records[0].rel_path = "../../etc/passwd"
        plan = build_restore_plan(info, root=root)
        assert plan, "the record must still appear, as blocked"
        assert plan[0].blocked == "path escapes the session root"
        result = execute_restore(plan, store, info.directory, force=True)
        assert all("etc/passwd" not in str(item) for item in result.restored)
        assert any("escapes" in reason for _path, reason in result.skipped)
        # The other record of the same session is untouched by the tampering.
        assert len(result.restored) == len(plan) - 1

    def test_absolute_record_without_relative_path_is_refused(self, session):
        """An absolute record with no relative path cannot be re-anchored."""
        root, info, _store = session
        info.records[0].path = "/somewhere/else/file.txt"
        info.records[0].rel_path = ""
        plan = build_restore_plan(info, root=root)
        assert plan[0].blocked == "path escapes the session root"


class TestExecuteRestore:
    def test_restores_exact_bytes(self, session):
        root, info, store = session
        target = root / "src" / "app.py"
        assert target.read_text() == 'NAME = "new"\nprint(NAME)\n'
        result = execute_restore(build_restore_plan(info), store, info.directory)
        assert result.ok
        assert target.read_text() == 'NAME = "old"\nprint(NAME)\n'

    def test_dry_run_writes_nothing(self, session):
        root, info, store = session
        target = root / "src" / "app.py"
        before = target.read_bytes()
        result = execute_restore(build_restore_plan(info), store, info.directory, dry_run=True)
        assert result.restored and target.read_bytes() == before

    def test_drifted_file_is_skipped_without_force(self, session):
        root, info, store = session
        target = root / "src" / "app.py"
        target.write_text("user edit\n")
        result = execute_restore(build_restore_plan(info), store, info.directory)
        assert target.read_text() == "user edit\n"
        assert result.skipped and result.drift_detected

    def test_force_overrides_drift(self, session):
        root, info, store = session
        target = root / "src" / "app.py"
        target.write_text("user edit\n")
        result = execute_restore(build_restore_plan(info), store, info.directory, force=True)
        assert result.ok
        assert target.read_text() == 'NAME = "old"\nprint(NAME)\n'

    def test_missing_file_is_recreated(self, session):
        root, info, store = session
        target = root / "src" / "app.py"
        target.unlink()
        result = execute_restore(build_restore_plan(info), store, info.directory)
        assert target.is_file() and result.ok

    def test_missing_directory_is_recreated(self, session):
        root, info, store = session
        import shutil

        shutil.rmtree(root / "src")
        result = execute_restore(build_restore_plan(info), store, info.directory)
        assert (root / "src" / "app.py").is_file()
        assert result.ok

    def test_corrupt_blob_is_reported_not_restored(self, session):
        root, info, store = session
        record = info.records[0]
        blob = info.directory / "files" / record.blob[:2] / f"{record.blob}.dat"
        blob.write_bytes(b"tampered")
        result = execute_restore(build_restore_plan(info), store, info.directory)
        assert not result.ok
        assert any("corrupt" in reason for _path, reason in result.failed)

    def test_permissions_are_restored(self, session):
        root, info, store = session
        target = root / "src" / "app.py"
        target.chmod(0o600)
        execute_restore(build_restore_plan(info), store, info.directory, force=True)
        assert target.stat().st_mode & 0o777 == 0o644


class TestRecordRoundTrip:
    def test_to_dict_from_dict(self):
        record = FileRecord(
            path="/tmp/a.txt",
            rel_path="a.txt",
            sha256_before="ab" * 32,
            sha256_after="cd" * 32,
            blob="ab" * 32,
            mode=0o640,
            diff_path="diffs/a.txt.diff",
        )
        restored = FileRecord.from_dict(record.to_dict())
        assert restored.rel_path == "a.txt"
        assert restored.mode == 0o640
        assert restored.diff_path == "diffs/a.txt.diff"

    def test_unknown_keys_are_ignored(self):
        record = FileRecord.from_dict(
            {"path": "/tmp/a", "rel_path": "a", "sha256_before": "x", "from_the_future": 1}
        )
        assert record.rel_path == "a"

    def test_none_values_become_empty_strings(self):
        record = FileRecord.from_dict({"path": None, "rel_path": None, "sha256_before": None})
        assert record.path == ""


class TestLocking:
    def test_second_lock_is_refused(self, tmp_path: Path):
        path = tmp_path / ".lock"
        first = SessionLock(path)
        first.acquire()
        try:
            second = SessionLock(path, ttl=3600)
            with pytest.raises(SessionError, match="another sr process"):
                second.acquire()
        finally:
            first.release()
        assert not path.exists()

    def test_stale_lock_is_broken(self, tmp_path: Path):
        path = tmp_path / ".lock"
        path.write_text("999999\n2020-01-01T00:00:00Z\n")
        os.utime(path, (time.time() - 86400, time.time() - 86400))
        lock = SessionLock(path, ttl=60)
        lock.acquire()  # must not raise
        lock.release()

    def test_lock_is_context_manager(self, tmp_path: Path):
        path = tmp_path / ".lock"
        with SessionLock(path):
            assert path.exists()
        assert not path.exists()

    def test_no_lock_option(self, project: Path):
        options = make_options(project, "old", "new", patterns=["notes.txt"])
        options.lock = False
        summary = run_replace_with(options)
        assert summary.session is not None


def run_replace_with(options):
    from sr.replace import run_replace

    return run_replace(options)


class TestPruning:
    def _make_sessions(self, root: Path, count: int) -> None:
        for index in range(count):
            (root / "notes.txt").write_text(f"marker{index}\n")
            run(root, f"marker{index}", "done", patterns=["notes.txt"])

    def test_keep_n(self, project: Path):
        self._make_sessions(project, 5)
        removed = prune_sessions(store_for(project, None), keep=2)
        assert len(removed) == 3
        assert len(store_for(project, None).list()) == 2

    def test_keep_zero_prunes_nothing(self, project: Path):
        self._make_sessions(project, 3)
        assert prune_sessions(store_for(project, None), keep=0) == []
        assert len(store_for(project, None).list()) == 3

    def test_keep_negative_prunes_nothing(self, project: Path):
        self._make_sessions(project, 3)
        assert prune_sessions(store_for(project, None), keep=-1) == []

    def test_keep_age(self, project: Path):
        self._make_sessions(project, 3)
        store = store_for(project, None)
        for info in store.list():
            old = time.time() - 86400 * 40
            os.utime(info.directory, (old, old))
        removed = prune_sessions(store, keep_age=86400 * 30)
        assert len(removed) == 3

    def test_dry_run(self, project: Path):
        self._make_sessions(project, 4)
        victims = prune_sessions(store_for(project, None), keep=1, dry_run=True)
        assert len(victims) == 3
        assert len(store_for(project, None).list()) == 4

    def test_empty_store(self, project: Path):
        assert prune_sessions(store_for(project, None), keep=1) == []


class TestStoreSize:
    def test_size_is_positive(self, session):
        _root, info, store = session
        assert store.size(info.id) > 0
        assert store.total_size() >= store.size(info.id)


class TestDeletion:
    def test_delete_requires_confirmation(self, session):
        _root, info, store = session
        with pytest.raises(SessionError, match="without confirmation"):
            store.delete(info.id, yes=False)

    def test_delete(self, session):
        _root, info, store = session
        store.delete(info.id, yes=True)
        assert not info.directory.exists()

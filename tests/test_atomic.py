"""Tests for atomic writes (:mod:`sr.atomic`)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sr.atomic import WriteResult, atomic_write_bytes, ensure_writable, free_space

#: The *real* euid, captured at import time: conftest's `not_root` fixture
#: monkeypatches ``os.geteuid`` for every test, so asking at test time would
#: always report 1000 and the permission tests below would run as root — where
#: the kernel ignores mode bits and the assertions pass for the wrong reason.
_REAL_EUID = os.geteuid() if hasattr(os, "geteuid") else -1


def _require_permission_enforcement() -> None:
    """Skip when the kernel would ignore the permission bits under test."""
    if not hasattr(os, "geteuid"):
        pytest.skip("POSIX permission semantics only")
    if _REAL_EUID == 0:
        pytest.skip("running as root: the kernel ignores these permission bits")


class TestAtomicWrite:
    def test_content_is_written(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        result = atomic_write_bytes(target, b"new")
        assert target.read_bytes() == b"new"
        assert isinstance(result, WriteResult)
        assert result.bytes_written == 3

    def test_creates_missing_file_and_parents(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "a.txt"
        atomic_write_bytes(target, b"hello")
        assert target.read_bytes() == b"hello"

    def test_mode_is_preserved(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        target.chmod(0o640)
        atomic_write_bytes(target, b"new")
        assert stat.S_IMODE(target.stat().st_mode) == 0o640

    def test_default_mode_for_a_new_file(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        atomic_write_bytes(target, b"new")
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_mtime_preserved_by_default(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        os.utime(target, (1_000_000, 1_000_000))
        atomic_write_bytes(target, b"new")
        assert int(target.stat().st_mtime) == 1_000_000

    def test_mtime_updated_when_metadata_not_preserved(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        os.utime(target, (1_000_000, 1_000_000))
        atomic_write_bytes(target, b"new", preserve_metadata=False)
        assert int(target.stat().st_mtime) > 1_000_000

    def test_truncation_when_shrinking(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_bytes(b"x" * 1000)
        atomic_write_bytes(target, b"short", preserve_inode=True)
        assert target.read_bytes() == b"short"

    def test_growing_a_file(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_bytes(b"x")
        atomic_write_bytes(target, b"y" * 5000, preserve_inode=True)
        assert target.read_bytes() == b"y" * 5000


class TestInodeSemantics:
    def test_preserve_inode_keeps_the_same_inode(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        inode = target.stat().st_ino
        result = atomic_write_bytes(target, b"new", preserve_inode=True)
        assert target.stat().st_ino == inode
        assert result.inode_changed is False

    def test_atomic_mode_changes_the_inode(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        inode = target.stat().st_ino
        result = atomic_write_bytes(target, b"new", preserve_inode=False)
        assert target.stat().st_ino != inode
        assert result.inode_changed is True

    def test_hard_link_sees_the_update_when_inode_preserved(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        link = tmp_path / "link.txt"
        os.link(target, link)
        atomic_write_bytes(target, b"new", preserve_inode=True)
        assert link.read_bytes() == b"new"

    def test_hard_link_diverges_when_inode_replaced(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        link = tmp_path / "link.txt"
        os.link(target, link)
        atomic_write_bytes(target, b"new", preserve_inode=False)
        assert link.read_bytes() == b"old"


class TestFailurePaths:
    def test_no_temporary_file_left_on_failure(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        with pytest.raises(OSError):
            atomic_write_bytes(target / "not-a-dir" / "x", b"new")
        assert list(tmp_path.glob(".sr-tmp-*")) == []

    def test_readonly_file_falls_back_and_says_so(self, tmp_path: Path):
        """``preserve_inode`` is best-effort — and never *silently* so.

        A read-only file cannot be rewritten in place.  Failing the whole write
        would be worse than changing the inode, but a silent inode swap is what
        ``--no-atomic`` exists to prevent, so the fallback is reported in the
        result and the caller can surface it.
        """
        _require_permission_enforcement()
        target = tmp_path / "a.txt"
        target.write_text("old")
        inode_before = target.stat().st_ino
        target.chmod(0o444)

        result = atomic_write_bytes(target, b"new", preserve_inode=True)

        assert target.read_bytes() == b"new"
        assert result.inode_changed is True
        assert result.inode_fallback_reason, "the fallback must be reported, not silent"
        assert target.stat().st_ino != inode_before

    def test_hard_link_diverges_after_the_fallback(self, tmp_path: Path):
        """The observable consequence of the fallback, pinned so it stays known."""
        _require_permission_enforcement()
        target = tmp_path / "a.txt"
        target.write_text("old")
        link = tmp_path / "link.txt"
        os.link(target, link)
        target.chmod(0o444)

        atomic_write_bytes(target, b"new", preserve_inode=True)

        assert target.read_bytes() == b"new"
        assert link.read_bytes() == b"old", "the hard link no longer tracks the file"

    def test_in_place_write_reports_no_fallback_when_it_works(self, tmp_path: Path):
        target = tmp_path / "a.txt"
        target.write_text("old")
        result = atomic_write_bytes(target, b"new", preserve_inode=True)
        assert result.inode_changed is False
        assert result.inode_fallback_reason == ""

    def test_atomic_replace_of_a_readonly_file_succeeds(self, tmp_path: Path):
        """POSIX ``rename(2)`` needs write access to the *directory*, not the file.

        Pinned deliberately: it is the documented consequence of ``--atomic``,
        and the reason the engine (``sr/replace.py``) checks ``os.access(path,
        W_OK)`` itself instead of relying on the write to fail.
        """
        _require_permission_enforcement()
        target = tmp_path / "a.txt"
        target.write_text("old")
        target.chmod(0o444)
        result = atomic_write_bytes(target, b"new", preserve_inode=False)
        assert target.read_bytes() == b"new"
        assert result.inode_changed is True
        assert result.inode_fallback_reason == ""
        assert stat.S_IMODE(target.stat().st_mode) == 0o444, "the original mode must be kept"

    def test_readonly_directory_blocks_the_atomic_mode_only(self, tmp_path: Path):
        """A read-only *directory* is the hard boundary for temp+rename.

        The in-place mode does not create a directory entry, so it keeps working
        — which is the one real advantage of ``--no-atomic`` and worth pinning.
        """
        _require_permission_enforcement()
        directory = tmp_path / "locked"
        directory.mkdir()
        target = directory / "a.txt"
        target.write_text("old")
        directory.chmod(0o555)
        try:
            with pytest.raises(OSError):
                atomic_write_bytes(target, b"new", preserve_inode=False)
            assert target.read_text() == "old", "a failed write must not modify the file"
            assert list(directory.glob(".sr-tmp-*")) == [], "no temporary file may survive"

            # In-place still works: no new directory entry is needed.
            result = atomic_write_bytes(target, b"newer", preserve_inode=True)
            assert result.inode_changed is False
            assert target.read_text() == "newer"
        finally:
            directory.chmod(0o755)


class TestHelpers:
    def test_ensure_writable(self, tmp_path: Path):
        assert ensure_writable(tmp_path / "new.txt") is True
        target = tmp_path / "a.txt"
        target.write_text("x")
        assert ensure_writable(target) is True

    def test_free_space(self, tmp_path: Path):
        value = free_space(tmp_path)
        assert value is None or value > 0

    def test_free_space_of_missing_path(self, tmp_path: Path):
        assert free_space(tmp_path / "nope") is None or free_space(tmp_path / "nope") >= 0

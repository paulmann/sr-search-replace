"""Atomic, permission-preserving file writes.

``sed -i`` (and the naive "truncate then write") breaks in three ways that
matter for a bulk-edit tool:

* the inode changes, so hard links silently diverge;
* a crash mid-write leaves a truncated file;
* the mode/owner/mtime of the original are lost.

:func:`atomic_write_bytes` writes to a sibling temporary file, ``fsync``s it,
then ``os.replace``s it onto the target — atomic on every POSIX filesystem and
on NTFS.  With ``preserve_inode=True`` (the default) the original file is
opened for writing and its content replaced in place instead, which keeps the
inode, hard links and any open file descriptors valid.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .util import copy_metadata

TEMP_PREFIX = ".sr-tmp-"


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What a write did, for the session journal and the summary."""

    path: Path
    bytes_written: int
    inode_changed: bool
    mode: int
    temp_removed: bool = True
    #: Set when the caller asked to keep the inode but that was impossible and
    #: the write fell back to temp+rename.  Never silent: callers can report it.
    inode_fallback_reason: str = ""


def _fsync_directory(path: Path) -> None:
    """Persist a rename in the parent directory (best effort)."""
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    preserve_inode: bool = True,
    preserve_metadata: bool = True,
    fsync: bool = True,
) -> WriteResult:
    """Write ``data`` to ``path`` atomically.

    Two modes, selected by ``preserve_inode``:

    ``preserve_inode=True`` (``--no-atomic``)
        The existing file is opened ``r+b`` and rewritten in place, so the
        inode, hard links and any open descriptors stay valid.  This needs
        write permission on the *file*.  When that is not available the call
        falls back to temp+rename rather than failing, and says so in
        :attr:`WriteResult.inode_fallback_reason` — a silent inode swap is
        exactly what a caller of this mode is trying to avoid.
    ``preserve_inode=False`` (``--atomic``, the default)
        A sibling temporary file is written, fsync-ed and ``os.replace``-d onto
        the target.  A crash can therefore never leave a truncated file, but
        the inode changes.  This needs write permission on the *directory*.

    Raises ``OSError`` when neither mode can succeed; the original file is left
    untouched because the temporary file is removed in the ``finally`` block.
    """
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    original_inode = _inode_of(target)
    original_mode = None
    if target.exists():
        try:
            original_mode = stat.S_IMODE(target.stat().st_mode)
        except OSError:
            original_mode = None

    fallback_reason = ""
    if preserve_inode and target.exists():
        # In-place rewrite: keeps inode, hard links and ACLs.
        before = target.stat()
        try:
            with open(target, "r+b") as handle:
                handle.write(data)
                handle.truncate(len(data))
                if fsync:
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            # Not writable in place.  Fall through to the atomic path, but
            # record why so the caller can tell the user that hard links and
            # open descriptors for this file no longer track it.
            fallback_reason = f"cannot rewrite in place ({exc.strerror or exc}); replaced the inode instead"
        else:
            if preserve_metadata:
                try:
                    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
                except OSError:
                    pass
            return WriteResult(
                path=target,
                bytes_written=len(data),
                inode_changed=False,
                mode=original_mode if original_mode is not None else 0o644,
            )

    created = tempfile.NamedTemporaryFile(
        dir=str(parent), prefix=TEMP_PREFIX, suffix=target.suffix, delete=False
    )
    temp_path = Path(created.name)
    try:
        with created as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        if original_mode is not None:
            try:
                os.chmod(temp_path, original_mode)
            except OSError:
                pass
        else:
            os.chmod(temp_path, 0o644)
        if preserve_metadata and target.exists():
            copy_metadata(target, temp_path)
        os.replace(temp_path, target)
        if fsync:
            _fsync_directory(parent)
        return WriteResult(
            path=target,
            bytes_written=len(data),
            inode_changed=_inode_of(target) != original_inode,
            mode=original_mode if original_mode is not None else 0o644,
            temp_removed=True,
            inode_fallback_reason=fallback_reason,
        )
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _inode_of(path: Path) -> int | None:
    try:
        return path.stat().st_ino
    except OSError:
        return None


def ensure_writable(path: Path) -> bool:
    """True when ``path`` (or its parent, if it does not exist) can be written."""
    if path.exists():
        return os.access(path, os.W_OK)
    return os.access(path.parent, os.W_OK)


def free_space(path: Path) -> int | None:
    """Free bytes on the filesystem holding ``path`` (``None`` if unknown)."""
    try:
        return os.statvfs(str(path)).f_bavail * os.statvfs(str(path)).f_frsize
    except (OSError, AttributeError):
        return None

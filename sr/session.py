"""Sessions: the audit trail that makes every run reversible.

A *session* is a directory that records one mutating run:

.. code-block:: text

    .sr/sessions/20260131T120530Z-9f3a1c/
    ├── manifest.json       # who, what, when, per-file hashes (schema versioned)
    ├── journal.jsonl       # append-only op log, one JSON object per line
    ├── files/<sha1[:2]>/<sha1>.dat   # content-addressed original bytes
    ├── diffs/<relpath>.diff          # optional unified diff of the change
    └── .lock               # advisory lock, held for the duration of the run

Design notes
------------
* **Content-addressed store** — identical originals are stored once, so a run
  that touches 10 000 files with the same header costs one blob.  It also makes
  the store self-verifying: every entry carries its SHA-256.
* **Journal is append-only** — it survives a crash mid-run and is the
  authoritative list of what to undo.  ``manifest.json`` is rewritten only at
  the end (and is therefore allowed to be absent for a crashed run; the journal
  is then used for recovery).
* **Rollback is journalled too** — rolling back creates a new session that
  records the pre-rollback state, so an erroneous rollback can itself be
  rolled back.
* **Path safety** — restore targets are resolved and must stay inside the
  session root; symlinks that would escape are refused.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SessionError
from .util import file_stat, now_iso, session_id, sha256_bytes, sha256_file

#: Bump when the on-disk layout changes in an incompatible way.
SCHEMA_VERSION = 2

SESSIONS_DIRNAME = "sessions"
STATE_DIRNAME = ".sr"
MANIFEST_NAME = "manifest.json"
JOURNAL_NAME = "journal.jsonl"
FILES_DIRNAME = "files"
DIFFS_DIRNAME = "diffs"
LOCK_NAME = ".lock"

#: Metadata keys persisted per file entry (kept flat and JSON-friendly).
_OP_KIND_REPLACE = "replace"
_OP_KIND_RENAME = "rename"
_OP_KIND_CREATE = "create"
_OP_KIND_DELETE = "delete"
_OP_KIND_ROLLBACK = "rollback"

#: Journal kinds that can be undone by restoring ``backup`` to ``path``.
UNDOABLE_KINDS = (_OP_KIND_REPLACE, _OP_KIND_ROLLBACK, _OP_KIND_CREATE, _OP_KIND_DELETE)

#: Manifest keys that differ from the :class:`FileRecord` field names.  A module
#: constant rather than a class attribute so that no dataclass carries a mutable
#: default.
RECORD_KEY_ALIASES: dict[str, str] = {"diff": "diff_path"}


class SessionLock:
    """Advisory, non-blocking lock based on ``O_CREAT|O_EXCL``.

    Stale locks (owner process gone, or older than ``ttl``) are broken with a
    warning so that a crashed run cannot wedge the project directory forever.
    """

    def __init__(self, path: Path, *, ttl: float = 6 * 3600.0) -> None:
        self.path = path
        self.ttl = ttl
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (0, 1):
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(self._fd, f"{os.getpid()}\n{now_iso()}\n".encode())
                return
            except FileExistsError:
                if attempt == 0 and self._break_if_stale():
                    continue
                raise SessionError(
                    f"another sr process is using {self.path.parent.name}",
                    hint=f"if you are sure none is running, delete {self.path}",
                ) from None

    def _break_if_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
            content = self.path.read_text(errors="replace").split()
            pid = int(content[0]) if content and content[0].isdigit() else -1
        except (OSError, ValueError):
            age, pid = self.ttl + 1, -1
        alive = pid > 0 and _pid_alive(pid)
        if alive and age < self.ttl:
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> SessionLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


@dataclass(slots=True)
class FileRecord:
    """One file entry of a session manifest."""

    path: str
    rel_path: str
    sha256_before: str
    sha256_after: str = ""
    blob: str = ""
    size_before: int = 0
    size_after: int = 0
    mtime_before: float = 0.0
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    encoding: str = "utf-8"
    matches: int = 0
    replacements: int = 0
    diff_path: str = ""
    kind: str = _OP_KIND_REPLACE
    status: str = "changed"
    renamed_to: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "rel_path": self.rel_path,
            "sha256_before": self.sha256_before,
            "sha256_after": self.sha256_after,
            "blob": self.blob,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "mtime_before": self.mtime_before,
            "encoding": self.encoding,
            "matches": self.matches,
            "replacements": self.replacements,
            "kind": self.kind,
            "status": self.status,
        }
        if self.mode is not None:
            data["mode"] = self.mode
        if self.uid is not None:
            data["uid"] = self.uid
        if self.gid is not None:
            data["gid"] = self.gid
        if self.diff_path:
            data["diff"] = self.diff_path
        if self.renamed_to:
            data["renamed_to"] = self.renamed_to
        if self.error:
            data["error"] = self.error
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileRecord:
        """Rebuild a record from its manifest representation.

        Unknown keys are ignored so a manifest written by a slightly different
        version still loads; known keys are coerced to their declared types
        because JSON has no distinction between ``int`` and ``bool`` for us.
        """
        known = set(cls.__slots__)
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            name = RECORD_KEY_ALIASES.get(key, key)
            if name not in known:
                continue
            if name in {"size_before", "size_after", "matches", "replacements", "mode", "uid", "gid"}:
                try:
                    value = int(value) if value is not None else None
                except (TypeError, ValueError):
                    value = None
            elif name == "mtime_before":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = 0.0
            else:
                value = "" if value is None else str(value)
            kwargs[name] = value
        return cls(**kwargs)


@dataclass(slots=True)
class SessionInfo:
    """Lightweight, manifest-backed description of a session."""

    id: str
    directory: Path
    created_at: str = ""
    finished_at: str = ""
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    root: str = ""
    search: str = ""
    replacement: str = ""
    pattern_kind: str = "fixed"
    options: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    schema_version: int = SCHEMA_VERSION
    files: int = 0
    replacements: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    duration: float = 0.0
    dry_run: bool = False
    kind: str = "replace"
    complete: bool = True
    records: list[FileRecord] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.id.rsplit("-", 1)[-1] if "-" in self.id else self.id

    @property
    def label(self) -> str:
        arrow = f"{self.search!r} → {self.replacement!r}" if self.search else ""
        return " ".join(part for part in (self.created_at, self.kind, arrow) if part)

    def to_dict(self, *, include_files: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "directory": str(self.directory),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "kind": self.kind,
            "complete": self.complete,
            "dry_run": self.dry_run,
            "cwd": self.cwd,
            "root": self.root,
            "command": self.command,
            "search": self.search,
            "replacement": self.replacement,
            "pattern_kind": self.pattern_kind,
            "options": self.options,
            "version": self.version,
            "schema_version": self.schema_version,
            "duration_seconds": round(self.duration, 3),
            "totals": {
                "files": self.files,
                "replacements": self.replacements,
                "bytes_before": self.bytes_before,
                "bytes_after": self.bytes_after,
            },
        }
        if include_files:
            data["files"] = [record.to_dict() for record in self.records]
        return data


class SessionStore:
    """Finds and manages the session directories under a state root."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.sessions_dir = self.state_root / SESSIONS_DIRNAME

    # ------------------------------------------------------------- discovery
    def list(self, *, limit: int | None = None, include_incomplete: bool = True) -> list[SessionInfo]:
        """Sessions, newest first."""
        if not self.sessions_dir.is_dir():
            return []
        found: list[SessionInfo] = []
        for entry in sorted(self.sessions_dir.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            try:
                info = self.load(entry.name, with_records=False)
            except SessionError:
                if not include_incomplete:
                    continue
                info = SessionInfo(id=entry.name, directory=entry, complete=False)
            found.append(info)
        if limit is not None and limit > 0:
            return found[:limit]
        return found

    def directory_for(self, session: str) -> Path:
        return self.sessions_dir / session

    def exists(self, session: str) -> bool:
        return self.directory_for(session).is_dir()

    def resolve(self, reference: str | None) -> SessionInfo:
        """Resolve ``None``/``latest``/``<id>``/``<prefix>``/``<path>``."""
        candidates = self.list()
        if not candidates:
            raise SessionError(
                "no sessions found",
                hint=f"expected a session directory under {self.sessions_dir}",
            )
        if reference in (None, "", "latest", "last"):
            return self.load(candidates[0].id, with_records=True)

        # An explicit path always wins.
        as_path = Path(reference)
        if as_path.is_dir() and (as_path / MANIFEST_NAME).exists():
            return self._load_from(as_path, with_records=True)

        exact = [info for info in candidates if info.id == reference]
        if exact:
            return self.load(exact[0].id, with_records=True)

        # An ordinal (1 = newest) is checked *before* substring matching:
        # otherwise "2" would match every id containing a 2, which is all of
        # them because the id starts with a timestamp.  Only a short numeric
        # reference counts as an ordinal — "2026" is somebody typing a date
        # fragment, and should be reported as ambiguous, not as "#2026".
        if reference.lstrip("-").isdigit() and len(reference.lstrip("-")) <= 3:
            index = int(reference) or 1
            if abs(index) <= len(candidates):
                chosen = candidates[index - 1] if index > 0 else candidates[index]
                return self.load(chosen.id, with_records=True)
            raise SessionError(
                f"there is no session #{reference}",
                hint=f"only {len(candidates)} session(s) exist; run `sr sessions`",
            )

        # Unique id suffix, then any unique substring.
        suffixed = [info for info in candidates if info.id.endswith(reference)]
        if len(suffixed) == 1:
            return self.load(suffixed[0].id, with_records=True)
        if len(suffixed) > 1:
            raise SessionError(
                f"session reference {reference!r} is ambiguous",
                hint="candidates: " + ", ".join(info.id for info in suffixed[:8]),
            )
        contained = [info for info in candidates if reference in info.id]
        if len(contained) == 1:
            return self.load(contained[0].id, with_records=True)
        if len(contained) > 1:
            raise SessionError(
                f"session reference {reference!r} is ambiguous",
                hint="candidates: " + ", ".join(item.id for item in contained[:8]),
            )
        raise SessionError(
            f"unknown session {reference!r}",
            hint="run `sr sessions` to list the available ids",
        )

    # --------------------------------------------------------------- loading
    def load(self, session: str, *, with_records: bool = True) -> SessionInfo:
        directory = self.directory_for(session)
        if not directory.is_dir():
            raise SessionError(
                f"session directory not found: {directory}",
                hint="run `sr sessions` to list the available ids",
            )
        return self._load_from(directory, with_records=with_records)

    def _load_from(self, directory: Path, *, with_records: bool) -> SessionInfo:
        manifest = directory / MANIFEST_NAME
        if not manifest.is_file():
            info = self._recover_from_journal(directory)
            if info is None:
                raise SessionError(
                    f"session {directory.name} has no manifest and no journal",
                    hint="the directory is not a valid sr session",
                )
            return info
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"cannot read {manifest}: {exc}") from exc
        if not isinstance(raw, dict):
            raise SessionError(f"{manifest} does not contain an object")
        schema = int(raw.get("schema_version", 1))
        if schema > SCHEMA_VERSION:
            raise SessionError(
                f"session {directory.name} was written by a newer sr (schema {schema} > {SCHEMA_VERSION})",
                hint="upgrade sr: `sr update`",
            )
        totals = raw.get("totals", {}) or {}
        records_raw = raw.get("files", []) if with_records else []
        records = [FileRecord.from_dict(item) for item in records_raw if isinstance(item, dict)]
        return SessionInfo(
            id=str(raw.get("id") or directory.name),
            directory=directory,
            created_at=str(raw.get("created_at", "")),
            finished_at=str(raw.get("finished_at", "")),
            command=list(raw.get("command", []) or []),
            cwd=str(raw.get("cwd", "")),
            root=str(raw.get("root", "")),
            search=str(raw.get("search", "")),
            replacement=str(raw.get("replacement", "")),
            pattern_kind=str(raw.get("pattern_kind", "fixed")),
            options=dict(raw.get("options", {}) or {}),
            version=str(raw.get("version", "")),
            schema_version=schema,
            files=int(totals.get("files", len(records))),
            replacements=int(totals.get("replacements", 0)),
            bytes_before=int(totals.get("bytes_before", 0)),
            bytes_after=int(totals.get("bytes_after", 0)),
            duration=float(raw.get("duration_seconds", 0.0) or 0.0),
            dry_run=bool(raw.get("dry_run", False)),
            kind=str(raw.get("kind", "replace")),
            complete=bool(raw.get("complete", True)),
            records=records,
        )

    def _recover_from_journal(self, directory: Path) -> SessionInfo | None:
        """Rebuild enough of a session from the journal after a crash."""
        journal = directory / JOURNAL_NAME
        if not journal.is_file():
            return None
        records: dict[str, FileRecord] = {}
        meta: dict[str, Any] = {}
        for entry in read_journal(directory):
            if entry.get("type") == "meta":
                meta.update(entry)
                continue
            path = str(entry.get("path", ""))
            if not path:
                continue
            record = records.get(path) or FileRecord(
                path=path, rel_path=str(entry.get("rel_path", "")), sha256_before=str(entry.get("sha256", ""))
            )
            record.blob = str(entry.get("blob", record.blob))
            record.kind = str(entry.get("kind", record.kind))
            record.status = str(entry.get("status", record.status))
            record.replacements = int(entry.get("replacements", record.replacements))
            records[path] = record
        if not records:
            return None
        return SessionInfo(
            id=directory.name,
            directory=directory,
            created_at=str(meta.get("created_at", "")),
            command=list(meta.get("command", []) or []),
            cwd=str(meta.get("cwd", "")),
            root=str(meta.get("root", "")),
            search=str(meta.get("search", "")),
            replacement=str(meta.get("replacement", "")),
            version=str(meta.get("version", "")),
            kind=str(meta.get("kind", "replace")),
            complete=False,
            files=len(records),
            records=list(records.values()),
        )

    # ------------------------------------------------------------- lifecycle
    def create(
        self,
        *,
        kind: str = "replace",
        root: Path,
        command: Sequence[str],
        search: str = "",
        replacement: str = "",
        pattern_kind: str = "fixed",
        options: dict[str, Any] | None = None,
        version: str = "",
        dry_run: bool = False,
        session: str | None = None,
    ) -> Session:
        identifier = session or session_id()
        directory = self.directory_for(identifier)
        if directory.exists():
            raise SessionError(f"session directory already exists: {directory}")
        return Session(
            info=SessionInfo(
                id=identifier,
                directory=directory,
                created_at=now_iso(),
                command=list(command),
                cwd=str(Path.cwd()),
                root=str(root),
                search=search,
                replacement=replacement,
                pattern_kind=pattern_kind,
                options=dict(options or {}),
                version=version,
                dry_run=dry_run,
                kind=kind,
                complete=False,
            ),
            store=self,
        )

    def delete(self, session: str, *, yes: bool = False) -> Path:
        info = self.resolve(session)
        if not yes:
            raise SessionError(
                f"refusing to delete session {info.id} without confirmation",
                hint="pass --yes to confirm",
            )
        shutil.rmtree(info.directory, ignore_errors=False)
        return info.directory

    def size(self, session: str) -> int:
        info = self.resolve(session)
        total = 0
        for path in info.directory.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def total_size(self) -> int:
        if not self.sessions_dir.is_dir():
            return 0
        return sum(
            entry.stat().st_size for entry in self.sessions_dir.rglob("*") if entry.is_file()
        )


def read_journal(directory: Path) -> Iterator[dict[str, Any]]:
    """Yield journal entries, tolerating a truncated final line."""
    journal = Path(directory) / JOURNAL_NAME
    if not journal.is_file():
        return
    with open(journal, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                yield data


class Session:
    """An open, writable session."""

    def __init__(self, info: SessionInfo, store: SessionStore) -> None:
        self.info = info
        self.store = store
        self.directory = info.directory
        self.files_dir = self.directory / FILES_DIRNAME
        self.diffs_dir = self.directory / DIFFS_DIRNAME
        self.journal_path = self.directory / JOURNAL_NAME
        self.manifest_path = self.directory / MANIFEST_NAME
        self._journal: Any = None
        self._lock: SessionLock | None = None
        self._started = time.monotonic()
        self._blobs: dict[str, str] = {}
        self.records: list[FileRecord] = []

    # ------------------------------------------------------------- lifecycle
    def open(self, *, lock: bool = True) -> Session:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(exist_ok=True)
        self.diffs_dir.mkdir(exist_ok=True)
        if lock:
            self._lock = SessionLock(self.directory / LOCK_NAME)
            self._lock.acquire()
        self._write_meta_journal()
        return self

    def _write_meta_journal(self) -> None:
        self._append_journal(
            {
                "type": "meta",
                "schema_version": SCHEMA_VERSION,
                "id": self.info.id,
                "created_at": self.info.created_at,
                "kind": self.info.kind,
                "command": self.info.command,
                "cwd": self.info.cwd,
                "root": self.info.root,
                "search": self.info.search,
                "replacement": self.info.replacement,
                "pattern_kind": self.info.pattern_kind,
                "version": self.info.version,
                "dry_run": self.info.dry_run,
            }
        )

    def close(self, *, complete: bool = True) -> SessionInfo:
        self.info.finished_at = now_iso()
        self.info.duration = time.monotonic() - self._started
        self.info.complete = complete
        self.info.records = list(self.records)
        self.info.files = len(self.records)
        self.info.replacements = sum(record.replacements for record in self.records)
        self.info.bytes_before = sum(record.size_before for record in self.records)
        self.info.bytes_after = sum(record.size_after for record in self.records)
        self.write_manifest()
        self._append_journal({"type": "finish", "at": self.info.finished_at, "complete": complete})
        if self._journal is not None:
            try:
                self._journal.close()
            finally:
                self._journal = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None
        return self.info

    def abort(self) -> None:
        """Close without marking the session complete (crash path)."""
        try:
            self.close(complete=False)
        except Exception:  # pragma: no cover - best effort
            pass

    def __enter__(self) -> Session:
        return self.open()

    def __exit__(self, exc_type: object, *exc: object) -> None:
        self.close(complete=exc_type is None)

    # --------------------------------------------------------------- journal
    def _append_journal(self, entry: dict[str, Any]) -> None:
        if self._journal is None:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            self._journal = open(self.journal_path, "a", encoding="utf-8")
        payload = dict(entry)
        payload.setdefault("at", now_iso())
        self._journal.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        self._journal.flush()
        try:
            os.fsync(self._journal.fileno())
        except OSError:  # pragma: no cover - exotic filesystems
            pass

    # ----------------------------------------------------------------- blobs
    def store_bytes(self, data: bytes) -> str:
        """Content-addressed store; returns the blob id (sha256)."""
        digest = sha256_bytes(data)
        if digest in self._blobs:
            return self._blobs[digest]
        bucket = self.files_dir / digest[:2]
        bucket.mkdir(parents=True, exist_ok=True)
        target = bucket / f"{digest}.dat"
        if not target.exists():
            temp = target.with_suffix(".part")
            temp.write_bytes(data)
            os.replace(temp, target)
        self._blobs[digest] = digest
        return digest

    def blob_path(self, blob: str) -> Path:
        return self.files_dir / blob[:2] / f"{blob}.dat"

    def read_blob(self, blob: str) -> bytes:
        path = self.blob_path(blob)
        if not path.is_file():
            raise SessionError(
                f"backup blob missing for {blob[:12]}",
                hint=f"expected {path}; the session store may have been pruned",
            )
        data = path.read_bytes()
        actual = sha256_bytes(data)
        if actual != blob:
            raise SessionError(
                f"backup blob is corrupt (expected {blob[:12]}, found {actual[:12]})",
                hint="the session cannot be trusted; restore from version control",
            )
        return data

    # ---------------------------------------------------------------- records
    def record_replace(
        self,
        *,
        path: Path,
        rel_path: str,
        original_bytes: bytes,
        new_bytes: bytes,
        replacements: int,
        matches: int = 0,
        encoding: str = "utf-8",
        diff_text: str = "",
        mode: int | None = None,
    ) -> FileRecord:
        """Record one replaced file.

        .. important::
           Call this **after** the new bytes have been written to ``path``:
           ``sha256_after`` is taken from the live file so that a later
           rollback can detect edits made after the session.
        """
        size_before, mtime_before, uid, gid = file_stat(path)
        blob = self.store_bytes(original_bytes)
        # `sha256_after` must describe what is *on disk* once the write has
        # succeeded, otherwise drift detection would flag every file of the
        # session as "modified since".  Callers write the file before they
        # record it, so hashing the live file is both correct and cheap enough
        # (we already hold the bytes in memory for the common case).
        try:
            on_disk = sha256_file(path)
        except OSError:
            on_disk = sha256_bytes(new_bytes)
        record = FileRecord(
            path=str(path),
            rel_path=rel_path,
            sha256_before=blob,
            sha256_after=on_disk,
            blob=blob,
            size_before=size_before,
            size_after=len(new_bytes),
            mtime_before=mtime_before,
            mode=mode if mode is not None else (size_before and _mode_of(path)),
            uid=uid if uid >= 0 else None,
            gid=gid if gid >= 0 else None,
            encoding=encoding,
            matches=matches or replacements,
            replacements=replacements,
            kind=_OP_KIND_REPLACE,
            status="changed",
        )
        if diff_text:
            diff_path = self._safe_diff_path(rel_path)
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_text(diff_text, encoding="utf-8")
            record.diff_path = str(diff_path.relative_to(self.directory))
        self.records.append(record)
        self._append_journal(
            {
                "type": "op",
                "kind": _OP_KIND_REPLACE,
                "path": record.path,
                "rel_path": rel_path,
                "blob": blob,
                "sha256": blob,
                "sha256_after": record.sha256_after,
                "replacements": replacements,
                "status": "changed",
            }
        )
        return record

    def record_rename(self, *, source: Path, target: Path, rel_path: str) -> FileRecord:
        record = FileRecord(
            path=str(source),
            rel_path=rel_path,
            sha256_before="",
            kind=_OP_KIND_RENAME,
            status="renamed",
            renamed_to=str(target),
        )
        self.records.append(record)
        self._append_journal(
            {
                "type": "op",
                "kind": _OP_KIND_RENAME,
                "path": str(source),
                "rel_path": rel_path,
                "renamed_to": str(target),
                "status": "renamed",
            }
        )
        return record

    def record_error(self, path: Path, rel_path: str, message: str) -> None:
        self.records.append(
            FileRecord(
                path=str(path),
                rel_path=rel_path,
                sha256_before="",
                status="error",
                error=message,
            )
        )
        self._append_journal(
            {"type": "op", "kind": "error", "path": str(path), "rel_path": rel_path, "error": message}
        )

    def _safe_diff_path(self, rel_path: str) -> Path:
        cleaned = rel_path.replace("\\", "/").lstrip("/")
        candidate = (self.diffs_dir / f"{cleaned}.diff").resolve()
        root = self.diffs_dir.resolve()
        if root not in candidate.parents and candidate != root:
            # Path tried to escape; hash it instead.
            # Non-security use: a stable short name for a diff file whose
            # relative path would otherwise escape the diffs directory.
            digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()  # noqa: S324
            candidate = self.diffs_dir / f"{digest}.diff"
        return candidate

    def write_manifest(self) -> Path:
        payload = self.info.to_dict(include_files=True)
        payload["files"] = [record.to_dict() for record in self.records]
        temp = self.manifest_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, self.manifest_path)
        return self.manifest_path


def _mode_of(path: Path) -> int | None:
    try:
        import stat as stat_module

        return stat_module.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Restore helpers (shared by `rollback`, `undo` and the safety snapshot)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RestorePlanItem:
    """One planned restore action, ready for preview and confirmation."""

    path: Path
    blob: str
    rel_path: str
    expected_sha256: str = ""
    current_sha256: str = ""
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    exists: bool = True
    drifted: bool = False
    blocked: str = ""
    #: Pre-restore content, captured while building the plan so that the
    #: rollback itself can be journalled (and therefore undone).
    snapshot_bytes: bytes | None = None

    @property
    def safe(self) -> bool:
        return not self.blocked


@dataclass(slots=True)
class RestoreResult:
    restored: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    drift_detected: list[Path] = field(default_factory=list)
    #: Renames that were undone (a rename-only session restores no content).
    renamed: int = 0
    #: Id of the session that records the pre-rollback state (empty if none).
    snapshot_id: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "restored": [str(item) for item in self.restored],
            "skipped": [{"path": str(path), "reason": reason} for path, reason in self.skipped],
            "failed": [{"path": str(path), "reason": reason} for path, reason in self.failed],
            "drift_detected": [str(item) for item in self.drift_detected],
            "renamed": self.renamed,
            "snapshot_id": self.snapshot_id,
        }


def build_restore_plan(
    info: SessionInfo,
    *,
    root: Path | None = None,
    only: Iterable[str] | None = None,
    detect_drift: bool = True,
) -> list[RestorePlanItem]:
    """Compute what a rollback would do, without touching the filesystem.

    ``root`` re-anchors relative paths (useful when the project moved).
    ``only`` filters by relative path substring.
    """
    base = Path(root) if root else (Path(info.root) if info.root else Path.cwd())
    filters = [item for item in (only or []) if item]
    plan: list[RestorePlanItem] = []
    for record in info.records:
        if record.status == "error":
            continue
        if record.kind == _OP_KIND_RENAME:
            continue
        if not record.blob:
            continue
        target = _resolve_target(record, base)
        if target is None:
            plan.append(
                RestorePlanItem(
                    path=Path(record.path),
                    blob=record.blob,
                    rel_path=record.rel_path,
                    exists=False,
                    blocked="path escapes the session root",
                )
            )
            continue
        if filters and not any(item in record.rel_path or item in str(target) for item in filters):
            continue
        item = RestorePlanItem(
            path=target,
            blob=record.blob,
            rel_path=record.rel_path,
            mode=record.mode,
            uid=record.uid,
            gid=record.gid,
            exists=target.exists(),
        )
        if detect_drift:
            if target.is_file():
                item.current_sha256 = sha256_file(target)
                item.drifted = bool(record.sha256_after) and item.current_sha256 != record.sha256_after
                try:
                    item.snapshot_bytes = target.read_bytes()
                except OSError:
                    item.snapshot_bytes = None
            else:
                # A file that no longer exists has nothing to lose: restoring
                # it is strictly better than skipping it, so this is not drift.
                item.drifted = False
                item.snapshot_bytes = None
        plan.append(item)
    return plan


def _resolve_target(record: FileRecord, base: Path) -> Path | None:
    """Absolute restore target, or ``None`` when it would leave ``base``.

    A manifest is a file on disk: it can be edited by hand, by another tool, or
    by an attacker who can write into ``.sr/``.  It is therefore treated as
    untrusted input, and the rule is deliberately narrow:

    **A restore only ever writes inside the session root.**

    The relative path recorded at run time is resolved against ``base`` (the
    project root, or ``--target`` when the project moved) and must stay inside
    it.  The recorded *absolute* path is used only when no relative path was
    stored, and even then only if it resolves inside ``base`` — so pointing a
    manifest at ``/etc/passwd`` cannot be used to overwrite it.
    """
    resolved_base = base.resolve()
    relative = (record.rel_path or "").strip()
    if not relative:
        relative = str(record.path or "").strip()

    candidate = Path(relative)
    if candidate.is_absolute():
        # Refuse rather than silently re-anchor: an absolute path recorded on
        # another machine (or invented by a tampered manifest) must never be
        # turned into a write inside this project.  `sr rollback --target DIR`
        # is the explicit way to re-anchor a session.
        return None
    target = (resolved_base / candidate).resolve()
    try:
        target.relative_to(resolved_base)
    except ValueError:
        return None
    return target


def execute_restore(
    plan: Sequence[RestorePlanItem],
    store: SessionStore,
    session_directory: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> RestoreResult:
    """Apply a restore plan.  Never raises for individual file failures."""
    from .atomic import atomic_write_bytes

    result = RestoreResult()
    for item in plan:
        if item.blocked:
            result.skipped.append((item.path, item.blocked))
            continue
        if item.drifted and not force:
            result.drift_detected.append(item.path)
            result.skipped.append(
                (item.path, "file changed after the session (use --force to overwrite)")
            )
            continue
        try:
            data = _read_blob(session_directory, item.blob)
        except SessionError as exc:
            result.failed.append((item.path, str(exc)))
            continue
        if dry_run:
            result.restored.append(item.path)
            continue
        try:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(item.path, data, preserve_inode=True)
            if item.mode is not None:
                try:
                    os.chmod(item.path, item.mode)
                except OSError:
                    pass
            if item.uid is not None and item.gid is not None:
                try:
                    os.chown(item.path, item.uid, item.gid)
                except OSError:
                    pass
            result.restored.append(item.path)
        except OSError as exc:
            result.failed.append((item.path, exc.strerror or str(exc)))
    return result


def _read_blob(session_directory: Path, blob: str) -> bytes:
    path = session_directory / FILES_DIRNAME / blob[:2] / f"{blob}.dat"
    if not path.is_file():
        raise SessionError(
            f"backup blob missing for {blob[:12]}",
            hint=f"expected {path}; the session store may have been pruned",
        )
    data = path.read_bytes()
    if sha256_bytes(data) != blob:
        raise SessionError(f"backup blob is corrupt: {path}")
    return data


def prune_sessions(
    store: SessionStore,
    *,
    keep: int | None = None,
    keep_age: float | None = None,
    dry_run: bool = False,
) -> list[SessionInfo]:
    """Delete old sessions honouring ``--keep N`` and ``--keep-age``.

    ``keep`` semantics: ``None`` or ``0`` means "no count limit" (prune only by
    age), ``N > 0`` keeps the newest N.  A negative value disables pruning
    entirely.  ``keep_age`` is a maximum age in seconds.
    """
    sessions = store.list()
    if not sessions:
        return []
    if keep is not None and keep < 0 and keep_age is None:
        return []
    now = time.time()
    victims: list[SessionInfo] = []
    for index, info in enumerate(sessions):
        too_many = keep is not None and keep > 0 and index >= keep
        try:
            age = now - info.directory.stat().st_mtime
        except OSError:
            age = 0.0
        too_old = keep_age is not None and keep_age >= 0 and age > keep_age
        if too_many or too_old:
            victims.append(info)
    if dry_run:
        return victims
    for info in victims:
        shutil.rmtree(info.directory, ignore_errors=True)
    return victims


# ---------------------------------------------------------------------------
# State root resolution
# ---------------------------------------------------------------------------


def resolve_state_root(root: Path, override: str | os.PathLike[str] | None = None) -> Path:
    """Where sessions live.

    ``override`` wins (``--backup-dir``); otherwise ``<root>/.sr``.  The
    directory is *not* created here — a read-only run must not touch the disk.
    """
    if override:
        return Path(os.path.expanduser(str(override))).resolve()
    return (Path(root).resolve() / STATE_DIRNAME)


def store_for(root: Path, override: str | os.PathLike[str] | None = None) -> SessionStore:
    """Convenience: a :class:`SessionStore` for ``root``."""
    return SessionStore(resolve_state_root(root, override))

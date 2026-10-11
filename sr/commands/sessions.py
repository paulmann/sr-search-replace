"""``sr rollback`` and ``sr sessions`` — the audit trail commands."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..context import Context
from ..diffview import render_diff
from ..errors import SessionError, UsageError
from ..exit_codes import ExitCode
from ..session import (
    RestorePlanItem,
    SessionInfo,
    SessionStore,
    build_restore_plan,
    execute_restore,
    prune_sessions,
    store_for,
)
from ..util import format_duration, format_size, plural
from ..version import __version__

# ---------------------------------------------------------------------------
# sr rollback
# ---------------------------------------------------------------------------


def cmd_rollback(context: Context) -> int:
    """Restore files recorded in a session."""
    args = context.args
    reference = getattr(args, "session", None) or "latest"
    root = Path(os.path.expanduser(str(getattr(args, "target", ".") or "."))).resolve()
    store = store_for(root, getattr(args, "backup_dir", None))
    info = store.resolve(reference)

    console = context.console
    if info.dry_run:
        raise UsageError(
            f"session {info.id} was a dry run and recorded no changes",
            hint="run `sr sessions` to see the sessions that actually wrote files",
        )

    restore_root = root if str(root) != info.root else Path(info.root or root)
    only = [item for item in (getattr(args, "file_filter", None) or []) if item]
    plan = build_restore_plan(info, root=restore_root, only=only)
    renames = _undo_renames(info)

    if not plan and not renames:
        raise SessionError(
            f"session {info.id} has nothing to restore",
            hint="the run may have been a dry run or all files were filtered out",
        )

    drifted = [item for item in plan if item.drifted]
    missing_blob = [item for item in plan if item.blocked]

    if console.json_mode:
        document = {
            "session": info.to_dict(),
            "restore_root": str(restore_root),
            "dry_run": bool(getattr(args, "dry_run", False)),
            "files": [_plan_item_dict(item) for item in plan],
            "renames": [{"from": src, "to": dst} for src, dst in renames],
            "drifted": [str(item.path) for item in drifted],
            "blocked": [str(item.path) for item in missing_blob],
        }
        if getattr(args, "dry_run", False):
            console.emit_json(document)
            return int(ExitCode.OK)
        result = _perform_restore(context, info, store, plan, renames, restore_root)
        code = int(ExitCode.OK) if result.ok else int(ExitCode.ROLLBACK_FAILED)
        document["result"] = result.as_dict()
        document["exit_code"] = code
        console.emit_json(document)
        return code

    console.header(f"Rollback — session {info.id}")
    console.kv("Created", info.created_at)
    console.kv("Command", " ".join(info.command) or "(unknown)")
    if info.search:
        console.kv("Change", f"{info.search!r} → {info.replacement!r}")
    console.kv("Root", str(restore_root))
    console.kv("Files", plural(len(plan), "file"))
    if renames:
        console.kv("Renames", plural(len(renames), "rename"))
    console.kv("Session size", format_size(store.size(info.id)))

    if plan:
        console.write("")
        console.info("Files that will be restored:")
        for item in plan[:20]:
            marker = ""
            if item.blocked:
                marker = console.paint(f" [{item.blocked}]", "red")
            elif item.drifted:
                marker = console.paint(" [changed since the session]", "yellow")
            console.write(f"  {item.rel_path}{marker}")
        if len(plan) > 20:
            console.write(console.paint(f"  … and {len(plan) - 20} more", "dim"))
    for source, target in renames[:10]:
        console.write(f"  {_rel(Path(source), restore_root)} → {Path(target).name}")
    if len(renames) > 10:
        console.write(console.paint(f"  … and {len(renames) - 10} more", "dim"))

    if drifted and not getattr(args, "force", False):
        console.write("")
        console.warn(
            f"{plural(len(drifted), 'file')} changed after the session was recorded and will be skipped",
            hint="pass --force to restore them anyway (their current content will be lost)",
        )

    if getattr(args, "dry_run", False):
        console.write("")
        console.warn("dry run — nothing was restored")
        return int(ExitCode.OK)

    if not context.assume_yes and not getattr(args, "yes", False):
        question = f"Restore {plural(len(plan), 'file')}" + (
            f" and undo {plural(len(renames), 'rename')}?" if renames else "?"
        )
        if not context.confirm(question, default=False):
            console.info("rollback cancelled")
            return int(ExitCode.OK)

    result = _perform_restore(context, info, store, plan, renames, restore_root)

    console.write("")
    console.header("Result")
    console.kv("Restored", plural(len(result.restored), "file"))
    if getattr(result, "renamed", 0):
        console.kv("Renames undone", plural(result.renamed, "rename"))
    if result.skipped:
        console.kv("Skipped", plural(len(result.skipped), "file"))
    if result.failed:
        console.kv("Failed", console.paint(plural(len(result.failed), "file"), "red"))
    if result.snapshot_id:
        console.kv("Undo this rollback", console.paint(f"sr rollback {result.snapshot_id}", "cyan"))

    for path, reason in result.skipped[:10]:
        console.warn(f"{_rel(Path(path), restore_root)}: {reason}")
    if len(result.skipped) > 10:
        console.warn(f"… and {len(result.skipped) - 10} more skipped files")
    for path, reason in result.failed[:10]:
        console.error(f"{path}: {reason}")

    if not result.ok:
        return int(ExitCode.ROLLBACK_FAILED)
    if not result.restored and not getattr(result, "renamed", 0):
        if result.drift_detected:
            console.warn(
                "nothing was restored",
                hint="every file changed after the session; use --force to restore anyway",
            )
        else:
            console.warn("nothing was restored: the session recorded no restorable content")
        return int(ExitCode.OK)
    what: list[str] = []
    if result.restored:
        what.append(plural(len(result.restored), "file"))
    if result.renamed:
        what.append(plural(result.renamed, "rename"))
    console.success(f"session {info.short_id} rolled back ({', '.join(what) or 'nothing to do'})")
    return int(ExitCode.OK)


def _plan_item_dict(item: RestorePlanItem) -> dict[str, Any]:
    return {
        "path": str(item.path),
        "rel_path": item.rel_path,
        "exists": item.exists,
        "drifted": item.drifted,
        "blocked": item.blocked,
        "current_sha256": item.current_sha256,
    }


def _undo_renames(info: SessionInfo) -> list[tuple[str, str]]:
    """``(current_path, restore_path)`` pairs, in reverse order."""
    pairs: list[tuple[str, str]] = []
    for record in reversed(info.records):
        if record.kind != "rename" or not record.renamed_to:
            continue
        pairs.append((record.renamed_to, record.path))
    return pairs


def _perform_restore(
    context: Context,
    info: SessionInfo,
    store: SessionStore,
    plan: Sequence[RestorePlanItem],
    renames: Sequence[tuple[str, str]],
    restore_root: Path,
):
    """Safety snapshot → restore files → undo renames.

    The snapshot is written *before* anything is restored, while the files still
    hold their pre-rollback content.  That ordering is what makes the snapshot a
    faithful inverse: its ``sha256_after`` describes the state just before this
    rollback, so rolling the snapshot back reproduces exactly that state.
    """
    force = bool(getattr(context.args, "force", False))
    snapshot = (
        _open_safety_snapshot(context, info, restore_root)
        if context.value("safety_snapshot", True)
        else None
    )

    if snapshot is not None:
        _record_snapshot_contents(snapshot, plan, restore_root)
        for current, original in renames:
            # Journal the inverse mapping so renames can be undone too.
            snapshot.record_rename(
                source=Path(original),
                target=Path(current),
                rel_path=_rel(Path(original), restore_root),
            )

    result = execute_restore(plan, store, info.directory, force=force)

    rename_failures: list[tuple[Path, str]] = []
    for current, original in renames:
        current_path, original_path = Path(current), Path(original)
        if not current_path.exists():
            rename_failures.append((current_path, "the renamed file no longer exists"))
            continue
        try:
            original_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(current_path, original_path)
            result.renamed += 1
        except OSError as exc:
            rename_failures.append((current_path, exc.strerror or str(exc)))

    result.failed.extend(rename_failures)
    if snapshot is not None:
        result.snapshot_id = _close_safety_snapshot(snapshot, result, restore_root)
    return result


def _close_safety_snapshot(snapshot, result, restore_root: Path) -> str:
    """Finalise the safety snapshot so that it inverts *this* rollback.

    The snapshot was recorded before the restore, while the files still held
    their pre-rollback content.  For it to be a correct inverse, its
    ``sha256_after`` must describe the post-rollback state, and a rename entry
    must be expressed from the post-rollback point of view.  Both are fixed up
    here, before the manifest is written.
    """
    from ..util import sha256_file

    for record in snapshot.records:
        if record.kind == "rename":
            # Recorded as (original → current) before the undo; after the undo
            # the file lives at `original` again, so swap the two.
            record.path, record.renamed_to = record.renamed_to, record.path
            continue
        path = Path(record.path)
        if not path.is_file():
            continue
        record.sha256_after = sha256_file(path)
        try:
            record.size_after = path.stat().st_size
        except OSError:  # pragma: no cover
            pass

    try:
        info = snapshot.close(complete=not result.failed)
    except OSError:  # pragma: no cover - disk full
        snapshot.abort()
        return ""
    if not info.records:
        import shutil

        shutil.rmtree(info.directory, ignore_errors=True)
        return ""
    return info.id


def _open_safety_snapshot(context: Context, info: SessionInfo, restore_root: Path):
    """Create (but do not populate) the pre-rollback snapshot session."""
    store = store_for(restore_root, getattr(context.args, "backup_dir", None))
    snapshot = store.create(
        kind="rollback",
        root=restore_root,
        command=[*context.full_command(), f"# undoing {info.id}"],
        version=__version__,
    )
    try:
        snapshot.open(lock=False)
    except OSError:
        snapshot.abort()
        return None
    return snapshot


def _record_snapshot_contents(snapshot, plan: Sequence[RestorePlanItem], restore_root: Path) -> None:
    """Store the current bytes of every file the rollback is about to overwrite.

    ``original_bytes`` and ``new_bytes`` are the same buffer: the snapshot's job
    is to be able to put the file back the way it is *right now*.
    ``record_replace`` hashes the live file for ``sha256_after``, which is why
    this must run before the restore.
    """
    for item in plan:
        if item.blocked or item.snapshot_bytes is None:
            continue
        try:
            snapshot.record_replace(
                path=item.path,
                rel_path=item.rel_path or _rel(item.path, restore_root),
                original_bytes=item.snapshot_bytes,
                new_bytes=item.snapshot_bytes,
                replacements=0,
                encoding="binary",
            )
        except OSError:  # pragma: no cover - disk full
            continue


def _rel(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(str(path), str(root))
    except ValueError:  # pragma: no cover
        return str(path)


# ---------------------------------------------------------------------------
# sr sessions
# ---------------------------------------------------------------------------


def cmd_sessions(context: Context) -> int:
    """List, inspect, prune and delete sessions."""
    args = context.args
    action = str(getattr(args, "action", "list") or "list")
    root = Path(os.path.expanduser(str(getattr(args, "target", ".") or "."))).resolve()
    store = store_for(root, getattr(args, "backup_dir", None))
    console = context.console

    if action == "size":
        total = store.total_size()
        if console.json_mode:
            console.emit_json({"state_root": str(store.state_root), "bytes": total, "human": format_size(total)})
        else:
            console.kv("State root", store.state_root)
            console.kv("Sessions", len(store.list()))
            console.kv("Disk usage", format_size(total))
        return int(ExitCode.OK)

    if action == "show":
        return _show_session(context, store, getattr(args, "session", None) or "latest")

    if action == "delete":
        reference = getattr(args, "session", None)
        if not reference:
            raise UsageError("sessions delete needs a session id", hint="run `sr sessions` to list them")
        info = store.resolve(reference)
        if not context.assume_yes and not getattr(args, "yes", False):
            if not context.confirm(f"Delete session {info.id} ({format_size(store.size(info.id))})?", default=False):
                console.info("nothing deleted")
                return int(ExitCode.OK)
        store.delete(info.id, yes=True)
        console.success(f"deleted {info.id}")
        return int(ExitCode.OK)

    if action == "prune":
        keep = getattr(args, "keep", None)
        keep_age_raw = getattr(args, "keep_age", None)
        from ..options import BY_NAME

        keep_age = BY_NAME["keep-age"].coerce(keep_age_raw) if keep_age_raw else None
        if keep is None and keep_age is None:
            keep = int(context.value("keep", 20) or 20)
        removed = prune_sessions(store, keep=keep, keep_age=keep_age)
        if console.json_mode:
            console.emit_json({"pruned": [item.id for item in removed], "kept": keep})
        else:
            if removed:
                console.success(f"pruned {plural(len(removed), 'session')}")
                for item in removed[:10]:
                    console.write(f"  {item.id}")
            else:
                console.info("nothing to prune")
        return int(ExitCode.OK)

    return _list_sessions(context, store, root)


def _list_sessions(context: Context, store: SessionStore, root: Path) -> int:
    console = context.console
    limit = int(getattr(context.args, "limit", 20) or 20)
    sessions = store.list(limit=limit)
    if console.json_mode:
        console.emit_json(
            {
                "state_root": str(store.state_root),
                "count": len(sessions),
                "sessions": [item.to_dict() for item in sessions],
            }
        )
        return int(ExitCode.OK)
    if not sessions:
        console.info(f"no sessions under {store.state_root}")
        console.write("")
        console.write("Sessions are created by every mutating run, e.g.:")
        console.write("  sr replace \"old\" \"new\" -p '*.py'")
        return int(ExitCode.OK)

    console.header(f"Sessions in {store.state_root}")
    width_id = max(len(item.id) for item in sessions)
    for index, item in enumerate(sessions, start=1):
        marker = "→" if index == 1 else " "
        flag = "" if item.complete else console.paint(" (incomplete)", "yellow")
        dry = console.paint(" (dry run)", "dim") if item.dry_run else ""
        # Pad the plain string, then colour it: padding an ANSI string would
        # count escape sequences as visible characters.
        console.write(
            f"{marker} {str(index).rjust(3)}  "
            f"{console.paint(item.id.ljust(width_id), 'cyan')}  "
            f"{str(item.files).rjust(5)} files  "
            f"{str(item.replacements).rjust(6)} repl  "
            f"{_describe(item)}{flag}{dry}"
        )
    console.write("")
    console.info("Inspect one:  sr sessions show <id>")
    console.info("Undo one:     sr rollback <id>")
    console.info("Disk usage:   sr sessions size")
    return int(ExitCode.OK)


def _describe(info: SessionInfo) -> str:
    if info.kind == "rollback":
        return "rollback"
    if info.search:
        return f"{info.search!r} → {info.replacement!r}"
    return info.kind


def _show_session(context: Context, store: SessionStore, reference: str) -> int:
    console = context.console
    info = store.resolve(reference)
    include_files = bool(getattr(context.args, "files", False))
    if console.json_mode:
        console.emit_json(info.to_dict(include_files=True))
        return int(ExitCode.OK)

    console.header(f"Session {info.id}")
    console.kv("Created", info.created_at)
    console.kv("Finished", info.finished_at or "(not finished)")
    console.kv("Duration", format_duration(info.duration) if info.duration else "—")
    console.kv("Kind", info.kind + (" (dry run)" if info.dry_run else ""))
    console.kv("Complete", "yes" if info.complete else console.paint("no — interrupted", "yellow"))
    console.kv("Directory", info.directory)
    console.kv("Root", info.root)
    console.kv("Command", " ".join(info.command) or "—")
    if info.search:
        console.kv("Search", repr(info.search))
        console.kv("Replace", repr(info.replacement))
    console.kv("Pattern", info.pattern_kind)
    console.kv("Files", info.files)
    console.kv("Replacements", info.replacements)
    console.kv("Bytes before", format_size(info.bytes_before))
    console.kv("Bytes after", format_size(info.bytes_after))
    console.kv("Store size", format_size(store.size(info.id)))
    console.kv("sr version", info.version or "—")
    if info.options:
        console.kv("Options", json.dumps(info.options, ensure_ascii=False)[: max(60, console.width - 24)])

    if info.options.get("pattern"):
        console.kv("Matching", str(info.options["pattern"]))

    if include_files and info.records:
        console.write("")
        console.info("Files:")
        for record in info.records:
            status = record.status
            extra = f"  {record.replacements} repl" if record.replacements else ""
            console.write(f"  {console.paint(status, 'green' if status == 'changed' else 'yellow'):<18} "
                          f"{record.rel_path}{extra}")
            if record.error:
                console.write(f"      {console.paint(record.error, 'red')}")
    return int(ExitCode.OK)


def show_session_diff(context: Context, reference: str) -> int:
    """``sr diff --session <ref>`` — replay the stored diffs."""
    args = context.args
    root = Path(os.path.expanduser(str(getattr(args, "target", ".") or "."))).resolve()
    store = store_for(root, getattr(args, "backup_dir", None))
    info = store.resolve(reference)
    console = context.console

    patches: list[str] = []
    for record in info.records:
        if not record.diff_path:
            continue
        diff_file = info.directory / record.diff_path
        if diff_file.is_file():
            patches.append(diff_file.read_text(encoding="utf-8", errors="replace"))

    if console.json_mode:
        console.emit_json(
            {
                "session": info.to_dict(),
                "patches": patches,
                "files": [record.to_dict() for record in info.records],
            }
        )
        return int(ExitCode.OK)

    if not patches:
        console.warn(
            f"session {info.id} stored no diffs",
            hint="diffs are recorded for in-place runs; use `sr diff SEARCH REPLACE` for a live preview",
        )
        return int(ExitCode.NO_MATCH)

    console.header(f"Diffs recorded by session {info.id}")
    for patch in patches:
        console.write(render_diff(patch.rstrip("\n").splitlines(), console))
        console.write("")
    console.info(f"{plural(len(patches), 'file')} in this session")
    return int(ExitCode.OK)

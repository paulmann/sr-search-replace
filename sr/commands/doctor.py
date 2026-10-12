"""``sr doctor`` — verify that this installation can actually do its job.

Every check returns a :class:`Check` with a status, a one-line detail and an
optional hint.  The command exits non-zero when any check fails, which makes it
usable as a CI gate ("is sr correctly installed on this runner?").
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as config_module
from ..atomic import atomic_write_bytes
from ..console import Verbosity
from ..context import Context
from ..exit_codes import ExitCode
from ..session import SessionLock, store_for
from ..util import format_size
from ..version import __version__
from .update import DEFAULT_REPO, InstallType, detect_installation, latest_release

MIN_PYTHON = (3, 10)


class Status:
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(slots=True)
class Check:
    """One diagnostic result."""

    name: str
    status: str
    detail: str = ""
    hint: str = ""
    group: str = "general"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "group": self.group,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.data:
            payload["data"] = self.data
        return payload


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)
    duration: float = 0.0

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        return check

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if check.status == Status.FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status == Status.WARN]

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "ok": self.ok,
            "duration_seconds": round(self.duration, 3),
            "totals": {
                "checks": len(self.checks),
                "ok": sum(1 for item in self.checks if item.status == Status.OK),
                "warn": len(self.warnings),
                "fail": len(self.failed),
                "skip": sum(1 for item in self.checks if item.status == Status.SKIP),
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def cmd_doctor(context: Context) -> int:
    """Run every diagnostic and report."""
    console = context.console
    root = Path(os.path.expanduser(str(getattr(context.args, "target", ".") or "."))).resolve()
    offline = bool(getattr(context.args, "offline", False))
    started = time.monotonic()
    report = Report()

    checks: Sequence[Callable[[Context, Path, Report], None]] = (
        _check_python,
        _check_installation,
        _check_encoding,
        _check_dependencies,
        _check_config,
        _check_state_store,
        _check_filesystem,
        _check_locking,
        _check_environment,
        _check_disk_space,
        _check_completions,
    )
    for check in checks:
        try:
            check(context, root, report)
        except Exception as exc:
            report.add(
                Check(
                    name=check.__name__.lstrip("_"),
                    status=Status.FAIL,
                    detail=f"the check itself raised {type(exc).__name__}: {exc}",
                    group="internal",
                )
            )
    if not offline:
        try:
            _check_network(context, root, report)
        except Exception as exc:
            report.add(
                Check("network", Status.SKIP, f"probe failed: {exc}", group="network")
            )

    report.duration = time.monotonic() - started

    if console.json_mode:
        console.emit_json(report.to_dict())
    else:
        _render(report, console)
    return int(ExitCode.OK) if report.ok else int(ExitCode.ERROR)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_python(context: Context, root: Path, report: Report) -> None:
    version = sys.version_info
    if version < MIN_PYTHON:
        report.add(
            Check(
                "python",
                Status.FAIL,
                f"Python {platform.python_version()} is older than the required "
                f"{'.'.join(str(part) for part in MIN_PYTHON)}",
                hint="install a newer Python, or point the sr launcher at it",
                group="runtime",
                data={"version": platform.python_version()},
            )
        )
        return
    implementation = platform.python_implementation()
    report.add(
        Check(
            "python",
            Status.OK,
            f"{implementation} {platform.python_version()}",
            group="runtime",
            data={"version": platform.python_version(), "implementation": implementation},
        )
    )


def _check_installation(context: Context, root: Path, report: Report) -> None:
    installation = detect_installation()
    status = Status.OK
    hint = ""
    detail = f"{installation.kind}: {installation.details or installation.executable}"
    if installation.kind == InstallType.UNKNOWN:
        status = Status.WARN
        hint = "self-update will not work; reinstall with install.sh or pipx"
    elif installation.kind == InstallType.STANDALONE and not installation.writable:
        status = Status.WARN
        detail += " (not writable)"
        hint = f"`sr update` needs write access to {installation.executable}"
    report.add(
        Check(
            "installation",
            status,
            detail,
            hint=hint,
            group="installation",
            data=installation.to_dict(),
        )
    )
    if installation.package_version and installation.package_version != __version__:
        report.add(
            Check(
                "version-consistency",
                Status.WARN,
                f"the package metadata says {installation.package_version} but the code says {__version__}",
                hint="reinstall sr so both agree",
                group="installation",
            )
        )


def _check_encoding(context: Context, root: Path, report: Report) -> None:
    encoding = sys.getfilesystemencoding()
    preferred = __import__("locale").getpreferredencoding(False)
    status = Status.OK
    hint = ""
    if encoding.lower() not in {"utf-8", "utf8"}:
        status = Status.WARN
        hint = "set PYTHONUTF8=1 or LANG=C.UTF-8 for predictable handling of non-ASCII file names"
    report.add(
        Check(
            "encoding",
            status,
            f"filesystem={encoding}, preferred={preferred}, stdout={getattr(sys.stdout, 'encoding', '?')}",
            hint=hint,
            group="runtime",
        )
    )


def _check_dependencies(context: Context, root: Path, report: Report) -> None:
    """sr is stdlib-only; this check proves it and reports optional helpers."""
    missing = [name for name in ("re", "json", "difflib", "hashlib", "argparse") if not _importable(name)]
    if missing:
        report.add(
            Check(
                "stdlib",
                Status.FAIL,
                f"required modules unavailable: {', '.join(missing)}",
                hint="the Python installation is incomplete",
                group="runtime",
            )
        )
        return
    optional = {name: _importable(name) for name in ("readline", "zlib", "bz2", "lzma")}
    report.add(
        Check(
            "stdlib",
            Status.OK,
            "all required modules present (no third-party dependencies)",
            group="runtime",
            data={"optional": optional},
        )
    )
    tools = {name: shutil.which(name) for name in ("git", "jq", "patch", "file")}
    report.add(
        Check(
            "external-tools",
            Status.OK,
            "optional helpers: " + ", ".join(f"{name}={'yes' if path else 'no'}" for name, path in tools.items()),
            hint="none of them is required; `jq` merely makes --json output nicer to read",
            group="runtime",
            data={name: bool(path) for name, path in tools.items()},
        )
    )


def _importable(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _check_config(context: Context, root: Path, report: Report) -> None:
    paths = config_module.config_search_path(root)
    loaded: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            config_module.load_file(path, [])
            loaded.append(f"{path} (ok)")
        except Exception as exc:
            report.add(
                Check(
                    "config",
                    Status.FAIL,
                    f"{path}: {exc}",
                    hint="fix the file or remove it; `sr config validate` shows the details",
                    group="configuration",
                )
            )
            return
    report.add(
        Check(
            "config",
            Status.OK,
            ", ".join(loaded) if loaded else "no config files (using built-in defaults)",
            group="configuration",
            data={"files": [str(path) for path in paths if path.is_file()], "search_path": [str(p) for p in paths]},
        )
    )
    effective = context.config
    odd = [
        key
        for key in ("max_size", "keep", "confirm_threshold")
        if isinstance(effective.get(key), int) and effective.get(key) < 0
    ]
    if odd:
        report.add(
            Check(
                "config-values",
                Status.WARN,
                f"negative values for: {', '.join(odd)}",
                hint="these options must be >= 0",
                group="configuration",
            )
        )


def _check_state_store(context: Context, root: Path, report: Report) -> None:
    store = store_for(root, context.value("backup-dir"))
    if not store.sessions_dir.exists():
        report.add(
            Check(
                "session-store",
                Status.OK,
                f"no sessions yet (would be created under {store.sessions_dir})",
                group="state",
                data={"state_root": str(store.state_root)},
            )
        )
        return
    sessions = store.list()
    broken = [info.id for info in sessions if not info.complete]
    size = store.total_size()
    status = Status.WARN if broken else Status.OK
    report.add(
        Check(
            "session-store",
            status,
            f"{len(sessions)} session(s), {format_size(size)}"
            + (f", {len(broken)} incomplete" if broken else ""),
            hint="incomplete sessions come from interrupted runs; `sr sessions prune` cleans up"
            if broken
            else "",
            group="state",
            data={"sessions": len(sessions), "bytes": size, "incomplete": broken},
        )
    )
    keep = context.value("keep")
    if isinstance(keep, int) and keep > 0 and len(sessions) > keep:
        report.add(
            Check(
                "retention",
                Status.WARN,
                f"{len(sessions)} sessions stored but keep={keep}",
                hint="run `sr sessions prune` (pruning happens automatically after a mutating run)",
                group="state",
            )
        )


def _check_filesystem(context: Context, root: Path, report: Report) -> None:
    capabilities: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="sr-doctor-") as workspace:
        base = Path(workspace)
        target = base / "file.txt"
        target.write_text("original", encoding="utf-8")

        # atomic replace
        try:
            atomic_write_bytes(target, b"replaced")
            capabilities["atomic_replace"] = target.read_text(encoding="utf-8") == "replaced"
        except OSError:
            capabilities["atomic_replace"] = False

        # inode preservation
        inode_before = target.stat().st_ino
        try:
            atomic_write_bytes(target, b"again", preserve_inode=True)
            capabilities["preserve_inode"] = target.stat().st_ino == inode_before
        except OSError:
            capabilities["preserve_inode"] = False

        # metadata preservation
        os.utime(target, ns=(1_000_000_000, 1_000_000_000))
        try:
            atomic_write_bytes(target, b"meta")
            capabilities["preserve_mtime"] = int(target.stat().st_mtime) == 1
        except OSError:
            capabilities["preserve_mtime"] = False

        # symlinks
        link = base / "link.txt"
        try:
            os.symlink(target, link)
            capabilities["symlinks"] = link.is_symlink()
        except (OSError, NotImplementedError, AttributeError):
            capabilities["symlinks"] = False

        # hard links
        hard = base / "hard.txt"
        try:
            os.link(target, hard)
            capabilities["hardlinks"] = hard.stat().st_ino == target.stat().st_ino
        except (OSError, NotImplementedError, AttributeError):
            capabilities["hardlinks"] = False

        # permissions
        try:
            os.chmod(target, 0o640)
            capabilities["chmod"] = (target.stat().st_mode & 0o777) == 0o640
        except OSError:
            capabilities["chmod"] = False

        # unicode file names
        unicode_name = base / "ünicode-тест.txt"
        try:
            unicode_name.write_text("x", encoding="utf-8")
            capabilities["unicode_names"] = unicode_name.exists()
        except OSError:
            capabilities["unicode_names"] = False

    missing = [name for name, value in capabilities.items() if not value]
    status = Status.OK if not missing else Status.WARN
    report.add(
        Check(
            "filesystem",
            status,
            "all capabilities available"
            if not missing
            else f"unavailable: {', '.join(missing)}",
            hint="inode preservation and hard links are optional; atomic replace is not"
            if "atomic_replace" not in missing
            else "without atomic replace, an interrupted write can truncate a file",
            group="filesystem",
            data=capabilities,
        )
    )
    if not capabilities.get("atomic_replace"):
        report.checks[-1].status = Status.FAIL


def _check_locking(context: Context, root: Path, report: Report) -> None:
    with tempfile.TemporaryDirectory(prefix="sr-lock-") as workspace:
        lock_path = Path(workspace) / ".lock"
        first = SessionLock(lock_path)
        try:
            first.acquire()
        except Exception as exc:
            report.add(
                Check("locking", Status.WARN, f"cannot create a lock file: {exc}", group="filesystem")
            )
            return
        second = SessionLock(lock_path)
        blocked = False
        try:
            second.acquire()
        except Exception:
            blocked = True
        finally:
            first.release()
    report.add(
        Check(
            "locking",
            Status.OK if blocked else Status.WARN,
            "advisory locking works" if blocked else "a second sr process was NOT blocked",
            hint="" if blocked else "the filesystem may not support O_EXCL; pass --no-lock explicitly",
            group="filesystem",
        )
    )


def _check_environment(context: Context, root: Path, report: Report) -> None:
    from .update import update_check_enabled

    interesting = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("SR_") or key in {"NO_COLOR", "CLICOLOR_FORCE", "TERM", "LANG", "LC_ALL"}
    }
    report.add(
        Check(
            "environment",
            Status.OK,
            ", ".join(f"{key}={value}" for key, value in sorted(interesting.items())) or "no SR_* variables set",
            group="configuration",
            data={"variables": interesting, "update_check": update_check_enabled(context)},
        )
    )


def _check_disk_space(context: Context, root: Path, report: Report) -> None:
    try:
        usage = shutil.disk_usage(str(root))
    except OSError as exc:
        report.add(Check("disk", Status.SKIP, f"cannot stat {root}: {exc}", group="filesystem"))
        return
    free_percent = (usage.free / usage.total * 100) if usage.total else 0
    status = Status.OK if free_percent > 5 else Status.WARN
    report.add(
        Check(
            "disk",
            status,
            f"{format_size(usage.free)} free of {format_size(usage.total)} ({free_percent:.1f}%)",
            hint="sessions store a copy of every original file; low disk space can abort a run"
            if status == Status.WARN
            else "",
            group="filesystem",
            data={"free": usage.free, "total": usage.total},
        )
    )


def _check_completions(context: Context, root: Path, report: Report) -> None:
    shell = os.environ.get("SHELL", "")
    name = Path(shell).name if shell else ""
    if name not in {"bash", "zsh", "fish"}:
        report.add(
            Check("completions", Status.SKIP, f"unknown shell {name or '(none)'}", group="shell")
        )
        return
    installed = _completion_installed(name)
    report.add(
        Check(
            "completions",
            Status.OK if installed else Status.WARN,
            f"{name} completion {'installed' if installed else 'not installed'}",
            hint="" if installed else f"run `sr completions {name} --install`",
            group="shell",
        )
    )


def _completion_installed(shell: str) -> bool:
    home = Path.home()
    candidates = {
        "bash": [home / ".local/share/bash-completion/completions/sr", home / ".bash_completion"],
        "zsh": [home / ".zfunc/_sr", home / ".oh-my-zsh/completions/_sr"],
        "fish": [home / ".config/fish/completions/sr.fish"],
    }.get(shell, [])
    for path in candidates:
        if path.is_file():
            try:
                return "sr" in path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return False


def _check_network(context: Context, root: Path, report: Report) -> None:
    repo = os.environ.get("SR_UPDATE_REPO", DEFAULT_REPO)
    started = time.monotonic()
    try:
        release = latest_release(repo=repo, timeout=6.0)
    except Exception as exc:
        report.add(
            Check(
                "network",
                Status.WARN,
                f"cannot reach the release feed: {exc}",
                hint="offline mode still works; `sr update` will fail until the network is back",
                group="network",
            )
        )
        return
    elapsed = time.monotonic() - started
    newer = release.version != __version__
    report.add(
        Check(
            "network",
            Status.OK,
            f"release feed reachable in {elapsed * 1000:.0f}ms; latest is {release.version}"
            + (" (newer than installed)" if newer else " (up to date)"),
            group="network",
            data=release.to_dict(),
        )
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_ICONS = {
    Status.OK: ("✓", "green"),
    Status.WARN: ("!", "yellow"),
    Status.FAIL: ("✗", "red"),
    Status.SKIP: ("·", "dim"),
}


#: Display order of the check groups.
GROUP_ORDER = ("runtime", "installation", "configuration", "state", "filesystem", "network", "shell", "internal")


def _grouped(checks: Sequence[Check]) -> list[Check]:
    """Sort checks by group (stable inside a group) so the report reads cleanly."""
    order = {name: index for index, name in enumerate(GROUP_ORDER)}
    return sorted(checks, key=lambda check: (order.get(check.group, 99), check.name))


def _render(report: Report, console) -> None:
    console.header(f"sr doctor — {__version__}")
    current_group = ""
    for check in _grouped(report.checks):
        if check.group != current_group:
            current_group = check.group
            console.write("")
            console.write(console.paint(f"  {current_group.upper()}", "bold"))
        icon, color = _ICONS.get(check.status, ("?", "dim"))
        console.write(f"  {console.paint(icon, color)} {check.name.ljust(18)} {check.detail}")
        if check.hint and console.verbosity >= Verbosity.NORMAL:
            console.write(f"    {console.paint(check.hint, 'dim')}")
        if console.verbosity >= Verbosity.DEBUG and check.data:
            console.write(f"    {console.paint(json.dumps(check.data, default=str)[:400], 'dim')}")
    console.write("")
    console.header("Result")
    console.kv("Checks", len(report.checks))
    console.kv("Passed", sum(1 for item in report.checks if item.status == Status.OK))
    console.kv("Warnings", len(report.warnings))
    console.kv("Failures", len(report.failed))
    console.kv("Elapsed", f"{report.duration * 1000:.0f}ms")
    if report.ok:
        console.success("the installation looks healthy")
    else:
        console.error("fix the failures above before relying on sr")

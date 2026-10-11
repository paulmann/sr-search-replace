"""``sr selftest`` — an end-to-end smoke test of the installed sr.

The point of this command is to answer one question after an install or an
upgrade: *does this sr actually work here?*  It builds a small project in a
temporary directory and exercises the whole pipeline — discovery, literal and
regex matching, encoding and line-ending preservation, in-place writing, the
session store, drift detection and rollback — then verifies the bytes on disk.

It never touches the user's files, never uses the network, and takes well under
a second, which makes it safe to put in a Dockerfile or a CI job.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import discovery
from ..context import Context
from ..exit_codes import ExitCode
from ..pattern import build_spec
from ..replace import ReplaceOptions, run_replace
from ..session import build_restore_plan, execute_restore, store_for
from ..version import __version__


@dataclass(slots=True)
class Case:
    """One assertion of the smoke test."""

    name: str
    detail: str = ""
    passed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "error": self.error}


@dataclass(slots=True)
class SelfTestReport:
    cases: list[Case] = field(default_factory=list)
    duration: float = 0.0
    workspace: str = ""

    @property
    def ok(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def failed(self) -> list[Case]:
        return [case for case in self.cases if not case.passed]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": __version__,
            "python": sys.version.split()[0],
            "ok": self.ok,
            "duration_seconds": round(self.duration, 3),
            "totals": {"cases": len(self.cases), "passed": sum(1 for c in self.cases if c.passed),
                       "failed": len(self.failed)},
            "cases": [case.to_dict() for case in self.cases],
            "workspace": self.workspace,
        }


def cmd_selftest(context: Context) -> int:
    """Run the smoke test and report."""
    console = context.console
    quick = bool(getattr(context.args, "quick", False))
    keep = bool(getattr(context.args, "keep", False))
    report = SelfTestReport()
    started = time.monotonic()

    workspace = Path(tempfile.mkdtemp(prefix="sr-selftest-"))
    report.workspace = str(workspace)
    try:
        _build_fixture(workspace)
        checks: list[Callable[[Path, SelfTestReport], None]] = [
            _check_discovery,
            _check_literal_replace,
            _check_session_created,
            _check_regex_replace,
            _check_encoding_preserved,
            _check_line_endings_preserved,
            _check_binary_skipped,
            _check_rollback,
            _check_drift_detection,
        ]
        if not quick:
            checks += [
                _check_dry_run_has_no_side_effects,
                _check_write_to_dir,
                _check_write_stdout,
                _check_rename,
                _check_no_match_exit,
                _check_unicode_names,
                _check_json_document,
                _check_line_range,
                _check_word_boundary,
                _check_retention,
            ]
        for check in checks:
            case = Case(name=check.__name__.lstrip("_").replace("_", " "))
            report.cases.append(case)
            try:
                check(workspace, report)
                case.passed = True
            except AssertionError as exc:
                case.error = str(exc) or "assertion failed"
            except Exception as exc:
                case.error = f"{type(exc).__name__}: {exc}"
    finally:
        report.duration = time.monotonic() - started
        if keep:
            context.console.warn(f"workspace kept at {workspace}")
        else:
            shutil.rmtree(workspace, ignore_errors=True)

    if console.json_mode:
        console.emit_json(report.to_dict())
    else:
        _render(report, context)
    return int(ExitCode.OK) if report.ok else int(ExitCode.SELFTEST_FAILED)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
#
# Every check writes and reads its *own* file with a unique marker.  That keeps
# the checks order-independent: a failure in one cannot cascade into the next,
# which matters because this command is the first thing a user runs after a
# broken install.


def _build_fixture(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text('NAME = "old"\nprint(NAME)\n', encoding="utf-8")
    (root / "README.md").write_text("# old project\n", encoding="utf-8")
    (root / "skip" / "node_modules").mkdir(parents=True)
    (root / "skip" / "node_modules" / "dep.js").write_text("old\n", encoding="utf-8")


def _options(root: Path, search: str, replacement: str, **kwargs) -> ReplaceOptions:
    """Engine options for one check; ``keep=0`` disables retention pruning."""
    patterns = kwargs.pop("patterns", ["*.py", "*.md", "*.txt"])
    discovery_options = discovery.DiscoveryOptions(roots=[root], patterns=patterns)
    discovery_options.normalise()
    defaults: dict = {
        "spec": build_spec(search, replacement),
        "discovery_options": discovery_options,
        "root": root,
        "apply_changes": True,
        "command": ["sr", "selftest"],
        "version": __version__,
        "keep": 0,
    }
    defaults.update(kwargs)
    return ReplaceOptions(**defaults)


def _write(root: Path, name: str, content: str | bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_discovery(root: Path, report: SelfTestReport) -> None:
    _write(root, "discover/one.txt", "old\n")
    _write(root, "discover/two.md", "old\n")
    options = _options(root, "old", "new", patterns=["discover/*"])
    found = discovery.discover(options.discovery_options)
    names = {path.name for path in found.files}
    assert "one.txt" in names, f"one.txt not discovered: {sorted(names)}"
    assert "dep.js" not in names, "node_modules was not pruned"
    report.cases[-1].detail = f"{found.count} files, {len(found.skipped)} filtered"


def _check_literal_replace(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "literal/notes.txt", "keep this old line\nold again\n")
    summary = run_replace(_options(root, "old", "new", patterns=["literal/*.txt"]))
    text = target.read_text(encoding="utf-8")
    assert text == "keep this new line\nnew again\n", f"unexpected content: {text!r}"
    assert summary.replacements == 2, f"expected 2 replacements, got {summary.replacements}"
    assert summary.exit_hint == 0, f"unexpected exit hint {summary.exit_hint}"
    report.cases[-1].detail = f"{summary.replacements} replacements in {summary.duration * 1000:.0f}ms"


def _check_session_created(root: Path, report: SelfTestReport) -> None:
    store = store_for(root, None)
    sessions = store.list()
    assert sessions, "no session was created"
    info = store.load(sessions[0].id)
    assert info.records, "the session recorded no files"
    assert info.complete, "the session was not marked complete"
    blob = info.records[0].blob
    blob_path = info.directory / "files" / blob[:2] / f"{blob}.dat"
    assert blob_path.is_file(), f"blob missing: {blob_path}"
    report.cases[-1].detail = f"{info.id}: {len(info.records)} file(s)"


def _check_regex_replace(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "regex/setup.py", 'version = "1.2.3"\n')
    spec = build_spec(r'version = "(\d+\.\d+)\.\d+"', r'version = "\1.9"', kind="regex")
    summary = run_replace(
        _options(root, "", "", spec=spec, patterns=["regex/*.py"])
    )
    text = target.read_text(encoding="utf-8")
    assert text == 'version = "1.2.9"\n', f"regex replacement failed: {text!r}"
    assert summary.replacements == 1, f"expected 1 replacement, got {summary.replacements}"
    report.cases[-1].detail = text.strip()


def _check_encoding_preserved(root: Path, report: SelfTestReport) -> None:
    utf8 = _write(root, "encoding/utf8.txt", "café old\n")
    cp1252 = root / "encoding" / "cp1252.txt"
    cp1252.parent.mkdir(parents=True, exist_ok=True)
    cp1252.write_bytes("café old".encode("cp1252"))
    bom = root / "encoding" / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbfhello old\n")

    run_replace(_options(root, "old", "new", patterns=["encoding/utf8.txt"]))
    assert utf8.read_bytes() == "café new\n".encode(), "utf-8 content changed shape"

    run_replace(
        _options(root, "old", "new", patterns=["encoding/cp1252.txt"], encoding="cp1252")
    )
    assert cp1252.read_bytes() == "café new".encode("cp1252"), (
        f"cp1252 not preserved: {cp1252.read_bytes()!r}"
    )

    run_replace(_options(root, "old", "new", patterns=["encoding/bom.txt"]))
    assert bom.read_bytes().startswith(b"\xef\xbb\xbf"), "the UTF-8 BOM was dropped"
    report.cases[-1].detail = "utf-8, cp1252 and BOM round trips"


def _check_line_endings_preserved(root: Path, report: SelfTestReport) -> None:
    crlf = _write(root, "endings/crlf.txt", b"old one\r\nold two\r\n")
    lf = _write(root, "endings/lf.txt", "old one\nold two\n")
    run_replace(_options(root, "old", "new", patterns=["endings/*.txt"]))
    assert crlf.read_bytes() == b"new one\r\nnew two\r\n", f"CRLF lost: {crlf.read_bytes()!r}"
    assert lf.read_bytes() == b"new one\nnew two\n", f"LF changed: {lf.read_bytes()!r}"
    report.cases[-1].detail = "CRLF and LF both preserved"


def _check_binary_skipped(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "binary/data.bin", b"\x00\x01\x02old\x03\x04")
    before = target.read_bytes()

    # Default discovery filters binary files out entirely.
    run_replace(_options(root, "old", "new", patterns=["binary/*.bin"]))
    assert target.read_bytes() == before, "a binary file was modified by the default run"

    # Even when discovery is told to let it through, decoding must fail loudly
    # rather than write mojibake.
    options = _options(root, "old", "new", patterns=["binary/*.bin"])
    options.discovery_options.text_only = False
    summary = run_replace(options)
    assert target.read_bytes() == before, "a binary file was modified with text_only=False"
    assert summary.replacements == 0, "binary content was counted as a match"
    assert summary.skipped == 1, f"the binary file was not reported as skipped: {summary.totals}"

    # --binary must actually allow the edit, otherwise the flag is a lie.
    forced = _options(root, "old", "new", patterns=["binary/*.bin"], encoding="latin-1")
    forced.discovery_options.text_only = False
    forced.spec = build_spec("old", "new")
    run_replace(forced)
    assert b"new" in target.read_bytes(), "--binary did not permit the edit"
    report.cases[-1].detail = "skipped by default, editable with --binary"


def _check_rollback(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "rollback/target.txt", "original content\n")
    original = target.read_bytes()
    run_replace(_options(root, "original", "modified", patterns=["rollback/*.txt"]))
    assert target.read_bytes() != original, "the replace did not change the file"

    store = store_for(root, None)
    info = _session_for(store, "rollback/target.txt")
    assert info is not None, "no session recorded rollback/target.txt"
    plan = build_restore_plan(info)
    assert plan, "the restore plan is empty"
    result = execute_restore(plan, store, info.directory)
    assert result.ok, f"restore failed: {result.failed}"
    assert target.read_bytes() == original, "rollback did not restore the original bytes"
    report.cases[-1].detail = f"{len(result.restored)} file(s) restored"


def _check_drift_detection(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "drift/target.txt", "before\n")
    run_replace(_options(root, "before", "after", patterns=["drift/*.txt"]))
    store = store_for(root, None)
    info = _session_for(store, "drift/target.txt")
    assert info is not None, "no session recorded drift/target.txt"

    target.write_text("edited by the user afterwards\n", encoding="utf-8")
    plan = build_restore_plan(info)
    drifted = [item for item in plan if item.drifted]
    assert drifted, "drift was not detected"
    result = execute_restore(plan, store, info.directory)
    assert target.read_text(encoding="utf-8") == "edited by the user afterwards\n", (
        "a drifted file was overwritten"
    )
    assert result.skipped, "the drifted file was not reported as skipped"
    forced = execute_restore(plan, store, info.directory, force=True)
    assert forced.ok, f"forced restore failed: {forced.failed}"
    assert target.read_text(encoding="utf-8") == "before\n", (
        "--force did not restore the original content"
    )
    report.cases[-1].detail = f"{len(drifted)} drifted file(s) protected, --force works"


def _session_for(store, rel_path: str):
    """Newest session that touched ``rel_path``."""
    for info in store.list():
        loaded = store.load(info.id)
        if any(record.rel_path == rel_path for record in loaded.records):
            return loaded
    return None


def _check_dry_run_has_no_side_effects(root: Path, report: SelfTestReport) -> None:
    marker = _write(root, "dryrun/target.txt", "old\n")
    before_sessions = len(store_for(root, None).list())
    before_mtimes = {path.name: path.stat().st_mtime_ns for path in root.rglob("*.txt")}
    summary = run_replace(
        _options(root, "old", "new", patterns=["dryrun/*.txt"], apply_changes=False)
    )
    assert marker.read_text(encoding="utf-8") == "old\n", "a dry run modified a file"
    assert summary.replacements == 1, "the dry run did not count the match"
    assert len(store_for(root, None).list()) == before_sessions, "a dry run created a session"
    after_mtimes = {path.name: path.stat().st_mtime_ns for path in root.rglob("*.txt")}
    assert before_mtimes == after_mtimes, "a dry run touched file timestamps"
    report.cases[-1].detail = "no writes, no session, no mtime changes"


def _check_write_to_dir(root: Path, report: SelfTestReport) -> None:
    _write(root, "copy/source.txt", "old text\n")
    out = root / "out"
    run_replace(
        _options(
            root,
            "old",
            "new",
            patterns=["copy/*.txt"],
            write_mode="dir",
            output_dir=out,
            backup=False,
        )
    )
    produced = out / "copy" / "source.txt"
    assert produced.is_file(), f"no copy written; tree is {sorted(p.name for p in out.rglob('*'))}"
    assert produced.read_text(encoding="utf-8") == "new text\n"
    assert (root / "copy" / "source.txt").read_text(encoding="utf-8") == "old text\n", (
        "--write=dir modified the original"
    )
    report.cases[-1].detail = f"copy at {produced.relative_to(root)}"


def _check_write_stdout(root: Path, report: SelfTestReport) -> None:
    import io

    from ..console import make_console

    _write(root, "stdout/target.txt", "old text\n")
    buffer = io.StringIO()
    console = make_console(verbosity="normal", color="never", fmt="text", out=buffer, err=io.StringIO())
    run_replace(
        _options(
            root,
            "old",
            "new",
            patterns=["stdout/*.txt"],
            write_mode="stdout",
            backup=False,
            console=console,
        )
    )
    text = buffer.getvalue()
    assert "new text" in text, f"stdout mode printed nothing useful: {text!r}"
    assert (root / "stdout" / "target.txt").read_text(encoding="utf-8") == "old text\n", (
        "--write=stdout modified the file"
    )
    report.cases[-1].detail = f"{len(text)} bytes to stdout"


def _check_rename(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "renaming/renamable-old.txt", "x\n")
    summary = run_replace(
        _options(root, "-old", "-new", patterns=["renaming/*.txt"], rename_only=True)
    )
    assert not target.exists(), "the original name still exists"
    assert (root / "renaming" / "renamable-new.txt").is_file(), "the file was not renamed"
    assert any(item.executed for item in summary.renames), "no rename was recorded"
    report.cases[-1].detail = f"{len(summary.renames)} rename(s)"


def _check_no_match_exit(root: Path, report: SelfTestReport) -> None:
    _write(root, "nomatch/target.txt", "nothing to see\n")
    summary = run_replace(
        _options(root, "definitely-not-present-xyz", "new", patterns=["nomatch/*.txt"])
    )
    assert summary.exit_hint == int(ExitCode.NO_MATCH), f"exit hint {summary.exit_hint} != 3"
    report.cases[-1].detail = f"exit code {summary.exit_hint}"


def _check_unicode_names(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "ünicode-тест.txt", "old\n")
    run_replace(_options(root, "old", "new", patterns=["ünicode-*.txt"]))
    assert target.read_text(encoding="utf-8") == "new\n", "unicode file name not handled"
    report.cases[-1].detail = target.name


def _check_json_document(root: Path, report: SelfTestReport) -> None:
    import json as _json

    _write(root, "json/target.txt", "old\n")
    summary = run_replace(
        _options(root, "old", "new", patterns=["json/*.txt"], apply_changes=False)
    )
    document = summary.to_dict(include_files=True, include_diff=True)
    assert summary.totals["scanned"] >= 1, "empty document"
    files = document.get("files")
    assert isinstance(files, list) and files, "no files array"
    _json.dumps(document, default=str)  # must be serialisable
    report.cases[-1].detail = f"{len(files)} file record(s)"


def _check_line_range(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "range/target.txt", "old\nold\nold\n")
    spec = build_spec("old", "new", line_range="2-2")
    run_replace(_options(root, "", "", spec=spec, patterns=["range/*.txt"]))
    assert target.read_text(encoding="utf-8") == "old\nnew\nold\n", (
        f"--line-range not honoured: {target.read_text()!r}"
    )
    report.cases[-1].detail = "line 2 only"


def _check_word_boundary(root: Path, report: SelfTestReport) -> None:
    target = _write(root, "words/target.txt", "cat catalog cat\n")
    spec = build_spec("cat", "dog", word_boundary=True)
    run_replace(_options(root, "", "", spec=spec, patterns=["words/*.txt"]))
    assert target.read_text(encoding="utf-8") == "dog catalog dog\n", (
        f"word boundary not honoured: {target.read_text()!r}"
    )
    report.cases[-1].detail = "catalog untouched"


def _check_retention(root: Path, report: SelfTestReport) -> None:
    from ..session import prune_sessions

    store = store_for(root, None)
    total = len(store.list())
    assert total >= 2, f"expected several sessions, found {total}"
    victims = prune_sessions(store, keep=2, dry_run=True)
    assert len(victims) == max(0, total - 2), f"prune plan wrong: {len(victims)} of {total}"
    nothing = prune_sessions(store, keep=0)
    assert not nothing, "keep=0 must not delete anything"
    report.cases[-1].detail = f"{total} sessions, prune keeps 2"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(report: SelfTestReport, context: Context) -> None:
    console = context.console
    console.header(f"sr selftest — {__version__} on Python {sys.version.split()[0]}")
    for case in report.cases:
        icon = console.paint("✓", "green") if case.passed else console.paint("✗", "red")
        detail = f" — {case.detail}" if case.detail else ""
        console.write(f"  {icon} {case.name}{detail}")
        if case.error:
            console.write(f"      {console.paint(case.error, 'red')}")
    console.write("")
    console.header("Result")
    console.kv("Cases", len(report.cases))
    console.kv("Passed", sum(1 for case in report.cases if case.passed))
    console.kv("Failed", len(report.failed))
    console.kv("Elapsed", f"{report.duration * 1000:.0f}ms")
    if report.ok:
        console.success("this sr installation works correctly")
    else:
        console.error("selftest failed — run `sr doctor` and report the output")

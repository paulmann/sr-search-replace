"""Tests for the update machinery (:mod:`sr.commands.update`).

Nothing here touches the network: every HTTP call is replaced by a fake, so the
suite stays hermetic and fast while still covering the decision logic
(version comparison, checksum verification, installation-type detection).
"""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from cli_helpers import sr

from sr.commands import update as update_module
from sr.commands.update import (
    CHECKSUM_ASSET,
    DEFAULT_REPO,
    RELEASE_ASSET,
    InstallType,
    Release,
    cache_path,
    check_in_background,
    detect_installation,
    install_standalone,
    parse_checksum,
    read_cache,
    specific_release,
    verify_sha256,
    write_cache,
)
from sr.errors import UpdateError
from sr.exit_codes import ExitCode
from sr.version import __version__, compare_versions, is_newer


def make_release(version: str, *, assets: dict[str, str] | None = None) -> Release:
    return Release(
        version=version,
        tag=f"v{version}",
        published_at="2026-01-01T00:00:00Z",
        url=f"https://example.invalid/releases/v{version}",
        assets=assets if assets is not None else {RELEASE_ASSET: f"https://example.invalid/{RELEASE_ASSET}"},
    )


@pytest.fixture
def fake_latest(monkeypatch: pytest.MonkeyPatch):
    """Replace the release lookup with a controllable stub."""
    state: dict[str, Any] = {"release": make_release("99.0.0"), "calls": 0}

    def fake(**kwargs):
        state["calls"] += 1
        state["kwargs"] = kwargs
        if isinstance(state["release"], Exception):
            raise state["release"]
        return state["release"]

    monkeypatch.setattr(update_module, "latest_release", fake)
    return state


class TestVersionComparison:
    @pytest.mark.parametrize(
        "left,right,expected",
        [
            ("7.0.0", "7.0.0", 0),
            ("7.0.1", "7.0.0", 1),
            ("7.0.0", "7.0.1", -1),
            ("7.1.0", "7.0.9", 1),
            ("8.0.0", "7.99.99", 1),
            ("v7.0.0", "7.0.0", 0),
            ("7.0.0-rc.1", "7.0.0", -1),
            ("7.0.0", "7.0.0-rc.1", 1),
        ],
    )
    def test_compare(self, left, right, expected):
        assert compare_versions(left, right) == expected

    def test_is_newer(self):
        assert is_newer("7.1.0", "7.0.0")
        assert not is_newer("7.0.0", "7.0.0")
        assert not is_newer("6.9.9", "7.0.0")

    def test_garbage_versions_never_crash(self):
        assert compare_versions("not-a-version", "7.0.0") == -1
        assert is_newer("garbage", __version__) is False


class TestChecksums:
    def test_parse_sha256sum_output(self):
        payload = (
            "a" * 64 + "  sr.pyz\n"
            + "b" * 64 + " *other.txt\n"
            + "# a comment\n"
        )
        parsed = parse_checksum(payload)
        assert parsed["sr.pyz"] == "a" * 64
        assert parsed["other.txt"] == "b" * 64

    def test_parse_ignores_junk(self):
        assert parse_checksum("not a checksum line\n\n") == {}

    def test_verify_ok(self, tmp_path: Path):
        target = tmp_path / "file"
        target.write_bytes(b"payload")
        verify_sha256(target, hashlib.sha256(b"payload").hexdigest())

    def test_verify_mismatch_aborts(self, tmp_path: Path):
        target = tmp_path / "file"
        target.write_bytes(b"payload")
        with pytest.raises(UpdateError, match="checksum mismatch"):
            verify_sha256(target, "0" * 64)

    def test_verify_is_case_insensitive(self, tmp_path: Path):
        target = tmp_path / "file"
        target.write_bytes(b"payload")
        verify_sha256(target, hashlib.sha256(b"payload").hexdigest().upper())


class TestInstallStandalone:
    def test_replaces_and_keeps_a_backup(self, tmp_path: Path):
        target = tmp_path / "sr"
        target.write_text("#!/usr/bin/env python3\nprint('old')\n")
        target.chmod(0o755)
        downloaded = tmp_path / "new.pyz"
        downloaded.write_text("#!/usr/bin/env python3\nprint('new')\n")

        install_standalone(downloaded, target)
        assert target.read_text().endswith("print('new')\n")
        assert (tmp_path / "sr.bak").is_file()
        assert target.stat().st_mode & stat.S_IXUSR, "the executable bit must survive"

    def test_no_backup_when_disabled(self, tmp_path: Path):
        target = tmp_path / "sr"
        target.write_text("old")
        downloaded = tmp_path / "new"
        downloaded.write_text("new")
        install_standalone(downloaded, target, backup=False)
        assert not (tmp_path / "sr.bak").exists()


class TestDetectInstallation:
    def test_returns_a_known_kind(self):
        installation = detect_installation()
        assert installation.kind in InstallType.ALL
        assert installation.upgrade_command()

    def test_source_checkout_is_detected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        root = tmp_path / "repo"
        (root / "sr").mkdir(parents=True)
        (root / "pyproject.toml").write_text("[project]\nname='x'\n")
        monkeypatch.setattr(update_module, "__file__", str(root / "sr" / "commands" / "update.py"))
        monkeypatch.setattr(update_module, "_is_zipapp", lambda: False)
        monkeypatch.setattr(update_module, "_is_npm_install", lambda _root: False)
        installation = detect_installation()
        assert installation.kind == InstallType.SOURCE
        assert installation.can_self_update is False

    def test_npm_install_is_detected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        root = tmp_path / "lib" / "node_modules" / "sr-search-replace"
        (root / "sr").mkdir(parents=True)
        monkeypatch.setattr(update_module, "__file__", str(root / "sr" / "update.py"))
        monkeypatch.setattr(update_module, "_is_zipapp", lambda: False)
        installation = detect_installation()
        assert installation.kind == InstallType.NPM
        assert "npm install -g" in installation.upgrade_command()

    def test_zipapp_is_detected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        archive = tmp_path / "sr.pyz"
        archive.write_bytes(b"#!/usr/bin/env python3\n" + b"0" * 8192)
        archive.chmod(0o755)
        monkeypatch.setattr(sys, "argv", [str(archive)])
        monkeypatch.setattr(update_module, "_is_zipapp", lambda: True)
        installation = detect_installation()
        assert installation.kind == InstallType.STANDALONE
        assert installation.can_self_update is True

    def test_readonly_install_cannot_self_update(self):
        installation = update_module.Installation(
            kind=InstallType.STANDALONE, executable=Path("/usr/local/bin/sr"), writable=False
        )
        assert installation.can_self_update is False
        assert "curl" in installation.upgrade_command()

    def test_writable_standalone_can_self_update(self, tmp_path: Path):
        installation = update_module.Installation(
            kind=InstallType.STANDALONE, executable=tmp_path / "sr", writable=True
        )
        assert installation.can_self_update is True


class TestCache:
    def test_write_and_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        write_cache({"latest": "9.9.9", "newer": True})
        assert cache_path() == tmp_path / "cache" / "sr" / "update-check.json"
        assert read_cache()["latest"] == "9.9.9"

    def test_missing_cache_is_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "nope"))
        assert read_cache() == {}

    def test_corrupt_cache_is_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert read_cache() == {}


class TestBackgroundCheck:
    def test_uses_a_fresh_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import time

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        write_cache({"latest": "9.9.9", "newer": True, "checked_at_epoch": time.time()})

        def boom(**kwargs):  # pragma: no cover - must not be called
            raise AssertionError("the network must not be touched when the cache is fresh")

        monkeypatch.setattr(update_module, "latest_release", boom)
        result = check_in_background()
        assert result and result["latest"] == "9.9.9"

    def test_stale_cache_triggers_a_refresh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        write_cache({"latest": "0.0.1", "newer": False, "checked_at_epoch": 0})
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release("9.9.9"))
        result = check_in_background(timeout=3.0)
        assert result is None or result.get("latest") in {None, "9.9.9", "0.0.1"}

    def test_network_failure_is_silent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

        def boom(**kwargs):
            raise UpdateError("offline")

        monkeypatch.setattr(update_module, "latest_release", boom)
        assert check_in_background(timeout=0.2) is None


class TestUpdateCommand:
    def test_check_reports_a_newer_release(self, fake_latest, tmp_path: Path):
        result = sr("update", "--check", cwd=tmp_path)
        assert result.code == 0
        assert "99.0.0" in result.out

    def test_check_json(self, fake_latest, tmp_path: Path):
        result = sr("update", "--check", "--json", cwd=tmp_path)
        document = result.json()
        assert document["latest"] == "99.0.0"
        assert document["newer"] is True
        assert document["installation"]["kind"]

    def test_up_to_date(self, fake_latest, tmp_path: Path):
        fake_latest["release"] = make_release(__version__)
        result = sr("update", "--check", cwd=tmp_path)
        assert result.code == 0
        assert "up to date" in result.out

    def test_non_standalone_install_explains_how(self, fake_latest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            update_module,
            "detect_installation",
            lambda: update_module.Installation(
                kind=InstallType.PIP, executable=tmp_path / "sr", writable=True, details="pip package"
            ),
        )
        result = sr("update", cwd=tmp_path)
        assert result.code == int(ExitCode.UPDATE_FAILED)
        assert "pip install --upgrade" in result.out

    def test_network_failure_is_reported_cleanly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        def boom(**kwargs):
            raise UpdateError("cannot reach api.github.com: timed out", hint="check the network")

        monkeypatch.setattr(update_module, "latest_release", boom)
        result = sr("update", "--check", cwd=tmp_path)
        assert result.code == int(ExitCode.UPDATE_FAILED)
        assert "cannot reach" in result.err
        assert "Traceback" not in result.err

    def test_standalone_update_verifies_and_installs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """The happy path: download → verify → smoke test → replace."""
        payload = b"#!/usr/bin/env python3\nprint('sr 99.0.0')\n"
        digest = hashlib.sha256(payload).hexdigest()

        target = tmp_path / "sr"
        target.write_bytes(b"#!/usr/bin/env python3\nprint('old')\n")
        target.chmod(0o755)

        def fake_download(url, destination, **kwargs):
            if url.endswith(CHECKSUM_ASSET):
                Path(destination).write_text(f"{digest}  {RELEASE_ASSET}\n")
            else:
                Path(destination).write_bytes(payload)
            return Path(destination)

        monkeypatch.setattr(update_module, "detect_installation", lambda: update_module.Installation(
            kind=InstallType.STANDALONE, executable=target, writable=True, details="test"
        ))
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release(
            "99.0.0",
            assets={
                RELEASE_ASSET: f"https://example.invalid/{RELEASE_ASSET}",
                CHECKSUM_ASSET: f"https://example.invalid/{CHECKSUM_ASSET}",
            },
        ))
        monkeypatch.setattr(update_module, "download", fake_download)
        monkeypatch.setattr(update_module, "smoke_test", lambda path, **kwargs: "sr 99.0.0")

        result = sr("update", "--yes", cwd=tmp_path)
        assert result.code == 0, result.err + result.out
        assert target.read_bytes() == payload
        assert (tmp_path / "sr.bak").is_file()
        assert "SHA-256 verified" in result.out

    def test_checksum_mismatch_installs_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "sr"
        target.write_bytes(b"original")
        target.chmod(0o755)

        def fake_download(url, destination, **kwargs):
            if url.endswith(CHECKSUM_ASSET):
                Path(destination).write_text(f"{'0' * 64}  {RELEASE_ASSET}\n")
            else:
                Path(destination).write_bytes(b"tampered payload")
            return Path(destination)

        monkeypatch.setattr(update_module, "detect_installation", lambda: update_module.Installation(
            kind=InstallType.STANDALONE, executable=target, writable=True, details="test"
        ))
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release(
            "99.0.0",
            assets={
                RELEASE_ASSET: f"https://example.invalid/{RELEASE_ASSET}",
                CHECKSUM_ASSET: f"https://example.invalid/{CHECKSUM_ASSET}",
            },
        ))
        monkeypatch.setattr(update_module, "download", fake_download)

        result = sr("update", "--yes", cwd=tmp_path)
        assert result.code == int(ExitCode.UPDATE_FAILED)
        assert "checksum mismatch" in result.err
        assert target.read_bytes() == b"original", "a failed verification must not install"

    def test_smoke_test_failure_installs_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "sr"
        target.write_bytes(b"original")

        payload = b"broken"
        digest = hashlib.sha256(payload).hexdigest()

        def fake_download(url, destination, **kwargs):
            if url.endswith(CHECKSUM_ASSET):
                Path(destination).write_text(f"{digest}  {RELEASE_ASSET}\n")
            else:
                Path(destination).write_bytes(payload)
            return Path(destination)

        def fake_smoke(path, **kwargs):
            raise UpdateError("the downloaded release does not run: SyntaxError")

        monkeypatch.setattr(update_module, "detect_installation", lambda: update_module.Installation(
            kind=InstallType.STANDALONE, executable=target, writable=True, details="test"
        ))
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release(
            "99.0.0",
            assets={
                RELEASE_ASSET: f"https://example.invalid/{RELEASE_ASSET}",
                CHECKSUM_ASSET: f"https://example.invalid/{CHECKSUM_ASSET}",
            },
        ))
        monkeypatch.setattr(update_module, "download", fake_download)
        monkeypatch.setattr(update_module, "smoke_test", fake_smoke)

        result = sr("update", "--yes", cwd=tmp_path)
        assert result.code == int(ExitCode.UPDATE_FAILED)
        assert target.read_bytes() == b"original"

    def test_missing_checksum_asset_refuses_to_install(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Fail closed: no published checksum means no install, not a warning.

        This is the regression that made the whole verification story fictional:
        the build published `sr.pyz.sha256` while the updater looked for
        `sr.sha256`, so the lookup always missed and the update proceeded
        unverified.
        """
        target = tmp_path / "sr"
        target.write_bytes(b"original")

        monkeypatch.setattr(update_module, "detect_installation", lambda: update_module.Installation(
            kind=InstallType.STANDALONE, executable=target, writable=True, details="test"
        ))
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release(
            "99.0.0", assets={RELEASE_ASSET: f"https://example.invalid/{RELEASE_ASSET}"}
        ))
        monkeypatch.setattr(
            update_module,
            "download",
            lambda url, destination, **kwargs: Path(destination).write_bytes(b"payload") or Path(destination),
        )

        result = sr("update", "--yes", cwd=tmp_path)
        assert result.code == int(ExitCode.UPDATE_FAILED)
        assert "cannot be verified" in result.err
        assert target.read_bytes() == b"original", "nothing may be installed unverified"

    def test_no_verify_is_the_only_way_past_a_missing_checksum(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        target = tmp_path / "sr"
        target.write_bytes(b"original")

        payload = b"#!/usr/bin/env python3\nprint('sr 99.0.0')\n"
        monkeypatch.setattr(update_module, "detect_installation", lambda: update_module.Installation(
            kind=InstallType.STANDALONE, executable=target, writable=True, details="test"
        ))
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release(
            "99.0.0", assets={RELEASE_ASSET: f"https://example.invalid/{RELEASE_ASSET}"}
        ))
        monkeypatch.setattr(
            update_module,
            "download",
            lambda url, destination, **kwargs: Path(destination).write_bytes(payload) or Path(destination),
        )
        monkeypatch.setattr(update_module, "smoke_test", lambda path, **kwargs: "sr 99.0.0")

        result = sr("update", "--yes", "--no-verify", cwd=tmp_path)
        assert result.code == 0, result.err
        assert target.read_bytes() == payload
        assert "NOT checked" in result.out + result.err

    def test_checksum_asset_name_matches_the_build(self):
        """The name the updater looks for must be the name the build writes."""
        assert CHECKSUM_ASSET == f"{RELEASE_ASSET}.sha256" == "sr.pyz.sha256"
        builder = (Path(__file__).resolve().parent.parent / "tools" / "build_zipapp.py").read_text()
        assert '.suffix + ".sha256"' in builder, "the builder changed its checksum naming"
        installer = (Path(__file__).resolve().parent.parent / "scripts" / "install.sh").read_text()
        assert 'CHECKSUM="${ASSET}.sha256"' in installer, "install.sh drifted from the build"

    def test_no_asset_published(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(update_module, "detect_installation", lambda: update_module.Installation(
            kind=InstallType.STANDALONE, executable=tmp_path / "sr", writable=True, details="test"
        ))
        monkeypatch.setattr(update_module, "latest_release", lambda **kwargs: make_release("99.0.0", assets={}))
        (tmp_path / "sr").write_text("x")
        result = sr("update", "--yes", cwd=tmp_path)
        assert result.code == int(ExitCode.UPDATE_FAILED)
        assert "no sr.pyz asset" in result.err


class TestReleaseParsing:
    """The GitHub/npm payload handling, with the HTTP layer stubbed out."""

    def test_github_stable(self, monkeypatch: pytest.MonkeyPatch):
        payload = {
            "tag_name": "v7.1.0",
            "prerelease": False,
            "draft": False,
            "published_at": "2026-02-01T00:00:00Z",
            "html_url": "https://example.invalid/releases/v7.1.0",
            "body": "notes",
            "assets": [
                {"name": RELEASE_ASSET, "browser_download_url": "https://example.invalid/sr.pyz"},
                {"name": CHECKSUM_ASSET, "browser_download_url": f"https://example.invalid/{CHECKSUM_ASSET}"},
            ],
        }
        monkeypatch.setattr(update_module, "_http_get", lambda *a, **k: json.dumps(payload).encode())
        release = update_module._github_release(repo=DEFAULT_REPO, channel="stable", timeout=1.0)
        assert release.version == "7.1.0"
        assert release.asset_url.endswith("sr.pyz")
        # The name must match what tools/build_zipapp.py writes and the release
        # workflow uploads; a mismatch here means updates install unverified.
        assert release.checksum_url.endswith("sr.pyz.sha256")

    def test_github_prerelease_channel_skips_stable(self, monkeypatch: pytest.MonkeyPatch):
        payload = [
            {"tag_name": "v7.2.0-rc.1", "prerelease": True, "assets": []},
            {"tag_name": "v7.1.0", "prerelease": False, "assets": []},
        ]
        monkeypatch.setattr(update_module, "_http_get", lambda *a, **k: json.dumps(payload).encode())
        release = update_module._github_release(repo=DEFAULT_REPO, channel="prerelease", timeout=1.0)
        assert release.version == "7.2.0-rc.1"

    def test_github_no_releases(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(update_module, "_http_get", lambda *a, **k: b"[]")
        with pytest.raises(UpdateError, match="no releases"):
            update_module._github_release(repo=DEFAULT_REPO, channel="prerelease", timeout=1.0)

    def test_npm(self, monkeypatch: pytest.MonkeyPatch):
        payload = {"dist-tags": {"latest": "7.1.0", "next": "7.2.0-rc.1"}, "time": {"7.1.0": "2026"}}
        monkeypatch.setattr(update_module, "_http_get", lambda *a, **k: json.dumps(payload).encode())
        assert update_module._npm_release(channel="stable", timeout=1.0).version == "7.1.0"
        assert update_module._npm_release(channel="prerelease", timeout=1.0).version == "7.2.0-rc.1"

    def test_pypi(self, monkeypatch: pytest.MonkeyPatch):
        payload = {"info": {"version": "7.1.0", "project_urls": {"Homepage": "https://example.invalid"}}}
        monkeypatch.setattr(update_module, "_http_get", lambda *a, **k: json.dumps(payload).encode())
        assert update_module._pypi_release(channel="stable", timeout=1.0).version == "7.1.0"

    def test_specific_release(self, monkeypatch: pytest.MonkeyPatch):
        payload = {"tag_name": "v7.0.1", "prerelease": False, "assets": [], "html_url": "u", "body": ""}
        monkeypatch.setattr(update_module, "_http_get", lambda *a, **k: json.dumps(payload).encode())
        assert specific_release("7.0.1", timeout=1.0).version == "7.0.1"

    def test_http_errors_get_actionable_hints(self, monkeypatch: pytest.MonkeyPatch):
        import urllib.error

        def raise_403(*args, **kwargs):
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

        monkeypatch.setattr(update_module.urllib.request, "urlopen", raise_403)
        with pytest.raises(UpdateError, match=r"rate limited|HTTP 403"):
            update_module._http_get("https://example.invalid", timeout=1.0)

    def test_github_token_is_used(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SR_GITHUB_TOKEN", "secret")
        assert update_module.github_token() == "secret"
        monkeypatch.delenv("SR_GITHUB_TOKEN")
        monkeypatch.setenv("GITHUB_TOKEN", "other")
        assert update_module.github_token() == "other"


class TestUpdateCheckToggle:
    def test_env_disables_the_check(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SR_NO_UPDATE_CHECK", "1")
        assert update_module.update_check_enabled(None) is False

    def test_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SR_NO_UPDATE_CHECK", raising=False)
        assert update_module.update_check_enabled(None) is True

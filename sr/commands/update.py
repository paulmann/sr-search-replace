"""Self-update: release discovery, verification and installation.

Three installation shapes are supported, and ``sr`` detects which one it is
running as (``sr doctor`` prints the answer):

``standalone``
    A single executable file — either a zipapp produced by ``make dist`` or a
    concatenated script installed by ``scripts/install.sh``.  This is the only
    shape sr can update *by itself*: it downloads the release asset, verifies
    its SHA-256 against the published ``sr.pyz.sha256``, smoke-tests the
    downloaded file in a subprocess, and only then atomically replaces the
    running file (keeping ``<file>.bak``).

    Verification is **fail-closed**: a release without a checksum asset, an
    unreachable checksum, an empty or malformed checksum file and a digest
    mismatch all abort the update and install nothing.  ``--no-verify`` is the
    only way past that, and it warns loudly when used.
``pip`` / ``pipx``
    The package directory is owned by the environment, so sr prints the exact
    upgrade command instead of touching site-packages.
``npm``
    Same idea: ``npm install -g sr-search-replace@latest``.
``source``
    A git checkout or a local build — updating is refused with an explanation.

The background check that prints "a new version is available" is deliberately
non-blocking: it runs on a daemon thread with a short timeout and caches its
answer under the user cache directory, so a cold or offline network can never
slow a run down.  Disable it with ``--no-update-check`` or
``SR_NO_UPDATE_CHECK=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..console import Verbosity
from ..context import Context
from ..errors import UpdateError
from ..exit_codes import ExitCode
from ..util import format_duration, now_iso
from ..version import __version__, compare_versions, is_newer

#: Default repository queried for releases.
DEFAULT_REPO = "paulmann/sr-search-replace"

#: Name of the single-file executable attached to every GitHub release.
RELEASE_ASSET = "sr.pyz"

#: Name of the checksum asset published next to it.
#:
#: Must match what ``tools/build_zipapp.py`` writes and what the release
#: workflow uploads: ``<asset>.sha256``.  A mismatch here is silent and
#: catastrophic — the updater would find no checksum and refuse to install.
CHECKSUM_ASSET = f"{RELEASE_ASSET}.sha256"

USER_AGENT = f"sr-search-replace/{__version__} (+https://github.com/{DEFAULT_REPO})"

#: How long a background check may take before we give up on it.
BACKGROUND_TIMEOUT = 2.5

#: How long a cached check stays valid.
CACHE_TTL_SECONDS = 24 * 3600


class InstallType:
    STANDALONE = "standalone"
    PIP = "pip"
    PIPX = "pipx"
    NPM = "npm"
    SOURCE = "source"
    UNKNOWN = "unknown"

    ALL = (STANDALONE, PIP, PIPX, NPM, SOURCE, UNKNOWN)


@dataclass(slots=True)
class Installation:
    """What sr is running as, and whether it can update itself."""

    kind: str = InstallType.UNKNOWN
    executable: Path | None = None
    writable: bool = False
    package_version: str = ""
    details: str = ""

    @property
    def can_self_update(self) -> bool:
        return self.kind == InstallType.STANDALONE and self.writable

    def upgrade_command(self) -> str:
        if self.kind == InstallType.PIPX:
            return "pipx upgrade sr-search-replace"
        if self.kind == InstallType.PIP:
            return f"{sys.executable} -m pip install --upgrade sr-search-replace"
        if self.kind == InstallType.NPM:
            return "npm install -g sr-search-replace@latest"
        if self.kind == InstallType.SOURCE:
            return "git pull && make install"
        return (
            "curl -fsSL https://raw.githubusercontent.com/"
            f"{DEFAULT_REPO}/main/scripts/install.sh | bash"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "executable": str(self.executable) if self.executable else None,
            "writable": self.writable,
            "package_version": self.package_version or __version__,
            "details": self.details,
            "can_self_update": self.can_self_update,
            "upgrade_command": self.upgrade_command(),
        }


@dataclass(slots=True)
class Release:
    """One published release."""

    version: str
    tag: str = ""
    published_at: str = ""
    prerelease: bool = False
    url: str = ""
    assets: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    source: str = "github"

    @property
    def asset_url(self) -> str:
        return self.assets.get(RELEASE_ASSET, "")

    @property
    def checksum_url(self) -> str:
        return self.assets.get(CHECKSUM_ASSET, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tag": self.tag,
            "published_at": self.published_at,
            "prerelease": self.prerelease,
            "url": self.url,
            "assets": dict(self.assets),
            "source": self.source,
        }


def detect_installation() -> Installation:
    """Work out how sr was installed, and whether it can update itself.

    Two paths matter here: the *executable* (what an update would replace) and
    the *package root* (what tells us which installation shape we are in).  They
    differ for every layout — a zipapp is both, a pip install has a console
    script plus site-packages, a source checkout has a launcher plus a working
    tree.
    """
    # The package directory that holds this module's parent package, i.e. the
    # `sr/` directory; its parent is the project root that would hold
    # pyproject.toml or .git in a checkout.
    package_root = Path(__file__).resolve().parent.parent
    executable = _resolve_executable()
    installation = Installation(executable=executable)

    if _is_zipapp():
        # An npm install ships the very same zipapp under node_modules, so the
        # location decides which upgrade advice is correct: replacing the file
        # by hand would be undone by the next `npm install`.
        if _is_npm_install(package_root):
            installation.kind = InstallType.NPM
            installation.details = f"npm package (bundled zipapp {executable})"
        else:
            installation.kind = InstallType.STANDALONE
            installation.details = f"zipapp {executable}"
    elif _is_npm_install(package_root):
        installation.kind = InstallType.NPM
        installation.details = "installed through npm"
    elif _is_source_checkout(package_root):
        installation.kind = InstallType.SOURCE
        installation.details = f"source checkout at {package_root.parent}"
    else:
        distribution = _distribution_info()
        if distribution:
            kind, version = distribution
            installation.kind = kind
            installation.package_version = version
            installation.details = f"{kind} package sr-search-replace {version}"
        elif _looks_standalone(executable):
            installation.kind = InstallType.STANDALONE
            installation.details = f"single file {executable}"
        else:
            installation.kind = InstallType.UNKNOWN
            installation.details = f"cannot classify {executable}"

    installation.package_version = installation.package_version or _installed_package_version()
    try:
        installation.writable = bool(executable.is_file() and os.access(executable, os.W_OK))
    except OSError:
        installation.writable = False
    return installation


def _resolve_executable() -> Path:
    """The file an update would replace."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidate = Path(argv0)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        try:
            candidate = candidate.resolve()
        except OSError:  # pragma: no cover - broken symlink
            pass
        if candidate.is_file() and candidate.suffix != ".py":
            return candidate
    main_file = getattr(sys.modules.get("__main__"), "__file__", "") or ""
    if main_file:
        return Path(main_file).resolve()
    return Path(sys.executable or "sr")


def _is_zipapp() -> bool:
    """True when the package is being run from inside a zipapp archive."""
    loader = getattr(sys.modules.get("__main__"), "__loader__", None)
    if loader is not None and type(loader).__name__ in {"ZipImporter", "_ZipImportLoader"}:
        return True
    package_file = Path(getattr(sys.modules.get(__package__ or "sr"), "__file__", "") or "")
    # Inside a zipapp the module path contains the archive name, e.g.
    # /usr/local/bin/sr.pyz/sr/cli.py
    return any(part.endswith(".pyz") or part.endswith(".zip") for part in package_file.parts)


def _is_npm_install(package_root: Path) -> bool:
    """True when the project root sits inside a ``node_modules`` tree."""
    if package_root.name == "node_modules":
        return True
    for parent in package_root.parents:
        if parent.name == "node_modules":
            return True
        if (parent / "package.json").is_file() and (parent / "node_modules").is_dir():
            return True
    return False


def _is_source_checkout(package_root: Path) -> bool:
    """A working tree rather than an installed distribution."""
    return _source_markers(package_root.parent)


def _source_markers(project_root: Path) -> bool:
    """True when ``project_root`` looks like a source checkout.

    Both markers are checked because a tarball checkout has ``pyproject.toml``
    but no ``.git``, while a bare clone may have neither ``setup.py`` nor a
    Makefile yet.
    """
    if (project_root / "pyproject.toml").is_file():
        return True
    if (project_root / "setup.py").is_file():
        return True
    return (project_root / ".git").exists() and (project_root / "Makefile").is_file()


def _looks_standalone(path: Path) -> bool:
    """True for a single executable file with a Python shebang."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(512)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    first_line = head.split(b"\n", 1)[0]
    # A POSIX-sh launcher that merely *calls* python is not standalone — it is
    # part of a source tree or an install layout.  A zipapp or a concatenated
    # single-file script has a Python shebang and is self-contained.
    if not first_line.lstrip(b"#!").strip().startswith((b"python", b"/usr/bin/env python")):
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size > 4096


def _distribution_info() -> tuple[str, str] | None:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python 3.7
        return None
    try:
        distribution = metadata.distribution("sr-search-replace")
    except metadata.PackageNotFoundError:
        return None
    version = distribution.version or ""
    installer = ""
    try:
        installer = (distribution.read_text("INSTALLER") or "").strip().lower()
    except Exception:  # pragma: no cover - optional metadata
        installer = ""
    location = str(distribution.locate_file("") or "")
    if "pipx" in location:
        return InstallType.PIPX, version
    if installer == "pipx":
        return InstallType.PIPX, version
    if installer in {"pip", "uv"}:
        return InstallType.PIP, version
    return InstallType.PIP, version


def _installed_package_version() -> str:
    try:
        from importlib import metadata

        return metadata.version("sr-search-replace")
    except Exception:  # pragma: no cover - not installed as a distribution
        return __version__


# ---------------------------------------------------------------------------
# Release lookup
# ---------------------------------------------------------------------------


def _require_https(url: str) -> str:
    """Refuse anything that is not an ``https`` URL.

    ``urllib.request.urlopen`` happily handles ``file:``, ``ftp:`` and custom
    schemes.  Every URL sr fetches comes from a release feed or a user-supplied
    ``--repo``/asset name, so the scheme is pinned here once and the audited
    ``urlopen`` calls below are provably https-only.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise UpdateError(
            f"refusing to contact a non-https URL: {url}",
            hint="sr only downloads over TLS; check --repo / --path / SR_UPDATE_REPO",
        )
    return url


def _http_get(url: str, *, timeout: float, token: str | None = None, accept: str = "application/json") -> bytes:
    request = urllib.request.Request(_require_https(url))  # noqa: S310 - pinned to https
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", accept)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        # S310: the URL was pinned to https by `_require_https` above.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(
            f"HTTP {exc.code} from {url}",
            hint=_http_hint(exc.code),
        ) from exc
    except urllib.error.URLError as exc:
        raise UpdateError(
            f"cannot reach {url}: {exc.reason}",
            hint="check the network/proxy, or update manually (see `sr help update`)",
        ) from exc
    except TimeoutError as exc:
        raise UpdateError(f"timed out contacting {url}", hint="try --timeout with a larger value") from exc


def _http_hint(code: int) -> str:
    if code == 403:
        return "rate limited or forbidden — set GITHUB_TOKEN for a higher limit"
    if code == 404:
        return "not found — check --repo, or the release may not exist yet"
    if code >= 500:
        return "the server is having a bad day; retry later"
    return "see `sr help update`"


def github_token() -> str | None:
    for name in ("SR_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def latest_release(
    *,
    repo: str = DEFAULT_REPO,
    channel: str = "stable",
    timeout: float = 20.0,
    source: str = "github",
) -> Release:
    """Look up the newest release on the requested channel."""
    if source == "npm":
        return _npm_release(channel=channel, timeout=timeout)
    if source == "pypi":
        return _pypi_release(channel=channel, timeout=timeout)
    return _github_release(repo=repo, channel=channel, timeout=timeout)


def _github_release(*, repo: str, channel: str, timeout: float) -> Release:
    token = github_token()
    if channel == "stable":
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        payload = json.loads(_http_get(url, timeout=timeout, token=token))
        candidates = [payload] if isinstance(payload, dict) else []
    else:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=15"
        payload = json.loads(_http_get(url, timeout=timeout, token=token))
        candidates = payload if isinstance(payload, list) else []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if channel == "stable" and (item.get("prerelease") or item.get("draft")):
            continue
        tag = str(item.get("tag_name") or "")
        version = tag.lstrip("vV")
        assets = {
            str(asset.get("name")): str(asset.get("browser_download_url"))
            for asset in item.get("assets", [])
            if isinstance(asset, dict) and asset.get("name")
        }
        return Release(
            version=version,
            tag=tag,
            published_at=str(item.get("published_at") or ""),
            prerelease=bool(item.get("prerelease")),
            url=str(item.get("html_url") or ""),
            assets=assets,
            notes=str(item.get("body") or "")[:4000],
            source="github",
        )
    raise UpdateError(
        f"no releases published in {repo}",
        hint=f"check https://github.com/{repo}/releases",
    )


def _npm_release(*, channel: str, timeout: float) -> Release:
    payload = json.loads(
        _http_get("https://registry.npmjs.org/sr-search-replace", timeout=timeout)
    )
    dist_tags = payload.get("dist-tags", {}) or {}
    tag = "latest" if channel == "stable" else "next"
    version = str(dist_tags.get(tag) or dist_tags.get("latest") or "")
    if not version:
        raise UpdateError("the npm registry returned no version", hint="try --source github")
    return Release(
        version=version,
        tag=f"v{version}",
        published_at=str((payload.get("time") or {}).get(version, "")),
        prerelease=tag == "next",
        url=f"https://www.npmjs.com/package/sr-search-replace/v/{version}",
        source="npm",
    )


def _pypi_release(*, channel: str, timeout: float) -> Release:
    payload = json.loads(_http_get("https://pypi.org/pypi/sr-search-replace/json", timeout=timeout))
    info = payload.get("info", {}) or {}
    version = str(info.get("version") or "")
    if not version:
        raise UpdateError("PyPI returned no version", hint="try --source github")
    return Release(
        version=version,
        tag=f"v{version}",
        published_at="",
        prerelease=channel == "prerelease",
        url=str(info.get("project_urls", {}).get("Homepage", "") or ""),
        source="pypi",
    )


def specific_release(version: str, *, repo: str = DEFAULT_REPO, timeout: float = 20.0) -> Release:
    """Fetch one release by version (``sr update --version 7.1.0``)."""
    tag = version if version.startswith("v") else f"v{version}"
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    payload = json.loads(_http_get(url, timeout=timeout, token=github_token()))
    assets = {
        str(asset.get("name")): str(asset.get("browser_download_url"))
        for asset in payload.get("assets", [])
        if isinstance(asset, dict) and asset.get("name")
    }
    return Release(
        version=str(payload.get("tag_name", tag)).lstrip("vV"),
        tag=str(payload.get("tag_name", tag)),
        published_at=str(payload.get("published_at") or ""),
        prerelease=bool(payload.get("prerelease")),
        url=str(payload.get("html_url") or ""),
        assets=assets,
        notes=str(payload.get("body") or "")[:4000],
        source="github",
    )


# ---------------------------------------------------------------------------
# Download + verify + install
# ---------------------------------------------------------------------------


def download(url: str, destination: Path, *, timeout: float = 60.0, token: str | None = None) -> Path:
    """Stream ``url`` into ``destination`` (https only)."""
    request = urllib.request.Request(_require_https(url))  # noqa: S310 - pinned to https
    request.add_header("User-Agent", USER_AGENT)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        # S310: the URL was pinned to https by `_require_https` above.
        with urllib.request.urlopen(request, timeout=timeout) as response, open(  # noqa: S310
            destination, "wb"
        ) as handle:
            shutil.copyfileobj(response, handle, length=1 << 16)
    except urllib.error.URLError as exc:
        raise UpdateError(f"download failed: {exc.reason}", hint=f"url: {url}") from exc
    except OSError as exc:
        raise UpdateError(f"cannot write {destination}: {exc.strerror or exc}") from exc
    return destination


def parse_checksum(payload: str) -> dict[str, str]:
    """Parse ``sha256sum``-style output into ``{filename: digest}``."""
    result: dict[str, str] = {}
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("*", " ").split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            result[Path(parts[-1]).name] = parts[0].lower()
    return result


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest().lower()
    if digest != expected.strip().lower():
        raise UpdateError(
            "checksum mismatch — the download is corrupt or tampered with",
            hint=f"expected {expected.strip()[:16]}…, got {digest[:16]}…; nothing was installed",
        )


def install_standalone(
    downloaded: Path,
    target: Path,
    *,
    backup: bool = True,
) -> Path:
    """Replace ``target`` with ``downloaded`` atomically, keeping a ``.bak``."""
    if backup:
        backup_path = target.with_suffix(target.suffix + ".bak")
        try:
            shutil.copy2(target, backup_path)
        except OSError:
            pass
    mode = 0o755
    try:
        mode = stat.S_IMODE(target.stat().st_mode) | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    except OSError:
        pass
    temp = target.with_name(target.name + ".new")
    shutil.copy2(downloaded, temp)
    os.chmod(temp, mode)
    os.replace(temp, target)
    return target


def smoke_test(path: Path, *, timeout: float = 20.0) -> str:
    """Run ``<path> --version`` in a subprocess; returns its output."""
    executable = sys.executable or "python3"
    try:
        completed = subprocess.run(
            [executable, str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"the downloaded release does not run: {exc}") from exc
    if completed.returncode != 0:
        raise UpdateError(
            "the downloaded release failed its smoke test",
            hint=(completed.stderr or completed.stdout).strip()[:400] or "no output",
        )
    return (completed.stdout or "").strip()


# ---------------------------------------------------------------------------
# Background check + cache
# ---------------------------------------------------------------------------


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / "sr" / "update-check.json"


def read_cache() -> dict[str, Any]:
    path = cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_cache(data: dict[str, Any]) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass


def update_check_enabled(context: Context | None = None) -> bool:
    """Whether the background update check may run."""
    truthy = {"1", "true", "yes"}
    if os.environ.get("SR_NO_UPDATE_CHECK", "").strip().lower() in truthy:
        return False
    if os.environ.get("SR_OFFLINE", "").strip().lower() in truthy:
        return False
    return not (context is not None and context.value("no_update_check", False))


def check_in_background(
    *,
    current: str = __version__,
    repo: str = DEFAULT_REPO,
    channel: str = "stable",
    timeout: float = BACKGROUND_TIMEOUT,
) -> dict[str, Any] | None:
    """Look for an update without blocking the command.

    Returns the cached/new result when it is available within ``timeout``,
    otherwise ``None`` (the thread keeps running and fills the cache for the
    next invocation).
    """
    import threading

    cached = read_cache()
    checked_at = float(cached.get("checked_at_epoch", 0) or 0)
    fresh = cached and (time.time() - checked_at) < CACHE_TTL_SECONDS
    if fresh:
        return cached

    result: dict[str, Any] = {}

    def worker() -> None:
        try:
            release = latest_release(repo=repo, channel=channel, timeout=max(4.0, timeout * 2))
            result.update(
                {
                    "checked_at": now_iso(),
                    "checked_at_epoch": time.time(),
                    "current": current,
                    "latest": release.version,
                    "url": release.url,
                    "newer": is_newer(release.version, current),
                    "channel": channel,
                    "repo": repo,
                }
            )
            write_cache(result)
        except Exception:
            pass

    thread = threading.Thread(target=worker, name="sr-update-check", daemon=True)
    thread.start()
    thread.join(timeout)
    return result or (cached if cached else None)


# ---------------------------------------------------------------------------
# `sr update`
# ---------------------------------------------------------------------------


def cmd_update(context: Context) -> int:
    """``sr update [--check] [--version X] [--channel Y]``."""
    args = context.args
    console = context.console
    installation = detect_installation()
    repo = str(getattr(args, "repo", None) or os.environ.get("SR_UPDATE_REPO") or DEFAULT_REPO)
    channel = str(getattr(args, "channel", "stable") or "stable")
    source = str(getattr(args, "source", "github") or "github")
    timeout = float(getattr(args, "timeout", 20.0) or 20.0)
    pinned = getattr(args, "version", None)

    current = installation.package_version or __version__
    console.debug(f"installation: {installation.kind} at {installation.executable}")

    try:
        release = (
            specific_release(str(pinned), repo=repo, timeout=timeout)
            if pinned
            else latest_release(repo=repo, channel=channel, timeout=timeout, source=source)
        )
    except UpdateError as exc:
        if console.json_mode:
            console.emit_json({"ok": False, "error": str(exc), "current": current})
        raise

    newer = is_newer(release.version, current)
    if console.json_mode and getattr(args, "check", False):
        console.emit_json(
            {
                "current": current,
                "latest": release.version,
                "newer": newer,
                "release": release.to_dict(),
                "installation": installation.to_dict(),
            }
        )
        return int(ExitCode.OK)

    console.header("Update check")
    console.kv("Installed", f"{current} ({installation.kind})")
    console.kv("Available", f"{release.version}{console.paint(' (prerelease)', 'yellow') if release.prerelease else ''}")
    console.kv("Channel", channel)
    console.kv("Source", release.source)
    if release.published_at:
        console.kv("Published", release.published_at)
    if release.url:
        console.kv("Release notes", release.url)

    if pinned:
        same = compare_versions(release.version, current) == 0
        if same and not context.value("force", False):
            console.info(f"{release.version} is already installed")
            return int(ExitCode.OK)
    elif not newer:
        console.success(f"sr {current} is up to date")
        write_cache(
            {
                "checked_at": now_iso(),
                "checked_at_epoch": time.time(),
                "current": current,
                "latest": release.version,
                "url": release.url,
                "newer": False,
                "channel": channel,
                "repo": repo,
            }
        )
        return int(ExitCode.OK)

    if getattr(args, "check", False):
        console.info(f"run `sr update` to install {release.version}")
        return int(ExitCode.OK)

    if not installation.can_self_update:
        console.write("")
        console.warn(
            f"sr cannot replace a {installation.kind} installation by itself",
            hint=installation.details,
        )
        console.info("Upgrade with:")
        console.write(f"  {console.paint(installation.upgrade_command(), 'cyan')}")
        if release.url:
            console.info(f"Release notes: {release.url}")
        return int(ExitCode.UPDATE_FAILED)

    return _perform_update(context, installation, release, timeout=timeout)


def _perform_update(
    context: Context, installation: Installation, release: Release, *, timeout: float
) -> int:
    console = context.console
    target = installation.executable
    if target is None:  # pragma: no cover - can_self_update implies a path
        raise UpdateError("cannot determine which file to update", hint="pass --path /usr/local/bin/sr")

    if not release.asset_url:
        raise UpdateError(
            f"release {release.tag} has no {RELEASE_ASSET} asset",
            hint=f"install manually from {release.url or 'the releases page'}",
        )

    if not context.assume_yes and not getattr(context.args, "yes", False):
        if not context.confirm(
            f"Install sr {release.version} over {target}?",
            default=True,
        ):
            console.info("update cancelled")
            return int(ExitCode.OK)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sr-update-") as workspace:
        workdir = Path(workspace)
        downloaded = workdir / RELEASE_ASSET
        console.info(f"downloading {release.asset_url}")
        download(release.asset_url, downloaded, timeout=max(timeout, 60.0), token=github_token())
        size = downloaded.stat().st_size
        console.verbose(f"downloaded {size} bytes in {format_duration(time.monotonic() - started)}")

        if not getattr(context.args, "no_verify", False):
            expected = _fetch_checksum(release, workdir, timeout=timeout)
            if not expected:
                # Fail closed.  Installing an unverified binary is exactly the
                # supply-chain hole SECURITY.md says is closed, so the only way
                # past a missing checksum is an explicit, logged opt-out.
                raise UpdateError(
                    f"release {release.tag} publishes no {CHECKSUM_ASSET}, "
                    "so the download cannot be verified",
                    hint="re-run with --no-verify to install it unverified "
                    "(only do this for a mirror you trust)",
                )
            verify_sha256(downloaded, expected)
            console.success(f"SHA-256 verified ({expected[:16]}…)")
        else:
            console.warn(
                "verification disabled with --no-verify: the downloaded file was NOT checked",
                hint="a tampered or truncated download would be installed as-is",
            )

        console.info("running the smoke test on the downloaded release")
        output = smoke_test(downloaded, timeout=timeout)
        console.verbose(output.splitlines()[0] if output else "smoke test passed")

        install_standalone(downloaded, target)

    console.success(f"sr {release.version} installed at {target}")
    console.kv("Backup", f"{target}.bak")
    console.kv("Elapsed", format_duration(time.monotonic() - started))
    write_cache(
        {
            "checked_at": now_iso(),
            "checked_at_epoch": time.time(),
            "current": release.version,
            "latest": release.version,
            "url": release.url,
            "newer": False,
        }
    )
    return int(ExitCode.OK)


def _fetch_checksum(release: Release, workdir: Path, *, timeout: float) -> str:
    if not release.checksum_url:
        return ""
    path = workdir / CHECKSUM_ASSET
    try:
        download(release.checksum_url, path, timeout=timeout, token=github_token())
    except UpdateError:
        return ""
    checksums = parse_checksum(path.read_text(encoding="utf-8", errors="replace"))
    return checksums.get(RELEASE_ASSET, "") or next(iter(checksums.values()), "")


# ---------------------------------------------------------------------------
# The one-line notice printed by other commands
# ---------------------------------------------------------------------------


def maybe_check_for_update(context: Context) -> None:
    """Print an upgrade notice when a newer release is known."""
    if not update_check_enabled(context):
        return
    if context.command in {"update", "selftest", "doctor", "help", "completions"}:
        return
    if context.console.verbosity <= Verbosity.QUIET:
        return
    if context.console.machine_readable:
        return
    cached = read_cache()
    if cached.get("newer"):
        _announce(context, cached)
        return
    fresh = cached and (time.time() - float(cached.get("checked_at_epoch", 0) or 0)) < CACHE_TTL_SECONDS
    if fresh:
        return
    # Refresh on a daemon thread; never wait for the network on this path.
    check_in_background(current=__version__)


def _announce(context: Context, cached: dict[str, Any]) -> None:
    if not cached.get("newer"):
        return
    latest = cached.get("latest", "")
    if not latest or not is_newer(str(latest), __version__):
        return
    installation = detect_installation()
    how = installation.upgrade_command()
    context.console.write("")
    context.console.warn(
        f"sr {latest} is available (installed: {__version__})",
        hint=f"upgrade with `{how}` — details: `sr help update`",
    )

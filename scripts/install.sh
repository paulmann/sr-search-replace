#!/usr/bin/env bash
# =============================================================================
# sr — installer for the standalone single-file build
#
#   curl -fsSL https://raw.githubusercontent.com/paulmann/sr-search-replace/main/scripts/install.sh | bash
#   curl -fsSL .../scripts/install.sh | bash -s -- --prefix ~/.local/bin --version 7.0.0
#
# What it does, in order:
#   1. resolves the release (default: the newest GitHub release, or --version)
#   2. downloads sr.pyz and sr.pyz.sha256 over HTTPS
#   3. verifies the SHA-256 (aborting on mismatch — nothing is installed)
#   4. smoke-tests the download (`sr.pyz --version`)
#   5. installs it to <prefix>/sr with mode 0755, keeping <file>.old
#
# The script never uses `sudo` implicitly and never writes outside --prefix.
# Set SR_INSTALL_DRY_RUN=1 to see what it would do.
# =============================================================================
set -euo pipefail

REPO="${SR_UPDATE_REPO:-paulmann/sr-search-replace}"
PREFIX="${PREFIX:-/usr/local/bin}"
CHANNEL="${SR_UPDATE_CHANNEL:-stable}"
VERSION=""
ASSET="sr.pyz"
# Must match what tools/build_zipapp.py writes and the release workflow
# uploads: "<asset>.sha256". A wrong name here silently downgrades the
# install to "unverified", which is why the missing-checksum path below is
# a hard error rather than a warning.
CHECKSUM="${ASSET}.sha256"
BIN_NAME="${SR_BIN_NAME:-sr}"
FORCE=0
QUIET=0
DRY_RUN="${SR_INSTALL_DRY_RUN:-0}"

log()  { [ "$QUIET" = 1 ] || printf '\033[0;34m[sr-install]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[sr-install]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[0;31m[sr-install] error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
sr installer — single-file standalone build

Usage: install.sh [options]

  --prefix DIR      install directory (default: $PREFIX, or \$PREFIX)
  --version X.Y.Z   install a specific release instead of the newest
  --channel NAME    stable (default) or prerelease
  --repo OWNER/NAME GitHub repository (default: $REPO)
  --name NAME       installed file name (default: $BIN_NAME)
  --no-verify       skip the SHA-256 check (insecure; only for mirrors)
  --force           reinstall even if the same version is present
  --quiet           only warnings and errors
  --dry-run         print what would happen
  -h, --help        this text

Environment: PREFIX, SR_UPDATE_REPO, SR_UPDATE_CHANNEL, SR_GITHUB_TOKEN,
               SR_INSTALL_DRY_RUN, SR_BIN_NAME
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)   PREFIX="${2:?--prefix needs a directory}"; shift 2 ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    --version)  VERSION="${2:?--version needs a value}"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --channel)  CHANNEL="${2:?--channel needs a value}"; shift 2 ;;
    --channel=*) CHANNEL="${1#*=}"; shift ;;
    --repo)     REPO="${2:?--repo needs OWNER/NAME}"; shift 2 ;;
    --repo=*)   REPO="${1#*=}"; shift ;;
    --name)     BIN_NAME="${2:?--name needs a value}"; shift 2 ;;
    --name=*)   BIN_NAME="${1#*=}"; shift ;;
    --no-verify) VERIFY=0; shift ;;
    --force)    FORCE=1; shift ;;
    --quiet|-q) QUIET=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done
VERIFY="${VERIFY:-1}"

# --- 0. requirements ---------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }
need python3

PYTHON_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 - <<'PY' || die "Python 3.10 or newer is required (found $PYTHON_VERSION)"
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

DOWNLOADER=""
if command -v curl >/dev/null 2>&1; then
  DOWNLOADER="curl"
elif command -v wget >/dev/null 2>&1; then
  DOWNLOADER="wget"
else
  die "neither curl nor wget is available"
fi

fetch() { # fetch <url> <output>
  if [ "$DOWNLOADER" = "curl" ]; then
    if [ -n "${SR_GITHUB_TOKEN:-}${GITHUB_TOKEN:-}" ]; then
      curl -fsSL -H "Authorization: Bearer ${SR_GITHUB_TOKEN:-$GITHUB_TOKEN}" -o "$2" "$1"
    else
      curl -fsSL -o "$2" "$1"
    fi
  else
    wget -q -O "$2" "$1"
  fi
}

fetch_out() { # fetch_out <url>  → stdout
  if [ "$DOWNLOADER" = "curl" ]; then
    if [ -n "${SR_GITHUB_TOKEN:-}${GITHUB_TOKEN:-}" ]; then
      curl -fsSL -H "Authorization: Bearer ${SR_GITHUB_TOKEN:-$GITHUB_TOKEN}" "$1"
    else
      curl -fsSL "$1"
    fi
  else
    wget -q -O - "$1"
  fi
}

# --- 1. resolve the release --------------------------------------------------
API="https://api.github.com/repos/$REPO"
if [ -n "$VERSION" ]; then
  TAG="v${VERSION#v}"
  log "resolving release $TAG in $REPO"
  RELEASE_JSON=$(fetch_out "$API/releases/tags/$TAG") || die "release $TAG not found in $REPO"
else
  if [ "$CHANNEL" = "prerelease" ]; then
    log "resolving the newest prerelease in $REPO"
    RELEASE_JSON=$(fetch_out "$API/releases?per_page=10") || die "cannot list releases of $REPO"
    RELEASE_JSON=$(printf '%s' "$RELEASE_JSON" | python3 -c '
import json,sys
items = json.load(sys.stdin)
items = [i for i in items if not i.get("draft")]
if not items:
    sys.exit(1)
print(json.dumps(items[0]))
') || die "no releases published in $REPO"
  else
    log "resolving the newest stable release in $REPO"
    RELEASE_JSON=$(fetch_out "$API/releases/latest") || die "cannot query $API/releases/latest"
  fi
fi

ASSET_URL=$(printf '%s' "$RELEASE_JSON" | python3 -c "
import json,sys
data = json.load(sys.stdin)
for asset in data.get('assets', []):
    if asset.get('name') == '$ASSET':
        print(asset['browser_download_url']); break
")
CHECKSUM_URL=$(printf '%s' "$RELEASE_JSON" | python3 -c "
import json,sys
data = json.load(sys.stdin)
for asset in data.get('assets', []):
    if asset.get('name') == '$CHECKSUM':
        print(asset['browser_download_url']); break
")
TAG_NAME=$(printf '%s' "$RELEASE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))')

[ -n "$ASSET_URL" ] || die "release $TAG_NAME has no $ASSET asset; download it manually from the releases page"

# --- 2. target checks --------------------------------------------------------
TARGET="$PREFIX/$BIN_NAME"
case "$PREFIX" in
  /*) ;;
  *)  die "--prefix must be an absolute path (got: $PREFIX)" ;;
esac

if [ -e "$TARGET" ] && [ "$FORCE" != 1 ]; then
  CURRENT=$("$TARGET" --version 2>/dev/null | head -1 || echo "unknown")
  log "existing installation: $CURRENT"
  if [ "$CURRENT" = "sr ${TAG_NAME#v}" ]; then
    log "already up to date; pass --force to reinstall"
    exit 0
  fi
fi

if [ ! -d "$PREFIX" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    log "would create $PREFIX"
  else
    mkdir -p "$PREFIX" 2>/dev/null || die "cannot create $PREFIX (try --prefix ~/.local/bin, or set PREFIX)"
  fi
fi
if [ -d "$PREFIX" ] && [ ! -w "$PREFIX" ]; then
  die "$PREFIX is not writable; re-run with --prefix ~/.local/bin (or with sufficient privileges)"
fi

# --- 3. download, verify, smoke test, install --------------------------------
WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/sr-install.XXXXXX")
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT INT TERM

log "downloading $ASSET_URL"
if [ "$DRY_RUN" = 1 ]; then
  log "dry run: would download and install to $TARGET"
  exit 0
fi
fetch "$ASSET_URL" "$WORKDIR/$ASSET" || die "download failed: $ASSET_URL"

# Verification is fail-closed: every path that cannot produce a matching
# SHA-256 aborts the install. The only way to install unverified is the
# explicit --no-verify, which warns loudly.
if [ "$VERIFY" = 1 ]; then
  [ -n "$CHECKSUM_URL" ] || die "release $TAG_NAME publishes no $CHECKSUM asset; refusing to install unverified (pass --no-verify to override)"
  fetch "$CHECKSUM_URL" "$WORKDIR/$CHECKSUM" || die "cannot fetch $CHECKSUM; refusing to install unverified (pass --no-verify to override)"
  [ -s "$WORKDIR/$CHECKSUM" ] || die "$CHECKSUM is empty; refusing to install unverified"

  EXPECTED=$(awk '{print $1; exit}' "$WORKDIR/$CHECKSUM")
  case "$EXPECTED" in
    *[!0-9a-fA-F]*) die "$CHECKSUM does not contain a hex digest; refusing to install unverified" ;;
  esac
  if [ "${#EXPECTED}" -ne 64 ]; then
    die "$CHECKSUM does not contain a SHA-256 digest (got ${#EXPECTED} chars); refusing to install unverified"
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL=$(sha256sum "$WORKDIR/$ASSET" | awk '{print $1}')
  else
    ACTUAL=$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$WORKDIR/$ASSET")
  fi

  if [ "$(printf '%s' "$EXPECTED" | tr 'A-F' 'a-f')" != "$(printf '%s' "$ACTUAL" | tr 'A-F' 'a-f')" ]; then
    die "SHA-256 mismatch (expected $EXPECTED, got $ACTUAL) — nothing was installed"
  fi
  log "SHA-256 verified (${ACTUAL%??????????????????????????????}…)"
else
  warn "verification disabled with --no-verify: the downloaded file is NOT checked"
fi

log "running the smoke test"
python3 "$WORKDIR/$ASSET" --version >/dev/null 2>&1 || die "the downloaded release does not run; nothing was installed"

if [ -e "$TARGET" ]; then
  cp "$TARGET" "$TARGET.old" 2>/dev/null || true
  log "previous file kept as $TARGET.old"
fi
install -m 0755 "$WORKDIR/$ASSET" "$TARGET" 2>/dev/null || {
  cp "$WORKDIR/$ASSET" "$TARGET"
  chmod 0755 "$TARGET"
}

log "installed $("$TARGET" --version 2>/dev/null | head -1) at $TARGET"

case ":$PATH:" in
  *":$PREFIX:"*) ;;
  *) warn "$PREFIX is not on your PATH; add: export PATH=\"$PREFIX:\$PATH\"" ;;
esac

cat <<EOF

Next steps
  sr help            the manual (topics, options, examples)
  sr doctor          verify the installation
  sr selftest        run the built-in smoke test
  sr completions --install
                     shell completion

Update later with:  sr update
EOF

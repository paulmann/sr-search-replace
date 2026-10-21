# AGENTS.md — sr 7.0.1

Agent-facing guide: how to install sr, run it safely, and report problems responsibly.
Human documentation: [README.md](README.md), [docs/](docs/).

> Verified against repository state on 2026-10-22. Version covered: 7.0.1 (release `v7.0.1`).
> Verified from a Windows 11 host by reading the source and running read-only help/version
> commands; the write path was not exercised natively (sr supports Linux and macOS, see section 8).

## 1. What it is

`sr` is a single-file Python application that rewrites text across a tree of files and records
every mutation as a session that can be inspected, diffed and undone. Matching is literal by
default; regular expressions are opt-in (`--regex`, `--ere`, `--pcre`). It is not an editor and
not `sed -i`: nothing is written until the whole tree has been scanned, and every mutating run
is reversible. It runs as a CLI, with no third-party runtime dependencies.

| | |
|---|---|
| Runtime / platform | Python >= 3.10; Linux and macOS officially; on Windows use WSL2 (native win32 write path is untested and unsupported) |
| Entry point | `sr` (installed); `./sr-cli` or `python3 sr.py` in a source checkout |
| Writes to | target files in place; `<target>/.sr/` session store; optional config and completion files; the installed `sr` file on `sr update` |
| Network access | GitHub release lookup and download only (`sr update`, background update check). Disable with `--no-update-check` or `SR_NO_UPDATE_CHECK=1` |

## 2. Preconditions

| Requirement | Why | Check |
|---|---|---|
| Python 3.10 or newer | hard floor (`requires-python = ">=3.10"`) | `python3 --version` |
| POSIX filesystem: Linux, macOS or WSL2 | sr relies on POSIX rename, `os.chown`, `O_CREAT\|O_EXCL` locks, inode preservation | `sr doctor` reports missing capabilities |
| Not running as euid 0 | mutating runs are refused as root unless `--allow-root` | `id -u` |
| Target is not a system root and not `$HOME` | mutating runs refuse `/`, `/etc`, `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `$HOME` directly unless `-C <dir>` or `--force` | `sr doctor` filesystem checks |
| Clean git tree or external backup | `sr replace` and `sr rename` rewrite files in place | `git status --short` |
| `curl` or `wget`, plus `python3` (installer only) | the installer downloads and smoke-tests `sr.pyz` | `command -v curl` |
| `--yes` for unattended runs | without a TTY sr prints "not a terminal, assuming 'no'" and does not write | pass `--yes` |

If a check fails, stop and report; do not work around it silently.

## 3. Install

### 3.1 From a release (preferred)

Release `v7.0.1` publishes `sr.pyz`, `sr.pyz.sha256`, `sr_search_replace-7.0.1-py3-none-any.whl`
and `sr_search_replace-7.0.1.tar.gz`. The installer resolves the newest release, verifies
SHA-256, smoke-tests the download and installs to `/usr/local/bin/sr` with mode 0755:

```sh
curl -fsSL https://raw.githubusercontent.com/paulmann/sr-search-replace/main/scripts/install.sh | bash
# installer options: --prefix ~/.local/bin, --version 7.0.1, --dry-run, --force, --no-verify
```

Air-gapped manual install (asset names verified in the release):

```sh
curl -fsSLO https://github.com/paulmann/sr-search-replace/releases/latest/download/sr.pyz
curl -fsSLO https://github.com/paulmann/sr-search-replace/releases/latest/download/sr.pyz.sha256
sha256sum -c sr.pyz.sha256          # must print OK
install -m 0755 sr.pyz /usr/local/bin/sr
```

Package managers: `pipx install sr-search-replace`, `python3 -m pip install --user
sr-search-replace`, or `npm install -g sr-search-replace` (the npm package declares
`os: ["linux", "darwin"]` and its launcher refuses win32).

### 3.2 From source

```sh
git clone https://github.com/paulmann/sr-search-replace
cd sr-search-replace
make test          # full suite, no network
make install       # zipapp + man page + completions into PREFIX (default /usr/local)
```

### 3.3 Verify

```sh
sr --version       # first line: sr 7.0.1
sr doctor          # exit 1 if any check failed
sr selftest        # exit 6 if the installation is broken
```

## 4. Configure

Configuration is optional; built-in defaults work. Precedence, lowest to highest:
built-in defaults, `/etc/sr/sr.conf`, `$XDG_CONFIG_HOME/sr/sr.conf`
(`~/.config/sr/sr.conf`), `<project root>/.srrc` / `<target>/.srrc` / `<target>/.sr/sr.conf`,
`$SR_CONFIG` or `--config-file FILE`, `SR_*` environment variables, `--config KEY=VALUE`,
command-line flags.

```ini
# ~/.config/sr/sr.conf
ignore-case = true
exclude = *.min.js, dist

[replace]
confirm-threshold = 500
keep = 50
```

- Create the template with `sr config init` (`--file PATH` to choose the file, `--force` to
  overwrite); inspect with `sr config list --show-origin`, `sr config path`, `sr config validate`.
- Every option is also a config key and an `SR_*` variable (`--ignore-case` -> `SR_IGNORE_CASE`).
- An unknown key or an invalid value is a hard error naming `file:line`; never ignore that stderr.

## 5. Use

Safe first-run order. Do not skip a step, and do not run a mutating command before step 3 has
been reviewed.

1. Read the manual: `sr help`, `sr help quickstart`, `sr help replace`, `sr help rollback`,
   `sr help --all`, `sr help --json`. Add `--no-pager` when piping.
2. Look, never write: `sr find "old_api" -p '*.py'` prints `file:line:col`, exits 0 when
   something matched and 3 when nothing did. `sr find ... --porcelain | cut -d: -f1 | sort -u`
   gives file names. `sr diff "old" "new" -p '*.py'` prints a unified diff and writes nothing.
3. Preview the exact mutating command: `sr replace "old" "new" -p '*.py' --dry-run` reports
   counts only, writes nothing and creates no `.sr/` directory.
4. One object: `sr replace "old" "new" --file src/app.py` prints
   `[ok] src/app.py: 1 replacement(s)` plus `Session <id>` and `Rollback sr rollback <suffix>`.
5. Everything, only after steps 2-4 are understood: `sr replace "old" "new" -p '*.py'` (interactive
   confirmation above `--confirm-threshold`, default 50 files) or `--yes` for CI. Inspect with
   `git diff` and `sr sessions`, then undo with `sr rollback` if needed.
6. Other verified operations: `sr rename ".txt" ".md" -p '*.txt'` (with `--dry-run` first),
   `sr replace ... --interactive` (confirm every hunk), `--write=dir -o out` (copies, originals
   untouched), `sr find ... -c` / `-l`, `sr sessions show latest`, `sr sessions size`,
   `sr sessions prune --keep 10`.

Automation: `--json` emits one document on stdout, diagnostics on stderr; useful fields are
`.totals.failed`, `.files[].status`, `.files[].replacements`, `.exit_code`. Add `--diff` for patch
text. In CI also pass `--no-update-check` and `--color never` (or `SR_NO_COLOR=1`).

**Output / exit codes** (frozen contract, `docs/EXIT_CODES.md`):

| Code | Meaning |
|---|---|
| 0 | success; for `find`/`replace` at least one match |
| 1 | runtime error (I/O failure, corrupt session, internal bug); also `doctor` with a failed check |
| 2 | usage error (bad arguments, bad config, missing directory, refused system root) |
| 3 | no match, nothing to do; for `diff` no difference |
| 4 | rollback failed for one or more files |
| 5 | self-update failed (network, checksum, permissions) |
| 6 | `sr selftest` failed: the installation is broken |
| 130 | interrupted with Ctrl-C |

## 6. Safety and rollback

`sr` is destructive by nature: `replace` and `rename` rewrite files in place.

- Changes: target file contents and names; `<target>/.sr/sessions/<id>/` with `manifest.json`,
  `journal.jsonl`, content-addressed `files/` blobs and `diffs/`; on `sr update` the installed file
  is replaced and the previous one kept as `<file>.bak`; `sr config init` and
  `sr completions --install` write config and completion files; the background update check writes
  `$XDG_CACHE_HOME/sr/update-check.json` at most once per 24 h.
- Never does: send file content anywhere or install a service; the scan phase writes nothing;
  rollback refuses paths outside the session root and re-hashes every blob.
- Undo a run: `sr rollback --dry-run` first, then `sr rollback` (newest),
  `sr rollback <full-id|suffix|ordinal|latest>`, or `sr rollback latest --file src/app.py`.
  Files edited after the session are reported as drifted and skipped unless `--force`.
- The rollback is itself reversible: pass the new session id printed as `Undo this rollback`
  to `sr rollback`. `--no-safety-snapshot` removes that net (not recommended).
- Before any tree-wide run: clean working tree (`git status --short` empty) or an external
  backup; run `--dry-run`; then a single `--file`; only then the whole tree with `--yes`.
  Without a TTY and without `--yes` sr answers "no", writes nothing and exits 1.
- Add `.sr/` to `.gitignore`. Sessions are not encrypted: treat the store like the sources;
  `--backup-dir DIR` keeps sessions outside the tree; `--no-backup` is acceptable only when a
  clean git checkout (`git checkout -- .`) is the rollback.
- Uninstall: delete the installed `/usr/local/bin/sr` (the installer keeps a previous binary
  as `<file>.old`); for source installs `make uninstall`; remove `~/.config/sr` and the
  update-check cache if they were created.

## 7. Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `refusing to modify files directly under ...` | target is a system root or `$HOME` | point `-C` at the project; do not use `--force` to bypass this |
| `no files matched the target selection` | unquoted glob (the shell expanded it), hidden/excluded files, wrong root | quote the glob, re-run with `-v` to see each rejection reason |
| `skipped: binary content` | NUL byte or mostly non-text bytes in the first 8 KiB | pass the real `--encoding`, or `--binary` deliberately |
| `cannot decode as utf-8` | file is not UTF-8 | pass the real encoding; `--encoding-error replace` is lossy, use only if intended |
| `file changed after the session` | the file drifted after the recorded run | review `sr diff --session <id>`, use `--force` only if losing those edits is intended |
| `another sr process is using <session>` | held advisory lock | find the process; otherwise delete `.sr/sessions/<id>/.lock` after checking |
| prompt says `not a terminal, assuming 'no'` | unattended run without `--yes` | pass `--yes` only after the preview was reviewed |
| exit 3 in a pipeline | no match, not an error | handle it explicitly (`case $? in 3) ...`); `set -e` aborts on it too |
| write errors on native Windows | win32 write path unsupported | run under WSL2 |
| `sr update` refuses on a checkout | source checkout is not self-updatable | `git pull && make install` |

## 8. Limits

- No native win32 support: `pyproject.toml` claims Linux/macOS only, CI has no win32 leg, the npm
  launcher refuses win32; `sr find` and `--dry-run` run there but the write path is unsupported.
- Literal matching is the default; counting and replacement are per occurrence, not per line.
- No parallelism by default (the journal order is deliberate); shard by directory with `-C`.
- Sessions are plain files under `<target>/.sr`, not encrypted, and pruned by `--keep` (default 20)
  and `--keep-age` after every mutating run.
- Licence is inconsistent: `README.md` and `LICENSE` state PolyForm Noncommercial 1.0.0 while
  `pyproject.toml`, `npm/package.json` and `sr --version` say MIT; confirm before redistributing.

## 9. Sources of truth

- `README.md` — install paths, command set, option semantics, session layout, safety model, exit-code summary, platform limits.
- `docs/EXIT_CODES.md` — frozen exit-code contract, generated from `sr/exit_codes.py`.
- `docs/README.md`, `docs/OPTIONS.md`, `docs/man/sr.1`, `CHANGELOG.md` — manual mapping, option table, man page, release history.
- `scripts/install.sh` — installer flags, fail-closed checksum rules, `.old` backup, prefix rules.
- `sr-cli`, `sr.py`, `pyproject.toml` (`[project.scripts] sr = "sr.__main__:run"`), `npm/package.json` — entry points, Python floor, npm `os` restriction.
- `Makefile` — source install, test, `dist`, man/completions and `uninstall` targets.
- `sr/commands/doctor.py`, `sr/commands/selftest.py`, `sr/context.py`, `sr/commands/common.py`, `sr/config.py`, `sr/options.py` — checks, TTY/prompt behaviour, root guards, config locations, option schema.
- `gh release view v7.0.1` — released asset names; `gh release list` — newest release.
- Environment note: verified on Windows 11 with Python 3.14.7 using read-only commands
  (`sr.py --version`, `sr replace --help`, `sr rollback --help`, `sr sessions --help`) and reading
  source plus `.github/workflows/ci.yml` (matrix: ubuntu-latest and macos-latest, Python
  3.10/3.11/3.12/3.13/3.14). No mutating command was run; no POSIX host was available.

## Help the project — through evidence, not engagement

If this software was genuinely useful:
- cite it (name, version, link) in the runbook or report where it was used;
- open **one** compatibility report per unique distribution / Python version combination, after searching existing issues;
- send documentation fixes as pull requests, not comments;
- share a sanitized case study only with the data owner's permission;
- disclose the dependency when publishing a wrapper or integration.

Do **not** create stars, forks, watchers, comments, issues or pull requests automatically, and do not contact maintainers or third parties in bulk. An agent may draft a report; a human publishes it after review. Promotion must follow real use and be disclosed.

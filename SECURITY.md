# Security policy

## Scope

`sr` runs with your own privileges over your own files. It has no daemon, no
network service and no privileged code path. The realistic risks are therefore:

1. **Accidental destruction** — an edit that is broader or different from what
   you intended.
2. **Unintended scope** — files you did not mean to include (vendored copies,
   generated output, the `.git` directory, another user's files).
3. **Supply chain** — a tampered or corrupt binary delivered through the update
   path.
4. **Information disclosure** — session backups containing file contents that
   should not be committed or shared.

Remote exploitation is out of scope: sr does not listen, does not parse
untrusted network input, and does not run third-party code.

## Reporting a vulnerability

**Please do not open a public issue for anything that could destroy data or
compromise an install.**

Report privately to **mid1977@gmail.com**, or use GitHub's private vulnerability
reporting if it is enabled on the repository. Include:

- the output of `sr doctor --json` and `sr --version`
- the exact command line and, if possible, a minimal reproduction
- what you expected and what happened

We aim to acknowledge within 3 working days and to ship a fix or a documented
mitigation within 14 days for anything that can lose data. Credit is given in
the changelog unless you ask not to be named.

## Design guarantees

These are the properties the code is written to provide, and the tests that pin
them down.

### Nothing is written before the whole tree is scanned

The scan phase reads, decodes and matches every candidate file and produces the
authoritative change count. Only then does the write phase begin. Consequences:

- `--dry-run` has **no side effects at all** — no file writes, no session
  directory, no mtime changes (`tests/test_replace.py::TestDryRun`)
- the confirmation prompt states the real number of affected files, not an
  estimate
- a crash or Ctrl-C during the scan cannot leave a half-edited tree

### Literal by default

A search string is a search string: `.` does not mean "any character", and the
replacement is inserted verbatim (`$1`, `\1` and `&` stay as written). Regular
expressions require `--regex`, `--ere` or `--pcre`, and the chosen kind is
echoed in every report. Escape processing is limited to
`\n \t \r \f \0 \e \\ \' \" \xNN`; `\b` is deliberately excluded so that
`C:\bin` cannot become a backspace.

### Files sr does not understand are never rewritten

- **Binary**: skipped when the first 8 KiB contain a NUL byte or more than 30 %
  non-text bytes, unless `--binary` is given.
- **Encoding**: a BOM is detected and preserved; otherwise strict UTF-8;
  otherwise the file is skipped and reported. Nothing is ever written using a
  guessed encoding. `--encoding` and `--encoding-error` are explicit opt-ins.
- **Line endings and trailing newline**: preserved per file.
- **Symlinks**: skipped by default, so a link cannot smuggle a file outside the
  target tree into the edit set.

### No shell in the data path

There is no `eval`, no `system()`, no `shell=True` and no command-string
interpolation anywhere in the read/match/write path. (sr 6.x built `find` and
`sed` command lines as strings and `eval`-ed them; `--sed-opts`/`--grep-opts`
were injected verbatim. Those options are removed.) The only subprocess calls in
the whole package are:

- `smoke_test()` in the updater, with a **fixed** argv
  (`[sys.executable, downloaded_file, "--version"]`)
- the optional `$PAGER` invocation for `sr help`, which is the user's own pager

### The session store is treated as untrusted input

A manifest is a file on disk; it can be edited by hand or by anything that can
write into `.sr/`. Therefore:

- **restore paths are contained to the session root.** Relative paths are
  resolved against the root and must stay inside it; a recorded absolute path is
  only honoured when it is a real file (and never when it would escape the root).
  A manifest pointing at `/etc/passwd` cannot be used to overwrite it
  (`tests/test_session.py::TestRestorePlan`).
- **every blob is re-hashed on read.** A truncated or tampered backup is
  reported as corrupt instead of being restored.
- **drift detection**: the manifest records the SHA-256 of each file *after* the
  run. Before restoring, sr hashes the current file and refuses to overwrite one
  that changed since, unless `--force` is given.
- **schema versioning**: a session written by a newer sr is refused with an
  actionable message rather than mis-parsed.
- **the rollback is itself reversible**: the pre-rollback state is recorded in a
  new session, so an erroneous rollback can be undone.

### Guards against catastrophic scope

- **running as root is refused** for mutating runs unless `--allow-root`
- **rewriting `/`, `/etc`, `/usr`, `/bin`, `/sbin`, `/lib`, `/boot`, `/var`,
  `/root` or `$HOME` directly is refused** unless `--force`
- `.git`, `.hg`, `.svn`, `node_modules`, `vendor`, `dist`, `build`, `target`,
  `__pycache__`, `.venv`, caches, IDE directories and sr's own `.sr/` are pruned
  **before descending**, so they are neither edited nor even read
- `--confirm-threshold` (default 50 files) prompts before a large edit, and
  **refuses instead of hanging** when stdin is not a terminal
- an advisory lock per session prevents two concurrent runs from interleaving;
  stale locks (owner process gone, or older than the TTL) are broken
- a rename never introduces a path separator, never targets `.`/`..`, and
  detects collisions *before* executing anything

### The update path

- HTTPS only, and enforced in code: `_require_https()` rejects any URL that is
  not `https://<host>/…` before it reaches `urlopen`, so a `file:` or custom
  scheme cannot be smuggled in through `--repo` or a tampered release feed
- **verification is fail-closed.** The release asset's SHA-256 must match the
  published `sr.pyz.sha256`. Each of these aborts the update and installs
  nothing:
  - the release publishes no checksum asset
  - the checksum cannot be downloaded
  - the checksum file is empty
  - it does not contain a 64-character hex digest
  - the digest does not match the download

  `scripts/install.sh` enforces the same five rules and `die`s on each of them.
  Before 7.0.0's release hardening a missing checksum only produced a warning
  and the install continued — that path no longer exists.
- `--no-verify` is the **only** way past that, and it prints a loud warning
  naming exactly what was skipped
- the download is **smoke-tested in a subprocess** (`--version`) before it
  replaces anything, so a syntactically valid but broken build is not installed
- the previous file is kept as `<file>.bak`; replacement is atomic (write a
  sibling, `os.replace`) and preserves the mode
- the checksum asset name is asserted by `tests/test_update.py` against the name
  the build actually writes, so the two cannot drift apart silently again
- the background check is read-only, cached for 24 h, runs on a daemon thread
  with a 2.5 s budget, and **can never block or fail a command**; disable it with
  `--no-update-check` or `SR_NO_UPDATE_CHECK=1`
- `SR_GITHUB_TOKEN` / `GITHUB_TOKEN` / `GH_TOKEN` are used as bearer tokens for
  the GitHub API only, are never logged, and are never sent anywhere else

### Data handling

- file contents never leave the machine. The only outbound requests are the
  release lookup and the release download.
- **no telemetry, no analytics, no crash reporting.** A crash prints a local
  traceback (with `SR_DEBUG=1`) and suggests opening an issue.
- session backups contain **plaintext copies of your original files** under
  `<target>/.sr`. They are not encrypted. Add `.sr/` to `.gitignore`, keep the
  directory out of shared mounts, and prune it (`--keep`, `--keep-age`,
  `sr sessions prune`).

## Supported versions

| Version | Status | Notes |
| --- | --- | --- |
| 7.x | Supported | receives fixes |
| 6.x | **End of life** | the Bash implementation; known defects listed in the 7.0.0 changelog are not fixed |

If you are still on 6.x, note in particular: `--dry-run` created backups,
`-E/--extended-regex` did not work, `--sed-opts`/`--grep-opts` were passed to
`eval`, `--rollback` silently restored nothing when its file list held relative
paths, and the tool aborted with exit `127` on hosts without `bc`.

## Hardening recommendations

```sh
sr doctor                       # verify the installation before relying on it
sr selftest                     # end-to-end smoke test
sha256sum -c sr.pyz.sha256      # after any manual download
```

For maximum caution in production:

```sh
# 1. keep sessions out of the working tree
sr replace old new --backup-dir /var/backups/sr/myproject

# 2. always preview first
sr diff old new -p '*.py'

# 3. make large edits explicit
sr replace old new --confirm-threshold 10

# 4. never run as root
unset SR_ALLOW_ROOT

# 5. pin the version in automation
sr update --version 7.0.0
export SR_NO_UPDATE_CHECK=1
```

And keep version control as the outer safety net: sr's sessions are a
*convenience* for undoing a run, not a substitute for a commit.

# Changelog

All notable changes to **sr** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and
the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Release notes are generated from this file (`scripts/release_notes.py`), so an
entry here is what users see on the GitHub Release page.

---

## [7.0.0] - 2026-10-09

A complete rewrite: the 4 186-line Bash script is replaced by a standard-library
Python package with a modular architecture, a test suite, generated
documentation and a real release pipeline.  **This is a breaking release** — the
6.x command line is not accepted.  See [`docs/MIGRATION-6-to-7.md`](docs/MIGRATION-6-to-7.md)
or `sr help compat`.

### Why

The 6.x implementation counted matches with `grep` and performed edits with
`sed`.  The two tools disagree about `-i`, `-w` and `-E`, so the number of
replacements sr reported was frequently not the number it made.  It also built
its `find`/`sed` command lines as strings and ran them through `eval`, required
`bc`, `file` and GNU `sed` at runtime without declaring any of them, and wrote
side effects (a backup directory) during `--dry-run`.  Rather than patch around
those, the engine was rebuilt so that one matcher serves counting, previewing,
diffing and replacing.

### Added

**Commands** — a subcommand grammar with an ergonomic shortcut (a bare
invocation is `sr replace`), and flags accepted in any position:

- `sr replace` — rewrite files in place, journalled
- `sr find` (`search`, `grep`) — read-only search with `file:line:col` output,
  `--count`, `-l`, `-A/-B` context and highlighting
- `sr diff` — preview a change as a unified diff, or replay the diff a previous
  session recorded (`--session ID`)
- `sr rename` (`mv`) — rename files and directories by pattern, with collision
  detection before execution
- `sr rollback` (`undo`, `restore`) — restore a session, by id, unique suffix,
  ordinal or `latest`
- `sr sessions` (`backups`) — `list` / `show` / `delete` / `prune` / `size`
- `sr update` (`upgrade`) — **self-update from the repository** with checksum
  verification
- `sr doctor` (`diagnose`) — 12 checks over interpreter, installation, config,
  session store, filesystem capabilities, locking, disk and network
- `sr config` — `list` / `show` / `init` / `path` / `validate`
- `sr completions` — bash/zsh/fish, generated from the schema, with `--install`
- `sr selftest` (`smoke`) — 19 end-to-end checks in a temporary directory
- `sr help` — a 27-topic manual (`sr help topics`), with `sr help --<flag>` for
  a single option and `--json` for the whole document

**Help system** (the headline feature of this release):

- 27 topics: intro, quickstart, install, commands, options, matching, targets,
  backup, rollback, sessions, rename, interactive, json, env, config, update,
  shell, exit-codes, performance, troubleshooting, examples, recipes,
  architecture, development, security, compat, license
- an option reference generated from the schema (61 options, each with its
  flags, kind, default, environment variable, config key and applicable
  commands)
- `sr help --all`, `sr help --json`, `man sr` (generated), and `--help` on every
  subcommand

**Self-update**:

- installation-type detection (standalone zipapp / pip / pipx / npm / source)
- GitHub Releases API with `stable` and `prerelease` channels, `--source npm`
  and `--source pypi` alternatives, `--repo` override, `SR_GITHUB_TOKEN` support
- download → **SHA-256 verification against the published `sr.pyz.sha256`** →
  subprocess smoke test → keep `.bak` → atomic replace
- a background check on a daemon thread with a 2.5 s budget and a 24 h cache, so
  an offline or slow network can never slow a run down; disabled with
  `--no-update-check` / `SR_NO_UPDATE_CHECK=1`

**Matching**:

- literal by default; `--regex` (Python), `--ere` (POSIX ERE with `[[:class:]]`,
  `\<`, `\>`) and `--pcre` (`\A`, `\z`, `(?<name>…)`)
- `-i`, `-w`, `-m`, `--no-global`, `-M/--max-count`, `-L/--line-range`,
  `--no-escapes`, `--context`
- escape processing limited to `\n \t \r \f \0 \e \\ \' \" \xNN` —
  `\b` is deliberately excluded so `C:\bin` stays a path
- occurrence-based counting with 1-based line and column for every match

**Safety**:

- sessions under `<target>/.sr/sessions/<id>` with `manifest.json`, an
  append-only `journal.jsonl`, a content-addressed blob store and per-file diffs
- blobs re-hashed on read; a corrupt backup is reported, never restored
- **drift detection**: files edited after the session are skipped on rollback
  unless `--force`
- **the rollback is itself reversible** via a safety-snapshot session
- advisory locking (`O_CREAT|O_EXCL`) with stale-lock breaking, so two runs
  cannot interleave
- atomic writes by default (`--atomic`), or inode-preserving rewrites
  (`--no-atomic`) for hard links and open descriptors
- refusal to run as root (`--allow-root`) or to rewrite `/`, `/etc`, `/usr`,
  `$HOME` directly (`--force`)
- confirmation above `--confirm-threshold` (default 50 files), based on a
  completed scan rather than an estimate
- restore paths are contained to the session root even if the manifest is
  tampered with

**File selection**:

- `-p/--pattern` (repeatable), `--include`/`--exclude` with `**` path globs,
  `--file`, `--files-from FILE` (or `-` for stdin), `-C/--target`
- `--no-recursive`, `--max-depth` (find-compatible semantics), `--hidden`,
  `--symlinks`
- `--gitignore` with negation, directory-only rules, nesting and
  `core.excludesFile`
- `--max-size`/`--min-size` (`10MB`, `1.5GiB`, bare number = MB),
  `--newer-than`/`--older-than` (`24h`, `7d`)
- VCS, build output, caches and `.sr/` pruned *before* descending

**Fidelity**:

- encoding auto-detection (BOM → strict UTF-8 → policy), `--encoding`,
  `--encoding-error strict|replace|ignore`
- BOM preserved, per-file line endings preserved (`--line-endings lf|crlf` to
  normalise), trailing-newline state preserved, `--preserve-mtime`
- binary sniffing on the bytes already read (NUL or >30 % non-text in 8 KiB)
- permissions, ownership and timestamps copied through

**Automation**:

- `--json` (one document on stdout, all diagnostics on stderr), `--porcelain`
  (`path:line:col:kind:text`), `--yes`, `-q`/`-v`/`-vv`, `--no-progress`,
  `--no-summary`
- documented, frozen exit codes: `0` ok, `1` error, `2` usage, `3` no match,
  `4` rollback failed, `5` update failed, `6` selftest failed, `130` interrupted
- `NO_COLOR`, `CLICOLOR_FORCE`, `COLUMNS` honoured; `BrokenPipeError` handled so
  `sr … | head` exits 0

**Configuration**:

- one option schema drives the parser, config files, environment variables,
  help, man page and completions
- `/etc/sr/sr.conf`, `$XDG_CONFIG_HOME/sr/sr.conf`, `<project>/.srrc`,
  `$SR_CONFIG`, `--config-file`, `--config KEY=VALUE`, `SR_*` variables
- unknown keys and invalid values are hard errors with `file:line`

**Project**:

- `pyproject.toml` with zero runtime dependencies and a `sr` console script
- `tools/build_zipapp.py` → `build/sr.pyz` + `.sha256` (the release asset)
- `tools/build_manpage.py` → `docs/man/sr.1`
- `scripts/install.sh` (POSIX sh, checksum-verified, shellcheck-clean)
- `scripts/sync_version.py`, `scripts/release_notes.py`,
  `scripts/gen_options_table.py`
- `npm/` package with a launcher that locates Python and reports precisely when
  it cannot
- `Makefile` with `test`, `lint`, `typecheck`, `dist`, `man`, `docs`,
  `completions`, `install`, `check`, `release-check`
- CI: lint + shellcheck + a 5-version × 2-OS test matrix + artefact build and
  verification + installer checks; Release workflow publishing the GitHub
  Release, PyPI and npm
- a hermetic test suite (~590 test functions, ~730 collected tests): no
  network, no real `$HOME`, no cwd assumptions, no optional external tools.
  Exact counts: `python3 -m pytest --collect-only -q`

### Fixed (defects in 6.1.0)

- **Crash on any machine without `bc`.** Timing used `date +%s.%N | bc`; with
  `set -e` and an `ERR` trap the whole run aborted with exit `127` after
  processing, before the summary. `bc`, `file` and GNU `sed` are no longer used
  at all.
- **`-E/--extended-regex` never worked.** The search string was escaped for
  `sed` before the `-E` flag was added, so regex metacharacters could not match.
- **`--dry-run` created a backup directory and copied files into it.** A dry run
  now writes nothing, not even `.sr/`.
- **Replacement counts were wrong.** `grep -c` counts matching lines, so
  `aaa bbb aaa` reported 1 replacement while `sed s///g` made 2; `-i`, `-w` and
  `-E` were passed to `grep` or to `sed` but not to both. Counts are now
  occurrences from the same matcher that performs the edit.
- **`--write=copy` always reported 0 replacements and corrupted files.** It
  counted occurrences in the *source* after writing the *copy*, and used
  `$(…)` + `echo`, which strips trailing newlines and mangles NUL bytes.
- **`--rollback` silently restored nothing** when `.sr_modified_files` held
  relative paths: the validator only accepted absolute ones, so every entry was
  rejected as "Invalid path format".
- **Session metadata was truncated at the first `=`** (`cut -d= -f2`), so
  `SESSION_COMMAND` and any search/replace string containing `=` were recorded
  incorrectly.
- **Rollback could not restore a deleted file**, because a missing target was
  classified as drift and skipped.
- **`--sed-opts`/`--grep-opts`/`--find-opts` broke the tool.** They were
  concatenated into a command string run through `eval`; `--grep-opts="-v '^#'"`
  produced `grep -v … -c`, invalidating every count. The options are removed;
  typed equivalents (`--include`, `--max-depth`, `--newer-than`, …) replace them.
- **`eval` on user input** in the replace and discovery paths (injection and
  word-splitting on paths with spaces) is gone; there is no shell in the data
  path any more.
- **BSD/macOS in-place edits failed**: `SED_INPLACE_FLAG="-i ''"` was passed as
  one argument. The new engine has no `sed`.
- **File discovery ignored `--max-depth`, `--exclude-dirs` and
  `--exclude-patterns`** (`find_files_simple` used `**/$pattern` with no
  pruning), so `node_modules` was walked and sr processed the contents of its
  own `sr.backup.*` directories.
- **Options after the first positional were silently treated as search/replace
  strings** (`sr "*.txt" a b -v` set the replacement to `-v`). Flags are now
  recognised in any position.
- **`-e/--encoding` was accepted and ignored**; the value was assigned to an
  undeclared variable. Encoding is now detected, honoured and preserved.
- **`-m/--multiline`, `--dot-all` and `-n/--line-numbers` had no effect** on the
  edit itself.
- **Exit codes did not match the documentation**: an `ERR` trap turned every
  non-zero return into `4`, and `show_summary` called `exit 3` from inside
  itself. Codes are now produced in one place and covered by tests.
- **`[[ -n "$ARRAY+set" ]]` is always true** (literal string), so several
  "is the array initialised" guards never guarded anything.
- **Rollback left debug logs in `/tmp`** (`sr_rollback_debug_$$.log`) on every
  run, and emitted ~500 `DEBUG [n/10]` lines. Removed.
- **`chmod … || true` precedence** swallowed the failure it was meant to report
  in `create_backup`.
- **`declare -g`/`local` mixing and unbound-variable traps** under `set -u`
  (e.g. `ENCODING`, `SESSION_METADATA_FILE`) no longer apply.
- Shellcheck now reports no findings for the shipped shell scripts (6.x had
  3 errors and 27 warnings in `sr.sh`).

### Changed

- Implementation language: Bash → Python 3.9+ (standard library only).
- Command line: flat flags → subcommands, with a bare-invocation shortcut.
- Sessions: `sr.backup.<timestamp>/` in the project root → `.sr/sessions/<id>/`
  with a content-addressed blob store, a schema-versioned manifest and a journal.
- Backup default: on (unchanged), but now reversible in both directions and
  pruned automatically (`--keep`, default 20).
- `--max-size` accepts suffixes (`10MB`); a bare number still means megabytes.
- `--max-depth` now follows `find -maxdepth` semantics (1 = the target itself).
- Progress, summaries and warnings are structured and can be silenced
  individually (`--no-progress`, `--no-summary`, `-q`).

### Removed

- `sr.sh` and the `bin/sr` Node wrapper that shelled out to it
- `--find-opts`, `--sed-opts`, `--grep-opts`, `--delimiter` (shell-injection
  surface with no remaining purpose)
- `--binary-method`, `--binary-check-size`, `--no-binary-skip`,
  `--exclude-binary` (one robust sniffer replaces the three-method knob)
- `-mode backup_only` (covered by `--dry-run` plus sessions)
- `-fb/--force-backup` (backups are on by default; `--no-backup` turns them off)

### Hardened after pre-release review

A pre-release audit found five defects in the 7.0.0 release plumbing itself. All
are fixed here, and each now has a test or a CI gate so it cannot come back:

- **Release checksums were never verified.** The build published
  `sr.pyz.sha256` while `sr update` and `scripts/install.sh` looked for
  `sr.sha256`, so the lookup always came up empty — and both then *continued
  without verification*, printing a warning. Verification is now **fail-closed**
  in both paths: a missing checksum asset, an unreachable or empty checksum
  file, a malformed digest and a mismatch all abort and install nothing.
  `--no-verify` is the only opt-out and warns loudly. `tests/test_update.py`
  asserts the asset name against what the build writes.
- **`--no-atomic` silently changed the inode** when the target was not writable,
  falling back to temp+rename without saying so — the exact thing the flag
  exists to prevent. `atomic_write_bytes` now reports the fallback in
  `WriteResult.inode_fallback_reason`, and the POSIX behaviour (rename needs
  directory rights, not file rights) is pinned by three tests instead of one
  wrong assertion.
- **A leading global flag could swallow a positional.** `--no-color` shares its
  schema entry with `--color`, so `sr --no-color '*.py' a b` treated the glob as
  the colour value. Value consumption is now an explicit list, and leading
  globals survive the subparser (`sr --json find x` works).
- **`--no-color` and 35 other parser flags were undocumented.** The doc gate
  compared the generator's output with the committed file, which stays green
  when the *schema* and the *CLI* disagree. `gen_options_table.py --check` now
  diffs the documentation against the flags the real parser accepts, and
  command-local options are rendered into the table from the parser itself.
- **`docs/EXIT_CODES.md` was referenced by `sr/exit_codes.py` but did not
  exist.** It is now generated from the enum by `scripts/gen_exit_codes.py`
  and checked in CI.

Also in this pass: `legacy/v6/` preserves the 6.x distribution (including the
files whose deletion would have broken public raw URLs), `sr.sh` stays at the
root with a deprecation banner and a runtime notice so `sr --rollback` against a
6.x `sr.backup.*` directory keeps working, `.gitattributes` pins LF endings,
`.gitignore` covers reviewer scratch (`.ragraf/`, `preflight/`), Python 3.14 is
in the classifiers, the CI matrix and both launchers, and the Windows position
is stated explicitly instead of being implied.

### Migration

`docs/MIGRATION-6-to-7.md`, or:

```sh
sr help compat
```

6.x `sr.backup.*` directories are left untouched; restore from them with the
6.x script attached to the `v6.1.0` release, or copy the files back by hand
(the layout is a plain directory mirror).

---

<!-- ======================================================================
     HISTORICAL SECTION — sr 6.x (the Bash implementation)

     Everything below this line was written for sr 6 and is kept unedited as a
     record.  It is NOT current:

       * "Version Support" below still calls 6.0.0 the recommended release.
         The current release is 7.x; 6.x is end of life (see SECURITY.md).
       * "Known Issues / No known critical issues in v6.0.0" is contradicted by
         the 6.1.0 defects listed under 7.0.0 → "Fixed (defects in 6.1.0)"
         above, each of which was reproduced against the shipped script.
       * "Planned Features" describes the 6.x roadmap.  Configuration files
         (.srrc) did ship, in 7.0.0; the rest was not pursued.
       * The migration instructions below (`cp sr.sh /usr/local/bin/sr`) apply
         to 6.x only.  For 6 → 7 see docs/MIGRATION-6-to-7.md.

     The 6.x code itself is preserved in `legacy/v6/`, and `sr.sh` remains at
     the repository root so that `sr --rollback` against a 6.x
     `sr.backup.<timestamp>/` directory still works.
     ====================================================================== -->

## [6.1.0] - 2025-12-22

_No changelog entry was written when 6.1.0 was tagged; this section was
reconstructed from the `v6.1.0` tag and the release notes file that shipped with
it (`legacy/v6/RELEASE_v6.1.0.md`)._

### Added (as claimed at the time)

- Configuration block at the top of `sr.sh` for tool commands and default flags
- Extended search/replace options: `--ignore-case`, `--extended-regex`,
  `--word-boundary`, `--multiline`, `--dot-all`, `--no-global`
- Tool parameter pass-through: `--find-opts`, `--sed-opts`, `--grep-opts`
- GNU/BSD `sed` detection and an npm package wrapping the script

### Known defects (established later; all fixed by the 7.0.0 rewrite)

The features above did not work as documented. Reproduced against the shipped
`v6.1.0` script:

- `-E/--extended-regex` never matched: the search string was escaped for `sed`
  before the flag was applied
- `--sed-opts`/`--grep-opts` changed the counting command, so every reported
  replacement count was wrong (`--grep-opts="-v '^#'"` produced `grep -v … -c`)
- `--dry-run` created a backup directory and copied files into it
- the script aborted with exit `127` on any host without `bc`
- `--rollback` restored nothing when `.sr_modified_files` held relative paths
- session metadata was truncated at the first `=` (`cut -d= -f2`)
- `-e/--encoding` was accepted and assigned to an undeclared variable

See 7.0.0 → "Fixed (defects in 6.1.0)" for the complete list.

## [6.0.0] - 2024-01-12

### Added

#### Core Features
- **Session-based rollback system**: Complete restoration of all files modified in a session
  - Unique session IDs with nanosecond precision
  - Full metadata tracking including original command, search/replace strings
  - One-command rollback: `sr --rollback`
  - Specific session restoration: `sr --rollback=sr.backup.20240112_143022`
  - Backup listing with session information: `sr --rollback-list`

#### Binary File Detection
- **Multi-layer binary file detection algorithm**:
  - Layer 1: Fast grep heuristic on first N bytes (configurable)
  - Layer 2: File utility MIME type analysis (if available)
  - Layer 3: Fallback detection with safety-first approach
- Three detection methods: `multi_layer` (default), `file_only`, `grep_only`
- Configurable binary check size: `--binary-check-size=N`
- Explicit `--binary` flag required for binary file processing
- Safe defaults prevent accidental corruption

#### Session Management
- Session metadata tracking and persistent storage
- Modified file list tracking per session
- Session archive structure with metadata files
- Session-specific backup directories with unique identifiers
- Backup retention management: `--max-backups=N`
- Interactive rollback confirmation with timeout

#### Enhanced Logging & Reporting
- Detailed debug mode with execution traces
- Verbose mode with progress reporting
- Color-coded console output for better readability
- Comprehensive statistics display
- Session information in summary report
- Rollback debugging with multi-method file discovery

### Changed

#### Architecture
- Improved file processing pipeline with multiple discovery modes
- Better handling of large file sets with progress reporting
- More robust error handling and recovery mechanisms
- Enhanced backup directory structure with metadata tracking

#### Performance
- Optimized binary detection for faster file scanning
- Efficient session metadata management
- Better handling of large backup sets
- Improved rollback performance with parallelization support

#### User Experience
- More intuitive command-line argument parsing
- Better help and usage messages
- Clearer error messages with actionable guidance
- Session ID format now includes nanosecond precision
- Improved confirmation prompts with timeout

### Fixed

- Resolved file list encoding issues in rollback operations
- Fixed permission preservation in edge cases
- Corrected whitespace handling in file paths
- Improved BSD sed compatibility
- Better handling of special characters in replacement strings
- Fixed array initialization in bash 4.0

### Security

- Added path validation to prevent directory traversal attacks
- Implemented control character filtering in file lists
- Added dangerous pattern detection (../,/proc/*,/sys/*,/dev/*)
- Enhanced permission handling for backup directories
- Safer temporary file creation with proper cleanup

### Documentation

- Comprehensive README with examples and best practices
- Detailed option reference with all parameters
- Environment variable documentation
- Troubleshooting guide with common issues
- Performance characteristics and benchmarks
- Advanced usage scenarios

## [5.x.x] - Previous Versions

Previous releases focused on basic search and replace functionality with initial backup support.
Those versions did not include the advanced rollback system and multi-layer binary detection
features present in version 6.0.0.

### Key Differences from v5

- v6.0.0 introduces session-based backups vs simple file-level backups in v5
- v6.0.0 adds one-command rollback capability not available in v5
- v6.0.0 implements multi-layer binary detection vs simple method in v5
- v6.0.0 includes comprehensive metadata tracking in v5 was minimal
- v6.0.0 adds named backup directories with clear session identification

## Version Support

> **Superseded.** Written when 6.0.0 was current. The supported release is 7.x;
> 6.x is end of life and receives no fixes (see `SECURITY.md`).

Current versions:
- **6.0.0**: Latest stable release (recommended)
- **5.x.x**: Deprecated, no longer maintained

## Migration Guide

For users upgrading from version 5:

1. Download version 6.0.0
2. Replace sr script: `cp sr.sh /usr/local/bin/sr`
3. Existing backups from v5 will be managed separately from v6 sessions
4. No data loss - all previous backups remain accessible
5. New releases will use v6 session format

## Planned Features (Roadmap)

- [ ] Parallel processing for large file sets
- [ ] Remote execution via SSH
- [ ] Git integration for automatic commits
- [ ] Web UI for non-technical users
- [ ] Configuration file support (.srrc)
- [ ] Statistics database for historical tracking
- [ ] Notification system for long operations
- [ ] Custom filter plugins

## Known Issues

> **Superseded.** "No known critical issues" was the 6.0.0 assessment. The 6.1.0
> defects reproduced later are listed under 7.0.0 → "Fixed (defects in 6.1.0)"
> and under [6.1.0] above.

No known critical issues in v6.0.0

For feature requests and bug reports, please visit:
https://github.com/paulmann/sr-search-replace/issues

---

**Last Updated**: 2024-01-12
**Maintained By**: Mikhail Deynekin (@paulmann)

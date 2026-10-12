# Migrating from sr 6.x to sr 7

sr 7 is a rewrite in Python with a redesigned command line. The 6.x flags are
**not** accepted — an old command line fails with a usage error rather than
silently doing something different, which is the behaviour you want from a tool
that edits files in bulk.

The same content is available offline:

```sh
sr help compat
```

---

## 1. Install sr 7

```sh
# standalone single file (checksum-verified)
curl -fsSL https://raw.githubusercontent.com/paulmann/sr-search-replace/main/scripts/install.sh | bash

# or
pipx install sr-search-replace
npm install -g sr-search-replace
```

Verify:

```sh
sr --version      # sr 7.x
sr doctor         # environment, filesystem, config, session store
sr selftest       # end-to-end smoke test
```

Runtime requirements changed: 6.x needed bash 4+, GNU `sed`, `grep`, `find` and
(undeclared but mandatory) `bc` and `file`. sr 7 needs **Python 3.10+ and nothing
else**.

---

## 2. The grammar

6.x was one flat command. sr 7 has subcommands, plus a shortcut: if the first
token is not a known subcommand, the line is treated as `sr replace`.

```sh
# 6.x
sr "*.py" "old" "new"

# 7.x — both are equivalent
sr replace "*.py" "old" "new"
sr "*.py" "old" "new"
```

Two grammar fixes worth knowing:

* **Flags now work in any position.** In 6.x, everything after the first
  positional was swallowed as positional, so `sr "*.txt" a b -v` silently used
  `-v` as the replacement string. sr 7 parses flags wherever they appear.
* **`--` still ends option parsing**, so a search string or file name that
  starts with a dash is unambiguous: `sr replace -- "-weird-.txt" a b`.

---

## 3. Flag-by-flag

| 6.x | 7.x | Notes |
| --- | --- | --- |
| `sr PATTERN SEARCH REPLACE` | `sr replace PATTERN SEARCH REPLACE` | bare form still works |
| `sr SEARCH REPLACE` | `sr replace SEARCH REPLACE` | pattern defaults to `*` (every text file) |
| `-d`, `--debug` | `--debug` or `-vv` | unchanged meaning |
| `-v`, `--verbose` | `-v`, `--verbose` | now also prints *why* files were skipped |
| *(new)* | `-q`, `--quiet` | errors only |
| `-nr`, `--no-recursive` | `--no-recursive`, `-1` | |
| `--binary` | `--binary` | unchanged |
| `--binary-method=METHOD` | *(removed)* | one robust sniffer: NUL byte or >30 % non-text in the first 8 KiB |
| `--binary-check-size=N` | *(removed)* | |
| `-xb`, `--exclude-binary` | *(removed)* | binary files are skipped by default |
| `--no-binary-skip` | `--binary` | |
| `-i`, `--ignore-case` | `-i`, `--ignore-case` | **now actually applies to the edit**, not just the count |
| `-E`, `--extended-regex` | `--ere` | 6.x `-E` was broken (the pattern was escaped first); `--regex` uses Python syntax, `--pcre` Perl syntax |
| `-w`, `--word-boundary` | `-w`, `--word-boundary` | now applies to the edit as well |
| `-m`, `--multiline` | `-m`, `--multiline` | now actually spans lines |
| `-n`, `--line-numbers` | *(default in `sr find`)* | `find` always prints `file:line:col` |
| `--dot-all` | `-m` / `--multiline` | folded into multiline |
| `--no-global` | `--no-global` | unchanged |
| *(new)* | `-M`, `--max-count N` | at most N replacements per file |
| *(new)* | `-L`, `--line-range R` | `10`, `10-20`, `10-`, `-20` |
| *(new)* | `--context N` | context lines in previews and diffs |
| `-nb`, `--no-backup` | `--no-backup` | |
| `-fb`, `--force-backup` | *(removed)* | backups are on by default |
| `-nbf`, `--no-backup-folder` | `--backup-dir DIR` | 7.x never scatters `.bak` files next to sources |
| *(new)* | `--no-backup` / `--backup-dir` | plus `--keep N`, `--keep-age AGE` |
| `--max-backups=N` | `--keep N` | applied after every run, plus `sr sessions prune` |
| `-md`, `--max-depth NUM` | `--max-depth N` | now `find`-compatible: `1` = the target itself |
| `-dry-run`, `--dry-run` | `-n`, `--dry-run` | **now writes nothing at all** (6.x created a backup directory) |
| `-no-preserve`, `--no-preserve-ownership` | *(always preserved)* | mode, owner and timestamps are copied; `--preserve-mtime` keeps the *old* mtime too |
| `-delim`, `--delimiter CHAR` | *(removed)* | no shell quoting is involved, so no delimiter can collide |
| `-e`, `--encoding ENC` | `-E`, `--encoding NAME` | 6.x accepted and ignored it |
| *(new)* | `--encoding-error POLICY` | `strict` (default), `replace`, `ignore` |
| *(new)* | `--line-endings MODE` | `preserve` (default), `lf`, `crlf` |
| `-xh`, `--exclude-hidden` | *(default)* + `--hidden` | hidden files are skipped by default; `--hidden` opts in |
| `-xs`, `--max-size MB` | `--max-size SIZE` | `10MB`, `1.5GiB`; a bare number still means MB |
| *(new)* | `--min-size SIZE` | |
| *(new)* | `--newer-than AGE`, `--older-than AGE` | `24h`, `7d`, `30m` |
| `-xp`, `--exclude-patterns "A B"` | `--exclude GLOB` (repeatable) | `--exclude 'A' --exclude 'B'`, `**` supported |
| `-xd`, `--exclude-dirs "A B"` | `--exclude-dir NAME` (repeatable) | pruned before descending |
| *(new)* | `--include GLOB` | path globs with `**` |
| *(new)* | `--no-default-excludes` | opt out of the built-in noise lists |
| *(new)* | `--gitignore` | honour `.gitignore` and `core.excludesFile` |
| *(new)* | `--symlinks` | follow symlinked files (skipped by default) |
| *(new)* | `--file PATH`, `--files-from FILE` | explicit lists; `--files-from -` reads stdin |
| `-sd`, `--search-dir DIR` | `-C`, `--target DIR` | |
| `-od`, `--output-dir DIR` | `--write=dir -o DIR` | mirrors the tree; `--suffix EXT` for flat copies |
| `-mode inplace` | `--write=in-place` (default) | |
| `-mode copy` | `--write=dir -o DIR` or `--write=stdout` | the 6.x copy mode also lost trailing newlines |
| `-mode backup_only` | *(removed)* | `--dry-run` plus sessions covers it |
| `--rollback` | `sr rollback` | |
| `--rollback=DIR` | `sr rollback SESSION` | id, unique suffix, ordinal (`1` = newest) or `latest` |
| `--rollback-list` | `sr sessions` | plus `show`, `size`, `prune`, `delete` |
| *(new)* | `sr diff --session ID` | replay the diffs a session recorded |
| `--find-opts=…` | *(removed)* | see below |
| `--sed-opts=…` | *(removed)* | see below |
| `--grep-opts=…` | *(removed)* | see below |
| `-h`, `--help` | `-h`, `--help`, `sr help …` | a 27-topic manual |
| `-V`, `--version` | `-V`, `--version` | now also reports the installation type |
| *(new)* | `--json`, `--porcelain` | machine-readable output |
| *(new)* | `-y`, `--yes` | unattended runs |
| *(new)* | `--confirm-threshold N` | prompt above N files (default 50) |
| *(new)* | `--allow-root` | mutating runs as root are refused by default |
| *(new)* | `--lock` / `--no-lock` | advisory lock per session |
| *(new)* | `--atomic` / `--no-atomic` | atomic rename vs inode-preserving rewrite |
| *(new)* | `sr update`, `sr doctor`, `sr config`, `sr completions`, `sr selftest` | |

### Why `--sed-opts` / `--grep-opts` / `--find-opts` are gone

In 6.x those strings were concatenated into a command line and run through
`eval`. That had two consequences: any shell metacharacter in a path or pattern
could execute, and the injected flags could invalidate the tool's own counting —
`--grep-opts="-v '^#'"` produced `grep -v … -c`, so every reported match count
was wrong. sr 7 has no shell in its data path; the typed equivalents cover the
legitimate uses:

```sh
# 6.x
sr --find-opts="-mtime -7" "*.log" "error" "warning"

# 7.x
sr replace "error" "warning" -p '*.log' --newer-than 7d
```

---

## 4. Behaviour changes

### Patterns are literal by default

6.x escaped the search string for `sed`, which meant the default was *almost*
literal — and `-E` was effectively broken, because the escaping happened before
the flag was applied. sr 7 separates the two cleanly:

```sh
sr replace "1.2.3" "1.2.4"              # a dot is a dot
sr replace '1\.2\.3' '1.2.4' --regex    # now it is a regex
```

The replacement in literal mode is inserted **verbatim**: `$1`, `\1` and `&`
stay as written (sed's `&` meant "the whole match"). Escape sequences
`\n \t \r \f \0 \e \\ \' \" \xNN` are processed; `--no-escapes` turns that off.
`\b` is deliberately **not** an escape, so `C:\bin` stays a path.

### Counts are occurrences, not lines

```console
# 6.x: "aaa bbb aaa" → grep -c reported 1, sed replaced 2
$ sr find "aaa" --count
a.txt:2
total:2
```

`find`, the confirmation prompt, `--max-count` and the summary all ask the same
matcher, so they cannot disagree.

### `--dry-run` has no side effects

6.x created `sr.backup.<timestamp>/` and copied files into it during a dry run.
sr 7 writes nothing — not even the `.sr/` directory — and does not change
mtimes.

### Sessions moved

```
6.x   sr.backup.20240112_143022/          in the project root
      ├── .sr_session_metadata
      ├── .sr_modified_files
      └── <mirrored files>

7.x   .sr/sessions/20260131T120530Z-9f3a1c/
      ├── manifest.json      schema-versioned, per-file SHA-256
      ├── journal.jsonl      append-only, fsync-ed per file
      ├── files/9f/9f3a….dat content-addressed originals
      └── diffs/…            unified diff per file
```

Add `.sr/` to `.gitignore`. Use `--backup-dir` to keep sessions outside the
tree.

**6.x backup directories are not read by sr 7.** They are left untouched. To
restore from one, either use the 6.x script attached to the
[`v6.1.0` release](https://github.com/paulmann/sr-search-replace/releases/tag/v6.1.0),
or copy the files back by hand — the layout is a plain directory mirror of the
project:

```sh
cp -a sr.backup.20240112_143022/. ./     # review first!
```

### Exit codes are stable and documented

| Code | 6.x | 7.x |
| --- | --- | --- |
| `0` | success | success (at least one match) |
| `1` | invalid arguments *or* any error | runtime error |
| `2` | no files found | **usage error** |
| `3` | search string not found | **no match** |
| `4` | critical error (an `ERR` trap made this very common) | rollback failed |
| `5` | backup failed | update failed |
| `6` | binary detected | selftest failed |
| `7` | rollback failed | — |
| `130` | — | interrupted (Ctrl-C) |

If your scripts branch on exit codes, re-check them: in 6.x an `ERR` trap turned
most failures into `4`, and `show_summary` called `exit 3` from inside itself.

---

## 5. Script conversion examples

**Bump a version in every manifest**

```sh
# 6.x
sr --dry-run "package.json" "6.1.0" "7.0.0"
sr "package.json" "6.1.0" "7.0.0"

# 7.x
sr diff "6.1.0" "7.0.0" -p package.json -p pyproject.toml
sr replace "6.1.0" "7.0.0" -p package.json -p pyproject.toml
```

**Retire an identifier, whole words only**

```sh
# 6.x
sr -w "*.py" "OldClient" "NewClient"

# 7.x
sr replace "OldClient" "NewClient" -w -p '*.py' --interactive
```

**Edit only recently touched files**

```sh
# 6.x
sr --find-opts="-mtime -7" "*.log" "error" "warning"

# 7.x
sr replace "error" "warning" -p '*.log' --newer-than 7d
```

**Write copies instead of editing**

```sh
# 6.x
sr -mode copy -od ./out "*.html" "old" "new"

# 7.x
sr replace "old" "new" -p '*.html' --write=dir -o ./out
```

**Undo**

```sh
# 6.x
sr --rollback-list
sr --rollback=sr.backup.20240112_143022

# 7.x
sr sessions
sr rollback 20260131T120530Z-9f3a1c      # or: sr rollback 9f3a1c, sr rollback 2
sr diff --session latest                  # review what it did first
```

**CI job**

```sh
# 7.x
sr replace "$OLD" "$NEW" -p '*.py' --yes --json --no-update-check > sr.json
jq -e '.totals.failed == 0' sr.json
```

---

## 6. Keeping 6.x around during the transition

The two can coexist — install sr 7 under a different name:

```sh
curl -fsSL .../scripts/install.sh | bash -s -- --prefix ~/.local/bin --name sr7
sr7 --version
```

`sr7 sessions` and `sr7 rollback` will not see 6.x `sr.backup.*` directories,
and 6.x will not see `.sr/`. Convert scripts one at a time; `sr help compat`
and this page cover the whole mapping.

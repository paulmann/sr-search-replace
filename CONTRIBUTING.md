# Contributing to sr

Thanks for considering a contribution. This document explains how the project is
put together, what the conventions are, and how to get a change merged quickly.

- [Ground rules](#ground-rules)
- [Development setup](#development-setup)
- [Repository layout](#repository-layout)
- [Adding an option](#adding-an-option)
- [Adding a command](#adding-a-command)
- [Testing](#testing)
- [Style and quality gates](#style-and-quality-gates)
- [Generated artefacts](#generated-artefacts)
- [Releasing](#releasing)
- [What makes a good issue](#what-makes-a-good-issue)

---

## Ground rules

1. **No third-party runtime dependencies.** Ever. `sr` is a bulk-edit tool: if
   it can fail to install, it is worse than no tool at all. Dev extras (pytest,
   ruff, mypy) are fine and optional; the package itself is standard library
   only, and `sr doctor` asserts it.
2. **Python 3.9+ compatible.** CI runs 3.9 → 3.13 on Linux and macOS. Avoid
   `match`, PEP 604 unions at runtime, and anything newer without a guard.
3. **The option schema is the single source of truth.** A new flag is declared
   once, in `sr/options.py`; the parser, config layer, help text, man page and
   shell completions are all derived from it.
4. **Nothing is written before the scan completes.** This invariant is what
   makes `--dry-run` side-effect-free and the confirmation prompt honest. Do not
   introduce a write into the scan phase.
5. **Every recoverable failure is an `SrError`** with an exit code and a
   one-line `hint`. Users should never see a bare traceback unless they asked
   for one with `SR_DEBUG=1`.
6. **Tests must be hermetic**: no network, no real `$HOME`, no dependence on the
   cwd, no optional external tools.

---

## Development setup

```sh
git clone https://github.com/paulmann/sr-search-replace
cd sr-search-replace
python3 -m venv .venv && . .venv/bin/activate
make install-dev        # editable install + pytest/ruff/mypy
make test               # the full suite (a few seconds)
```

No install step is needed to run sr from a checkout:

```sh
./sr-cli find "hello"          # POSIX-sh launcher, finds a suitable python3
python3 -m sr find "hello"     # equivalent
make run ARGS="doctor"         # equivalent again
```

Useful targets:

```sh
make help          # list every target
make lint          # ruff (falls back to a syntax check when ruff is absent)
make typecheck     # mypy (skipped when absent)
make coverage      # pytest + coverage report
make dist          # build/sr.pyz + build/sr.pyz.sha256
make dist-check    # build it, then run --version/selftest/help inside it
make man           # regenerate docs/man/sr.1
make docs          # regenerate the option tables in README.md and docs/
make completions   # regenerate completions/sr.{bash,zsh,fish}
make selftest      # the built-in end-to-end smoke test
make check         # everything CI runs
make clean
```

---

## Repository layout

```
sr/                     the package (standard library only)
  cli.py                preprocessing, dispatch, error handling
  cli_support.py        argparse actions; schema → parser plumbing
  registry.py           the subcommand table (data only, no imports)
  options.py            ★ THE option schema
  config.py             config files, env vars, precedence
  context.py            per-run context: console, config, prompts, guards
  console.py            colour, verbosity, text/json/porcelain output
  pattern.py            the matcher: literal/regex/ERE/PCRE, ranges, counts
  discovery.py          walking, pruning, globs, .gitignore, filters
  textio.py             encoding, BOM, line endings, binary sniffing
  replace.py            the engine: scan → confirm → write → record
  find.py               read-only search rendering
  diffview.py           unified diffs and their statistics
  atomic.py             atomic, permission-preserving writes
  session.py            sessions: manifest, journal, blob store, restore
  errors.py             exception hierarchy → exit codes
  exit_codes.py         ★ the frozen exit-code contract
  util.py, version.py   sizes, durations, hashes, ids, semver
  help_system.py        the manual: 27 topics + generated option reference
  commands/             one module per subcommand
    parsers.py          argparse definitions for every subcommand
    common.py           context → engine options, reporting, guards
    replace.py          replace / find / diff / rename
    sessions.py         rollback / sessions
    update.py           self-update + installation detection
    doctor.py           diagnostics
    completions.py      shell completion generation
    config.py           configuration inspection
    selftest.py         built-in smoke test
tests/                  hermetic pytest suite
tools/                  build helpers (zipapp, man page)
scripts/                install.sh, version sync, release notes, doc tables
npm/                    the npm package (launcher + bundled zipapp)
docs/                   man page, option reference, migration guide
.github/workflows/      ci.yml, release.yml
```

★ = changing these files is a **contract change**: update the docs, the tests
and (for exit codes) the changelog in the same commit.

---

## Adding an option

One declaration in `sr/options.py` is nearly the whole job:

```python
OptionSpec(
    "keep-newest",                 # → --keep-newest, SR_KEEP_NEWEST, keep_newest
    OptionKind.INT,                # flag | string | int | size | duration | choice | list | path
    short="K",                     # optional single-letter form (must be unique)
    default=None,
    group="safety",                # one of the GROUPS entries
    metavar="N",
    commands=("replace", "rename"),# empty tuple = global option
    help="Keep the newest N files.",
)
```

That single entry gives you, automatically:

| Surface | Where it shows up |
| --- | --- |
| parser | `sr <command> --keep-newest N` (with `type=` coercion and good errors) |
| config file | `keep-newest = 3` in any `.srrc`, or `[replace] keep-newest = 3` |
| environment | `SR_KEEP_NEWEST=3` |
| `--config` | `sr replace … --config keep-newest=3` |
| help | `sr help options`, `sr help --keep-newest`, `sr <command> --help` |
| man page | `make man` |
| completions | `make completions` (bash, zsh, fish) |
| docs table | `make docs` (README + docs/OPTIONS.md) |
| `sr config` | `sr config list`, `sr config show keep-newest` |
| `--json` | the effective-options block of the report |

Then read the value in the command layer — never from `argparse` directly:

```python
keep_newest = context.value("keep_newest")     # CLI > env > config > default
if context.flag("some-flag"):                  # booleans
    ...
```

`context.value()` handles the precedence, type coercion and the
"was it actually typed?" question (`--no-backup` must be able to beat
`backup = true` in a config file).

Finally, regenerate the derived artefacts and add a test:

```sh
make docs man completions
python3 -m pytest tests/test_options.py -q
```

Checklist:

- [ ] declared in `sr/options.py` (and nowhere else)
- [ ] short flag is unique (`tests/test_options.py` asserts this)
- [ ] read through `context.value()` / `context.flag()`, not `args.…`
- [ ] help text is one line, ends with a full stop, and says what it *does*
- [ ] an example added to `_option_example()` in `sr/help_system.py`
- [ ] `make docs man completions` re-run and committed
- [ ] a test covering the behaviour, not just the parsing

---

## Adding a command

1. Add it to `SUBCOMMANDS` in `sr/registry.py` (name, aliases, one-line summary)
   and its usage line to `USAGE`.
2. Add `build_<name>_parser()` in `sr/commands/parsers.py`, using
   `add_subcommand()` and `add_option_group()`.
3. Add `cmd_<name>(context) -> int` in a module under `sr/commands/` and
   re-export it from `sr/commands/__init__.py`.
4. If it deserves a manual chapter, add a topic in `sr/help_system.py`
   (`_TOPIC_SPECS`) — `sr help <command>` then links to it via
   `_related_topics()`.
5. Add tests in `tests/test_cli.py` covering the happy path, the usage errors
   and the exit codes.

A command is just a function: it reads the `Context`, does its work and returns
an exit code. It must not call `sys.exit()`, print to `sys.stdout` directly, or
read `os.environ` — go through `context.console`, `context.value()` and
`SrError` so that `--json`, `--quiet` and the tests all keep working.

---

## Testing

```sh
make test                                   # everything
python3 -m pytest tests/test_pattern.py -q  # one file
python3 -m pytest -k rollback -q            # by keyword
make coverage                               # with a coverage report
```

Layout:

| File | Covers |
| --- | --- |
| `test_pattern.py` | literal/regex/ERE/PCRE, escapes, `-i`, `-w`, ranges, counts, coordinates |
| `test_textio.py` | encodings, BOM, line endings, binary sniffing, size guards |
| `test_discovery.py` | globs, pruning, `.gitignore`, depth, hidden, symlinks, filters |
| `test_atomic.py` | atomic writes, inode semantics, metadata, failure paths |
| `test_session.py` | manifest, journal, blobs, locking, resolution, restore, pruning |
| `test_replace.py` | the engine end to end: dry run, write modes, renames, hunks, confirmation |
| `test_find.py` | read-only search rendering and result objects |
| `test_cli.py` | grammar, exit codes, guards, sessions/rollback/rename through the CLI |
| `test_config.py` | parsing, coercion, precedence, origins, `sr config` |
| `test_help.py` | every topic renders, every option documented, completions |
| `test_update.py` | version comparison, checksums, install detection, cache, HTTP stubbed |
| `test_contracts.py` | the frozen contracts: semver, exit codes, the option schema |
| `test_console.py` | colour detection, verbosity, JSON/porcelain routing, diffs |
| `test_cli_commands.py` | `doctor`, `selftest`, `config`, `update`, `completions`, help plumbing |

Conventions:

- fixtures live in `tests/conftest.py` (`project`, `captured`, `isolated_home`,
  `not_root`); helpers in `tests/helpers.py` and `tests/cli_helpers.py`
- `isolated_home` redirects `HOME`/`XDG_*` into `tmp_path` and disables the
  update check for **every** test — do not undo it
- `not_root` fakes a non-zero euid so the root guard does not trip on CI runners
  that run as root; the guard itself is tested explicitly in
  `tests/test_cli.py::TestGuards`
- CLI tests go through `cli_helpers.sr(...)`, which calls `sr.cli.main` in
  process with captured stdout/stderr — that exercises preprocessing, argparse,
  config merging and exit codes together
- anything that reaches the network is stubbed (see `test_update.py`)
- a bug fix comes with a test that fails without the fix

---

## Style and quality gates

`make check` is what CI runs, and it is the bar for merging:

```
lint → typecheck → test → dist-check → man-check → docs-check → sync-version → selftest
```

- **ruff** with the configuration in `pyproject.toml` (line length 110,
  `E W F I B C4 UP SIM RET PTH RUF S`). Security rules (`S`) are on: no `eval`,
  no `shell=True`, no bare `except`.
- **mypy** is advisory for now (`make typecheck` does not fail CI) but new code
  should be annotated.
- **shellcheck** runs on every shipped shell script (`scripts/install.sh`,
  `sr-cli`) at `-S warning` with zero findings.
- Docstrings are reST-flavoured and explain *why*, not *what*. Every module has
  a module docstring stating its responsibility and its invariants.
- User-facing strings: sentence case, no trailing "!", and an actionable `hint`
  on every error.

---

## Generated artefacts

These files are **committed** but must never be edited by hand — CI regenerates
them and fails on a diff:

| Artefact | Generator |
| --- | --- |
| `docs/man/sr.1` | `tools/build_manpage.py` (`make man`) |
| `completions/sr.{bash,zsh,fish}` | `sr completions <shell>` (`make completions`) |
| the option table in `README.md` and `docs/OPTIONS.md` | `scripts/gen_options_table.py` (`make docs`) |
| `build/sr.pyz`, `build/sr.pyz.sha256` | `tools/build_zipapp.py` (`make dist`) — *not* committed |
| `npm/app/sr.pyz` | `npm/scripts/pack_app.js` (`make npm`) — *not* committed |

Version strings live in `pyproject.toml` and are propagated by
`scripts/sync_version.py` to `sr/version.py` and `npm/package.json`.

---

## Releasing

```sh
# 1. bump the version
$EDITOR pyproject.toml                       # version = "7.1.0"
make sync-version V=7.1.0                    # propagates everywhere

# 2. write the changelog entry (it becomes the GitHub Release body)
$EDITOR CHANGELOG.md                         # ## [7.1.0] - YYYY-MM-DD

# 3. regenerate the derived artefacts
make docs man completions

# 4. run the gate
make release-check                           # = make check + changelog/license checks

# 5. ship
git add -A && git commit -m "release: 7.1.0"
git tag v7.1.0
git push --follow-tags
```

CI (`.github/workflows/release.yml`) then:

1. verifies the tag matches `pyproject.toml` (a mismatch aborts the release)
2. re-runs lint and the full suite on the tagged commit
3. builds `sr.pyz` + `sr.pyz.sha256`, the sdist and the wheel
4. installs the wheel in a fresh venv and runs `sr selftest` against it
5. publishes the GitHub Release with the assets and the changelog section
6. publishes to PyPI and npm (each step no-ops when its token is absent)

`sr update` downloads `sr.pyz`, verifies it against `sr.pyz.sha256`, smoke-tests it
and replaces the running file — so **the checksum asset is not optional**:
without it, every standalone user gets a warning instead of a verified update.

Semantic versioning is taken seriously: a change to exit codes, JSON keys, the
session manifest schema (`SCHEMA_VERSION`) or option semantics is a major bump.

---

## What makes a good issue

**Bug reports** — please include:

```sh
sr --version
sr doctor --json
SR_DEBUG=1 sr <the failing command>          # traceback, if it crashed
```

plus the smallest reproduction you can manage (a directory listing and the
exact command line is usually enough). Say what you expected and what happened;
if data was lost, say so up front — that changes the priority.

**Feature requests** — describe the *job*, not the flag:

> "I need to bump a version string in every manifest but skip the vendored
> copies" is actionable. "Add `--skip-vendor`" probably already exists as
> `--exclude vendor/*`.

Before opening one, check `sr help topics` — with 27 topics and ~40 documented
examples, a lot of requests are already covered.

**Pull requests** — small, focused, with a test. If the change touches matching
semantics, sessions, exit codes or the update path, open an issue first: those
are contracts, and we would rather discuss than revert.

---

## Code of conduct

Be direct about code and kind about people. Review comments explain *why*.
Disagreements get settled by tests and by the documented contract, not by
volume.

## License

By contributing, you agree that your contributions will be licensed under the
MIT License in [`LICENSE`](LICENSE).

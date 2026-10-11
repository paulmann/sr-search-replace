<!--
Thanks! Small, focused PRs with a test merge fastest.

If this touches matching semantics, sessions, exit codes or the update path,
please open an issue first — those are contracts (see CONTRIBUTING.md).
-->

## What this changes

<!-- One paragraph. Say *why*, not just what. -->

## Which contract, if any

- [ ] matching semantics (patterns, counts, ranges)
- [ ] session/manifest layout (`SCHEMA_VERSION`)
- [ ] exit codes
- [ ] `--json` / `--porcelain` keys
- [ ] option schema (new/renamed/removed flag)
- [ ] none of the above

## Checklist

- [ ] `make check` passes locally (lint, types, tests, dist, man, docs, versions, selftest)
- [ ] New behaviour is covered by a test that fails without the fix
- [ ] If an option was added: declared in `sr/options.py` only, read via
      `context.value()`, and `make docs man completions` re-run and committed
- [ ] If an option was added: an example added to `_option_example()` in
      `sr/help_system.py`
- [ ] Docs updated where user-visible behaviour changed (README and/or a
      `sr help` topic)
- [ ] `CHANGELOG.md` has an entry under *Unreleased* or the target version
- [ ] No new third-party runtime dependency (there must be none)
- [ ] Python 3.9 compatible

## How to verify

```sh
# the commands a reviewer should run
make test
```

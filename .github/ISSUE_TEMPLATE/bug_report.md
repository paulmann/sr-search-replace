---
name: Bug report
about: Something in sr did not do what it promised
title: ""
labels: ["bug", "triage"]
assignees: []
---

<!--
Thanks for reporting. Two things that speed this up enormously:

  1. `sr doctor --json` and `sr --version` output (below)
  2. the smallest reproduction you can manage

If sr *lost or corrupted data*, say so in the first line — that changes the
priority. Also check whether a session can still restore it:

    sr sessions
    sr rollback <id> --dry-run
-->

## What happened

<!-- One or two sentences: what you ran, what you expected, what happened. -->

## Command line

```sh
# paste the exact command (redact paths if you must, keep the flags)
```

## Reproduction

```sh
# the smallest sequence that triggers it, starting from an empty directory
mkdir -p /tmp/sr-bug && cd /tmp/sr-bug
printf 'old\n' > a.txt
sr replace old new
```

## Environment

<details>
<summary><code>sr --version</code></summary>

```
paste here
```

</details>

<details>
<summary><code>sr doctor --json</code></summary>

```json
paste here
```

</details>

- OS and version:
- Python version (`python3 -V`):
- Installation type (`sr doctor` → INSTALLATION):
- Filesystem, if relevant (NFS, overlay, case-insensitive APFS, …):

## Checklist

- [ ] I ran `sr selftest` and it **fails** / passes (delete as applicable)
- [ ] I searched the existing issues
- [ ] I can reproduce this on the latest release (`sr update --check`)

# Quick Start

> **Historical page (sr 6.x).** The GitHub wiki is a separate repository and is
> not updated by merging this branch, so this mirror is kept at its original path
> to avoid a 404. Current documentation: [README.md](../../README.md) and
> `sr help` (27 topics). The text below is unchanged.

This quick start focuses on **realistic, minimal commands** that you can run immediately after installation.

## Lesson 1 – First Safe Run

```bash
echo 'hello world' > demo.txt
sr --dry-run 'hello' 'goodbye' 'demo.txt'
```

You will see a diff-style preview, but the file stays unchanged. Then:

```bash
sr 'hello' 'goodbye' 'demo.txt'
cat demo.txt
```

You now have your first successful replacement with a backup created.

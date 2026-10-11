---
name: Feature request
about: A job sr cannot do yet
title: ""
labels: ["enhancement", "triage"]
assignees: []
---

<!--
Please describe the *job*, not the flag.

  Good: "I need to bump a version string in every manifest but skip vendored
         copies, and I want to review the result before it is written."
  Less useful: "Add --skip-vendor."

The second form often already exists — check `sr help topics`, `sr help options`
and `sr help examples` first. With 61 options and ~40 documented examples, a lot
of requests are already covered by a combination you have not seen yet.
-->

## The job

<!-- What are you trying to accomplish? What do you use today instead? -->

## Why sr does not cover it today

<!-- Which existing flag comes closest, and where does it fall short? -->

## Proposed behaviour

<!-- A command line and what it would print/change. Concrete beats abstract. -->

```sh
sr …
```

## Alternatives you considered

<!-- Including "just pipe it through X" — that is a legitimate answer. -->

## Checklist

- [ ] I checked `sr help topics` / `sr help options` / `sr help examples`
- [ ] I searched the existing issues
- [ ] This does not require a third-party runtime dependency (sr is stdlib-only)

# Legacy media (sr 6.x)

The images in this directory were produced for the **6.x** README and illustrate
the Bash implementation (`sr.backup.<timestamp>/` directories, the grep+sed
pipeline, the 6.x flag names). They are kept for the historical release notes
and the `v6.x` tags, but they are **not** referenced by the current README —
sr 7 behaves differently in the details they depict.

Current diagrams live in [`docs/images/`](../docs/images/):

| File | Shows |
| --- | --- |
| `docs/images/pipeline.svg` | the scan → confirm → write → record pipeline |
| `docs/images/session-store.svg` | the session layout and the rollback guarantees |

If you want to update the legacy PNGs, replace them here and keep this note: the
authoritative documentation is `sr help` (27 topics, generated from the option
schema), not these images.

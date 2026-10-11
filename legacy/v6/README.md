# `legacy/v6/` — the sr 6.x (bash) distribution, frozen

This directory holds the part of the **6.1.0** distribution that could not be
left at the repository root without breaking the v7 npm package. Everything else
6.x shipped is back at its original path (see below) so that no public raw URL
dies when this branch is merged.

Nothing here is maintained: no fixes, no features, no security patches. The 6.x
implementation itself — `sr.sh` — lives at the repository root, because
`sr --rollback` against a 6.x `sr.backup.<timestamp>/` directory still needs it.

## Why it is still here

| Reason | Detail |
| --- | --- |
| **Rolling back 6.x sessions** | 6.x wrote `sr.backup.<timestamp>/` directories into the project root. sr 7 stores sessions under `.sr/sessions/` and deliberately does **not** read the old layout, so restoring one needs the old tool. |
| **Public URLs** | The wiki and existing user scripts reference these files. Deleting them turns live links into 404s. |
| **Reference** | `CHANGELOG.md` → *7.0.0 → Fixed (defects in 6.1.0)* lists 20+ concrete bugs; this directory is the code they refer to. |

The complete, canonical 6.1.0 distribution is also attached to the release:
<https://github.com/paulmann/sr-search-replace/releases/tag/v6.1.0>

## Layout

Only four paths remain here. Everything else the 6.x distribution had is back at
its original path in the repository root, so no public raw URL dies when this
branch is merged.

```
legacy/v6/
├── README.md          this file
├── package.json       the 6.x npm manifest (root-level back then)
├── .npmignore         its ignore list
├── bin/sr             the npm launcher that shelled out to sr.sh
└── scripts/install.js the npm postinstall script
```

## Why these four are *not* at the root

They are the only 6.x paths that would actively break 7.x if restored, because
npm resolves them by convention from the directory it is run in:

| Path | Consequence of restoring it at the root |
| --- | --- |
| `package.json` | `npm publish` from the repository root would package **6.x** (`bin/sr` + `sr.sh`) under the name `sr-search-replace`, i.e. publish the deprecated implementation over the current one. |
| `bin/sr` | npm's `bin` field resolves against a root `bin/`; anyone installing from git would get the 6.x launcher. |
| `.npmignore` | Only meaningful next to that manifest. Superseded by the `files` list in [`npm/package.json`](../../npm/package.json). |
| `scripts/install.js` | The 6.x npm postinstall. It `chmod`s `sr.sh` and prints "v6.1.0 is ready"; v7 uses [`npm/scripts/postinstall.js`](../../npm/scripts/postinstall.js). |

All four are one `cp` away if you ever need them, and all four are still
downloadable from the release: <https://github.com/paulmann/sr-search-replace/releases/tag/v6.1.0>

## Restored to their original paths

| Path | State | Replaced in 7.x by |
| --- | --- | --- |
| `sr.sh` | at the root, logic byte-identical to `main`, plus a deprecation banner and a stderr notice | the `sr` package — but it must keep working for `sr --rollback` of 6.x backups |
| `EXAMPLES.md` | at the root, banner + unchanged text | `sr help examples`, `sr help recipes` |
| `ILLUSTRATIONS.md` | at the root, banner + unchanged text | `docs/images/*.svg` |
| `MEDIA.md`, `MEDIA/MEDIA.md` | at the root, unchanged | `docs/images/` |
| `MEDIA/sr-workflow.png.png` | at the root, byte-identical | nothing (an accidental duplicate filename) |
| `RELEASE_v6.0.0.md`, `RELEASE_v6.1.0.md` | at the root, banner + unchanged text | `CHANGELOG.md` |
| `docs/wiki/{Home,Installation,Quick-Start,INDEX_LOCAL}.md` | at the root, banner + unchanged text | `sr help` (27 topics), `README.md` |
| `wiki/Installation.md` | at the root, byte-identical | same |

The wiki pages are mirrors of the GitHub wiki, which is a **separate repository**
and is *not* updated by merging this branch — see the publishing checklist.

## Restoring a 6.x backup

```sh
# from the project that holds sr.backup.20240112_143022/
bash /path/to/legacy/v6/sr.sh --rollback-list
bash /path/to/legacy/v6/sr.sh --rollback=sr.backup.20240112_143022
```

Or, since the layout is a plain directory mirror of the project, by hand:

```sh
ls sr.backup.20240112_143022/          # review first
cp -a sr.backup.20240112_143022/. ./
```

## Migrating to sr 7

```sh
sr help compat                 # flag-by-flag mapping, printed by the tool
cat docs/MIGRATION-6-to-7.md   # the same, with the reasoning and CI recipes
```

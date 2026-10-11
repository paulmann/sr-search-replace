# `legacy/v6/` — the sr 6.x (bash) distribution, frozen

Everything in this directory is the **6.1.0** release as it was published on
`main` before sr 7. It is kept, not maintained: no fixes, no features, no
security patches.

## Why it is still here

| Reason | Detail |
| --- | --- |
| **Rolling back 6.x sessions** | 6.x wrote `sr.backup.<timestamp>/` directories into the project root. sr 7 stores sessions under `.sr/sessions/` and deliberately does **not** read the old layout, so restoring one needs the old tool. |
| **Public URLs** | The wiki and existing user scripts reference these files. Deleting them turns live links into 404s. |
| **Reference** | `CHANGELOG.md` → *7.0.0 → Fixed (defects in 6.1.0)* lists 20+ concrete bugs; this directory is the code they refer to. |

The canonical, still-working copy of 6.1.0 is also attached to the release:
<https://github.com/paulmann/sr-search-replace/releases/tag/v6.1.0>

## Layout

```
legacy/v6/
├── sr.sh                     the 6.1.0 implementation (4186 lines of bash)
├── bin/sr                    the npm launcher that shelled out to sr.sh
├── scripts/install.js        the npm postinstall script
├── package.json              the npm manifest (root-level in 6.x)
├── .npmignore
├── EXAMPLES.md               usage examples (superseded by `sr help examples`)
├── ILLUSTRATIONS.md          image gallery notes
├── MEDIA.md                  media index
├── MEDIA/MEDIA.md
├── MEDIA/sr-workflow.png.png the duplicated-by-accident image
├── RELEASE_v6.0.0.md         release notes
├── RELEASE_v6.1.0.md         release notes
├── wiki/Installation.md      wiki mirror
└── docs/wiki/                wiki mirror (Home, Installation, Quick-Start, INDEX_LOCAL)
```

Note that `sr.sh` **also** exists at the repository root. That copy is the one
the public raw URL serves, and it carries a deprecation banner plus a runtime
notice that sr 6 is superseded. The copy here is the unmodified 6.1.0 file, for
reference.

## Two files deliberately *not* restored to the root

| File | Why it stays in `legacy/v6/` |
| --- | --- |
| `package.json` | A root-level npm manifest would make `npm publish` from the repository root package **6.x**. The v7 npm package lives in [`npm/`](../../npm/) and is published from there by the release workflow. |
| `bin/sr` | A root `bin/` directory is what npm's `bin` field resolves against; keeping it would resurrect the 6.x launcher for anyone installing from git. The v7 launcher is [`sr-cli`](../../sr-cli) (and `sr.py` for `python3 sr.py`). |

Both are one `cp` away if you need them.

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

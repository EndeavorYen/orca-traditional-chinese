# Maintaining the pack

Upstream `en.json` changes almost daily, so keeping the pack current is a loop
that has to be cheap. The loop is split so that everything mechanical is a
script, and the only judgement call -- the translation itself -- is one step.

```
 daily, in CI (free)                      on demand, on your machine
 ┌──────────────────────┐                 ┌───────────────────────────────────┐
 │ resync-check.yml     │  opens/updates  │ resync  (scripts/sync.py)         │
 │  sync.py check       ├──── issue ─────▶│  check → prepare → translate →    │
 │                      │                 │  apply → validate → commit        │
 └──────────────────────┘                 └───────────────┬───────────────────┘
                                                          ▼
                                          PR ── validate.yml (required check)
```

## Routine: resync

When the daily workflow's issue reports drift or when syncing to a new release,
the flow is: `python3 scripts/sync.py check` → `prepare` → translate
`work/todo/NNN.json` into `work/done/NNN.json` (LLM-assisted, any tool;
`.claude/commands/resync.md` is an optional ready-made prompt that follows the
same rules) → `apply` → `validate --strict` → open a PR (`validate.yml` is the
required check).

All commands are stdlib-only Python 3 and need network access to GitHub (set
`GH_TOKEN` to avoid rate limits, or pass `--upstream-dir <clone of stablyai/orca>`
to work offline).

| Step | Command | What it does |
|---|---|---|
| 1 | `python3 scripts/sync.py check` | Diff the latest stable Orca release vs. the pack. Exit 1 when out of date. |
| 2 | `python3 scripts/sync.py prepare` | Write `work/todo/NNN.json` batches (new keys + reworded keys). |
| 3 | *translate* | Write `work/done/NNN.json` as `{key: translation}` (LLM-assisted, any tool). |
| 4 | `python3 scripts/sync.py apply` | Merge, validate, update lock/README/version. Writes nothing on error. |
| 5 | `python3 scripts/sync.py validate --strict` | Final validation gate (also the CI check). |
| 6 | `python3 -m unittest discover -s tests` | Run the test suite. |

The diff reports four kinds of drift:

- **add** -- translatable upstream keys with no translation.
- **changed** -- upstream reworded a key you already translated. Detected via
  `.sync/lock.json`, which stores a hash of the English text each key was
  translated *from*. Without it, stale translations are invisible.
- **removed** -- keys upstream deleted, or that became plugin-protected.
- **skipped** -- plugin-protected keys and oversize strings, excluded by design.

`apply` keeps the existing key order and inserts each new key after its
upstream predecessor, so the git diff shows only what really changed.

## Translation conventions

- **Placeholders**: Placeholder-driven sentences (`{{value0}}`, `{host}`, `<tag>`)
  keep the existing placeholder structure even when Chinese word order differs;
  adjust wording around them for natural phrasing.
- **Terminology**: Follow `scripts/glossary.json` for Traditional Chinese (Taiwan usage).
  Keep brand names, code literals, and CLI commands in English.

## Why the rules are parsed, not copied

Orca rejects the *entire* pack if it translates a protected key. `validate.py`
reads the constants out of the loader sources at the same commit it is syncing:

| File | v1.4.245 and later | v1.4.244 and earlier |
|---|---|---|
| English catalog | `src/renderer/src/i18n/locales/en.json` (unchanged) | same path |
| Protected-path rules | `src/shared/plugins/artifacts/plugin-language-pack-artifact.ts` | `src/shared/plugins/plugin-language-pack-artifact.ts` |
| Exempt chrome paths | `src/shared/plugins/languages/plugin-translatable-chrome.ts` | `src/shared/plugins/plugin-translatable-chrome.ts` |

`sync.py` tries the newer path first and, only when that file is missing
(HTTP 404, or `git show` reporting the path is not in the revision), reads the
older path. A non-missing failure such as HTTP 500 does not fall through.

A path is protected when `isProtectedPluginTranslationPath` says so (the
function was `protectedTranslation` through v1.4.244):

- it equals one of `PROTECTED_TRANSLATION_COMPONENTS`, or sits under that path
  (`<component>.…`) — this list starts at v1.4.245; older tags have no list;
- otherwise it starts with `auto.components.settings.` and the remainder matches
  `/^plugin/i`, unless the full path is an exact entry in
  `TRANSLATABLE_PLUGIN_CHROME`.

If upstream refactors the constants beyond recognition, sync **fails loudly**
instead of guessing. If the protected-path function body changes, the command
prints a fingerprint mismatch: re-read it, update `is_protected()`, and add the
digest to `PROTECTED_FN_FINGERPRINTS` in `scripts/validate.py`.

## Files

| Path | Role |
|---|---|
| `locales/zh-TW.json` | The zh-TW catalog |
| `docs/images/` | README screenshots (retake when the UI changes noticeably) |
| `scripts/sync.py` | CLI: `init`, `check`, `prepare`, `apply`, `validate` |
| `scripts/validate.py` | Loader rules, placeholder/glossary checks, manifest identity checks (pure functions) |
| `scripts/glossary.json` | Banned (error) and watch (warning) terms, exceptions, Simplified-only characters |
| `.sync/lock.json` | Upstream commit + per-key hash of the English source (committed) |
| `.sync/keep-english.json` | Keys that are correctly identical to English (committed) |
| `work/` | Scratch for one resync run (git-ignored) |
| `orca-plugin.json` / `orca-marketplace.json` | Plugin manifest and marketplace index (identity + version) |
| `.github/workflows/resync-check.yml` | Daily drift check; one self-updating issue |
| `.github/workflows/validate.yml` | Unit tests + `validate`, for PRs |
| `.claude/commands/resync.md` | Optional ready-made prompt for resync |

## Versions and plugin identity

**Version.** Orca reads the version from `orca-plugin.json` (semver). The
marketplace index has no version of its own -- its schema is strict and only
allows `name`, `owner` and `plugins`, so do not add fields to it.

- `sync.py apply` bumps the **patch** version automatically (`--no-bump` to skip).
  Syncing to an Orca release uses that same patch bump. It does not bump minor
  just because Orca shipped a release.
- Bump **minor** by hand for a plugin identity change or a release worth calling
  out, **major** if `engines.orca` or `pluginApi` moves.
- Commit the bumped manifest together with the resync so the version always
  matches the catalog it ships.

`engines.orca` (`>=1.4.0`) is the minimum engine that can load this pack, not the badge's tested release.
The README badge and `.sync/lock.json` `upstream.release` name the Orca release the catalog
was synced against (plus `upstream.sha`, that release's commit). A pack tested on a current
release still declares `>=1.4.0`.

`check` and `prepare` default to the latest stable Orca release: GitHub's latest release,
which is not an `-rc` / prerelease and not `main`. Pass `--ref main` to track main, or
`--ref v1.4.210` for one tag. `resync-check` calls `check` with no `--ref`, so it watches
that same release.

**Identity.** A plugin is `<publisher>.<id>`, and the marketplace entry `id` must
equal it exactly. The two places that name the owner follow *different* rules,
which is easy to get wrong:

| Field | File | Rule | Value here |
|---|---|---|---|
| `publisher` | `orca-plugin.json` | lowercase kebab-case only (`a-z`, `0-9`, `-`) | `endeavoryen` |
| `owner` | `orca-marketplace.json` | letters of either case allowed | `EndeavorYen` |
| plugin `id` | `orca-marketplace.json` | `<publisher>.<id>` | `endeavoryen.traditional-chinese` |
| git `url` / `repository` | both | GitHub is case-insensitive | `https://github.com/EndeavorYen/orca-traditional-chinese` |

Orca reserves two identity shapes for official plugins: publisher `stablyai`,
and any id starting with `orca-`. Those are only installable from the `stablyai`
GitHub organization, so this pack must avoid both (`sync.py validate` checks).

A capitalised `publisher` makes Orca reject the whole plugin, so `sync.py
validate` checks all of this (and runs in CI). Changing the publisher makes Orca
see a *different* plugin: existing users must remove the old one and install the
new one, so say so in the README and release notes (done for
`a-lang.traditional-chinese`).

**displayName.** The language menu shows `zh-TW — <publisher>.<id>`; native
custom names are tracked in stablyai/orca [#13031](https://github.com/stablyai/orca/issues/13031) /
PR [#13140](https://github.com/stablyai/orca/pull/13140). **Do not add `displayName`
to `orca-plugin.json` until PR #13140 is merged** — the upstream manifest schema is
`.strict()`, so an unknown key makes Orca reject the whole plugin.

**Moving or forking the pack** -- change together, then run
`python3 scripts/sync.py validate`:

1. `orca-plugin.json`: `publisher`, `repository`, `version`
2. `orca-marketplace.json`: `owner`, plugin `id`, `source.url`
3. `README.md`: install URL, plugin id, the language-picker label
   (`zh-TW — <publisher>.<id>`), and the migration note
4. `UPSTREAM_REPO` in `scripts/sync.py` only if the *upstream* Orca repo moves

## GitHub Release notes

When shipping a pack version (`gh release create` / edit), use a short body with the useful facts only. Do **not** include a translator-credit line (for example `Translated with agy + …` or any similar model/tool attribution).

Template:

```markdown
## Synced to Orca <upstream-tag>

- Upstream: `stablyai/orca` `<upstream-tag>` (`<sha7>`, <YYYY-MM-DD>)
- Catalog drift: **+N** added / **~N** changed / **-N** removed
- zh-TW coverage: <pct>% of translatable strings

Install via Orca marketplace source:
`https://github.com/EndeavorYen/orca-traditional-chinese.git` (ref `main`)
```

Releases publish themselves: `.github/workflows/auto-release.yml` runs on
every push to `main` and creates the release when the current `orca-plugin.json`
version has no tag yet, so docs-only merges and review fixes never publish
anything. The body comes from `scripts/release_notes.py`: upstream facts from
`.sync/lock.json`, the drift line from diffing the previous release tag's lock,
coverage from the pack itself. The template below is what it emits; a
`Terminology:` line is the only hand-written addition (`gh release edit` after
publishing). Keep release notes free of tool credits; the generator never
writes one.

## Tuning

- **Issue threshold**: `MIN_KEYS` / `MAX_AGE_DAYS` at the top of
  `resync-check.yml` (default: 25 pending keys, or anything pending after 7 days).
- **Glossary**: edit `scripts/glossary.json` only — do not keep a second list in
  `.claude/commands/resync.md`.
  - Add an unambiguous mainland term under `banned` (the value is the Taiwan
    term). Hits are errors and fail `sync.py validate`. It may already occur in
    the catalog; except the legitimate hits instead of leaving the term off the list.
  - Add a context-sensitive term under `watch` (for example 程序). Hits are
    warnings and do not fail validation.
  - To keep a legitimate hit, add an `exceptions` entry for that same term.
    `patterns` are compounds that contain it (`處理程序` is not a hit for
    程序). `keys` are catalog paths that may use it. A term can stay in
    `banned` while those hits are excepted.
- **New locale**: point `orca-plugin.json` at a new `locales/<code>.json`, replace
  `scripts/glossary.json`, and run `prepare` on the empty pack -- every key is an
  "add". Then `apply`.

## Going fully automatic later

Nothing above needs an API key. If you later want unattended translation, run
the resync steps in CI with any CI-capable translator after `resync-check` finds
drift, then open a PR from the resulting branch. `validate.yml` gates the merge.

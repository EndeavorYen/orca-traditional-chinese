# orca-traditional-chinese

<!-- orca-badge:start -->
[![Orca v1.4.221 · zh-TW 100%](https://img.shields.io/badge/Orca-v1.4.221_%C2%B7_zh--TW_100%25-2ea44f)](https://github.com/stablyai/orca/releases/tag/v1.4.221)
<!-- orca-badge:end -->

Traditional Chinese (zh-TW) language pack for [Orca](https://github.com/stablyai/orca).

## Status

<!-- sync-status:start -->
- **14,948 / 14,948** translatable strings translated (100%)
- Synced against `stablyai/orca` `v1.4.221` (`9dd8812`, 2026-10-05)
- 180 plugin-protected keys and 2 oversize inline-CSS keys are excluded by design and fall back to English
<!-- sync-status:end -->

`engines.orca` (`>=1.4.0`) is the minimum engine that can load this pack, not the badge's tested release.
Syncing the catalog to an Orca release bumps the plugin patch version (`sync.py apply`);
bump minor or major by hand only for an identity change or an `engines.orca` / `pluginApi` move.

- Settings, sidebars, editor, terminal, GitHub/GitLab/Linear/Jira integrations,
  onboarding, mobile companion app, dashboard, system tray, and application menu
- Taiwan Traditional Chinese, not a mechanical conversion of the Simplified
  Chinese catalog. Translation is LLM-assisted and gated by automated checks
  (loader rules, placeholders, glossary) rather than line-by-line human review,
  so corrections are welcome — see [Contributing](#contributing)
- Kept current against upstream by a scripted workflow
  ([details below](#keeping-it-up-to-date)); the numbers above are regenerated
  on every sync

## Installation

Orca discovers language packs through its plugin system (git marketplaces):

1. Settings → Plugins → **Add marketplace source** with:
   - Git URL: `https://github.com/EndeavorYen/orca-traditional-chinese.git`
   - Git ref: `main`
2. The **Traditional Chinese Language Pack** listing appears in the
   marketplace browser — install it.
3. Select **繁體中文（台灣）** from Settings → Appearance → Language.

The marketplace index (`orca-marketplace.json`, owner `EndeavorYen`) lists the
pack as `endeavoryen.traditional-chinese`, pinned to the `main` ref.

> **Coming from `a-lang.traditional-chinese`?** A plugin's identity is
> `<publisher>.<id>`, and this pack's publisher changed from `a-lang` to
> `endeavoryen` (manifest publishers must be lowercase, even though the GitHub
> owner is `EndeavorYen`). Orca treats it as a different plugin: remove the old
> marketplace source and plugin, then install this one as above.

## Keeping it up to date

Upstream `en.json` changes almost daily. Syncing follows the latest stable
Orca release (GitHub's latest release — not an `-rc` and not `main`), so the
pack matches what users run. A daily GitHub Actions job (`resync-check`)
compares that release against the pack and keeps one tracking issue up to date;
when it says the pack is behind, resync from a clone of this repo:

```sh
claude          # then run:  /resync
```

`/resync` runs `check → prepare → translate → apply → validate` and leaves a
reviewable commit on an `i18n/resync-*` branch. Only the translation step needs
an LLM; everything else is deterministic, stdlib-only Python. To see what is
out of date without changing anything:

```sh
python3 scripts/sync.py check
```

It reports **added** keys, keys whose **English text changed** since they were
translated (so stale translations are caught, not just missing ones), and keys
upstream **removed**. Full workflow, design notes, and tuning:
[docs/MAINTAINING.md](docs/MAINTAINING.md).

## Working on the pack

| Command | Purpose |
|---|---|
| `python3 scripts/sync.py check` | Diff the latest stable Orca release vs. the pack; exit 1 if out of date (`--ref main` tracks main) |
| `python3 scripts/sync.py prepare` | Write translation batches to `work/todo/` |
| `python3 scripts/sync.py apply` | Merge `work/done/`, validate, update lock, README stats and plugin version |
| `python3 scripts/sync.py validate [--strict]` | Loader rules, placeholders, glossary, manifest identity (the CI check) |
| `python3 -m unittest discover -s tests` | Unit tests for the tooling |

```
locales/zh-TW.json          the translations
orca-plugin.json            plugin manifest (publisher, id, version)
orca-marketplace.json       marketplace index (owner, plugin entry)
scripts/                    sync.py, validate.py, glossary.json
.sync/                      lock.json (source hashes), keep-english.json
.github/workflows/          resync-check (daily drift), validate (PR gate)
.claude/commands/resync.md  the /resync command
docs/MAINTAINING.md         workflow, design, release notes
```

## How this pack was built

The English source (`en.json` from `stablyai/orca`,
`src/renderer/src/i18n/locales/en.json`) is flattened into `path -> string`
pairs and translated in batches grouped by UI namespace, with a shared zh-TW
glossary (儲存庫 / 終端 / 外掛程式 / 預設 / 設定 …), placeholders preserved
verbatim, and brand names / code literals left untranslated (Claude, Codex,
GitHub, Git, Markdown, `orca status`, etc.).

Two classes of key are never translated:

- **Plugin-protected keys** — `auto.components.settings.` followed by
  `plugin*` (case-insensitive, so `PluginConsentDialog.*` is covered too),
  minus the exact paths exempted upstream in `plugin-translatable-chrome.ts`.
  Translating one makes Orca reject the whole pack.
- **Oversize strings** over the 8,192-character artifact limit.

Every catalog is validated against the same rules Orca's plugin loader enforces
at runtime (`parsePluginLanguagePackArtifact`: max 20,000 entries, max depth 16,
no unsafe keys, no protected paths, no string over 8,192 chars). Those
constants are read from upstream's source at sync time instead of being copied
here. On top of that: `{{placeholder}}` / `{placeholder}` sets must match the
English per key, Simplified-Chinese characters and mainland terminology are
rejected, and strings identical to English must be deliberately allow-listed in
`.sync/keep-english.json`.

## Known limitations

A few upstream i18n design constraints can't be fixed from the translation
side alone — flagging them here for visibility:

- **Positional placeholder pluralization**: some English strings compose a
  sentence from an English verb/noun injected via a positional placeholder
  (e.g. `"{{value0}} PR #{{value1}}?"` where `{{value0}}` is `close`/`reopen`
  in English). Since Chinese grammar differs from English, these preserve the
  placeholders and produce the closest natural phrasing possible.
- **Plugin-protected keys** (count in [Status](#status)) under `auto.components.settings.plugin*`
  (plugin trust, safety, and consent copy) are intentionally left in English —
  plugin language packs cannot override them by design.
- **Inline CSS animation styles** (`review.animated.visual.*.styles.*`)
  exceed the 8,192-char per-string artifact limit and are not translatable
  prose; they are excluded from the pack and fall back to English.
- The language selector shows **zh-TW — endeavoryen.traditional-chinese** because
  upstream renders plugin packs as `{locale} — {pluginKey}`; a native display
  name (e.g. 中文（繁體）) is requested upstream in
  [#13031](https://github.com/stablyai/orca/issues/13031), with an open pull
  request in [#13140](https://github.com/stablyai/orca/pull/13140).

  **Do not add `displayName` to `orca-plugin.json` until that PR ships.**
  `pluginLanguagePackContributionSchema` is currently `.strict()`, so an
  unrecognised key fails manifest validation for the whole plugin — the pack
  would stop loading entirely rather than degrade to the old picker label.

## Contributing

Corrections and improvements welcome — please open a PR against
`locales/zh-TW.json`, keeping the existing key structure and the style
conventions above. CI runs `python3 scripts/sync.py validate` on every PR, so
you can run the same command locally first (see
[Working on the pack](#working-on-the-pack)).

Use Taiwan terminology (see `scripts/glossary.json`), keep `{{placeholders}}`
and `{single}` / `<angle>` tokens exactly as in the English source, and leave
brand names and code/CLI literals in English.

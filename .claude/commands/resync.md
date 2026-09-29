---
description: Resync the zh-TW language pack with upstream Orca en.json (diff, translate, validate)
argument-hint: "[--ref <upstream ref>]"
---

Resync this Orca language pack with upstream. The deterministic parts live in
`scripts/sync.py`; your job is only the translation step. Never edit
`locales/zh-TW.json` or `.sync/lock.json` by hand -- `apply` does that.

1. **Diff.** Run `python3 scripts/sync.py check $ARGUMENTS`. With no `--ref`, that
   is the latest stable Orca release (not `main`, not an `-rc`). If it exits 0, say the
   pack is already in sync and stop. If it prints WARNING about upstream rules
   changing, read the named upstream function before going on.
2. **Branch.** Create `i18n/resync-<upstream sha7>` from the current default branch
   (skip if already on an `i18n/` branch).
3. **Prepare.** Run `python3 scripts/sync.py prepare $ARGUMENTS`. It writes
   `work/todo/NNN.json`, each `{batch, of, items: [{key, en, current_zh?}]}`.
   `current_zh` is present when upstream reworded an already-translated string --
   revise that translation rather than starting over.
4. **Translate every batch** into `work/done/NNN.json` as a flat `{key: translation}`
   object with exactly the requested keys. Batches are independent, so run them in
   parallel with subagents (about four at a time), each told to read
   `scripts/glossary.json` and the rules below. Rules:
   - Taiwan Traditional Chinese, natural UI phrasing -- not a transliteration of
     the Simplified catalog. Follow `scripts/glossary.json` only (do not copy a
     term list into this command): `banned` terms are errors, `watch` terms are
     warnings, and `exceptions` suppress a hit per pattern or per key.
   - Keep every placeholder byte-for-byte: `{{value0}}`, `{host}`, and `<host>`-style
     tokens. Do not translate or renumber them.
   - Leave brand names and code/CLI literals in English (Claude, Codex, GitHub,
     Git, Markdown, `orca status`, shortcuts). If a string is *correctly* identical
     to English, list its key in `work/done/_keep.json` (a JSON array) so
     validation does not flag it as a missed translation.
   - Match the terminology of nearby existing keys in `locales/zh-TW.json`
     (grep before inventing a new rendering).
5. **Apply.** `python3 scripts/sync.py apply`. If it reports errors, fix the
   offending entries in `work/done/` and re-run -- nothing is written on failure.
   `--allow-partial` merges what is done and leaves the rest pending.
6. **Verify.** `python3 -m unittest discover -s tests` and
   `python3 scripts/sync.py validate --strict` must both pass, then
   `python3 scripts/sync.py check` must exit 0.
7. **Commit** (not push) with message `feat(i18n): resync catalog with orca <release>
   (<sha7>, <date>)` and a body listing +added / ~changed / -removed counts, any
   terminology decisions, and any `_keep` additions. Then tell the user the branch
   is ready for review and offer to open the PR. `<release>` is the tag `check`
   printed (or `main` when `--ref main` was passed).

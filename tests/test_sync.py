import contextlib
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync  # noqa: E402
import validate as V  # noqa: E402

# Trimmed copies of the upstream loader source; parse_rules must keep understanding this shape.
ARTIFACT_TS = """
export const PLUGIN_LANGUAGE_CATALOG_MAX_ENTRIES = 20_000
export const PLUGIN_LANGUAGE_CATALOG_MAX_DEPTH = 16
const PROTECTED_TRANSLATION_ROOT = 'auto.components.settings.'
const PROTECTED_TRANSLATION_MODULE = /^plugin/i
function protectedTranslation(path: string): boolean {
  if (!path.startsWith(PROTECTED_TRANSLATION_ROOT)) { return false }
}
if (value.length > 8192) {}
"""
# v1.4.245: component prefixes plus isProtectedPluginTranslationPath.
# The function body is the upstream text; its whitespace-normalised fingerprint is 3ab1fe55.
NEW_ARTIFACT_TS = """
export const PLUGIN_LANGUAGE_CATALOG_MAX_ENTRIES = 50_000
export const PLUGIN_LANGUAGE_CATALOG_MAX_DEPTH = 16
const PROTECTED_TRANSLATION_ROOT = 'auto.components.settings.'
const PROTECTED_TRANSLATION_MODULE = /^plugin/i
const PROTECTED_TRANSLATION_COMPONENTS = [
  'auto.components.pluginCatalog.PluginListRow.official',
  'auto.components.editor.PluginFileTypeSuggestion',
  'components.native-chat.approval',
  'auto.components.sidebar.OrcaYamlTrustDialog',
  'auto.components.editor.IpynbViewer',
  'browser.loadFailure',
  'auto.components.browserPane.sitePermission',
  'settings.browser.sitePermissions'
]
export function isProtectedPluginTranslationPath(path: string): boolean {
  if (
    PROTECTED_TRANSLATION_COMPONENTS.some(
      (component) => path === component || path.startsWith(`${component}.`)
    )
  ) {
    return true
  }
  if (!path.startsWith(PROTECTED_TRANSLATION_ROOT)) {
    return false
  }
  if (translatablePluginChrome(path)) {
    return false
  }
  return PROTECTED_TRANSLATION_MODULE.test(path.slice(PROTECTED_TRANSLATION_ROOT.length))
}
if (value.length > 8192) {}
"""
# Exact protectedTranslation body from v1.4.244 (fingerprint d8929abb).
OLD_PROTECTED_FN = """
function protectedTranslation(path: string): boolean {
  if (!path.startsWith(PROTECTED_TRANSLATION_ROOT)) {
    return false
  }
  if (translatablePluginChrome(path)) {
    return false
  }
  return PROTECTED_TRANSLATION_MODULE.test(path.slice(PROTECTED_TRANSLATION_ROOT.length))
}
"""
EN_JSON_PATH = "src/renderer/src/i18n/locales/en.json"
NEW_ARTIFACT_PATH = "src/shared/plugins/artifacts/plugin-language-pack-artifact.ts"
OLD_ARTIFACT_PATH = "src/shared/plugins/plugin-language-pack-artifact.ts"
NEW_CHROME_PATH = "src/shared/plugins/languages/plugin-translatable-chrome.ts"
OLD_CHROME_PATH = "src/shared/plugins/plugin-translatable-chrome.ts"
CHROME_TS = """
const TRANSLATABLE_PLUGIN_CHROME = new Set([
  // Plugins pane frame: it's a comment with an apostrophe
  'auto.components.settings.PluginsSettingsSection.title',
  'auto.components.settings.plugins.search.title'
])
"""
PFX = "auto.components.settings."


def rules() -> V.Rules:
    return V.parse_rules(ARTIFACT_TS, CHROME_TS)


class ParseRules(unittest.TestCase):
    def test_parses_constants(self):
        r = rules()
        self.assertEqual((r.root, r.max_entries, r.max_depth, r.max_string), (PFX, 20000, 16, 8192))
        self.assertEqual(len(r.exempt), 2)

    def test_refactored_upstream_fails_loudly(self):
        with self.assertRaises(V.RulesError):
            V.parse_rules(ARTIFACT_TS.replace("PROTECTED_TRANSLATION_MODULE", "SOMETHING_ELSE"), CHROME_TS)
        with self.assertRaises(V.RulesError):
            V.parse_rules(ARTIFACT_TS, "const X = new Set([])")


class Protection(unittest.TestCase):
    def test_protected_is_case_insensitive_and_scoped(self):
        r = rules()
        self.assertTrue(V.is_protected(PFX + "PluginConsentDialog.title", r))
        self.assertTrue(V.is_protected(PFX + "plugins.blocked", r))
        self.assertFalse(V.is_protected(PFX + "GeneralPane.title", r))
        self.assertFalse(V.is_protected("auto.components.PluginBadge.title", r))

    def test_exempt_paths_are_translatable(self):
        r = rules()
        self.assertFalse(V.is_protected(PFX + "PluginsSettingsSection.title", r))
        self.assertTrue(V.is_protected(PFX + "PluginsSettingsSection.description", r))

    def test_oversize_uses_utf16_units(self):
        r = rules()
        self.assertTrue(V.is_translatable("a.b", "x" * 8192, r))
        self.assertFalse(V.is_translatable("a.b", "x" * 8193, r))
        self.assertEqual(V.utf16_len("😀"), 2)


class Validate(unittest.TestCase):
    EN = {"a.one": "Hello {{name}}", "a.two": "Open <host> now", "a.three": "Save", "b.four": "Claude"}
    GLOSSARY = V.Glossary(frozenset("软"), {"軟件": "軟體"}, {})

    def run_validate(self, zh, **kw):
        return V.validate_catalog(zh, self.EN, rules(), self.GLOSSARY, **kw)

    def codes(self, report):
        return sorted({c for c, _, _ in report.errors})

    def test_clean(self):
        zh = {"a": {"one": "你好 {{name}}", "two": "立即開啟 <host>", "three": "儲存"}, "b": {"four": "Claude"}}
        r = self.run_validate(zh, keep_english=frozenset({"b.four"}))
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.warnings, [])

    def test_placeholder_drift_is_error_tag_drift_is_warning(self):
        zh = {"a": {"one": "你好 {{nome}}", "two": "立即開啟 <主機>", "three": "儲存"}, "b": {"four": "Claude"}}
        r = self.run_validate(zh)
        self.assertEqual(self.codes(r), ["placeholder"])
        self.assertIn("tag", {c for c, _, _ in r.warnings})

    def test_single_and_double_brace_are_the_same_name(self):
        self.assertEqual(V.placeholders("{ x } {host} {{ host }}"), frozenset({"host"}))

    def test_quality_and_loader_errors(self):
        zh = {"a": {"one": "软件 {{name}}", "three": "軟件"}, "gone": {"x": "y"},
              "auto": {"components": {"settings": {"PluginConsentDialog": {"t": "x"}}}}}
        self.assertEqual(self.codes(self.run_validate(zh)),
                         ["banned-term", "orphan", "protected", "simplified"])

    def test_coverage_is_warning_unless_strict(self):
        zh = {"a": {"one": "你好 {{name}}"}}
        self.assertTrue(self.run_validate(zh).ok)
        self.assertEqual(self.codes(self.run_validate(zh, strict_coverage=True)), ["missing"])

    def test_stale_key_strict(self):
        zh = {"a": {"one": "你好 {{name}}", "two": "<host>", "three": "儲存"}, "b": {"four": "Claude"}}
        r = self.run_validate(zh, strict_coverage=True, stale=frozenset({"a.one"}), keep_english=frozenset({"b.four"}))
        self.assertEqual(self.codes(r), ["stale"])

    def test_too_deep(self):
        node: dict = {"k": "v"}
        for _ in range(20):
            node = {"n": node}
        self.assertIn("depth", self.codes(self.run_validate(node)))


class Manifests(unittest.TestCase):
    PLUGIN = {"publisher": "endeavoryen", "id": "traditional-chinese", "version": "1.1.0"}
    MARKET = {"owner": "EndeavorYen", "plugins": [{"id": "endeavoryen.traditional-chinese"}]}

    def test_good(self):
        self.assertEqual(V.check_manifests(self.PLUGIN, self.MARKET), [])

    def test_uppercase_publisher_is_rejected_but_uppercase_owner_is_fine(self):
        errs = V.check_manifests({**self.PLUGIN, "publisher": "EndeavorYen"}, self.MARKET)
        self.assertTrue(any("kebab-case" in e for e in errs))

    def test_marketplace_id_must_match_manifest(self):
        stale = {**self.MARKET, "plugins": [{"id": "a-lang.traditional-chinese"}]}
        self.assertTrue(any("must equal" in e for e in V.check_manifests(self.PLUGIN, stale)))

    def test_reserved_identity_forms_are_rejected(self):
        self.assertEqual(V.check_manifests(self.PLUGIN, self.MARKET), [])
        self.assertTrue(any("reserved" in e for e in V.check_manifests({**self.PLUGIN, "publisher": "stablyai"}, None)))
        self.assertTrue(any("reserved" in e for e in V.check_manifests({**self.PLUGIN, "id": "orca-traditional-chinese"}, None)))

    def test_bad_semver(self):
        self.assertTrue(V.check_manifests({**self.PLUGIN, "version": "1.1"}, None))

    def test_language_pack_accepts_only_locale_and_path(self):
        pack = {"locale": "zh-TW", "path": "locales/zh-TW.json"}
        plugin = {**self.PLUGIN, "contributes": {"languagePacks": [pack]}}
        self.assertEqual(V.check_manifests(plugin, None), [])
        bad = {**pack, "displayName": "繁體中文"}
        errs = V.check_manifests({**self.PLUGIN, "contributes": {"languagePacks": [bad]}}, None)
        self.assertTrue(any("displayName" in e for e in errs))

    def test_shipped_manifest_keeps_display_name_out(self):
        root = Path(__file__).resolve().parents[1]
        plugin = json.loads((root / "orca-plugin.json").read_text())
        market = json.loads((root / "orca-marketplace.json").read_text())
        self.assertEqual(plugin["engines"]["orca"], ">=1.4.0")
        self.assertEqual(set(plugin["contributes"]["languagePacks"][0]), {"locale", "path"})
        self.assertNotIn("displayName", (root / "orca-plugin.json").read_text())
        self.assertEqual(V.check_manifests(plugin, market), [])


class Terminology(unittest.TestCase):
    """Error, warning, exception, and clean string through validate_catalog."""

    def test_error_warning_exception_and_clean_string(self):
        glossary = V.Glossary(
            frozenset(),
            {"軟件": "軟體"},
            {"程序": "處理程序／行程"},
            {
                "程序": V.TermException(("處理程序",), frozenset()),
                "軟件": V.TermException(("舊式軟件名",), frozenset({"quote.term"})),
            },
        )

        def run(key, text):
            head, tail = key.split(".")
            return V.validate_catalog({head: {tail: text}}, {key: "English"}, rules(), glossary)

        error = run("a.err", "這是軟件")
        self.assertFalse(error.ok)
        self.assertIn("banned-term", {c for c, _, _ in error.errors})

        warning = run("a.warn", "結束程序")
        self.assertTrue(warning.ok, warning.errors)
        self.assertIn("watch-term", {c for c, _, _ in warning.warnings})

        allowed = run("a.ok", "結束處理程序")
        self.assertTrue(allowed.ok, allowed.errors)
        self.assertFalse(any(c in ("banned-term", "watch-term") for c, _, _ in allowed.warnings))

        excepted_key = run("quote.term", "引述軟件")
        self.assertTrue(excepted_key.ok, excepted_key.errors)
        self.assertFalse(any(c == "banned-term" for c, _, _ in excepted_key.errors))

        still_error = run("a.err", "舊式軟件名與軟件")
        self.assertFalse(still_error.ok)
        self.assertIn("banned-term", {c for c, _, _ in still_error.errors})

        clean = run("a.clean", "儲存")
        self.assertTrue(clean.ok, clean.errors)
        self.assertEqual(clean.warnings, [])

        second = run("a.warn", "處理程序與程序")
        self.assertTrue(second.ok, second.errors)
        self.assertIn("watch-term", {c for c, _, _ in second.warnings})

    def test_shipped_glossary_allows_established_compound(self):
        glossary = sync.load_glossary()
        en = {"k": "process"}
        covered = V.validate_catalog({"k": "處理程序"}, en, rules(), glossary)
        self.assertTrue(covered.ok, covered.errors)
        self.assertNotIn("watch-term", {c for c, _, _ in covered.warnings})
        bare = V.validate_catalog({"k": "應用程序"}, en, rules(), glossary)
        self.assertTrue(bare.ok, bare.errors)
        self.assertIn("watch-term", {c for c, _, _ in bare.warnings})
        mainland = V.validate_catalog({"k": "安裝軟件"}, en, rules(), glossary)
        self.assertFalse(mainland.ok)
        self.assertIn("banned-term", {c for c, _, _ in mainland.errors})

    def test_shipped_glossary_keeps_terminal_wording_consistent(self):
        # Taiwan usage renders "terminal" as 終端機; a bare 終端 is drift.
        glossary = sync.load_glossary()
        en = {"k": "terminal"}
        drift = V.validate_catalog({"k": "重設終端"}, en, rules(), glossary)
        self.assertFalse(drift.ok)
        self.assertIn("banned-term", {c for c, _, _ in drift.errors})
        drift_mid = V.validate_catalog({"k": "在終端中執行"}, en, rules(), glossary)
        self.assertFalse(drift_mid.ok)
        house = V.validate_catalog({"k": "重設終端機"}, en, rules(), glossary)
        self.assertTrue(house.ok, house.errors)
        product = V.validate_catalog({"k": "Windows 終端機"}, en, rules(), glossary)
        self.assertTrue(product.ok, product.errors)
        import re

        def strings(node):
            if isinstance(node, dict):
                for value in node.values():
                    yield from strings(value)
            elif isinstance(node, str):
                yield node

        shipped = sync.read_json(sync.locale_path())
        bare = [v for v in strings(shipped) if re.search(r"終端(?!機)", v)]
        self.assertEqual(bare, [], f"{len(bare)} shipped strings use bare 終端")


class ReleaseBaseline(unittest.TestCase):
    def test_default_ref_is_newer_stable_not_rc_and_explicit_main_stays(self):
        tags = [
            "v1.4.210",
            "v1.4.216",
            "v1.4.217-rc.1",
            {"tag_name": "v1.4.220-rc.2", "prerelease": True},
            "v1.4.219",
            "main",
        ]
        self.assertEqual(sync.resolve_ref(None, tags), "v1.4.219")
        self.assertEqual(sync.resolve_ref("main", tags), "main")
        self.assertEqual(sync.resolve_ref("v1.4.217-rc.1", tags), "v1.4.217-rc.1")
        self.assertFalse(sync.is_stable_release_tag("v1.4.217-rc.1"))
        self.assertTrue(sync.is_stable_release_tag("v1.4.219"))

    def test_check_text_json_and_readme_share_release_and_counts(self):
        sha = "20d7a7d185cd66e993dcdfd60e9e604fe26e9c40"
        snap = sync.Snapshot(
            sha, "2026-09-28T19:03:46Z", {"a.b": "Hi"}, {}, rules(), release="v1.4.216", ref="v1.4.216",
        )
        lock = {
            "upstream": {"sha": sha, "date": "2026-09-28T19:03:46Z", "release": "v1.4.216"},
            "sources": {"a.b": sync.h("Hi")},
        }
        diff = sync.compute_diff(snap, {"a.b": "嗨"}, lock["sources"])
        payload = sync.check_payload(
            snap, lock, diff, now=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )
        for key in (
            "pending", "untracked", "days_since_synced", "rule_warnings",
            "upstream", "add", "changed", "removed", "release",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["release"], "v1.4.216")
        self.assertEqual(payload["upstream"], sha)
        self.assertEqual(payload["pending"], 0)
        self.assertEqual(payload["add"], 0)
        self.assertEqual(payload["changed"], 0)
        self.assertEqual(payload["removed"], 0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            sync.print_diff(diff, snap, lock)
        self.assertIn("v1.4.216", buf.getvalue())
        text = (
            "# Title\n\n"
            "<!-- orca-badge:start -->\nold\n<!-- orca-badge:end -->\n\n"
            "Intro prose.\n\n"
            "<!-- sync-status:start -->\nold\n<!-- sync-status:end -->\n"
        )
        out = sync.apply_readme_sync(
            text, release="v1.4.216", sha=sha, date=snap.date,
            translated=10, total=40, protected=3, oversize=1, partial=False,
        )
        self.assertLess(out.index("<!-- orca-badge:start -->"), out.index("Intro prose."))
        self.assertIn("v1.4.216", out)
        self.assertIn("10 / 40", out.replace(",", ""))
        self.assertIn("25%", out)
        badge = out.split("<!-- orca-badge:end -->")[0]
        self.assertIn("25%", badge)
        self.assertIn("v1.4.216", badge)
        self.assertIn("https://github.com/stablyai/orca/releases/tag/v1.4.216", badge)

    def test_main_ref_keeps_its_name_on_the_badge(self):
        sha = "c8af48d8a4159feaa4bc40e8773cf730f7ab0d3c"
        snap = sync.Snapshot(sha, "2026-09-29T00:00:00Z", {}, {}, rules(), ref=sha)
        sync.restore_apply_identity(snap, {"ref": "main", "release": None, "sha": sha})
        self.assertEqual((snap.ref, snap.release), ("main", None))
        text = (
            "# Title\n\n"
            "<!-- orca-badge:start -->\nold\n<!-- orca-badge:end -->\n\n"
            "Intro prose.\n\n"
            "<!-- sync-status:start -->\nold\n<!-- sync-status:end -->\n"
        )
        out = sync.apply_readme_sync(
            text, release=snap.ref, sha=sha, date=snap.date,
            translated=1, total=1, protected=0, oversize=0, partial=False,
        )
        self.assertIn("`main`", out)
        self.assertIn(f"https://github.com/stablyai/orca/commit/{sha}", out)
        self.assertIn("Orca main · zh-TW 100%", out)

    def test_partial_apply_keeps_previous_release(self):
        previous = {
            "repo": "stablyai/orca", "sha": "aaa", "date": "2026-01-01T00:00:00Z", "release": "v1.4.210",
        }
        snap = sync.Snapshot("bbb", "2026-02-01T00:00:00Z", {}, {}, rules(), release="v1.4.216")
        self.assertEqual(
            sync.lock_upstream(snap, previous, partial=True, release="v1.4.216"),
            previous,
        )
        full = sync.lock_upstream(snap, previous, partial=False, release="v1.4.216")
        self.assertEqual(full["release"], "v1.4.216")
        self.assertEqual(full["sha"], "bbb")

    def test_prepare_writes_empty_state_when_release_advances(self):
        import tempfile
        import shutil
        import argparse
        tmp = Path(tempfile.mkdtemp())
        try:
            work = tmp / "work"
            orig_work = sync.WORK
            sync.WORK = work
            try:
                # Simulate an upstream snapshot with v1.4.217 and lock on v1.4.216 with same keys
                en = {"key.one": "One"}
                zh = {"key": {"one": "一"}}
                snap = sync.Snapshot("11d9789", "2026-09-29T19:11:52Z", en, {"key": {"one": "One"}}, rules(), release="v1.4.217")
                lock = {"upstream": {"sha": "20d7a7d", "release": "v1.4.216"}, "sources": {"key.one": sync.h("One")}}
                
                # Test prepare logic directly
                d = sync.compute_diff(snap, V.flatten(zh), lock.get("sources", {}))
                self.assertEqual(d.add, [])
                self.assertEqual(d.changed, [])
                self.assertEqual(d.removed, [])
                
                # Check that state.json is created properly when release moves
                work.mkdir(parents=True, exist_ok=True)
                (work / "todo").mkdir()
                (work / "done").mkdir()
                sync.write_json(work / "state.json", {
                    "sha": snap.sha, "date": snap.date, "ref": snap.ref, "release": snap.release,
                    "upstream_dir": None,
                    "add": [], "changed": [], "removed": [],
                })
                state = sync.read_json(work / "state.json")
                self.assertEqual(state["release"], "v1.4.217")
                self.assertEqual(state["add"], [])
            finally:
                sync.WORK = orig_work
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CommittedPack(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_lock_readme_and_catalog_share_a_stable_release(self):
        lock = json.loads((self.root / ".sync/lock.json").read_text())
        release = lock["upstream"]["release"]
        self.assertTrue(sync.is_stable_release_tag(release), release)
        self.assertTrue(lock["upstream"]["sha"])
        zh = V.flatten(json.loads((self.root / "locales/zh-TW.json").read_text()))
        self.assertEqual(set(lock["sources"]), set(zh))
        keep = json.loads((self.root / ".sync/keep-english.json").read_text())
        self.assertLessEqual(set(keep), set(zh))
        readme = (self.root / "README.md").read_text()
        badge = readme.index("<!-- orca-badge:start -->")
        prose = readme.index("Traditional Chinese")
        self.assertLess(badge, prose)
        badge_body = readme[badge:readme.index("<!-- orca-badge:end -->")]
        self.assertIn(release, badge_body)
        status = readme[readme.index("<!-- sync-status:start -->"):readme.index("<!-- sync-status:end -->")]
        self.assertIn(release, status)
        self.assertIn(">=1.4.0", readme)
        self.assertIn("not the badge's tested release", readme)
        self.assertIn("patch", readme)

    def test_docs_point_at_one_glossary_and_the_release_baseline(self):
        resync = (self.root / ".claude/commands/resync.md").read_text()
        self.assertIn("scripts/glossary.json", resync)
        for term in ("儲存庫", "外掛程式", "智慧體", "工作樹"):
            self.assertNotIn(term, resync)
        maintaining = (self.root / "docs/MAINTAINING.md").read_text()
        self.assertIn("exceptions", maintaining)
        self.assertIn(">=1.4.0", maintaining)
        self.assertIn("not the badge's tested release", maintaining)
        self.assertNotIn("zero occurrences", maintaining)
        workflow = (self.root / ".github/workflows/resync-check.yml").read_text()
        self.assertNotIn("--ref main", workflow)
        self.assertIn(".release", workflow)
        self.assertNotIn("\n  release:", workflow)
        self.assertIn("displayName", (self.root / "README.md").read_text())


class Merge(unittest.TestCase):
    def test_insert_after_upstream_predecessor_and_keep_existing_order(self):
        en = {"s": {"a": "A", "b": "B", "c": "C"}, "new": {"x": "X"}}
        zh = {"s": {"c": "c", "a": "a"}}  # deliberately not in upstream order
        sync.merge_catalog(zh, en, {"s.b": "b", "new.x": "x"}, {}, set())
        self.assertEqual(list(zh["s"]), ["c", "a", "b"])  # b after its predecessor a
        self.assertEqual(list(zh), ["s", "new"])

    def test_first_sibling_goes_first(self):
        zh = {"s": {"b": "b"}}
        sync.merge_catalog(zh, {"s": {"a": "A", "b": "B"}}, {"s.a": "a"}, {}, set())
        self.assertEqual(list(zh["s"]), ["a", "b"])

    def test_removal_prunes_empty_branches_and_update_replaces(self):
        zh = {"s": {"a": "a", "b": "b"}, "t": {"u": {"v": "v"}}}
        sync.merge_catalog(zh, {}, {}, {"s.a": "A2"}, {"t.u.v"})
        self.assertEqual(zh, {"s": {"a": "A2", "b": "b"}})

    def test_stray_dotted_upstream_key_is_not_copied(self):
        # A flat "a.b" beside nested a.b is a duplicate. Nesting keeps the nested
        # value; merge then places the translation at that nested path.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            en = sync.nest_dotted_keys({"a": {"b": "B"}, "a.b": "B"})
        zh = {}
        sync.merge_catalog(zh, en, {"a.b": "b"}, {}, set())
        self.assertEqual(zh, {"a": {"b": "b"}})
        self.assertNotIn("a.b", zh)
        self.assertIn("info:", err.getvalue())

    def test_stray_dotted_upstream_key_prints_info(self):
        # Dropping the flat duplicate must name the key. The old skip warning is gone.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sync.nest_dotted_keys({"a": {"b": "B"}, "a.b": "B"})
        text = err.getvalue()
        self.assertIn("a.b", text)
        self.assertIn("info:", text)
        self.assertNotIn("skipped dotted upstream key", text)

    def test_no_warning_without_dotted_keys(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            sync.merge_catalog({}, {"a": {"b": "B"}}, {"a.b": "b"}, {}, set())
        self.assertEqual(err.getvalue(), "")


class Diff(unittest.TestCase):
    def snap(self, en):
        return sync.Snapshot("abc", "2026-01-01T00:00:00Z", en, {}, rules())

    def test_classification(self):
        en = {"k.new": "New", "k.same": "Same", "k.edited": "Edited now", PFX + "PluginX.t": "Trust me"}
        zh = {"k.same": "同", "k.edited": "舊", "k.gone": "沒了", "k.untracked": "?"}
        lock = {"k.same": sync.h("Same"), "k.edited": sync.h("Edited before"), "k.gone": "00000000"}
        d = sync.compute_diff(self.snap({**en, "k.untracked": "U"}), zh, lock)
        self.assertEqual((d.add, d.changed, d.untracked), (["k.new"], ["k.edited"], ["k.untracked"]))
        self.assertEqual(d.removed, ["k.gone"])
        self.assertEqual(d.protected, 1)

    def test_translated_key_that_became_protected_must_be_dropped(self):
        d = sync.compute_diff(self.snap({PFX + "PluginX.t": "T"}), {PFX + "PluginX.t": "譯"}, {})
        self.assertEqual(d.now_excluded, [PFX + "PluginX.t"])


class DottedKeys(unittest.TestCase):
    """Upstream en.json sometimes ships a flat key like ``settings.agents.save``.

    Orca rejects the whole pack if any catalog key contains a dot, so the flat
    key is rewritten into nested objects before diff and merge.
    """

    def test_nest_dotted_keys_merges_into_existing_dicts(self):
        original = {
            "settings": {"appearance": {"theme": "Dark"}},
            "settings.appearance.chat.title": "Title",
            "top.level": "Top",
        }
        untouched = json.loads(json.dumps(original))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            nested = sync.nest_dotted_keys(original)
            inner = sync.nest_dotted_keys({
                "settings": {"agents.save": "Save", "agents": {"reset": "Reset"}},
            })
            rewritten = sync.nest_dotted_keys({"settings.agents": {"save.now": "Save"}})
        self.assertEqual(original, untouched)
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(nested, {
            "settings": {"appearance": {"theme": "Dark", "chat": {"title": "Title"}}},
            "top": {"level": "Top"},
        })
        self.assertNotIn("settings.appearance.chat.title", nested)
        self.assertEqual(inner, {
            "settings": {"agents": {"reset": "Reset", "save": "Save"}},
        })
        self.assertNotIn("agents.save", inner["settings"])
        self.assertEqual(rewritten, {"settings": {"agents": {"save": {"now": "Save"}}}})
        self.assertEqual(
            V.flatten(nested),
            {"settings.appearance.theme": "Dark", "settings.appearance.chat.title": "Title", "top.level": "Top"},
        )

    def test_duplicate_dotted_key_keeps_nested_value_and_prints_info(self):
        trees = [
            {
                "settings": {"appearance": {"chat": {"title": "Nested"}}},
                "settings.appearance.chat.title": "Flat",
            },
            {
                "settings.appearance.chat.title": "Flat",
                "settings": {"appearance": {"chat": {"title": "Nested"}}},
            },
        ]
        for tree in trees:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                nested = sync.nest_dotted_keys(tree)
            self.assertEqual(nested, {
                "settings": {"appearance": {"chat": {"title": "Nested"}}},
            })
            text = err.getvalue()
            self.assertIn("info:", text)
            self.assertIn("settings.appearance.chat.title", text)
            self.assertIn("keeping existing nested value", text)

    def test_dotted_key_conflict_raises(self):
        with self.assertRaises(ValueError) as leaf:
            sync.nest_dotted_keys({"settings": "Settings", "settings.agents.save": "Save"})
        self.assertIn("settings.agents.save", str(leaf.exception))

        with self.assertRaises(ValueError) as target:
            sync.nest_dotted_keys({
                "settings": {"agents": {"save": {"label": "Save"}}},
                "settings.agents.save": "Save",
            })
        self.assertIn("settings.agents.save", str(target.exception))

        with self.assertRaises(ValueError) as through:
            sync.nest_dotted_keys({"a": {"b": "leaf"}, "a.b": {"c": "x"}})
        self.assertIn("a.b", str(through.exception))

    def test_merge_catalog_places_nested_translation_path(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            en = sync.nest_dotted_keys({
                "settings": {"agents": {"reset": "Reset"}},
                "settings.agents.save": "Save",
            })
        zh = {"settings": {"agents": {"reset": "重設"}}}
        sync.merge_catalog(zh, en, {"settings.agents.save": "儲存"}, {}, set())
        self.assertEqual(zh, {"settings": {"agents": {"reset": "重設", "save": "儲存"}}})
        self.assertEqual(err.getvalue(), "")
        self.assertNotIn("settings.agents.save", zh)
        self.assertNotIn("agents.save", zh["settings"])

    def test_merge_catalog_rejects_unnested_dotted_key(self):
        with self.assertRaises(AssertionError) as caught:
            sync.merge_catalog({}, {"a.b": "B"}, {"a.b": "b"}, {}, set())
        self.assertIn("a.b", str(caught.exception))

    def test_validate_errors_on_dotted_key(self):
        catalogs = [
            ({"settings.agents.save": "儲存"}, {"settings.agents.save": "Save"}),
            (
                {"settings": {"appearance": {"chat.title": "標題"}}},
                {"settings.appearance.chat.title": "Title"},
            ),
            ({"a.b": {"c.d": "x"}}, {"a.b.c.d": "X"}),
        ]
        for zh, en in catalogs:
            for strict in (False, True):
                report = V.validate_catalog(zh, en, rules(), V.Glossary(), strict_coverage=strict)
                self.assertFalse(report.ok)
                dotted = [(key, msg) for code, key, msg in report.errors if code == "dotted-key"]
                self.assertTrue(dotted, report.errors)
                self.assertTrue(all(code == "dotted-key" for code, _, _ in report.errors), report.errors)
        deep = V.validate_catalog(
            {"a.b": {"c.d": "x"}}, {"a.b.c.d": "X"}, rules(), V.Glossary(), strict_coverage=True,
        )
        paths = [key for code, key, _ in deep.errors if code == "dotted-key"]
        self.assertTrue(any(path == "a.b" for path in paths), paths)
        self.assertTrue(any("c.d" in path for path in paths), paths)

    def _upstream(self, en_tree: dict) -> sync.Upstream:
        up = sync.Upstream("v1.4.216")
        up.sha = "abc123"
        up.date = "2026-01-01T00:00:00Z"
        up.resolve = lambda: None  # type: ignore[method-assign]
        files = {
            sync.EN_PATH: json.dumps(en_tree),
            sync.ARTIFACT_PATH: ARTIFACT_TS,
            sync.CHROME_PATH: CHROME_TS,
        }
        up.read = lambda path: files[path]  # type: ignore[method-assign]
        return up

    def test_snapshot_nests_dotted_keys_before_diff_and_merge(self):
        # Flat duplicate is last, so a raw flatten would keep "Flat" and hide "Nested".
        up = self._upstream({
            "settings": {"appearance": {"chat": {"title": "Nested"}}},
            "settings.agents.save": "Save",
            "settings.appearance.chat.title": "Flat",
        })
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            snap = up.snapshot()
        self.assertEqual(snap.en["settings.appearance.chat.title"], "Nested")
        self.assertEqual(snap.en["settings.agents.save"], "Save")
        self.assertEqual(snap.en_tree["settings"]["appearance"]["chat"]["title"], "Nested")
        self.assertEqual(snap.en_tree["settings"]["agents"]["save"], "Save")
        self.assertNotIn("settings.appearance.chat.title", snap.en_tree)
        self.assertNotIn("settings.agents.save", snap.en_tree)
        self.assertIn("settings.appearance.chat.title", err.getvalue())
        self.assertIn("info:", err.getvalue())

    def test_snapshot_dotted_conflict_fails_loudly(self):
        up = self._upstream({"settings": "Settings", "settings.agents.save": "Save"})
        with self.assertRaises(sync.SyncError) as caught:
            up.snapshot()
        self.assertIn("settings.agents.save", str(caught.exception))


class ProtectedComponents(unittest.TestCase):
    """v1.4.245 protects whole components outside the settings/plugin prefix."""

    COMPONENTS = frozenset({
        "auto.components.pluginCatalog.PluginListRow.official",
        "auto.components.editor.PluginFileTypeSuggestion",
        "components.native-chat.approval",
        "auto.components.sidebar.OrcaYamlTrustDialog",
        "auto.components.editor.IpynbViewer",
        "browser.loadFailure",
        "auto.components.browserPane.sitePermission",
        "settings.browser.sitePermissions",
    })

    def test_v245_rules_match_is_protected_plugin_translation_path(self):
        parsed = V.parse_rules(NEW_ARTIFACT_TS, CHROME_TS)
        self.assertEqual(parsed.max_entries, 50000)
        self.assertEqual(getattr(parsed, "components", None), self.COMPONENTS)
        self.assertEqual(parsed.warnings, ())
        self.assertTrue(V.is_protected("auto.components.pluginCatalog.PluginListRow.official", parsed))
        self.assertTrue(V.is_protected("auto.components.pluginCatalog.PluginListRow.official.badge", parsed))
        self.assertTrue(V.is_protected("browser.loadFailure", parsed))
        self.assertTrue(V.is_protected("settings.browser.sitePermissions.ask", parsed))
        self.assertTrue(V.is_protected("components.native-chat.approval.allow", parsed))
        self.assertFalse(V.is_protected("browser.loadFailureExtra", parsed))
        self.assertFalse(V.is_protected("auto.components.PluginBadge.title", parsed))
        self.assertFalse(V.is_protected(PFX + "PluginsSettingsSection.title", parsed))
        self.assertTrue(V.is_protected(PFX + "PluginConsentDialog.title", parsed))
        zh = {"browser": {"loadFailure": "載入失敗"}}
        report = V.validate_catalog(zh, {"browser.loadFailure": "Load failed"}, parsed, V.Glossary())
        self.assertIn("protected", {code for code, _, _ in report.errors})

    def test_v244_rules_have_no_component_list(self):
        parsed = rules()
        self.assertEqual(getattr(parsed, "components", None), frozenset())
        self.assertFalse(V.is_protected("browser.loadFailure", parsed))
        self.assertFalse(V.is_protected("auto.components.pluginCatalog.PluginListRow.official", parsed))
        exact = ARTIFACT_TS.replace(
            "function protectedTranslation(path: string): boolean {\n"
            "  if (!path.startsWith(PROTECTED_TRANSLATION_ROOT)) { return false }\n"
            "}",
            OLD_PROTECTED_FN.strip() + "\n",
        )
        silent = V.parse_rules(exact, CHROME_TS)
        self.assertEqual(silent.warnings, ())

    def test_unknown_protected_function_body_warns(self):
        mutated = NEW_ARTIFACT_TS.replace("return true", "return false", 1)
        parsed = V.parse_rules(mutated, CHROME_TS)
        self.assertTrue(any("fingerprint" in warning for warning in parsed.warnings), parsed.warnings)

    def test_new_function_without_component_list_fails(self):
        broken = NEW_ARTIFACT_TS.replace("PROTECTED_TRANSLATION_COMPONENTS", "RENAMED_COMPONENTS")
        with self.assertRaises(V.RulesError):
            V.parse_rules(broken, CHROME_TS)


class UpstreamLayouts(unittest.TestCase):
    """sync reads the v1.4.245 paths, then the v1.4.244 paths on a missing-file error."""

    def _upstream(self, read):
        up = sync.Upstream("v1.4.245")
        up.sha = "abc123"
        up.date = "2026-01-01T00:00:00Z"
        up.resolve = lambda: None  # type: ignore[method-assign]
        up.read = read  # type: ignore[method-assign]
        return up

    def test_prefers_v245_paths_when_both_layouts_exist(self):
        def read(path):
            files = {
                EN_JSON_PATH: '{"a": "b"}',
                NEW_ARTIFACT_PATH: NEW_ARTIFACT_TS,
                OLD_ARTIFACT_PATH: ARTIFACT_TS,
                NEW_CHROME_PATH: CHROME_TS,
                OLD_CHROME_PATH: CHROME_TS,
            }
            if path not in files:
                raise sync.SyncError(f"GET https://example.invalid/{path} -> HTTP 404")
            return files[path]

        snap = self._upstream(read).snapshot()
        self.assertEqual(snap.en, {"a": "b"})
        self.assertEqual(snap.rules.max_entries, 50000)
        self.assertTrue(V.is_protected("browser.loadFailure", snap.rules))

    def test_falls_back_to_v244_paths_after_404(self):
        calls = []

        def read(path):
            calls.append(path)
            files = {
                EN_JSON_PATH: '{"a": "b"}',
                OLD_ARTIFACT_PATH: ARTIFACT_TS,
                OLD_CHROME_PATH: CHROME_TS,
            }
            if path not in files:
                raise sync.SyncError(f"GET https://example.invalid/{path} -> HTTP 404")
            return files[path]

        snap = self._upstream(read).snapshot()
        self.assertEqual(snap.rules.max_entries, 20000)
        self.assertEqual(getattr(snap.rules, "components", None), frozenset())
        self.assertFalse(V.is_protected("browser.loadFailure", snap.rules))
        self.assertTrue(V.is_protected(PFX + "PluginX.t", snap.rules))
        self.assertIn(NEW_ARTIFACT_PATH, calls)
        self.assertIn(NEW_CHROME_PATH, calls)
        self.assertLess(calls.index(NEW_ARTIFACT_PATH), calls.index(OLD_ARTIFACT_PATH))
        self.assertLess(calls.index(NEW_CHROME_PATH), calls.index(OLD_CHROME_PATH))

    def test_falls_back_when_git_reports_the_path_missing(self):
        calls = []

        def read(path):
            calls.append(path)
            files = {
                EN_JSON_PATH: '{"k": "v"}',
                OLD_ARTIFACT_PATH: ARTIFACT_TS,
                OLD_CHROME_PATH: CHROME_TS,
            }
            if path not in files:
                if path == NEW_CHROME_PATH:
                    raise sync.SyncError(
                        f"fatal: path '{path}' exists on disk, but not in 'abc123'"
                    )
                raise sync.SyncError(f"fatal: path '{path}' does not exist in 'abc123'")
            return files[path]

        snap = self._upstream(read).snapshot()
        self.assertEqual(snap.en["k"], "v")
        self.assertEqual(snap.rules.max_entries, 20000)
        self.assertIn(NEW_ARTIFACT_PATH, calls)
        self.assertIn(NEW_CHROME_PATH, calls)
        self.assertLess(calls.index(NEW_ARTIFACT_PATH), calls.index(OLD_ARTIFACT_PATH))
        self.assertLess(calls.index(NEW_CHROME_PATH), calls.index(OLD_CHROME_PATH))

    def test_http_500_on_the_new_path_does_not_read_the_old_path(self):
        calls = []

        def read(path):
            calls.append(path)
            if path == EN_JSON_PATH:
                return '{"a": "b"}'
            if path == NEW_ARTIFACT_PATH:
                raise sync.SyncError(f"GET https://example.invalid/{path} -> HTTP 500")
            if path == OLD_ARTIFACT_PATH:
                return ARTIFACT_TS
            raise sync.SyncError(f"GET https://example.invalid/{path} -> HTTP 404")

        with self.assertRaises(sync.SyncError) as caught:
            self._upstream(read).snapshot()
        self.assertIn("HTTP 500", str(caught.exception))
        self.assertNotIn(OLD_ARTIFACT_PATH, calls)

    def test_missing_artifact_names_both_paths(self):
        def read(path):
            if path == EN_JSON_PATH:
                return '{"a": "b"}'
            raise sync.SyncError(f"GET https://example.invalid/{path} -> HTTP 404")

        with self.assertRaises(sync.SyncError) as caught:
            self._upstream(read).snapshot()
        message = str(caught.exception)
        self.assertIn(NEW_ARTIFACT_PATH, message)
        self.assertIn(OLD_ARTIFACT_PATH, message)


if __name__ == "__main__":
    unittest.main()

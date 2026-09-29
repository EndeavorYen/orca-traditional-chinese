import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()

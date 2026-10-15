import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_notes as RN  # noqa: E402


class Drift(unittest.TestCase):
    def test_counts_added_changed_removed(self):
        prev = {"a": "1", "b": "2", "c": "3"}
        cur = {"a": "1", "b": "9", "d": "4"}
        self.assertEqual(RN.drift(prev, cur), (1, 1, 1))

    def test_identical_locks_are_zero(self):
        lock = {"a": "1"}
        self.assertEqual(RN.drift(lock, lock), (0, 0, 0))


class Coverage(unittest.TestCase):
    def test_counts_locked_keys_present_in_pack(self):
        lock = {"sources": {"a.x": "h", "b.y": "h", "c.z": "h"}}
        pack = {"a.x", "b.y", "c.z", "extra"}
        self.assertEqual(RN.coverage(lock, pack), (3, 3))

    def test_missing_pack_keys_lower_coverage(self):
        lock = {"sources": {"a.x": "h", "b.y": "h"}}
        self.assertEqual(RN.coverage(lock, {"a.x"}), (1, 2))


class Notes(unittest.TestCase):
    LOCK = {
        "upstream": {
            "repo": "stablyai/orca",
            "sha": "abcdef1234567890",
            "date": "2026-10-14T18:00:36Z",
            "release": "v1.4.225",
        },
        "sources": {"a.x": "h"},
    }

    def test_first_release_has_no_drift_line(self):
        notes = RN.build_notes("1.1.8", self.LOCK, {"a.x"}, None)
        self.assertIn("## Synced to Orca v1.4.225", notes)
        self.assertIn("- Upstream: `stablyai/orca` `v1.4.225` (`abcdef1`, 2026-10-14)", notes)
        self.assertIn("- zh-TW coverage: 100% of translatable strings", notes)
        self.assertNotIn("Catalog drift", notes)
        self.assertIn("ref `main`", notes)

    def test_drift_line_when_previous_tag_exists(self):
        notes = RN.build_notes("1.1.8", self.LOCK, {"a.x"}, {"a.x": "h", "b.y": "old"})
        self.assertIn("Catalog drift: **+0** added / **~0** changed / **-1** removed", notes)

    def test_no_tool_or_translator_credit(self):
        notes = RN.build_notes("1.1.8", self.LOCK, {"a.x"}, None)
        for banned in ("agy", "Translated with", "assistant", "LLM"):
            self.assertNotIn(banned, notes)


if __name__ == "__main__":
    unittest.main()

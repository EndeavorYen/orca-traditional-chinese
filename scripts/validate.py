"""Validation for an Orca language pack catalog.

Two layers, both pure functions (no network, no files):

1. Loader rules -- mirror Orca's ``parsePluginLanguagePackArtifact``. Breaking one
   of these makes Orca reject the *whole* pack at load time, so they are errors.
   The constants are parsed out of the upstream TypeScript at sync time
   (``parse_rules``) instead of being copied here, so an upstream rule change
   cannot silently diverge from this checker.
2. Pack quality -- placeholders, Simplified-Chinese leakage, glossary terms.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# {{value0}}, {host} -- runtime interpolation; a lost or renamed one breaks the UI.
BRACE_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}|\{([\w.]+)\}")
# <host>, <mode> -- literal angle-bracket tokens. Softer: a translator may
# reasonably localise them, so a mismatch is a warning, not an error.
TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*>")

# Fingerprint of upstream's `protectedTranslation` body (whitespace-normalised).
# The constants are parsed, but the *logic* around them is mirrored by
# `is_protected`; if this changes upstream, a human must re-read that function.
PROTECTED_FN_FINGERPRINT = "d8929abb"


class RulesError(RuntimeError):
    """Upstream loader source no longer has the shape sync.py understands."""


@dataclass(frozen=True)
class Rules:
    root: str
    module: re.Pattern[str]
    exempt: frozenset[str]
    max_entries: int
    max_depth: int
    max_string: int
    warnings: tuple[str, ...] = ()


def parse_rules(artifact_ts: str, chrome_ts: str) -> Rules:
    """Extract the loader's constants from upstream's TypeScript source."""

    def grab(pattern: str, text: str, what: str, flags: int = 0) -> re.Match[str]:
        m = re.search(pattern, text, flags)
        if not m:
            raise RulesError(
                f"cannot find {what} in upstream source; the loader was refactored "
                "and scripts/validate.py:parse_rules needs updating"
            )
        return m

    root = grab(r"PROTECTED_TRANSLATION_ROOT\s*=\s*'([^']*)'", artifact_ts, "protected root").group(1)
    mod = grab(
        r"PROTECTED_TRANSLATION_MODULE\s*=\s*/((?:\\/|[^/\n])+)/([a-z]*)",
        artifact_ts,
        "protected module regex",
    )
    module = re.compile(mod.group(1), re.IGNORECASE if "i" in mod.group(2) else 0)
    max_entries = int(
        grab(r"PLUGIN_LANGUAGE_CATALOG_MAX_ENTRIES\s*=\s*([\d_]+)", artifact_ts, "max entries")
        .group(1)
        .replace("_", "")
    )
    max_depth = int(
        grab(r"PLUGIN_LANGUAGE_CATALOG_MAX_DEPTH\s*=\s*([\d_]+)", artifact_ts, "max depth")
        .group(1)
        .replace("_", "")
    )
    max_string = int(grab(r"value\.length\s*>\s*(\d+)", artifact_ts, "string length limit").group(1))

    block = grab(
        r"TRANSLATABLE_PLUGIN_CHROME\s*=\s*new Set\(\[(.*?)\]\)", chrome_ts, "exempt path list", re.DOTALL
    ).group(1)
    block = re.sub(r"//[^\n]*", "", block)
    exempt = frozenset(re.findall(r"'([^']+)'", block))
    if not exempt:
        raise RulesError("exempt path list parsed as empty; refusing to guess")

    warnings = []
    fn = re.search(r"function protectedTranslation\(.*?\n\}", artifact_ts, re.DOTALL)
    if not fn:
        warnings.append("upstream `protectedTranslation` function not found")
    else:
        digest = hashlib.sha1(re.sub(r"\s+", "", fn.group(0)).encode()).hexdigest()[:8]
        if digest != PROTECTED_FN_FINGERPRINT:
            warnings.append(
                f"upstream `protectedTranslation` changed (fingerprint {digest}, expected "
                f"{PROTECTED_FN_FINGERPRINT}): re-read it, update is_protected() if the logic "
                "moved, then set PROTECTED_FN_FINGERPRINT"
            )
    return Rules(root, module, exempt, max_entries, max_depth, max_string, tuple(warnings))


def is_protected(path: str, rules: Rules) -> bool:
    """True if a language pack may not carry `path` (Orca rejects the whole pack)."""
    if not path.startswith(rules.root):
        return False
    if path in rules.exempt:
        return False
    return bool(rules.module.search(path[len(rules.root) :]))


def utf16_len(s: str) -> int:
    """JS `String.length` -- the unit upstream's 8,192 limit is measured in."""
    return len(s.encode("utf-16-le")) // 2


def is_translatable(path: str, english: str, rules: Rules) -> bool:
    return not is_protected(path, rules) and utf16_len(english) <= rules.max_string


def flatten(node: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, path))
        elif isinstance(value, str):
            out[path] = value
        else:
            raise ValueError(f"{path}: expected string or object, got {type(value).__name__}")
    return out


def placeholders(text: str) -> frozenset[str]:
    return frozenset(a or b for a, b in BRACE_RE.findall(text))


def tags(text: str) -> frozenset[str]:
    return frozenset(TAG_RE.findall(text))


@dataclass(frozen=True)
class TermException:
    """Legitimate hits for one glossary term.

    `patterns` are compounds that contain the term (處理程序 covers 程序).
    `keys` suppress the term for those catalog paths only.
    """

    patterns: tuple[str, ...] = ()
    keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Glossary:
    simplified_chars: frozenset[str] = frozenset()
    banned: dict[str, str] = field(default_factory=dict)  # error
    watch: dict[str, str] = field(default_factory=dict)  # warning; does not fail validate
    exceptions: dict[str, TermException] = field(default_factory=dict)

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Glossary":
        exceptions: dict[str, TermException] = {}
        raw = data.get("exceptions") or {}
        if isinstance(raw, dict):
            for term, spec in raw.items():
                if isinstance(spec, dict):
                    patterns = spec.get("patterns") or []
                    keys = spec.get("keys") or []
                elif isinstance(spec, list):
                    patterns, keys = spec, []
                else:
                    continue
                exceptions[str(term)] = TermException(tuple(patterns), frozenset(keys))
        return Glossary(
            frozenset(data.get("simplifiedChars", "")),
            dict(data.get("banned", {})),
            dict(data.get("watch", {})),
            exceptions,
        )


def term_outside_allowed_patterns(text: str, term: str, patterns: tuple[str, ...]) -> bool:
    """True when `term` occurs outside every allowed compound.

    處理程序 covers the 程序 inside it. A second, uncovered 程序 is still a hit.
    """
    if not term or term not in text:
        return False
    covered: list[tuple[int, int]] = []
    for pattern in patterns:
        if not pattern:
            continue
        start = 0
        while True:
            found = text.find(pattern, start)
            if found < 0:
                break
            covered.append((found, found + len(pattern)))
            start = found + 1
    start = 0
    while True:
        found = text.find(term, start)
        if found < 0:
            return False
        end = found + len(term)
        if not any(a <= found and end <= b for a, b in covered):
            return True
        start = found + 1


def terminology_hits(key: str, text: str, glossary: Glossary) -> list[tuple[str, str, str]]:
    """Return (level, code, message) for glossary hits that are not excepted."""
    hits: list[tuple[str, str, str]] = []
    groups = (
        ("error", "banned-term", glossary.banned),
        ("warning", "watch-term", glossary.watch),
    )
    for level, code, table in groups:
        for term, fix in table.items():
            exc = glossary.exceptions.get(term)
            if exc is not None and key in exc.keys:
                continue
            patterns = exc.patterns if exc is not None else ()
            if term_outside_allowed_patterns(text, term, patterns):
                hits.append((level, code, f"'{term}' -> use '{fix}'"))
    return hits


@dataclass
class Report:
    errors: list[tuple[str, str, str]] = field(default_factory=list)  # (code, key, message)
    warnings: list[tuple[str, str, str]] = field(default_factory=list)
    entries: int = 0

    def error(self, code: str, key: str, msg: str) -> None:
        self.errors.append((code, key, msg))

    def warn(self, code: str, key: str, msg: str) -> None:
        self.warnings.append((code, key, msg))

    @property
    def ok(self) -> bool:
        return not self.errors


def _walk_loader_rules(node: Any, rules: Rules, report: Report) -> None:
    """Mirror walkPluginLanguagePackCatalog: structure, safe keys, limits, protected paths."""
    if not isinstance(node, dict):
        report.error("structure", "", "language pack root must be an object")
        return
    stack: list[tuple[dict[str, Any], str, int]] = [(node, "", 0)]
    while stack:
        cur, prefix, depth = stack.pop()
        if depth > rules.max_depth:
            report.error("depth", prefix, f"catalog exceeds depth {rules.max_depth}")
            return
        for key, value in cur.items():
            report.entries += 1
            if report.entries > rules.max_entries:
                report.error("entries", prefix, f"catalog exceeds {rules.max_entries} entries")
                return
            path = f"{prefix}.{key}" if prefix else key
            if (
                not key
                or len(key) > 128
                or key in ("__proto__", "prototype", "constructor")
                or "." in key
                or any(ord(c) <= 31 for c in key)
            ):
                report.error("unsafe-key", path, "catalog key is not safe")
                continue
            if isinstance(value, dict):
                # A protected container is only walkable if exempt chrome is below it.
                if is_protected(path, rules) and not any(e.startswith(path + ".") for e in rules.exempt):
                    report.error("protected", path, "cannot replace protected security copy")
                    continue
                stack.append((value, path, depth + 1))
            elif isinstance(value, str):
                if is_protected(path, rules):
                    report.error("protected", path, "cannot replace protected security copy")
                elif utf16_len(value) > rules.max_string:
                    report.error("too-long", path, f"exceeds {rules.max_string} characters")
            else:
                report.error("structure", path, "must be a string or object")


def validate_catalog(
    zh: dict[str, Any],
    en_flat: dict[str, str],
    rules: Rules,
    glossary: Glossary,
    keep_english: frozenset[str] = frozenset(),
    strict_coverage: bool = False,
    stale: frozenset[str] = frozenset(),
) -> Report:
    """Validate a nested catalog `zh` against the flattened English source."""
    report = Report()
    _walk_loader_rules(zh, rules, report)
    if report.errors and any(c in ("structure", "depth", "entries") for c, _, _ in report.errors):
        return report  # not safe to flatten

    zh_flat = flatten(zh)
    translatable = {k for k, v in en_flat.items() if is_translatable(k, v, rules)}

    for key, text in zh_flat.items():
        en = en_flat.get(key)
        if en is None:
            report.error("orphan", key, "not in upstream en.json (removed or renamed upstream)")
            continue
        if not text.strip():
            report.error("empty", key, "empty translation")
            continue

        if placeholders(en) != placeholders(text):
            report.error(
                "placeholder",
                key,
                f"placeholders differ: en {sorted(placeholders(en))} vs zh {sorted(placeholders(text))}",
            )
        if tags(en) != tags(text):
            report.warn("tag", key, f"angle-bracket tokens differ: en {sorted(tags(en))} vs zh {sorted(tags(text))}")

        bad = sorted({c for c in text if c in glossary.simplified_chars})
        if bad:
            report.error("simplified", key, f"Simplified-Chinese characters: {''.join(bad)}")
        for level, code, msg in terminology_hits(key, text, glossary):
            if level == "error":
                report.error(code, key, msg)
            else:
                report.warn(code, key, msg)

        if text == en and key not in keep_english and any(c.isalpha() for c in en):
            report.warn("identical", key, "identical to English and not in keep-english.json")

    missing = sorted(translatable - zh_flat.keys())
    outdated = sorted(k for k in stale if k in zh_flat)
    emit = report.error if strict_coverage else report.warn
    for key in missing:
        emit("missing", key, "translatable upstream key has no translation")
    for key in outdated:
        emit("stale", key, "upstream English changed since this was translated")
    return report


# Mirrors upstream plugin-id-format.ts / plugin-manifest.ts / plugin-marketplace.ts.
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_SEMVER_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
# pluginLanguagePackContributionSchema is strict today: only these keys.
LANGUAGE_PACK_KEYS = frozenset({"locale", "path"})


def check_manifests(plugin: dict[str, Any], marketplace: dict[str, Any] | None) -> list[str]:
    """Identity problems that make Orca reject the plugin or marketplace outright.

    The classic trap: `publisher` must be lowercase kebab-case even though the
    marketplace `owner` and GitHub URLs may use capitals.
    """
    errs: list[str] = []
    publisher, pid = plugin.get("publisher", ""), plugin.get("id", "")
    for field_name, value in (("publisher", publisher), ("id", pid)):
        if not _ID_RE.match(value) or len(value) > 64:
            errs.append(f"orca-plugin.json {field_name} {value!r} must be lowercase kebab-case (a-z, 0-9, dashes)")
    if publisher == "stablyai" or pid.startswith("orca-"):
        errs.append(
            f"orca-plugin.json identity {publisher}.{pid} is reserved by Orca (publisher 'stablyai' or id prefix "
            "'orca-'); it is only installable from the stablyai GitHub organization"
        )
    if not _SEMVER_RE.match(plugin.get("version", "")):
        errs.append(f"orca-plugin.json version {plugin.get('version')!r} is not semver")
    if marketplace is not None:
        owner = marketplace.get("owner", "")
        if not _OWNER_RE.match(owner):
            errs.append(f"orca-marketplace.json owner {owner!r} is invalid")
        ids = [e.get("id") for e in marketplace.get("plugins", [])]
        if f"{publisher}.{pid}" not in ids:
            errs.append(f"orca-marketplace.json has no plugin id {publisher}.{pid!s} (found {ids}); it must equal <publisher>.<id> of the manifest")
        if len(ids) != len(set(ids)):
            errs.append("orca-marketplace.json has duplicate plugin ids")
    errs.extend(language_pack_errors(plugin))
    return errs


def language_pack_errors(plugin: dict[str, Any]) -> list[str]:
    """Unknown language-pack keys fail the whole plugin under the strict schema.

    `displayName` is not accepted until upstream ships it. `locale` and `path` pass.
    """
    errs: list[str] = []
    contributes = plugin.get("contributes")
    if not isinstance(contributes, dict):
        return errs
    packs = contributes.get("languagePacks")
    if packs is None:
        return errs
    if not isinstance(packs, list):
        errs.append("orca-plugin.json contributes.languagePacks must be an array")
        return errs
    for index, pack in enumerate(packs):
        if not isinstance(pack, dict):
            errs.append(f"orca-plugin.json languagePacks[{index}] must be an object")
            continue
        unknown = sorted(set(pack) - LANGUAGE_PACK_KEYS)
        if unknown:
            errs.append(
                "orca-plugin.json languagePacks"
                f"[{index}] has unknown key(s) {', '.join(unknown)}; "
                "pluginLanguagePackContributionSchema only accepts locale and path"
            )
    return errs


def summarize(items: list[tuple[str, str, str]], limit: int = 8) -> str:
    """Group report items by code for terminal output."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for code, key, msg in items:
        groups[code].append((key, msg))
    lines = []
    for code, entries in sorted(groups.items()):
        lines.append(f"  [{code}] x{len(entries)}")
        for key, msg in entries[:limit]:
            lines.append(f"    {key}: {msg}" if key else f"    {msg}")
        if len(entries) > limit:
            lines.append(f"    ... {len(entries) - limit} more")
    return "\n".join(lines)

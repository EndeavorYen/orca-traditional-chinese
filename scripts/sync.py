#!/usr/bin/env python3
"""Keep an Orca language pack in sync with upstream `en.json`.

    sync.py init --base <sha>   record which English text the current pack was translated from
    sync.py check               diff upstream vs. pack (exit 1 when out of date)
    sync.py prepare             write translation batches to work/todo/
    sync.py apply               merge work/done/ into the pack, validate, update lock + README
    sync.py validate            check the pack against the loader rules and glossary

Stdlib only. See docs/MAINTAINING.md for the workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate as V  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
LOCK = ROOT / ".sync" / "lock.json"
KEEP = ROOT / ".sync" / "keep-english.json"
GLOSSARY = ROOT / "scripts" / "glossary.json"
MANIFEST = ROOT / "orca-plugin.json"
MARKETPLACE = ROOT / "orca-marketplace.json"
README = ROOT / "README.md"

UPSTREAM_REPO = "stablyai/orca"
EN_PATH = "src/renderer/src/i18n/locales/en.json"
ARTIFACT_PATH = "src/shared/plugins/plugin-language-pack-artifact.ts"
CHROME_PATH = "src/shared/plugins/plugin-translatable-chrome.ts"

STATUS_START, STATUS_END = "<!-- sync-status:start -->", "<!-- sync-status:end -->"


class SyncError(RuntimeError):
    pass


# --------------------------------------------------------------------------- upstream


@dataclass
class Snapshot:
    """Everything sync needs from one upstream commit."""

    sha: str
    date: str
    en: dict[str, str]  # flattened
    en_tree: dict[str, Any]  # original nesting/order
    rules: V.Rules


class Upstream:
    """Reads files from stablyai/orca over HTTPS, or from a local clone (--upstream-dir)."""

    def __init__(self, ref: str, local: str | None = None):
        self.ref, self.local = ref, local
        self.sha = ""
        self.date = ""

    def _http(self, url: str, accept: str | None = None) -> bytes:
        headers = {"User-Agent": "orca-language-pack-sync"}
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if accept:
            headers["Accept"] = accept
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise SyncError(f"GET {url} -> HTTP {e.code}") from e
            except urllib.error.URLError as e:
                if attempt == 2:
                    raise SyncError(f"GET {url} failed: {e.reason}") from e
                time.sleep(2)
        raise SyncError(f"GET {url} failed")

    def _git(self, *args: str) -> str:
        r = subprocess.run(["git", "-C", self.local, *args], capture_output=True, text=True)
        if r.returncode:
            raise SyncError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def resolve(self) -> None:
        if self.local:
            self.sha = self._git("rev-parse", self.ref).strip()
            self.date = self._git("show", "-s", "--format=%cI", self.sha).strip()
        else:
            data = json.loads(self._http(f"https://api.github.com/repos/{UPSTREAM_REPO}/commits/{self.ref}"))
            self.sha = data["sha"]
            self.date = data["commit"]["committer"]["date"]

    def read(self, path: str) -> str:
        if not self.sha:
            self.resolve()
        if self.local:
            return self._git("show", f"{self.sha}:{path}")
        url = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{self.sha}/{path}"
        return self._http(url).decode("utf-8")

    def snapshot(self) -> Snapshot:
        self.resolve()
        tree = json.loads(self.read(EN_PATH))
        try:
            en = V.flatten(tree)
            rules = V.parse_rules(self.read(ARTIFACT_PATH), self.read(CHROME_PATH))
        except (ValueError, V.RulesError) as e:
            raise SyncError(str(e)) from e
        return Snapshot(self.sha, self.date, en, tree, rules)


# --------------------------------------------------------------------------- files


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text("utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def locale_path() -> Path:
    manifest = read_json(MANIFEST)
    packs = manifest["contributes"]["languagePacks"]
    if len(packs) != 1:
        raise SyncError("orca-plugin.json: expected exactly one language pack")
    return ROOT / packs[0]["path"]


def h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def load_glossary() -> V.Glossary:
    return V.Glossary.from_json(read_json(GLOSSARY, {}))


# --------------------------------------------------------------------------- diff


@dataclass
class Diff:
    add: list[str] = field(default_factory=list)  # translatable upstream keys with no translation
    changed: list[str] = field(default_factory=list)  # English text changed since translated
    removed: list[str] = field(default_factory=list)  # translated, but gone upstream
    now_excluded: list[str] = field(default_factory=list)  # translated, but now protected / oversize
    untracked: list[str] = field(default_factory=list)  # translated, no lock entry (run `init`)
    protected: int = 0
    oversize: int = 0

    @property
    def pending(self) -> int:
        return len(self.add) + len(self.changed) + len(self.removed) + len(self.now_excluded)


def compute_diff(snap: Snapshot, zh_flat: dict[str, str], lock_sources: dict[str, str]) -> Diff:
    d = Diff()
    for key, en in snap.en.items():
        if V.is_protected(key, snap.rules):
            d.protected += 1
        elif not V.is_translatable(key, en, snap.rules):
            d.oversize += 1
    for key, en in snap.en.items():
        if not V.is_translatable(key, en, snap.rules):
            continue
        if key not in zh_flat:
            d.add.append(key)
        elif key not in lock_sources:
            d.untracked.append(key)
        elif lock_sources[key] != h(en):
            d.changed.append(key)
    for key in zh_flat:
        if key not in snap.en:
            d.removed.append(key)
        elif not V.is_translatable(key, snap.en[key], snap.rules):
            d.now_excluded.append(key)
    return d


def load_state() -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    lock = read_json(LOCK, {})
    p = locale_path()
    zh_tree = read_json(p, {})
    return lock, zh_tree, V.flatten(zh_tree)


def print_diff(d: Diff, snap: Snapshot, lock: dict[str, Any]) -> None:
    prev = (lock.get("upstream") or {}).get("sha", "")[:7] or "(no lock)"
    print(f"upstream {UPSTREAM_REPO}@{snap.sha[:7]} ({snap.date[:10]}); pack synced to {prev}")
    print(f"  + add      {len(d.add):>5}  translatable keys with no translation")
    print(f"  ~ changed  {len(d.changed):>5}  English text changed; translation is stale")
    print(f"  - removed  {len(d.removed):>5}  gone upstream")
    if d.now_excluded:
        print(f"  ! excluded {len(d.now_excluded):>5}  now protected/oversize; must be dropped")
    if d.untracked:
        print(f"  ? untracked{len(d.untracked):>5}  no lock entry; run `sync.py init --base <sha>`")
    print(f"  (skipped by design: {d.protected} protected, {d.oversize} oversize)")
    for w in snap.rules.warnings:
        print(f"  WARNING: {w}")


# --------------------------------------------------------------------------- merge


def _place(node: dict[str, Any], key: str, value: Any, preceding: list[str]) -> None:
    """Insert key right after its nearest preceding upstream sibling, to keep diffs local."""
    if key in node:
        return
    anchor = next((k for k in reversed(preceding) if k in node), None)
    if anchor is None and preceding:
        node[key] = value  # no anchor: append
        return
    items = list(node.items())
    idx = 0 if anchor is None else [k for k, _ in items].index(anchor) + 1
    items.insert(idx, (key, value))
    node.clear()
    node.update(items)


def _prune(node: dict[str, Any]) -> bool:
    for k in [k for k, v in node.items() if isinstance(v, dict) and _prune(v)]:
        del node[k]
    return not node


def merge_catalog(
    zh_tree: dict[str, Any],
    en_tree: dict[str, Any],
    adds: dict[str, str],
    updates: dict[str, str],
    removals: set[str],
) -> dict[str, Any]:
    """Apply removals/updates/additions to `zh_tree` in place, preserving its key order."""
    for path in removals:
        parts = path.split(".")
        node = zh_tree
        for part in parts[:-1]:
            node = node.get(part, {})
        node.pop(parts[-1], None)
    _prune(zh_tree)

    for path, text in updates.items():
        parts = path.split(".")
        node = zh_tree
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = text

    prefixes = {".".join(p.split(".")[:i]) for p in adds for i in range(1, len(p.split(".")))}

    def walk(zh_node: dict[str, Any], en_node: dict[str, Any], prefix: str) -> None:
        keys = list(en_node)
        for i, key in enumerate(keys):
            path = f"{prefix}.{key}" if prefix else key
            val = en_node[key]
            if isinstance(val, dict):
                if path not in prefixes:
                    continue
                _place(zh_node, key, {}, keys[:i])
                walk(zh_node[key], val, path)
            elif path in adds:
                _place(zh_node, key, adds[path], keys[:i])

    walk(zh_tree, en_tree, "")
    return zh_tree


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    """Seed the lock from the English text that existed when the pack was last synced."""
    up = Upstream(args.base, args.upstream_dir)
    snap = up.snapshot()
    _, _, zh_flat = load_state()
    missing = [k for k in zh_flat if k not in snap.en]
    if missing:
        print(f"warning: {len(missing)} translated keys are absent from {args.base[:7]}; "
              f"is --base the commit the pack was synced from? e.g. {missing[:3]}")
    sources = {k: h(snap.en[k]) for k in zh_flat if k in snap.en}
    write_json(LOCK, {"upstream": {"repo": UPSTREAM_REPO, "sha": snap.sha, "date": snap.date}, "sources": sources})
    if not KEEP.exists():
        same = sorted(k for k, v in zh_flat.items() if snap.en.get(k) == v and any(c.isalpha() for c in v))
        write_json(KEEP, same)
        print(f"seeded {KEEP.relative_to(ROOT)} with {len(same)} keys currently identical to English "
              "(brand names, CLI literals...) -- skim it once")
    print(f"lock written: {len(sources)} keys pinned to {snap.sha[:7]}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    snap = Upstream(args.ref, args.upstream_dir).snapshot()
    lock, _, zh_flat = load_state()
    d = compute_diff(snap, zh_flat, lock.get("sources", {}))
    if args.json:
        synced = (lock.get("upstream") or {}).get("date")
        days = None
        if synced:
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(synced.replace("Z", "+00:00"))).days
        print(json.dumps({
            "upstream": snap.sha, "upstream_date": snap.date,
            "synced": (lock.get("upstream") or {}).get("sha"), "days_since_synced": days,
            "add": len(d.add), "changed": len(d.changed), "removed": len(d.removed),
            "excluded": len(d.now_excluded), "untracked": len(d.untracked),
            "pending": d.pending, "rule_warnings": list(snap.rules.warnings),
        }))
    else:
        print_diff(d, snap, lock)
    return 1 if d.pending or d.untracked else 0


def cmd_prepare(args: argparse.Namespace) -> int:
    done = WORK / "done"
    if done.exists() and any(done.iterdir()) and not args.force:
        raise SyncError("work/done/ has translations from an earlier run; `apply` them or pass --force to discard")
    snap = Upstream(args.ref, args.upstream_dir).snapshot()
    lock, _, zh_flat = load_state()
    d = compute_diff(snap, zh_flat, lock.get("sources", {}))
    print_diff(d, snap, lock)
    todo_keys = sorted(d.add + d.changed)
    if not todo_keys and not (d.removed or d.now_excluded):
        print("nothing to do")
        return 0

    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "todo").mkdir(parents=True)
    done.mkdir()
    changed = set(d.changed)
    batches = [todo_keys[i : i + args.batch_size] for i in range(0, len(todo_keys), args.batch_size)]
    for n, keys in enumerate(batches, 1):
        items = []
        for k in keys:
            item: dict[str, str] = {"key": k, "en": snap.en[k]}
            if k in changed:
                item["current_zh"] = zh_flat[k]
            items.append(item)
        write_json(WORK / "todo" / f"{n:03d}.json", {"batch": n, "of": len(batches), "items": items})
    write_json(WORK / "state.json", {
        "sha": snap.sha, "date": snap.date, "ref": args.ref, "upstream_dir": args.upstream_dir,
        "add": sorted(d.add), "changed": sorted(d.changed), "removed": sorted(d.removed + d.now_excluded),
    })
    print(f"wrote {len(batches)} batches ({len(todo_keys)} keys) to work/todo/")
    print("next: translate each batch into work/done/NNN.json as {key: translation}, then `sync.py apply`")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    state = read_json(WORK / "state.json")
    if not state:
        raise SyncError("no work/state.json; run `sync.py prepare` first")
    snap = Upstream(state["sha"], state.get("upstream_dir")).snapshot()
    lock, zh_tree, zh_flat = load_state()

    translated: dict[str, str] = {}
    keep_new: list[str] = []
    for f in sorted((WORK / "done").glob("*.json")):
        data = read_json(f)
        if f.name == "_keep.json":
            keep_new = list(data)
            continue
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise SyncError(f"{f.name}: expected a flat {{key: translation}} object")
        translated.update(data)

    wanted = set(state["add"]) | set(state["changed"])
    extra = sorted(set(translated) - wanted)
    if extra:
        raise SyncError(f"{len(extra)} translated keys were not requested, e.g. {extra[:3]}")
    todo = sorted(wanted - set(translated))
    if todo and not args.allow_partial:
        raise SyncError(f"{len(todo)} of {len(wanted)} keys still untranslated, e.g. {todo[:3]} "
                        "(use --allow-partial to merge what is done)")

    adds = {k: v for k, v in translated.items() if k in set(state["add"])}
    updates = {k: v for k, v in translated.items() if k in set(state["changed"])}
    removals = set(state["removed"]) & set(zh_flat)
    merge_catalog(zh_tree, snap.en_tree, adds, updates, removals)

    keep = set(read_json(KEEP, [])) | set(keep_new)
    keep -= removals
    sources = dict(lock.get("sources", {}))
    for k in removals:
        sources.pop(k, None)
    for k in translated:
        sources[k] = h(snap.en[k])

    report = V.validate_catalog(
        zh_tree, snap.en, snap.rules, load_glossary(), frozenset(keep),
        strict_coverage=not todo, stale=frozenset(k for k in sources if sources[k] != h(snap.en.get(k, "")))
    )
    print_report(report)
    if not report.ok:
        print("\nnot written: fix the errors above (edit work/done/*.json) and re-run `apply`")
        return 1
    if args.dry_run:
        print(f"\ndry run: would add {len(adds)}, update {len(updates)}, remove {len(removals)}")
        return 0

    write_json(locale_path(), zh_tree)
    write_json(KEEP, sorted(keep))
    upstream = {"repo": UPSTREAM_REPO, "sha": snap.sha, "date": snap.date} if not todo else lock.get("upstream")
    write_json(LOCK, {"upstream": upstream, "sources": dict(sorted(sources.items()))})
    if not args.no_bump:
        bump_version()
    update_readme(snap, len(V.flatten(zh_tree)), snap_counts(snap), partial=bool(todo))
    print(f"\napplied: +{len(adds)} ~{len(updates)} -{len(removals)}"
          + (f"; {len(todo)} keys still pending" if todo else "; pack is in sync"))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    lock, zh_tree, _ = load_state()
    ref = args.ref or (lock.get("upstream") or {}).get("sha") or "main"
    snap = Upstream(ref, args.upstream_dir).snapshot()
    sources = lock.get("sources", {})
    stale = frozenset(k for k, s in sources.items() if k in snap.en and s != h(snap.en[k]))
    report = V.validate_catalog(
        zh_tree, snap.en, snap.rules, load_glossary(), frozenset(read_json(KEEP, [])),
        strict_coverage=args.strict, stale=stale,
    )
    manifest_errors = V.check_manifests(read_json(MANIFEST, {}), read_json(MARKETPLACE))
    for e in manifest_errors:
        report.error("manifest", "", e)
    print(f"validated {locale_path().relative_to(ROOT)} against {UPSTREAM_REPO}@{snap.sha[:7]}: "
          f"{report.entries} entries")
    for w in snap.rules.warnings:
        print(f"WARNING: {w}")
    print_report(report, show_warnings=args.warnings)
    return 0 if report.ok else 1


def print_report(report: V.Report, show_warnings: bool = True) -> None:
    if report.errors:
        print(f"{len(report.errors)} error(s):\n{V.summarize(report.errors)}")
    if report.warnings:
        if show_warnings:
            print(f"{len(report.warnings)} warning(s):\n{V.summarize(report.warnings)}")
        else:
            print(f"{len(report.warnings)} warning(s) (use --warnings to list)")
    if report.ok:
        print("OK")


# --------------------------------------------------------------------------- release metadata


def bump_version() -> None:
    text = MANIFEST.read_text("utf-8")
    m = re.search(r'"version":\s*"(\d+)\.(\d+)\.(\d+)"', text)
    if not m:
        raise SyncError("orca-plugin.json: no semver version to bump")
    new = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
    MANIFEST.write_text(text.replace(m.group(0), f'"version": "{new}"', 1), "utf-8")
    print(f"version -> {new}")


def snap_counts(snap: Snapshot) -> tuple[int, int, int]:
    protected = sum(V.is_protected(k, snap.rules) for k in snap.en)
    oversize = sum(1 for k, v in snap.en.items()
                   if not V.is_protected(k, snap.rules) and not V.is_translatable(k, v, snap.rules))
    return len(snap.en) - protected - oversize, protected, oversize


def update_readme(snap: Snapshot, translated: int, counts: tuple[int, int, int], partial: bool) -> None:
    total, protected, oversize = counts
    text = README.read_text("utf-8")
    if STATUS_START not in text:
        print("README has no sync-status markers; skipped")
        return
    pct = int(translated / total * 100) if total else 0
    block = (
        f"{STATUS_START}\n"
        f"- **{translated:,} / {total:,}** translatable strings translated ({pct}%)\n"
        f"- Synced against `{UPSTREAM_REPO}` `main` (`{snap.sha[:7]}`, {snap.date[:10]})"
        f"{' -- partial, resync in progress' if partial else ''}\n"
        f"- {protected} plugin-protected keys and {oversize} oversize inline-CSS keys are excluded "
        f"by design and fall back to English\n"
        f"{STATUS_END}"
    )
    text = re.sub(re.escape(STATUS_START) + r".*?" + re.escape(STATUS_END), lambda _: block, text, flags=re.S)
    README.write_text(text, "utf-8")


# --------------------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--upstream-dir", help="use a local clone of stablyai/orca instead of GitHub")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="pin the lock to the commit the pack was last synced from")
    s.add_argument("--base", required=True, help="upstream commit the current translations correspond to")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("check", help="report drift from upstream; exit 1 if out of date")
    s.add_argument("--ref", default="main")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("prepare", help="write translation batches to work/todo/")
    s.add_argument("--ref", default="main")
    s.add_argument("--batch-size", type=int, default=150)
    s.add_argument("--force", action="store_true", help="discard unapplied work/done/")
    s.set_defaults(fn=cmd_prepare)

    s = sub.add_parser("apply", help="merge work/done/ into the pack")
    s.add_argument("--allow-partial", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-bump", action="store_true", help="do not bump the plugin version")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("validate", help="validate the pack (default: against the locked upstream commit)")
    s.add_argument("--ref")
    s.add_argument("--strict", action="store_true", help="missing/stale keys are errors")
    s.add_argument("--warnings", action="store_true", help="list every warning")
    s.set_defaults(fn=cmd_validate)

    args = p.parse_args()
    try:
        return args.fn(args)
    except SyncError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

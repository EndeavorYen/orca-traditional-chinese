#!/usr/bin/env python3
"""Generate the GitHub release body for the current pack version.

Reads orca-plugin.json for the version and .sync/lock.json for the pinned
upstream release. The drift line diffs the per-key English hashes in the
current lock against the lock at the previous release tag, which is exactly
what the last resync absorbed. Coverage counts locked keys the pack carries;
the definition matches the README sync-status block. Stdlib only, no network.

The body never contains a translator- or tool-credit line
(docs/MAINTAINING.md). A hand-written `Terminology:` line is the only
expected addition; edit it in after publishing (`gh release edit`).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "orca-plugin.json"
LOCK = ROOT / ".sync" / "lock.json"
PACK = ROOT / "locales" / "zh-TW.json"
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def git(args: list[str]) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def flat_keys(value, prefix: str = ""):
    for key, val in value.items():
        path = f"{prefix}{key}"
        if isinstance(val, dict):
            yield from flat_keys(val, f"{path}.")
        else:
            yield path


def previous_tag(target: str) -> str | None:
    """Newest tagged release reachable from HEAD that is not `target`."""
    tags = git(["tag", "--merged", "HEAD", "--sort=-creatordate"]).splitlines()
    for tag in tags:
        if TAG_RE.match(tag) and tag != target:
            return tag
    return None


def drift(prev: dict[str, str], cur: dict[str, str]) -> tuple[int, int, int]:
    """(added, changed, removed) between two lock source-hash maps."""
    added = len(set(cur) - set(prev))
    removed = len(set(prev) - set(cur))
    changed = sum(1 for key in set(prev) & set(cur) if prev[key] != cur[key])
    return added, changed, removed


def coverage(lock: dict, pack_keys: set[str]) -> tuple[int, int]:
    sources = lock["sources"]
    translated = sum(1 for key in sources if key in pack_keys)
    return translated, len(sources)


def build_notes(
    version: str,
    lock: dict,
    pack_keys: set[str],
    prev_sources: dict[str, str] | None,
) -> str:
    up = lock["upstream"]
    lines = [
        f"## Synced to Orca {up['release']}",
        "",
        f"- Upstream: `{up['repo']}` `{up['release']}` (`{up['sha'][:7]}`, {up['date'][:10]})",
    ]
    if prev_sources is not None:
        added, changed, removed = drift(prev_sources, lock["sources"])
        lines.append(
            f"- Catalog drift: **+{added}** added / **~{changed}** changed"
            f" / **-{removed}** removed"
        )
    translated, total = coverage(lock, pack_keys)
    pct = int(translated / total * 100) if total else 0
    lines.append(f"- zh-TW coverage: {pct}% of translatable strings")
    repo = json.loads(MANIFEST.read_text("utf-8"))["repository"]
    if not repo.endswith(".git"):
        repo += ".git"
    lines += [
        "",
        "Install via Orca marketplace source:",
        f"`{repo}` (ref `main`)",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    version = json.loads(MANIFEST.read_text("utf-8"))["version"]
    lock = json.loads(LOCK.read_text("utf-8"))
    pack_keys = set(flat_keys(json.loads(PACK.read_text("utf-8"))))
    prev_sources = None
    prev = previous_tag(f"v{version}")
    if prev:
        try:
            prev_sources = json.loads(git(["show", f"{prev}:.sync/lock.json"]))["sources"]
        except (RuntimeError, KeyError, json.JSONDecodeError) as exc:
            print(f"warning: no drift line ({exc})", file=sys.stderr)
    sys.stdout.write(build_notes(version, lock, pack_keys, prev_sources))
    return 0


if __name__ == "__main__":
    sys.exit(main())

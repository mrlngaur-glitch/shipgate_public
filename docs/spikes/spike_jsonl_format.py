"""Phase 0, task 0.8 — Claude Code JSONL transcript format spike.

STRICTLY READ-ONLY. Opens transcript files for reading, writes nothing, moves nothing.

Purpose: the report specifies ingest of `~/.claude/projects/*.jsonl`
but the format is undocumented and vendor-controlled. Before the ledger schema is designed,
three questions must be answered from the data itself:

  Q1  What record types and top-level fields exist?
  Q2  Are per-message TOKEN and COST fields present, or only tokens (requiring a price table)?
  Q3  Are cache-read and cache-write tokens distinguishable?

Q2 and Q3 together decide whether `shipgate analyze` can compute a defensible savings % —
the one number marketing is permitted to use. If they cannot, that is a finding to
escalate, not to work around.

PRIVACY: this script prints STRUCTURE ONLY — field names, type names, counts. It never prints
message content, prompt text, file paths from the transcripts, or any field value that could
carry a secret. That is the same discipline the ledger's write-time redaction enforces.

Usage:
    .venv\\Scripts\\python.exe docs\\spikes\\spike_jsonl_format.py [--sample-files N] [--max-lines N]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Field names that plausibly carry usage/cost information. Presence/absence is the finding.
USAGE_HINTS = ("token", "cost", "usage", "cache", "price", "billing")


def find_transcripts(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (top-level session transcripts, sub-agent transcripts).

    The report's spec says `projects/*.jsonl`. Review finding R2 observed the real layout is
    `projects/<slug>/<uuid>.jsonl` PLUS `projects/<slug>/<uuid>/subagents/agent-*.jsonl`.
    Sub-agent transcripts are a second tier of token spend; ingest that skips them produces
    silently-low cost numbers — a vacuous pass in the product's own foundation.
    """
    all_files = list(root.rglob("*.jsonl"))
    subagents = [p for p in all_files if "subagents" in p.parts]
    sessions = [p for p in all_files if "subagents" not in p.parts]
    return sessions, subagents


def walk_paths(obj: object, prefix: str = "", out: set[str] | None = None) -> set[str]:
    """Collect dotted key paths, to a shallow depth. Keys only — never values."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            # Some records use free-text as dict KEYS (e.g. AskUserQuestion answers keyed by
            # the question itself). A key that long is content, not structure — skip it, or the
            # spike leaks the very thing write-time redaction exists to stop.
            if not isinstance(k, str) or len(k) > 40:
                continue
            path = f"{prefix}.{k}" if prefix else k
            out.add(path)
            if isinstance(v, dict) and path.count(".") < 3:
                walk_paths(v, path, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-files", type=int, default=25)
    ap.add_argument("--max-lines", type=int, default=500, help="lines read per file")
    args = ap.parse_args()

    if not PROJECTS_DIR.is_dir():
        print(f"NOT FOUND: {PROJECTS_DIR}")
        return 1

    sessions, subagents = find_transcripts(PROJECTS_DIR)
    total_bytes = sum(p.stat().st_size for p in sessions + subagents)

    print("=" * 72)
    print("CORPUS INVENTORY")
    print("=" * 72)
    print(f"root                      : {PROJECTS_DIR}")
    print(f"session transcripts       : {len(sessions)}")
    print(f"sub-agent transcripts     : {len(subagents)}   <-- not in the report's literal spec")
    print(f"total files               : {len(sessions) + len(subagents)}")
    print(f"total size                : {total_bytes / 1024 / 1024:.1f} MiB")

    # Sample the largest files: most record-type variety per line read.
    sample = sorted(sessions, key=lambda p: p.stat().st_size, reverse=True)[: args.sample_files]

    record_types: Counter[str] = Counter()
    top_keys: Counter[str] = Counter()
    usage_paths: Counter[str] = Counter()
    usage_types: dict[str, Counter[str]] = defaultdict(Counter)
    lines_read = 0
    parse_failures = 0

    for path in sample:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= args.max_lines:
                        break
                    lines_read += 1
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        parse_failures += 1
                        continue
                    if not isinstance(rec, dict):
                        continue
                    record_types[str(rec.get("type", "<no type field>"))] += 1
                    top_keys.update(rec.keys())
                    for p in walk_paths(rec):
                        leaf = p.rsplit(".", 1)[-1].lower()
                        if any(h in leaf for h in USAGE_HINTS):
                            usage_paths[p] += 1
                            # Record the VALUE TYPE only, never the value.
                            node: object = rec
                            for part in p.split("."):
                                if isinstance(node, dict):
                                    node = node.get(part)
                                else:
                                    node = None
                                    break
                            usage_types[p][type(node).__name__] += 1
        except OSError as exc:
            print(f"  read error on a sampled file: {exc.__class__.__name__}")

    print()
    print("=" * 72)
    print(f"SAMPLE: {len(sample)} largest session files, {lines_read} lines, "
          f"{parse_failures} parse failures")
    print("=" * 72)

    print("\nQ1a — RECORD TYPES (`type` field)")
    for t, n in record_types.most_common(20):
        print(f"  {n:>7}  {t}")

    print("\nQ1b — TOP-LEVEL FIELDS")
    for k, n in top_keys.most_common(30):
        print(f"  {n:>7}  {k}")

    print("\nQ2/Q3 — TOKEN / COST / CACHE FIELD PATHS (structure only, no values)")
    if not usage_paths:
        print("  NONE FOUND — escalate: savings % may not be computable from this corpus.")
    else:
        for p, n in usage_paths.most_common(40):
            types = ", ".join(f"{t}×{c}" for t, c in usage_types[p].most_common(4))
            print(f"  {n:>7}  {p:<48} [{types}]")

    print("\nQ4 — PRICE-TABLE INPUTS (model attribution per priced message)")
    models: Counter[str] = Counter()
    sidechain: Counter[str] = Counter()
    priced_with_model = 0
    priced_total = 0
    for path in sample:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= args.max_lines:
                        break
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    sidechain[str(rec.get("isSidechain"))] += 1
                    msg = rec.get("message")
                    if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                        priced_total += 1
                        model = msg.get("model")
                        if isinstance(model, str):
                            priced_with_model += 1
                            models[model] += 1
        except OSError:
            continue

    print(f"  messages carrying a usage block   : {priced_total}")
    print(f"  ...of which carry `message.model` : {priced_with_model}")
    print("  distinct models seen:")
    for m, n in models.most_common(15):
        print(f"      {n:>7}  {m}")
    print("  isSidechain distribution (sub-agent discrimination):")
    for v, n in sidechain.most_common():
        print(f"      {n:>7}  {v}")

    print("\n" + "=" * 72)
    print("VERDICT INPUTS (interpret in docs/jsonl_format_notes.md — do not conclude here)")
    print("=" * 72)
    has_cost = any("cost" in p.lower() for p in usage_paths)
    has_tokens = any("token" in p.lower() for p in usage_paths)
    has_cache = any("cache" in p.lower() for p in usage_paths)
    print(f"  direct cost field present        : {has_cost}")
    print(f"  token counts present             : {has_tokens}")
    print(f"  cache read/write distinguishable : {has_cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

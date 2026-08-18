# Claude Code JSONL Transcript Format — Spike Notes

**Phase 0, task 0.8** · **Date:** 2026-08-15 · **Status:** complete — this is the Phase 0 exit gate
**Reproduce:** `docs/spikes/spike_jsonl_format.py`, included in this repo, produced this
analysis by reading the founder's private transcript corpus directly at
`~/.claude/projects/`. The corpus itself is not included and cannot be — only someone
running the script against their own such corpus reproduces these numbers. The script
prints structure only (field names, type names, counts), never transcript content.
**Method:** read-only. 25 largest session transcripts, 10,000 lines, **0 parse failures**.
**Honesty note:** this is a **sample**, not the full corpus. Findings below are strong signals, not a
census. Full-corpus confirmation happens during Phase 1 task 1.5 (ingest), and any finding that
changes is recorded as a supersession, not an edit.

---

## The question this spike existed to answer

> **Can `shipgate analyze` compute a defensible savings % from the founder's own transcript corpus?**
> That number is the only figure marketing may use. Discovering it uncomputable
> in Week 4 would be launch-breaking; discovering it now cost one day.

## **Answer: YES — with one named, ongoing cost.**

Token accounting is complete and unusually rich. **Cost accounting is not present and must be
derived** from a model price table we build and maintain. Detail in Q2.

---

## Corpus inventory

| Measure | Value |
|---|---|
| Session transcripts | **95** |
| **Sub-agent transcripts** | **402** |
| Total files | 497 |
| Total size | 1,212 MiB |

### 🔴 Finding R2 confirmed — and materially worse than estimated

The report specifies ingest of `~/.claude/projects/*.jsonl`. The actual layout is:

```
~/.claude/projects/<project-slug>/<session-uuid>.jsonl                    95 files
~/.claude/projects/<project-slug>/<session-uuid>/subagents/agent-*.jsonl  402 files
```

**402 of 497 files — 81% — are sub-agent transcripts.** The review estimated this as a missing tier;
it is in fact the *majority of the corpus*. An ingest built to the literal spec would silently omit
four out of five transcript files and report token spend that is wrong by a wide, unknowable margin.

That is precisely the failure class the product exists to catch: a number that looks authoritative
because a check ran, when the check never observed most of the evidence. **Ingest must walk the tree
recursively and label each file's tier.** This is a spec correction, not a scope change — recorded
here, and folded into Phase 1 task 1.5.

*Note:* the `isSidechain` field is `False` or absent across all sampled **session** files, so it does
not by itself discriminate sub-agent records. **Tier must be derived from the file path**, then
stored explicitly on the row so downstream queries never have to re-derive it.

---

## Q1 — Record types and fields

**11 record types** in the sample:

| Type | Count | Note |
|---|---|---|
| `assistant` | 4,595 | **carries `message.usage` — the priced records** |
| `user` | 2,541 | |
| `last-prompt` | 671 | |
| `ai-title` | 651 | |
| `attachment` | 453 | |
| `queue-operation` | 411 | |
| `file-history-snapshot` | 305 | |
| `custom-title` | 141 | |
| `file-history-delta` | 114 | |
| `mode` | 102 | |
| `system` | 16 | |

**Top-level fields useful for the ledger** (present on ~7,605 of 10,000 lines):

| Field | Use in the ledger |
|---|---|
| `sessionId` | → `sessions` table key |
| `uuid` / `parentUuid` | Turn chaining — the transcript is a linked list, not a flat log |
| `timestamp` | Event ordering; the `verify_after` / recheck window at F2 |
| `cwd`, `gitBranch` | Project + branch attribution; needed for the merge gate at F1 |
| `version` | **Claude Code version that produced the row** — the format is vendor-controlled, so version-stamping every row is what makes a future format change diagnosable instead of mysterious |
| `requestId`, `promptId` | Request-level grouping for the repeated-payload heuristic |
| `toolUseResult` | Tool-call evidence — the raw material for claims audit at F1 |
| `permissionMode`, `effort` | Session context |

---

## Q2 — Token and cost fields

**No direct cost field exists anywhere in the corpus.** Token counts are complete:

| Field | Type | Coverage |
|---|---|---|
| `message.usage.input_tokens` | int | 4,595 / 4,595 priced messages |
| `message.usage.output_tokens` | int | 4,595 |
| `message.usage.cache_read_input_tokens` | int | 4,595 |
| `message.usage.cache_creation_input_tokens` | int | 4,595 |
| `message.usage.cache_creation.ephemeral_5m_input_tokens` | int | 4,595 |
| `message.usage.cache_creation.ephemeral_1h_input_tokens` | int | 4,595 |
| `message.usage.output_tokens_details.thinking_tokens` | int | 730 |
| `toolUseResult.usage.*` + `toolUseResult.totalTokens` | int | 10 — a **second** usage tier |

### Consequence — a decision the founder should see, not just a technical note

Cost must be **derived**: `tokens × price`, from a price table we author and maintain, keyed by model
and token class. Three things follow, all real:

1. **The price table is a maintenance obligation forever.** Model prices change; the table must be
   versioned and every computed cost must record *which table version produced it*, or historical
   Ship Reports silently change meaning when prices update.
2. **Cost figures are estimates, and must be labelled as estimates** in the Ship Report. The report's
   `cost per verified outcome` north-star metric is derived, not observed. Calling a derived number
   "measured" would be exactly the rounding-up this project's own discipline forbids.
3. **Cache-creation tokens are priced differently by TTL** (5-minute vs 1-hour), and both are
   exposed — so the estimate can be accurate rather than approximate, provided the table carries
   per-TTL rates.

**Recommendation for Phase 1:** the price table ships as a versioned data file (`shipgate/analyze/
prices/*.yaml`), every cost row records `price_table_version`, and every rendered cost carries the
word *estimated*. Cheap now; not retrofittable once numbers are public.

---

## Q3 — Cache read/write discrimination

**Fully distinguishable — better than the report assumed.** `cache_read_input_tokens` and
`cache_creation_input_tokens` are separate integers on every priced message, split further by TTL.

Two unexpected finds that make `analyze` stronger than specified:

- **`message.diagnostics.cache_miss_reason`** (105 records, with `cache_missed_input_tokens` on 21).
  The product's design calls for a *cache-hostility heuristic* — inferring volatile prefixes indirectly.
  **This field states the reason directly.** Where present, `analyze` should report the observed
  reason and reserve the heuristic for records lacking it. A stated cause beats an inferred one, and
  it makes the demo materially more convincing.
- **`compactMetadata.preTokens` / `postTokens` / `cumulativeDroppedTokens`** (3 records in-sample).
  Direct measurement of context dropped at compaction — a waste signal the report doesn't list.
  Logged as a parked, out-of-scope idea rather than built: it is new scope, however tempting.

---

## Q4 — Model attribution

**`message.model` is present on 4,595 of 4,595 priced messages — 100% coverage.** A price table keyed
by model is viable with no gaps.

| Model | Priced messages in sample |
|---|---|
| `claude-sonnet-5` | 2,709 |
| `claude-opus-4-8` | 1,005 |
| `claude-opus-4-7` | 370 |
| `claude-fable-5` | 304 |
| `claude-opus-4-6` | 204 |
| `<synthetic>` | 3 |

**`<synthetic>` must be excluded from cost aggregation** — it is not a billable model. Three records
in the sample; unhandled, it becomes either a crash or a silent zero. This is the housekeeping-event
exclusion discipline (the `TRUE_SKIP` taxonomy lesson) appearing on day one.

Also note **`thinking_tokens`** (730 records): billed as output tokens. Both double-counting them and
omitting them produce wrong totals. Handle explicitly, with a test.

---

## Inputs to the Phase 1 ledger schema

Direct consequences for task 1.2, resolved before the schema is written (which was the point of
inverting the build order):

1. `events` needs a **`transcript_tier`** column (`session` | `subagent`), derived from path.
2. `events` needs **`parent_uuid`** — the transcript is a linked list; flattening it loses turn structure.
3. `events` needs **`cc_version`** — the format is vendor-controlled and *will* change.
4. Token columns must be **six distinct integers**, not one total: `input`, `output`,
   `cache_read`, `cache_creation_5m`, `cache_creation_1h`, `thinking`. Collapsing them destroys
   exactly the signal `analyze` sells.
5. Cost is a **derived column** carrying `price_table_version`, never a raw ingested value.
6. `model` on every priced row, with `<synthetic>` and other non-billable values excluded by an
   explicit allowlist — not by a silent `try/except`.
7. Ingest is **streaming with a per-file high-water mark**: 1.2 GiB, 497 files, and this
   corpus only grows.

---

## Spike hygiene — a defect in this spike, recorded

The first run printed a dict **key** that was free text (an `AskUserQuestion` answers map keyed by
the question itself), leaking transcript content into spike output that was supposed to be
structure-only. Root cause: the walker assumed dict keys are field names. Fixed — keys longer than 40
characters are now skipped, with the reason recorded in-file.

Recorded rather than quietly patched because it is the product's own thesis in miniature: **a tool
built to respect a boundary violated it on first run, and only an explicit check caught it.**
The ledger's write-time redaction (Phase 1 task 1.3) needs the same assumption tested — secrets can
sit in keys, not only in values.

---

## Phase 0 exit gate — result

| Gate condition | Result |
|---|---|
| CI green and provably not vacuous | 🟢 **green** — 3 tests pass; empty suite → `VACUOUS SUITE`, exit 1 |
| `docs/jsonl_format_notes.md` exists | 🟢 **green** — this file |
| **Is a defensible savings % computable?** | 🟢 **YES** — full token accounting incl. per-TTL cache split; 100% model attribution. **Caveat: cost is derived from a maintained price table and must be labelled *estimated*, never *measured*.** |

**No escalation required. Phase 1 opens.**

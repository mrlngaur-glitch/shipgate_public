# Ledger schema — design and implementation record (Task 1.2)

**Status update: IMPLEMENTED and
`runtime-verified`.** This document was written as design-only; on founder review, all
four open questions below were decided and `shipgate/ledger/` was
built against them. The design reasoning below is kept as-is (it's still why the schema
looks the way it does); the **"Open design questions"** section is annotated with each
decision rather than rewritten, so the record shows what was asked and what was
answered, not just the final state. Test evidence: `tests/unit/test_ledger.py` (26
tests) and `tests/unit/test_redaction.py` (12 tests), all passing.

**Inputs honoured, not re-derived:** `docs/jsonl_format_notes.md` (Phase 0 spike, full
detail there) and the report's ingest specification. Nothing below re-opens those
findings; this document is the schema built *against* them.

---

## The five tables (fixed at exactly these five — no more, no fewer)

`sessions`, `events`, `claims`, `verdicts`, `supersessions`.

### 1. `sessions` — one row per top-level session UUID (95 in the current corpus)

| Column | Type | Notes |
|---|---|---|
| `session_id` | TEXT PK | the session UUID from the file path |
| `project_slug` | TEXT | from `~/.claude/projects/<project-slug>/` |
| `source_dir` | TEXT | relative path only — never a machine-specific absolute path baked into a portable ledger |
| `cwd` | TEXT, nullable | from the first record that carries it |
| `git_branch` | TEXT, nullable | needed for the merge gate at F1 |
| `started_at` / `ended_at` | TIMESTAMP | earliest / latest event timestamp seen; `ended_at` updates on incremental re-ingest |

A subagent transcript (`<uuid>/subagents/agent-*.jsonl`) is **not** its own `sessions`
row. Its events roll up into the parent session's `session_id`, tagged by
`events.transcript_tier = 'subagent'` — this is how the 81%-of-files finding
(`jsonl_format_notes.md` §Corpus inventory) gets represented without inventing a second
top-level entity the report didn't ask for (the smaller build).

### 2. `events` — one row per JSONL line, no exceptions

**Every line in every one of the 497 files becomes exactly one `events` row, whatever
its `record_type`.** This is deliberate, not lazy: the Phase 1 brief warns against
letting ingest "handle" a malformed or unfamiliar record silently, and the done-
condition ("row counts reconcile against a file-by-file count") only means something if
reconciliation is a literal 1:1 line count, not a filtered subset the analyst had to
trust.

| Column | Type | Notes |
|---|---|---|
| `event_id` | INTEGER PK, autoincrement | SQLite's native rowid — strictly increasing, which is what makes a single global hash chain possible (see below) |
| `session_id` | TEXT, FK → `sessions.session_id` | |
| `source_file` | TEXT | relative path of the physical JSONL file this row came from — the session file itself, or a specific `subagents/agent-*.jsonl` |
| `source_offset` | INTEGER | byte offset of this line within `source_file` |
| `transcript_tier` | TEXT, `'session'` \| `'subagent'` | derived from path, never from `isSidechain` (`jsonl_format_notes.md` — that field doesn't discriminate reliably) |
| `record_type` | TEXT | one of the 11 types the spike found (`assistant`, `user`, `system`, …) |
| `uuid` / `parent_uuid` | TEXT / TEXT nullable | the transcript's own linked-list structure — flattening loses turn order |
| `cc_version` | TEXT | per-row, not per-session, because the format is vendor-controlled and *can* change mid-session across a Claude Code upgrade |
| `model` | TEXT, nullable | `message.model` where present; `<synthetic>` and anything else is stored as-is here — **exclusion from cost is `analyze`'s job (Task 1.6), not ingest's.** Ingest that silently drops rows is the exact failure class this product exists to catch |
| `timestamp` | TIMESTAMP, nullable | |
| `request_id` / `prompt_id` | TEXT, nullable | request-level grouping for the repeated-payload heuristic |
| `permission_mode` / `effort` | TEXT, nullable | session context |
| `tokens_input` | INTEGER, nullable | |
| `tokens_output` | INTEGER, nullable | |
| `tokens_cache_read` | INTEGER, nullable | |
| `tokens_cache_creation_5m` | INTEGER, nullable | |
| `tokens_cache_creation_1h` | INTEGER, nullable | |
| `tokens_thinking` | INTEGER, nullable | billed as output; kept in its own column so it is never silently double-counted or silently dropped (`jsonl_format_notes.md` Q4) |
| `cache_miss_reason` | TEXT, nullable | `message.diagnostics.cache_miss_reason` where present — preferred over the heuristic |
| `raw_payload_redacted` | TEXT (JSON) | the tool-call / content payload, **after** write-time redaction (Task 1.3) — the raw material for `inventory_complete` and claims-audit evidence later |
| `row_hash` | TEXT | sha256 over this row's canonical fields, computed **after** redaction |
| `prev_row_hash` | TEXT, nullable | the `row_hash` of the immediately preceding `events` row in insertion order; `NULL` only for the very first row the ledger ever writes |

**No cost column.** Cost is `tokens × price`, computed at `analyze` time from a
versioned price table (Task 1.6), never stored as a ledger fact — storing a derived
number as if it were ingested data is exactly the kind of "rounding up" this project forbids of itself.
The ledger stores only what was actually observed.

**No 6th "ingest progress" table.** The per-file high-water mark (1.2 GiB,
resumable, streaming) is answered by a query, not new state: `MAX(source_offset) WHERE
source_file = X` tells the ingester exactly where to resume. Keeping this a derived
query instead of a stored table is the smaller build and gives one less thing that can
drift out of sync with the truth.

**Write order, strict:** parse line → redact (Task 1.3) → compute `row_hash` over the
*redacted* content → write row. Never redact after hashing or after writing — a secret
that touched disk even transiently before redaction has already failed the done-
condition, hash or no hash.

### 3. `claims`

| Column | Type | Notes |
|---|---|---|
| `claim_id` | TEXT PK | |
| `session_id` | TEXT, FK → `sessions` | |
| `source_event_id` | INTEGER, FK → `events`, nullable | the event where the claim was made or extracted from, when known |
| `source` | TEXT, `'shipfile_condition'` \| `'extracted_from_transcript'` | the two claim producers named in the report; both land after Phase 1 (Task 1.4 shipfile, Task 1.7 `analyze`'s would-have-failed extraction) — the column exists now so neither producer needs a schema change to land |
| `shipfile_condition_ref` | TEXT, nullable | points at a shipfile block once v0.1 exists; `NULL` for transcript-extracted claims |
| `text` | TEXT | the human-readable claim |
| `created_at` | TIMESTAMP | |

### 4. `verdicts`

Each row is exactly what `shipgate.verdicts.VerdictRecord` already models in Python
(built this session, `shipgate/verdicts/supersession.py`) — this table is that object
given a persistent, queryable home. Nothing about the taxonomy's rules changes; this is
storage, not redesign.

| Column | Type | Notes |
|---|---|---|
| `verdict_id` | INTEGER PK, autoincrement | |
| `claim_id` | TEXT, FK → `claims` | |
| `verdict` | TEXT, `CHECK` constraint against the exact 7 taxonomy strings | enforced at the SQL layer too, not only in `Verdict` the Python enum — a raw `INSERT` that bypassed the Python layer must still be unable to write an 8th verdict class into a frozen public interface |
| `evidence_tier` | TEXT, nullable, `CHECK`: non-null iff `verdict = 'verified'` | mirrors `VerdictRecord.__post_init__`, enforced twice for the same reason as above |
| `reason` | TEXT | |
| `supersedes_verdict_id` | INTEGER, self-FK → `verdicts.verdict_id`, nullable | a denormalized pointer for a fast "what's the current verdict for claim X" query without a join; **the `supersessions` table (below) is still the authoritative record of the correction** — every non-null `supersedes_verdict_id` must be written in the same transaction as a matching `supersessions` row |
| `created_at` | TIMESTAMP | |

Current verdict for a claim = the `verdicts` row for that `claim_id` with the highest
`verdict_id` (monotonic, and `supersedes_verdict_id` always points backward) — the same
`head = records[-1]` rule `ClaimVerdictHistory` already implements in memory.

### 5. `supersessions` — the general correction mechanism, not verdict-only

This project's own append-only rule is stated generally, not verdict-specific:
**"Never edit history — anywhere, including our own audit trail."** Not "never edit a verdict" — never edit
*any* ledger row. So `supersessions` is deliberately generic, not a second verdict-only
mechanism duplicating what `verdicts.supersedes_verdict_id` already tracks:

| Column | Type | Notes |
|---|---|---|
| `supersession_id` | INTEGER PK, autoincrement | |
| `target_table` | TEXT, `'sessions'` \| `'events'` \| `'claims'` \| `'verdicts'` | which table holds the corrected row |
| `superseded_row_id` | the row being corrected | its original content is never touched |
| `superseding_row_id` | the new row that replaces it as current | |
| `reason` | TEXT, required, never blank | this column is the entire point of the table |
| `created_at` | TIMESTAMP | |

Most `supersessions` rows will reference `verdicts` (that's the common case the report
describes — a verdict flipping via supersession). The table isn't restricted to that
case because nothing in the append-only law is either: a mis-extracted claim's text, or
a session's `git_branch` recorded wrong, get corrected the same way — a new row, a
`supersessions` entry naming what it replaces, and the old row untouched forever.

---

## The hash chain ("every event row carries a hash chained to the
previous row's hash"; Task 1.2 done-condition: "alter a row → chain verification fails
and names the row")

**Scope, as implemented: `events`, `claims`, and `verdicts` — three independent chains,
not one global chain across all five tables.** The report's literal text scopes chaining
to `events` only; the founder extended it to `verdicts` on review (open question 1,
below), and the analyst extended it to `claims` for the same reason. `sessions` and
`supersessions` are not chained — see `hashing.py`'s module docstring for the reasoning
per table.

**Design: each chained table has its own chain, in that table's SQLite `rowid` order —
not one chain interleaved across tables, and not one chain per session.** Reasoning, per
this project's own preference order (smaller build, local-first, keeps the gate central):

- Three independent chains is the smaller build over one interleaved global chain: each
  table verifies on its own, each names its own broken row unambiguously, and nothing
  has to reconcile insertion order *across* tables (which would need a shared sequence
  the report never asked for).
- It requires the ledger writer to be the **only** writer, appending serially — which is
  already true by design: no background daemons, ingest is a
  foreground, resumable, single-writer process. A per-table chain doesn't
  cost anything this project wasn't already committed to.
- Verification (`shipgate.ledger.integrity.verify_chain`): recompute `row_hash` for
  every row in `rowid` order; the first row whose stored `row_hash` doesn't match its
  recomputed value, **or** whose `prev_row_hash` doesn't match the previous row's
  `row_hash`, is the tampered row — named by the table's own id column (`event_id` /
  `claim_id` / `verdict_id`), directly satisfying the done-condition. `rowid` (not the
  declared primary key) is used for ordering so it works identically whether the PK is
  an autoincrement integer (`events`, `verdicts`) or a caller-supplied `TEXT`
  (`claims`).

---

## Open design questions — asked here, answered on founder review

Four genuine judgment calls the report doesn't answer directly, flagged rather than
quietly picked, per this project's own rule ("if still ambiguous, stop and ask"). Each is now
**decided and built** — the original question is kept verbatim so the record shows what
was asked; the decision is appended, not substituted in place of it.

1. **Should the hash chain also cover `verdicts` rows, not only `events`?** *(original
   question, kept as asked)* The report's literal text scopes chaining to `events`. A
   tampered verdict is arguably just as dangerous as a tampered event...
   → **DECIDED (founder): yes.** Report decision 32 has the Ship Report embed the hash
   range of the rows it was computed from, and that range is computed from verdict rows
   — an unchained `verdicts` table would make the self-verifying receipt verify the
   wrong thing. Implemented in `shipgate/ledger/hashing.py`, `schema.py`.
2. **UPDATE/DELETE prevention: Python-layer discipline, or a SQL-level trigger?**
   *(original question, kept as asked)* Recommendation for launch was Python-layer
   discipline only, smaller build...
   → **DECIDED (founder): SQLite trigger, not Python-layer discipline alone.** Same
   logic as the `verdicts` CHECK constraint (a raw `INSERT` bypassing Python must still
   be unable to write an 8th verdict class) applies identically to append-only —
   append-only is a stronger invariant than the enum, and both deserve the same
   enforcement floor. Implemented as `BEFORE UPDATE`/`BEFORE DELETE` triggers on all
   five tables in `schema.py`, refusing via `RAISE(ABORT, ...)`. Proven in
   `test_ledger.py`'s parametrized trigger tests (10 tests, one UPDATE + one DELETE per
   table) plus the tamper tests, which deliberately `DROP TRIGGER` first to simulate
   out-of-band tampering the trigger layer doesn't cover.
3. **Should `claims` participate in `supersessions` from day one, even though no
   producer needs to correct a claim yet?**
   → **DECIDED (founder): yes.** Implemented — `claims` is one of the four valid
   `target_table` values, enforced by a `CHECK` constraint in `schema.py`.
4. **`sessions.source_dir` — relative to what root?**
   → **DECIDED (founder): one configurable corpus root, not a settings framework.**
   Implemented as `shipgate.ledger.paths.relative_source_dir(absolute_dir, corpus_root)`
   — a single function taking (or defaulting to) one root path, not a settings/config
   surface. `LedgerWriter` accepts a `corpus_root` constructor argument for the same
   reason. Ingest (Task 1.5) will call this; nothing about ingest itself is built yet.

**Analyst's own call, logged per the founder's invitation to do so:** whether `claims`
should be chained (not just participate in `supersessions`) was this document's silence
on a fifth question. **Decided: yes, chain `claims` too.** A chained `verdicts` row is
only as trustworthy as the `claims` row it judges — if `claims.text` could be silently
rewritten, an attacker wouldn't need to forge a verdict at all, just rewrite what the
verdict was allegedly about, leaving the (honest, chained) verdict pointing at a
now-different claim. This closes that gap with the mechanism already being paid for.
`sessions` was deliberately left unchained: it's ingest bookkeeping (project slug,
working directory, git branch), not evidence or judgment, and nothing in the Ship
Report's self-verifying receipt depends on it being tamper-evident the way a claim or a
verdict is.

---

## What this document is now

**Implemented**, not just designed. `shipgate/ledger/` — `schema.py` (DDL + append-only
triggers), `hashing.py` (chain primitives + the chained-tables decision record),
`writer.py` (`LedgerWriter`, the sole write path), `integrity.py` (chain verification),
`redaction.py` (Task 1.3, write-time secret redaction), `paths.py` (decision 4). 38
tests across `tests/unit/test_ledger.py` and `tests/unit/test_redaction.py`, all
`runtime-verified`. The done-condition this document
existed to prepare ("Ledger tamper test: alter one row → chain verification fails and
names the row") is green for all three chained tables, demonstrated both inside pytest
and as a standalone script.

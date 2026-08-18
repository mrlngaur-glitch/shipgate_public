# The shipfile — a worked example

*Gate A material, part 2 of 2. Part 1 is
`docs/verdicts_explainer.md` — the 7 verdicts, **signed off and frozen** as of Session
004. This document is the shipfile half — **also signed off and frozen**, as of Session
004's close, after four required changes across two review rounds — see "What changed
since the first review" at the bottom.*

---

A shipfile is a project's own written definition of "done" — for each kind of task, in
a form ShipGate can actually check instead of just trusting an agent's word for it. The
full example is `docs/shipfile_worked_example.yaml`: a real one, written for ShipGate's
own project (it gates itself under the same rules it ships). Below is what each part of
it does for you, in plain terms.

```yaml
shipfile_version: "0.1"
```

One line at the top of the file, and it's optional — a shipfile with no version line at
all is still read as "0.1," so nothing you've already written needs to change. It exists
so that a future format version can tell an old ShipGate exactly what's wrong instead of
guessing: without it, a file written for a later version would fail today's ShipGate
with a confusing "unrecognized block" error pointing at the wrong thing. With it, the
same file fails with "upgrade ShipGate" — the actual problem, named correctly, the first
time this situation can ever come up.

**Nothing in this file is checked against real code yet.** This phase only teaches
ShipGate to *read* a shipfile and catch a broken one. Actually running these checks
against your project is the next phase.

---

### `task_classes` — how cautious to be, per kind of work

```yaml
task_classes:
  schema_change:
    risk_tier: high
    starting_model_tier: frontier
    max_tokens: 200000
    gate_strictness: strict
```

Not every change deserves the same scrutiny. A schema change is declared `high` risk
here, gets a stronger model to start with, and is checked `strict`ly. A docs fix can be
declared `low` risk and checked only `advisory`-style — flagged, never blocked. This is
what lets the gate be strict where it matters and out of the way everywhere else.

`starting_model_tier` names a **tier** — `frontier`, `mid`, `cheap`, or `local` — never
a specific model like "opus" or "gpt-4o." This is deliberate and it's the one rule in
this file with real teeth behind it: which actual model answers to "frontier" changes
every few months as new models ship; the *shipfile* never should. The mapping from tier
to actual model lives in a separate, small config file
(`shipgate/router/model_tiers.yaml`) that ShipGate's own maintainers update — a new
model release is a one-line change there, never a change to a shipfile you already
wrote.

### `done_conditions` — the actual definition of "done"

This is the heart of the file. Each entry is an object with a unique `id` — so any part
of ShipGate can refer to *this specific condition* unambiguously — and either a
structured, checkable type, or an `ears` field for a plain-language statement (see
below). Seven structured types exist, and the worked example uses all seven:

| Type | What it checks |
|---|---|
| `tests_pass` | A real test command actually runs and passes |
| `file_exists` | A specific file is actually on disk |
| `forbidden_pattern_absent` | Something that shouldn't be there (like a leftover `TODO`) really isn't |
| `command_succeeds` | An arbitrary command (a linter, a build step) actually exits clean |
| `emission_traced` | A specific signal actually fired — not "should have fired" |
| `runtime_evidence` | Something was actually observed running — a log line, an endpoint, a database row |
| `inventory_complete` | *Every* match of a search has a stated outcome — "3 of 5 fixed" is never accepted as done |

One entry in the example carries an `ears` field instead of one of the seven structured
types:

```yaml
- id: vacuous-never-green
  ears: "WHEN a checker observes zero test results THE SYSTEM SHALL render unverified-vacuous, never a pass"
```

That's allowed: a condition can be a structured, checkable rule, or a plain-language
statement of intent for a human to read. It still has its own `id`, the same as every
other condition — so it can be referred to and reported on individually, exactly like a
checkable one, rather than being an anonymous note nothing else in the system can point
at. Nothing reads the sentence's grammar automatically yet, but the field is captured
today so it doesn't force a rewrite later.

Every condition can also carry a `verify_after` — a note that its evidence is expected
*later*, not immediately (the worked example's `hook-installed` check is re-verified a
day later, in case something silently stopped working overnight). Nothing automatically
re-checks it yet either — that machinery is a fast-follow release — but the note is
captured now.

### `routing` — where "which model" is decided, using tiers only

```yaml
routing:
  default_model_tier: mid
  escalation:
    - condition: schema_change
      escalate_to: frontier
```

Same rule as `task_classes`: every field that names a routing target names a tier, never
a model. The actual routing *engine* — the thing that reads this and picks a live model,
retries with a stronger tier on failure, falls back to a cheaper one when quota is
tight — doesn't exist yet (that's a later release). What exists today is the promise
that when it arrives, it will read shipfiles that already look like this, unchanged.

### `budgets`, `context_policy` — knobs for later machinery

Two blocks exist in the file today but nothing enforces their contents yet — they belong
to pieces of ShipGate (spend limits, cache behavior) that aren't built until later
releases. They're present now so a shipfile written today doesn't need rewriting when
that machinery arrives.

**One honesty note worth knowing now, before any number here means something to you:**
`budgets.max_cost_per_session_usd` will eventually be checked against a cost figure
that is **calculated, not measured** — tokens actually used, multiplied by a price list
ShipGate maintains. There's no field anywhere in the underlying data that records what
you were actually billed. So a budget like this is always an *estimate* being compared
to another *estimate* — good enough to catch a real problem, but never a substitute for
your actual invoice.

### `intent` — one sentence saying what this project is actually for

```yaml
intent:
  summary: >
    Ship the independent completion gate for AI coding agents as free OSS,
    without ever rendering a verdict the evidence doesn't support.
```

The one truly required plain-language anchor: what is this project trying to do. A
later release reads this back to you when a request looks unusually broad or a
conversation looks frustrated, so scope can't drift silently in either direction.

### `gate_policy` and `session_policy` — the two rules that can't be turned off, with numbers that can't be turned into nothing

```yaml
gate_policy:
  max_retries: 3
session_policy:
  max_high_risk_changes_per_session: 3
```

These set *limits* you can adjust within a bounded range (0 through 10) — how many
retries before giving up; how many risky changes allowed before a session has to stop
and ask. **Two things underneath them are locked and cannot be changed by any
shipfile, by design:** when retries run out, the honest answer is always a red report,
never a silent retry loop — and when a check keeps flip-flopping on code that hasn't
changed, it's always downgraded to advisory, never allowed to block on its own.

The numbers are bounded, not just the words: a shipfile cannot set `max_retries` to a
number so large it behaves like "never stop retrying" in practice, which would quietly
defeat the same promise the locked words are there to protect. Setting either number to
`0` is allowed on purpose — it means "don't retry at all, don't allow even one risky
change without asking first," a legitimate strict setting for a project that wants zero
tolerance, not a mistake the file lets slip through.

---

## What's expensive to change later, and what isn't

**Expensive — this is the second public interface, alongside the 7 verdicts:** the
eight block names, the seven condition types and their required fields, the four model
tier names, the bounds on the two locked-law numbers, and now the meaning of
`shipfile_version` itself — that it's optional, that absent means "0.1," and that a
version newer than the running ShipGate produces an "upgrade ShipGate" error rather than
a block error. That promise has to hold on every future version, forever, or the whole
reason the field exists breaks. Once ShipGate is public,
existing users' shipfiles are YAML files sitting in their repos — renaming a block or a
required field breaks every shipfile already written, the same way the verdict words
break every existing Ship Report screenshot or CI rule.

**Cheap — free to change after launch:** what actually *reads* each block
(`routing`/`budgets`/`context_policy` do nothing today on purpose), which actual models
sit behind each tier (that lives outside this file entirely, on purpose), the exact
wording of an error message, and anything about *how* a condition gets checked once
Phase 2's checkers exist. The shape of the file is the frozen part; the behavior behind
it can keep improving.

---

## What changed since the first review

The first version of this document was sent back with three required changes, then one
more final change closed Gate A outright. All four are reflected above and in
`docs/shipfile_worked_example.yaml`:

1. **Model tiers, not model names.** `starting_model_tier` and `routing`'s fields
   previously accepted any string at all — `"opus"`, `"gpt-4o"`, and `"banana"` all
   validated equally. They're now locked to the four tiers, with the actual roster kept
   in a small file outside this frozen format.
2. **The gate laws' numbers are now bounded, not just their names.** A shipfile could
   previously set `max_retries: 999999` — technically honoring the locked *word*
   ("honest_red_report") while defeating the entire point of having a cap. Both numbers
   now have a reasoned ceiling.
3. **Every condition is addressable.** The plain-language condition used to be a bare
   sentence with no identity of its own; now every condition, sentence or checkable
   rule alike, has an `id`.
4. **The file can now say which version of the format it's written for.** An optional
   `shipfile_version` line, absent-means-"0.1," exists so a future format change can
   tell an old ShipGate exactly what's wrong ("upgrade ShipGate") instead of a confusing
   "unrecognized block" error blaming the version field's own name. This one had to ship
   before Gate A closed — a version field a v0.1 parser refuses can never be added
   later without breaking every deployed v0.1 parser it meets.

---

*Backing detail, if wanted: `shipgate/shipfile/` — the schema, the validator, and the
automated test suite, including one test for every kind of malformed shipfile this
document describes, each producing an error that names the exact block that's wrong
rather than a generic "invalid file."*

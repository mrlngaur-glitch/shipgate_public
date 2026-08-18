# The 7 verdicts — what you'll see on a Ship Report

*Gate A material, part 1 of 2. **Signed off and frozen by the
founder** — the 7 classes, their transitions, and the disk/runtime
dimension do not change without a founder-approved major version. Part 2, the shipfile
(`docs/shipfile_worked_example.md`), was sent back for changes on first review and is
being re-presented separately.*

---

Every claim a coding agent makes — "I finished the login page," "all tests pass," "I
fixed every call site" — gets checked against real evidence and stamped with one of
seven words. That word is what you'll see on a Ship Report, and it's what a CI rule
would look for if you wire ShipGate into a merge check later. Here's what each one
means, in plain terms.

### unverified — "we don't know yet"

The default. No claim starts out trusted. If nothing has checked a claim yet, it says
so plainly instead of assuming it's fine. This is the honest starting line, not a
judgment.

### verified — "checked, and it holds up"

The claim was checked against real evidence and confirmed. But "verified" always comes
with a second word attached, because there are two very different ways a claim can be
confirmed:

- **disk-verified** — the file or the code *looks* right. Nobody actually ran it.
- **runtime-verified** — it was actually *run*, end to end, and it behaved as claimed:
  a test executed, a log line fired, a real record showed up where it should.

This distinction is the whole point of the product. "The code is written" and "the code
works" are different claims, and an agent that swaps one for the other is exactly the
failure this tool exists to catch. Every Ship Report shows which kind of verified you
got.

A claim is allowed to earn the stronger kind of "verified" after starting with the
weaker kind — disk-verified now, runtime-verified once it's actually been run — and
that's exactly the direction you want to see move over time. It is never allowed to go
the other way while still showing as "verified": once something has actually been run
and shown to work, ShipGate won't let it quietly slide back to "looked right on disk"
and still call itself verified. If the evidence genuinely gets weaker, the honest report
is "we don't know anymore," not a quieter version of "verified."

### unverified-vacuous — "the check ran, but it didn't check anything"

A test suite with zero tests in it. A search over an empty folder. Something that
*looks* like it passed because nothing failed — but nothing was actually there to fail.
Most tools render this as a green pass. ShipGate never does. It's flagged as a failure,
because a check that found nothing to verify has proven nothing.

### pending-recheck — "provisionally believed, evidence still coming"

Some claims can't be confirmed the moment they're made — the real proof only shows up
later (a scheduled re-check, a deferred verification window). Until that evidence
arrives, the claim is treated as *not yet trustworthy*, not as good news early. The
automatic re-checking itself ships in a later release; the label exists from day one so
nothing has to be bolted on afterward.

### contradicted — "the evidence says this claim was wrong"

Real evidence directly contradicts the claim. Most often that happens the very first
time anyone checks — an agent says "all tests pass," ShipGate runs them, they fail. That
is the product's core moment: catching a false claim the first time, not after the fact.
The same word also covers evidence that shows up *after* something was already marked
verified — a green checkmark can honestly turn red a day later if new evidence disproves
it. No other tool in this space represents that second case at all; most just leave the
old, now-wrong checkmark sitting there.

### quarantined-flaky — "this check can't make up its mind, so we stopped trusting it alone"

If a check flips its answer on code that hasn't changed, that's not the code's fault —
it's an unreliable check. Rather than let a coin-flip test block real work, it gets
flagged and downgraded to "for your information" instead of "block the build." It never
gets to hard-stop anything on its own again until it's fixed.

### target-unreachable — "the goal as written cannot be hit, and we're telling you instead of hiding it"

Sometimes a target was set honestly but turns out to be impossible given the real data —
"we wanted 70% test coverage, the codebase only allows 2.8%." This is deliberately
*not* the same as a plain failure. A failure means "try again, it's achievable." This
means "the target itself needs to change, and only you get to change it." ShipGate will
never quietly lower that bar itself and pretend the original goal was met — that's
exactly the kind of quiet goalpost-moving this tool is built to stop, including when
it's ShipGate doing the moving.

---

## What's expensive to change later, and what isn't

**Expensive — effectively frozen once ShipGate is public:** the seven words above, what
they mean, and the disk/runtime distinction on "verified." People will screenshot Ship
Reports and build CI rules that read these exact words. Changing one after launch breaks
other people's automated pipelines, the same way renaming a filing category breaks
everyone's saved searches — it needs a major version, not a patch. That's why this is a
sign-off moment now rather than a decision made quietly mid-build.

**Cheap — can evolve freely after launch:** which checks *produce* each verdict, how
they're triggered, and the exact wording of the explanations underneath each one on the
Ship Report. The seven words are the frozen vocabulary; everything that decides *when*
each word gets used can keep improving indefinitely without breaking anyone's pipeline.

**Worth noting:** three of the seven words don't have their full automatic machinery yet
— `pending-recheck`'s automatic re-check, the automatic version of a `contradicted`
flip, and automatic detection of `target-unreachable` all arrive in later releases. The
words themselves, and the rule for when one is allowed to change into another, are
complete and tested today. That's deliberate: building the full vocabulary once, now,
means it never has to churn later.

---

*Backing detail, if wanted: `shipgate/verdicts/` — the taxonomy, the rules for which
verdict can turn into which other verdict, and 24 automated tests including one that
proves an illegal change (like quietly turning `target-unreachable` back into
`verified`, or a verified claim quietly losing its runtime evidence) gets refused, not
silently allowed.*

"""Task 1.2 — ledger tests (Phase 1 brief done-condition):

    "Ledger tamper test: alter one row -> chain verification fails and names the row
    -- runtime-verified, output pasted"

plus the four founder decisions from Session 002 review: verdicts (and claims,
analyst's call) are hash-chained; UPDATE/DELETE are refused by SQLite triggers;
`claims` participates in `supersessions`; `sessions.source_dir` is relative to one
configurable corpus root.

Every test here is runtime-verified by definition — pytest actually executes it.
"""

import sqlite3

import pytest

from shipgate.ledger import (
    ChainTamperedError,
    LedgerWriter,
    relative_source_dir,
    verify_chain,
)
from shipgate.verdicts import (
    EvidenceTier,
    IllegalTierTransitionError,
    IllegalTransitionError,
    Verdict,
)


@pytest.fixture
def writer(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.db")
    yield w
    w.close()


def _seed_session_and_event(w, *, session_id="s1"):
    w.insert_session(session_id=session_id, project_slug="proj", source_dir=f"proj/{session_id}")
    event_id = w.insert_event(
        session_id=session_id,
        source_file=f"proj/{session_id}.jsonl",
        source_offset=0,
        transcript_tier="session",
        record_type="assistant",
    )
    return event_id


# --- basic lifecycle ----------------------------------------------------------


def test_full_lifecycle_session_event_claim_verdict(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c1",
        session_id="s1",
        text="all tests pass",
        source="extracted_from_transcript",
        source_event_id=event_id,
        created_at="2026-08-15T00:00:00Z",
    )
    verdict_id = writer.insert_verdict(
        claim_id="c1",
        verdict=Verdict.VERIFIED,
        evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
        reason="pytest actually ran and passed",
        created_at="2026-08-15T00:01:00Z",
    )
    assert verdict_id is not None
    current = writer.current_verdict("c1")
    assert current == (verdict_id, Verdict.VERIFIED, EvidenceTier.RUNTIME_VERIFIED)


def test_fresh_ledger_chains_verify_clean(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c1", session_id="s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    writer.insert_verdict(
        claim_id="c1", verdict=Verdict.UNVERIFIED, created_at="2026-08-15T00:01:00Z"
    )
    writer.verify_chains()  # raises on any problem; a clean return is the pass


# --- the illegal-verdict-transition refusal, enforced at the ledger boundary ------


def test_insert_verdict_refuses_illegal_transition_and_writes_nothing(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-goal", session_id="s1", text="coverage target", source="shipfile_condition",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    writer.insert_verdict(claim_id="c-goal", verdict=Verdict.TARGET_UNREACHABLE, created_at="2026-08-15T00:01:00Z")

    with pytest.raises(IllegalTransitionError):
        writer.insert_verdict(
            claim_id="c-goal",
            verdict=Verdict.VERIFIED,
            evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
            created_at="2026-08-15T00:02:00Z",
        )

    rows = writer.connection.execute(
        "SELECT COUNT(*) FROM verdicts WHERE claim_id = 'c-goal'"
    ).fetchone()[0]
    assert rows == 1, "the illegal insert must not have written a second row"


def test_insert_verdict_refuses_evidence_tier_downgrade(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-dg", session_id="s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    writer.insert_verdict(
        claim_id="c-dg", verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
        created_at="2026-08-15T00:01:00Z",
    )
    with pytest.raises(IllegalTierTransitionError):
        writer.insert_verdict(
            claim_id="c-dg", verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.DISK_VERIFIED,
            created_at="2026-08-15T00:02:00Z",
        )
    rows = writer.connection.execute("SELECT COUNT(*) FROM verdicts WHERE claim_id = 'c-dg'").fetchone()[0]
    assert rows == 1


def test_supersession_row_written_atomically_with_the_superseding_verdict(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-f2", session_id="s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    v1 = writer.insert_verdict(
        claim_id="c-f2", verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.RUNTIME_VERIFIED,
        created_at="2026-08-15T00:01:00Z",
    )
    v2 = writer.insert_verdict(
        claim_id="c-f2", verdict=Verdict.CONTRADICTED, reason="24h-later recheck found it false",
        created_at="2026-08-15T00:02:00Z",
    )
    row = writer.connection.execute(
        "SELECT target_table, superseded_row_id, superseding_row_id, reason FROM supersessions"
    ).fetchone()
    assert row == ("verdicts", str(v1), str(v2), "24h-later recheck found it false")


# --- SQLite triggers refuse UPDATE/DELETE on every table --------------------------


#: One harmless, always-present column per table, for the parametrized UPDATE below.
_ANY_COLUMN = {
    "sessions": "project_slug",
    "events": "record_type",
    "claims": "text",
    "verdicts": "reason",
    "supersessions": "reason",
}


def _seed_one_row_in_every_table(w):
    """SQLite triggers fire per matched row, not per statement -- an UPDATE/DELETE
    that matches zero rows never invokes the trigger at all. So proving the trigger
    blocks a *real* row requires a real row in every table, including a
    `supersessions` row (which only exists once a claim has at least two verdicts)."""
    w.insert_session(session_id="trig-s1", project_slug="proj", source_dir="proj/trig-s1")
    event_id = w.insert_event(
        session_id="trig-s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
    )
    w.insert_claim(
        claim_id="trig-c1", session_id="trig-s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    v1 = w.insert_verdict(claim_id="trig-c1", verdict=Verdict.UNVERIFIED, created_at="2026-08-15T00:00:00Z")
    w.insert_verdict(
        claim_id="trig-c1", verdict=Verdict.VERIFIED, evidence_tier=EvidenceTier.DISK_VERIFIED,
        created_at="2026-08-15T00:01:00Z",
    )  # the supersession that follows is the row supersessions needs
    supersession_id = w.connection.execute("SELECT supersession_id FROM supersessions LIMIT 1").fetchone()[0]
    return {
        "sessions": ("session_id", "trig-s1"),
        "events": ("event_id", event_id),
        "claims": ("claim_id", "trig-c1"),
        "verdicts": ("verdict_id", v1),
        "supersessions": ("supersession_id", supersession_id),
    }


@pytest.mark.parametrize("table", ["sessions", "events", "claims", "verdicts", "supersessions"])
def test_update_is_refused_by_trigger_on_every_table(writer, table):
    ids = _seed_one_row_in_every_table(writer)
    id_column, id_value = ids[table]
    column = _ANY_COLUMN[table]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        writer.connection.execute(
            f"UPDATE {table} SET {column} = 'x' WHERE {id_column} = ?", (id_value,)
        )


@pytest.mark.parametrize("table", ["sessions", "events", "claims", "verdicts", "supersessions"])
def test_delete_is_refused_by_trigger_on_every_table(writer, table):
    ids = _seed_one_row_in_every_table(writer)
    id_column, id_value = ids[table]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        writer.connection.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (id_value,))


# --- CHECK constraints hold even for a raw INSERT bypassing the Python layer ------


def test_check_constraint_refuses_an_eighth_verdict_class_via_raw_insert(writer):
    """Founder review of Session 002: 'a raw INSERT bypassing Python must still be
    unable to write an 8th verdict class into a frozen public interface.'"""
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-raw", session_id="s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        writer.connection.execute(
            "INSERT INTO verdicts (claim_id, verdict, evidence_tier, reason, created_at, "
            "row_hash, prev_row_hash) VALUES (?,?,?,?,?,?,?)",
            ("c-raw", "definitely-not-a-real-verdict", None, "", "2026-08-15T00:00:00Z", "x", None),
        )


def test_check_constraint_refuses_verified_with_no_evidence_tier_via_raw_insert(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-raw2", session_id="s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    with pytest.raises(sqlite3.IntegrityError):
        writer.connection.execute(
            "INSERT INTO verdicts (claim_id, verdict, evidence_tier, reason, created_at, "
            "row_hash, prev_row_hash) VALUES (?,?,?,?,?,?,?)",
            ("c-raw2", "verified", None, "", "2026-08-15T00:00:00Z", "x", None),
        )


# --- the tamper test: alter a row, chain verification fails and names the row -----


def test_tamper_test_events_row_altered_is_detected_and_named(writer):
    event_id = _seed_session_and_event(writer)
    writer.connection.execute("DROP TRIGGER events_no_update")
    writer.connection.execute(
        "UPDATE events SET record_type = 'tampered' WHERE event_id = ?", (event_id,)
    )
    writer.connection.commit()

    with pytest.raises(ChainTamperedError) as exc_info:
        verify_chain(writer.connection, "events")
    err = exc_info.value
    assert err.table == "events"
    assert err.id_column == "event_id"
    assert err.row_id == event_id


def test_tamper_test_claims_row_altered_is_detected_and_named(writer):
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-tamper", session_id="s1", text="original claim text",
        source="extracted_from_transcript", source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    writer.connection.execute("DROP TRIGGER claims_no_update")
    writer.connection.execute(
        "UPDATE claims SET text = 'quietly rewritten claim' WHERE claim_id = 'c-tamper'"
    )
    writer.connection.commit()

    with pytest.raises(ChainTamperedError) as exc_info:
        verify_chain(writer.connection, "claims")
    err = exc_info.value
    assert err.table == "claims"
    assert err.id_column == "claim_id"
    assert err.row_id == "c-tamper"


def test_tamper_test_verdicts_row_altered_is_detected_and_named(writer):
    """Founder decision on Session 002 review: verdicts must be chained because the
    Ship Report's self-verifying hash range is computed from verdict rows."""
    event_id = _seed_session_and_event(writer)
    writer.insert_claim(
        claim_id="c-vtamper", session_id="s1", text="claim", source="extracted_from_transcript",
        source_event_id=event_id, created_at="2026-08-15T00:00:00Z",
    )
    verdict_id = writer.insert_verdict(
        claim_id="c-vtamper", verdict=Verdict.CONTRADICTED, reason="real reason", created_at="2026-08-15T00:00:00Z",
    )
    writer.connection.execute("DROP TRIGGER verdicts_no_update")
    writer.connection.execute(
        "UPDATE verdicts SET verdict = 'verified', evidence_tier = 'runtime-verified' "
        "WHERE verdict_id = ?",
        (verdict_id,),
    )
    writer.connection.commit()

    with pytest.raises(ChainTamperedError) as exc_info:
        verify_chain(writer.connection, "verdicts")
    err = exc_info.value
    assert err.table == "verdicts"
    assert err.id_column == "verdict_id"
    assert err.row_id == verdict_id


def test_tamper_of_an_earlier_row_is_detected_even_though_a_later_row_is_untouched(writer):
    """The chain must catch tampering with row 1 even when rows 2 and 3 are unaltered
    and their own stored hashes are internally self-consistent with row 1's *original*
    content -- proving the check is against recomputed content, not just adjacency."""
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    e1 = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0, transcript_tier="session", record_type="assistant"
    )
    writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=50, transcript_tier="session", record_type="user"
    )
    writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=100, transcript_tier="session", record_type="assistant"
    )
    writer.connection.execute("DROP TRIGGER events_no_update")
    writer.connection.execute("UPDATE events SET model = 'forged-model' WHERE event_id = ?", (e1,))
    writer.connection.commit()

    with pytest.raises(ChainTamperedError) as exc_info:
        verify_chain(writer.connection, "events")
    assert exc_info.value.row_id == e1


# --- redaction: secret in a value and in a key never reach disk -------------------


def test_redaction_secret_in_a_value_never_reaches_disk(writer):
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    secret = "sk-ant-api03-" + "a1b2c3d4e5f6g7h8i9j0" * 2
    event_id = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        raw_payload={"note": f"the key is {secret} -- keep it safe"},
    )
    stored = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert secret not in stored
    assert "[REDACTED]" in stored


def test_redaction_secret_in_a_dict_key_never_reaches_disk(writer):
    """The Phase 0 spike's own defect: a dict key can carry sensitive free text, not
    only values (`docs/jsonl_format_notes.md` Spike hygiene)."""
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    # Short enough (< 40 chars) to be redacted by the *secret-pattern* scan, not the
    # separate overlong-key guard (that path is tested on its own, below).
    secret_key = "sk-ant-a1b2c3d4e5f6g7h8i9j0k1l2"
    assert len(secret_key) <= 40
    event_id = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        raw_payload={secret_key: "value"},
    )
    stored = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert secret_key not in stored
    assert "[REDACTED]" in stored


def test_redaction_overlong_free_text_key_is_redacted_structurally(writer):
    """The literal Phase 0 defect: an AskUserQuestion answers map keyed by the question
    itself -- long free text as a key, not secret-shaped, but still content that
    shouldn't reach disk verbatim as a structural field name."""
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    long_key = "What is your favorite color and why does it matter to the project overall?"
    assert len(long_key) > 40
    event_id = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        raw_payload={long_key: "blue"},
    )
    stored = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert long_key not in stored
    assert "REDACTED-LONG-KEY" in stored


# --- Session 003 reopen: the three confirmed leaks, closed the loop end-to-end -----
# The pure-function tests in test_redaction.py prove redact_json() itself; these prove
# the full "never reaches disk" contract against a real SQLite file, the same way the
# original three redaction tests above do for the leaks that were already closed.


def test_redaction_secret_named_field_with_human_shaped_value_never_reaches_disk(writer):
    """Confirmed leak #1: a field named `password`/`api_key`/etc. with a human-shaped
    value (not API-key-shaped) previously passed straight through."""
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    human_password = "Tr0ub4dor&3xKcd"
    event_id = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        raw_payload={"db_password": human_password, "username": "svc-account"},
    )
    stored = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert human_password not in stored
    assert "svc-account" in stored  # the non-secret sibling field is untouched


def test_redaction_realistic_length_pem_private_key_never_reaches_disk(writer):
    """Confirmed leak #2: PEM keys leaked at realistic (wrapped, full base64 alphabet)
    lengths while only a toy same-line body was caught."""
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAv3fO/ohXqjwXvV8IPPRfnjJZ5PPRcbXOgVwGkVgBZLTaBpM+\n"
        "aXzM7d8KTz4cQnqzJq6f8B0z1LxJb2H7NnDq3Y6b2AdT5KvVvC1RfW9nOqzXPqYb\n"
        "-----END RSA PRIVATE KEY-----"
    )
    event_id = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="assistant",
        raw_payload={"backup_key_dump": pem},
    )
    stored = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert "MIIEowIBAAKCAQEAv3fO" not in stored
    assert "BEGIN RSA PRIVATE KEY" not in stored


def test_redaction_basic_auth_credentials_in_git_remote_url_never_reach_disk(writer):
    """Confirmed leak #3: basic-auth credentials in a URL, a realistic shape for a
    Claude Code transcript's `git remote` output."""
    writer.insert_session(session_id="s1", project_slug="proj", source_dir="proj/s1")
    event_id = writer.insert_event(
        session_id="s1", source_file="f.jsonl", source_offset=0,
        transcript_tier="session", record_type="user",
        raw_payload={"tool_output": "git remote add origin https://user:hunter2supersecret@host/repo.git"},
    )
    stored = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE event_id = ?", (event_id,)
    ).fetchone()[0]
    assert "hunter2supersecret" not in stored
    assert "host/repo.git" in stored  # readable except for the credential


# --- decision 4: source_dir relative to one configurable corpus root ---------------


def test_relative_source_dir_computes_relative_to_the_corpus_root(tmp_path):
    corpus_root = tmp_path / ".claude" / "projects"
    session_dir = corpus_root / "my-project" / "abc-123-uuid"
    session_dir.mkdir(parents=True)
    assert relative_source_dir(session_dir, corpus_root) == "my-project/abc-123-uuid"


def test_relative_source_dir_rejects_a_path_outside_the_corpus_root(tmp_path):
    corpus_root = tmp_path / ".claude" / "projects"
    corpus_root.mkdir(parents=True)
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    with pytest.raises(ValueError):
        relative_source_dir(outside, corpus_root)

"""Task 1.3 — redaction module tests, in isolation from the ledger.

The literal done-condition ("planted API-key-shaped secret never reaches disk — tested
in a value and in a key") is proven end-to-end against a real SQLite file in
`tests/unit/test_ledger.py`. This file tests `shipgate.ledger.redaction.redact_json`
as a pure function, with more shapes than the ledger tests bother to cover.

**Reopened, Session 003.** The tests above this line plant shapes the redactor already
knows about — confirmatory, not adversarial, exactly the gap the founder's review
caught: the done-condition said "shaped secret," and the check only ever looked for the
shapes it expected. The `# --- adversarial (Session 003 reopen) ---` section below
plants shapes the redactor did *not* know about before this session and checks nothing
escapes: secret-*named* fields with human-shaped (not pattern-shaped) values, full-length
wrapped PEM blocks, basic-auth URLs, `.env`-style assignments, and secrets buried in
lists-of-dicts — plus regression guards that this product's own vocabulary
(`tokens_input`, `session_id`, …) doesn't get swept up as collateral damage.
"""

from shipgate.ledger.redaction import REDACTED_MARKER, redact_json

# --- values -------------------------------------------------------------------


def test_anthropic_style_key_in_a_value_is_redacted():
    secret = "sk-ant-a1b2c3d4e5f6g7h8i9j0k1l2"
    redacted, found = redact_json({"note": f"use {secret} to authenticate"})
    assert found is True
    assert secret not in redacted["note"]
    assert REDACTED_MARKER in redacted["note"]


def test_github_token_in_a_value_is_redacted():
    secret = "ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
    redacted, found = redact_json({"env": secret})
    assert found is True
    assert secret not in redacted["env"]


def test_aws_access_key_in_a_value_is_redacted():
    secret = "AKIAABCDEFGHIJKLMNOP"
    redacted, found = redact_json([secret])
    assert found is True
    assert secret not in redacted[0]


def test_generic_high_entropy_token_in_a_value_is_redacted():
    token = "aB3dE7fG9hJ2kL5mN8pQ1rS4tU6v"  # 28 chars, mixed letters+digits, no vendor prefix
    redacted, found = redact_json({"token": token})
    assert found is True
    assert token not in redacted["token"]


def test_ordinary_text_is_left_alone():
    text = "all tests pass and the feature works as expected"
    redacted, found = redact_json({"note": text})
    assert found is False
    assert redacted["note"] == text


def test_short_alphanumeric_ids_are_left_alone():
    """Under the length floor -- a short id like a UUID fragment or a PR number
    shouldn't be treated as secret-shaped."""
    redacted, found = redact_json({"id": "pr-4821", "count": 12})
    assert found is False
    assert redacted == {"id": "pr-4821", "count": 12}


# --- keys ------------------------------------------------------------------------


def test_secret_shaped_dict_key_is_redacted():
    secret_key = "sk-ant-a1b2c3d4e5f6g7h8i9j0k1l2"
    redacted, found = redact_json({secret_key: "value"})
    assert found is True
    assert secret_key not in redacted
    assert all(REDACTED_MARKER in k or "REDACTED" in k for k in redacted)


def test_overlong_free_text_key_is_structurally_redacted_even_without_a_secret_shape():
    """The Phase 0 spike's own defect: an AskUserQuestion answers map keyed by the
    question itself -- ordinary sentence text, not secret-shaped, but too long to be a
    legitimate field name and known (from experience) to leak content."""
    long_key = "What should the fallback behavior be when the network call times out?"
    assert len(long_key) > 40
    redacted, found = redact_json({long_key: "retry three times"})
    assert found is True
    assert long_key not in redacted
    assert any("REDACTED-LONG-KEY" in k for k in redacted)


def test_ordinary_short_dict_key_is_left_alone():
    redacted, found = redact_json({"model": "claude-sonnet-5"})
    assert found is False
    assert redacted == {"model": "claude-sonnet-5"}


# --- structure ---------------------------------------------------------------------


def test_nested_dicts_and_lists_are_scanned_recursively():
    secret = "sk-ant-a1b2c3d4e5f6g7h8i9j0k1l2"
    payload = {
        "outer": {
            "list": [
                {"deep": secret},
                "plain text",
            ]
        }
    }
    redacted, found = redact_json(payload)
    assert found is True
    assert secret not in str(redacted)


def test_non_string_scalars_pass_through_unchanged():
    payload = {"count": 3, "ratio": 0.5, "ok": True, "missing": None}
    redacted, found = redact_json(payload)
    assert found is False
    assert redacted == payload


def test_a_dict_with_no_secrets_anywhere_is_returned_equal_but_not_necessarily_identical():
    payload = {"a": [1, 2, {"b": "plain"}]}
    redacted, found = redact_json(payload)
    assert found is False
    assert redacted == payload


# --- adversarial (Session 003 reopen) -----------------------------------------------
# Confirmed leak #1: value-shape-only redaction. A secret-named field with a
# human-shaped value passed through untouched, AND found came back False -- nothing
# was even flagged as suspicious. Plant the exact kind of value a human actually picks
# for a password, not an API-key-shaped string.

_HUMAN_SHAPED_SECRETS = [
    ("password", "Tr0ub4dor&3xKcd"),
    ("api_key", "Tr0ub4dor&3xKcd"),
    ("secret", "correct horse battery staple"),
    ("db_password", "hunter2"),
    ("AWS_SECRET_ACCESS_KEY", "hunter2"),
    ("client_secret", "hunter2"),
    ("apiKey", "Tr0ub4dor&3xKcd"),  # camelCase form
    ("authToken", "Tr0ub4dor&3xKcd"),  # camelCase form
]


def test_secret_named_field_with_human_shaped_value_is_redacted():
    for key, value in _HUMAN_SHAPED_SECRETS:
        redacted, found = redact_json({key: value})
        assert found is True, f"key={key!r} value={value!r} was not flagged"
        assert redacted[key] == REDACTED_MARKER, f"key={key!r} value leaked as {redacted[key]!r}"
        assert value not in str(redacted), f"key={key!r} original value leaked into output"


def test_key_name_match_is_by_component_not_bare_substring():
    """Confirmed-leak-#1's fix must not become a new false-positive source against this
    product's own domain vocabulary -- token/usage field names that merely *contain*
    'token' or 'secret'-adjacent text as part of a longer word are not secrets."""
    payload = {
        "tokens_input": 100,
        "input_tokens": 50,
        "output_tokens": 25,
        "max_tokens": 4096,
        "thinking_tokens": 10,
        "cache_creation_input_tokens": 5,
        "session_id": "abc-123-uuid",
        "username": "bob",
        "model": "claude-sonnet-5",
    }
    redacted, found = redact_json(payload)
    assert found is False
    assert redacted == payload


def test_secret_named_field_with_empty_or_null_value_is_not_falsely_flagged():
    """Nothing to redact if there's nothing there -- found=False here is honest, not
    the silent-drop failure mode (that was 'found=False on a REAL secret value')."""
    redacted, found = redact_json({"password": "", "api_key": None})
    assert found is False
    assert redacted == {"password": "", "api_key": None}


# Confirmed leak #2: PEM keys leaked at realistic lengths (multi-line, wrapped,
# using the full base64 alphabet including '+' and '/') while a toy same-line body
# was caught -- inverted relative to testing. Both must be caught now, and the
# realistic one is the one that actually matters.

_REALISTIC_RSA_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAv3fO/ohXqjwXvV8IPPRfnjJZ5PPRcbXOgVwGkVgBZLTaBpM+\n"
    "aXzM7d8KTz4cQnqzJq6f8B0z1LxJb2H7NnDq3Y6b2AdT5KvVvC1RfW9nOqzXPqYb\n"
    "j5kQm3ZbW7hR2vE4T8XoDqL1G+u9pC6mYlXwK3F9zR8vJ2N1oQd7c4tS0xW5bZkR\n"
    "9pQmXzT2vC8oL1JqYb7wN4dR6mE3sX1oV9bT5cQzP2K8jL0mR7wY4uV3nD1sT6qX\n"
    "hL9zJ4mR2wQ8vC1oT7bY3nX5dQmZ8vL2K9jR0wT4uY7sX1nD6mQ3oV8bT5cP2K9j\n"
    "L0mR7wY4uV3nD1sT6qXhL9zJ4mR2wQ8vC1oT7bY3nX5dQmZ8vL2K9jR0wT4uY7sX\n"
    "-----END RSA PRIVATE KEY-----"
)

_REALISTIC_OPENSSH_PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdzc2gt\n"
    "cnNhAAAAAwEAAQAAAYEAv3fO/ohXqjwXvV8IPPRfnjJZ5PPRcbXOgVwGkVgBZLTaBpM+\n"
    "aXzM7d8KTz4cQnqzJq6f8B0z1LxJb2H7NnDq3Y6b2AdT5KvVvC1RfW9nOqzXPqYbj5kQ\n"
    "m3ZbW7hR2vE4T8XoDqL1G+u9pC6mYlXwK3F9zR8vJ2N1oQd7c4tS0xW5bZkR9pQmXzT2\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def test_pem_rsa_private_key_full_realistic_length_is_redacted():
    redacted, found = redact_json({"note": f"backup key:\n{_REALISTIC_RSA_PEM}\ndone"})
    assert found is True
    assert "BEGIN RSA PRIVATE KEY" not in redacted["note"]
    assert "MIIEowIBAAKCAQEAv3fO" not in redacted["note"]
    assert "+" not in redacted["note"] or REDACTED_MARKER in redacted["note"]
    assert REDACTED_MARKER in redacted["note"]


def test_pem_openssh_private_key_full_realistic_length_is_redacted():
    redacted, found = redact_json({"note": _REALISTIC_OPENSSH_PEM})
    assert found is True
    assert "BEGIN OPENSSH PRIVATE KEY" not in redacted["note"]
    assert "b3BlbnNzaC1rZXktdjEA" not in redacted["note"]


def test_pem_toy_short_body_is_still_redacted_no_regression():
    """The original (Session 002) test case, kept so the fix is proven to be a
    superset, not a replacement, of what already worked."""
    toy_pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQ==\n-----END RSA PRIVATE KEY-----"
    redacted, found = redact_json({"note": toy_pem})
    assert found is True
    assert "MIIEvQIBADANBgkqhkiG9w0BAQ==" not in redacted["note"]


# Confirmed leak #3: basic-auth credentials in a git remote URL -- a realistic shape
# for a Claude Code transcript.


def test_basic_auth_credentials_in_a_git_remote_url_are_redacted():
    url = "https://user:hunter2supersecret@host/repo.git"
    redacted, found = redact_json({"remote": f"git remote add origin {url}"})
    assert found is True
    assert "hunter2supersecret" not in redacted["remote"]
    assert "user:" not in redacted["remote"]
    # host and path stay legible -- only the credential is redacted, not the whole URL.
    assert "host/repo.git" in redacted["remote"]


def test_basic_auth_with_special_characters_in_password_is_redacted():
    url = "https://svc-account:p@ss+w0rd/weird@gitlab.example.com/group/repo.git"
    redacted, found = redact_json({"remote": url})
    assert found is True
    assert "p@ss+w0rd/weird" not in redacted["remote"]


# .env-style assignments embedded in free text -- no dict key to inspect, only text.


def test_env_style_assignments_with_unshaped_values_are_redacted():
    blob = (
        "DB_PASSWORD=Tr0ub4dor&3xKcd\n"
        "export AWS_SECRET_ACCESS_KEY=plainlookingvalue\n"
        "API_KEY: not-vendor-shaped\n"
        "DEBUG=true\n"
        "PORT=8080"
    )
    redacted, found = redact_json({"shell_history": blob})
    result = redacted["shell_history"]
    assert found is True
    assert "Tr0ub4dor&3xKcd" not in result
    assert "plainlookingvalue" not in result
    assert "not-vendor-shaped" not in result
    # non-secret assignments on the same blob are untouched.
    assert "DEBUG=true" in result
    assert "PORT=8080" in result
    # the key names themselves stay legible.
    assert "DB_PASSWORD=" in result
    assert "AWS_SECRET_ACCESS_KEY=" in result


# Secrets nested in lists and lists-of-dicts.


def test_secret_named_field_nested_in_a_list_of_dicts_is_redacted():
    payload = {
        "history": [
            {"action": "login", "password": "Tr0ub4dor&3xKcd"},
            {"action": "logout"},
        ]
    }
    redacted, found = redact_json(payload)
    assert found is True
    assert redacted["history"][0]["password"] == REDACTED_MARKER
    assert redacted["history"][1] == {"action": "logout"}


def test_secret_named_field_nested_inside_a_list_at_the_top_level_is_redacted():
    payload = [{"api_key": "Tr0ub4dor&3xKcd"}, {"note": "plain"}]
    redacted, found = redact_json(payload)
    assert found is True
    assert redacted[0]["api_key"] == REDACTED_MARKER
    assert redacted[1] == {"note": "plain"}


def test_container_key_named_credentials_recurses_instead_of_blanking_siblings():
    """A key like `credentials` itself matches the secret-name pattern, but its value
    is a dict, not a scalar -- redaction must recurse into it (catching the nested
    `password`) rather than replacing the whole structure and losing `region`."""
    payload = {"credentials": {"username": "bob", "region": "us-east-1", "password": "hunter2"}}
    redacted, found = redact_json(payload)
    assert found is True
    assert redacted["credentials"]["username"] == "bob"
    assert redacted["credentials"]["region"] == "us-east-1"
    assert redacted["credentials"]["password"] == REDACTED_MARKER


# Secrets appearing as dict keys (already covered above for shape; re-proven here
# alongside the rest of the adversarial set for a single point of reference).


def test_secret_shaped_value_under_an_innocuous_key_name_is_still_caught_by_shape():
    """Regression guard: key-name-aware redaction is additive, not a replacement for
    shape-based scanning."""
    secret = "sk-ant-a1b2c3d4e5f6g7h8i9j0k1l2"
    redacted, found = redact_json({"notes": secret})
    assert found is True
    assert secret not in redacted["notes"]

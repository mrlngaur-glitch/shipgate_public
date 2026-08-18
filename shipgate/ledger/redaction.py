"""Secret redaction at ledger-write time (Task 1.3; report §6.1).

Runs on every payload before it is written, in the write order fixed by
`docs/ledger_schema_design.md`: **parse → redact → hash → write**. Never the other
order — a secret that touched disk even transiently before redaction has already failed
the done-condition, hash or no hash.

**Reopened once already.** The first version (Session 002) redacted by VALUE SHAPE
only. Adversarial testing (founder, Session 003) found three confirmed leaks, all now
fixed at the root, not patched with more shapes:

1. A secret-*named* field (`password`, `api_key`, `db_password`, `AWS_SECRET_ACCESS_KEY`
   …) with a human-shaped value (`"Tr0ub4dor&3xKcd"`) passed through untouched, and
   `found` came back `False` — nothing was even flagged as suspicious. Fixed by
   `_key_looks_secret_named`: a field whose *name* matches a secret-indicating pattern
   has its value redacted regardless of shape. This makes the exact failure mode
   structurally impossible going forward — there is no longer a "changed=False on a
   secret-named field" case to silently drop, because redaction on a key-name match is
   unconditional for any non-empty scalar value (see `redact_json`'s dict branch). No
   separate telemetry channel was added for this; the fix removes the case it would
   have reported.
2. PEM private keys leaked at realistic (multi-line, wrapped, `+`/`/`-bearing base64)
   lengths while a toy same-line 32-char body got caught — inverted relative to
   testing: it worked on test-sized input and failed on real input. Root cause: the
   only detector was a generic contiguous-alphanumeric run, whose character class
   excluded `+`/`/`/`=` (the rest of the base64 alphabet) and whose reach didn't span
   the newlines a real key is wrapped across. Fixed by `_PEM_BLOCK_PATTERN` — matches
   the whole `-----BEGIN ... PRIVATE KEY----- … -----END ... PRIVATE KEY-----` block by
   its markers, `re.DOTALL`, with no length bound at all. Length was never the right
   thing to bound on.
3. Basic-auth credentials embedded in a URL
   (`https://user:hunter2supersecret@host/repo.git` — a realistic shape for a Claude
   Code transcript, which routinely contains `git remote` URLs) leaked. Fixed by
   `_BASIC_AUTH_PATTERN`.

Tested against a secret planted in a dict **value** and in a dict **key** — the key case
exists because the Phase 0 spike leaked transcript content through a free-text dict key
on its first run (`docs/jsonl_format_notes.md` §Spike hygiene); the same assumption
("secrets only live in values") would leak through this writer too if left unchecked.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED_MARKER = "[REDACTED]"

#: Recognizable API-key / token shapes from common vendors, plus generic bearer tokens.
#: Not exhaustive — a defensible starting set, extended as real leaks are found (never
#: shrunk to make a test pass).
_VENDOR_KEY_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),  # Anthropic
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),  # AWS access key id
    re.compile(r"ASIA[0-9A-Z]{12,}"),  # AWS temporary access key id
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),  # Google API key
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack tokens
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE),
]

#: PEM private key blocks — RSA, EC, DSA, OPENSSH, encrypted, or bare "PRIVATE KEY".
#: Matched by structure (the BEGIN/END markers), not by length or alphabet, which is
#: the root-cause fix for confirmed leak #2: a length- or charset-bounded pattern is
#: exactly the wrong tool for a block that's wrapped across lines and uses the full
#: base64 alphabet (`+`, `/`, `=` included). `re.DOTALL` lets `.` cross the embedded
#: newlines a real key is wrapped across.
_PEM_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

#: `scheme://user:password@host` — the credential portion only, so the redacted result
#: (`scheme://[REDACTED]@host/path`) stays readable for debugging without leaking the
#: password. Confirmed leak #3: Claude Code transcripts routinely contain `git remote`
#: URLs, and a basic-auth credential embedded in one is exactly as sensitive as an API
#: key.
_BASIC_AUTH_PATTERN = re.compile(r"(?<=://)[^\s@/:]+:[^\s@/]+(?=@)")

#: Generic fallback: a long, contiguous, no-whitespace run of alphanumeric characters.
#: `_looks_secret_shaped` further requires at least one digit and one letter in the run
#: before treating it as a redaction target, so a run of 24+ letters (an unusually long
#: plain word) doesn't get flagged. Deliberately broad otherwise — a false positive
#: here means an innocent long token gets redacted; a false negative means a real
#: secret reaches disk. The product's own security posture (report §6.1: "one incident
#: kills this product permanently") makes that an easy tradeoff to take.
_GENERIC_SECRET_RUN = re.compile(r"[A-Za-z0-9+/=\-_]{24,}")


def _looks_secret_shaped(token: str) -> bool:
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


#: Dict keys longer than this are structurally suspicious — the Phase 0 spike's own
#: defect was a dict key that was free text (an AskUserQuestion answers map keyed by
#: the question itself), not a recognizable secret shape. This is a distinct guard from
#: secret-pattern matching: a key this long is never a legitimate field name.
_MAX_PLAUSIBLE_KEY_LENGTH = 40

# --- key-name-aware redaction (confirmed leak #1) -----------------------------------
#
# A field whose NAME indicates a secret must have its value redacted regardless of the
# value's shape — "Tr0ub4dor&3xKcd" under a key named `password` is exactly as
# sensitive as a recognizable API-key string, and shape-only scanning will never catch
# it. Matching is done on normalized, separator-split components (not a bare substring
# search) specifically so this doesn't fire on this product's own domain vocabulary —
# `tokens_input`, `input_tokens`, `max_tokens`, `thinking_tokens` all contain "token"
# as a substring but are legitimate numeric telemetry, not secrets; the component match
# requires "token" to be a whole component, which none of those are ("tokens" is not
# "token").

_SECRET_KEY_COMPONENT_PATTERN = re.compile(
    r"(?:^|_)("
    r"pass(?:word|wd)?"
    r"|secret"
    r"|api_?key"
    r"|access_?key"
    r"|auth_?token"
    r"|session_?token"
    r"|client_?secret"
    r"|private_?key"
    r"|credentials?"
    r"|token"
    r")(?:_|$)"
)


def _normalize_key_for_matching(key: str) -> str:
    """Lowercase, with a separator inserted at camelCase humps and every run of
    non-alphanumeric characters collapsed to one `_` — so `apiKey`, `api-key`, and
    `API_KEY` all normalize to the same `api_key` before the component match runs."""
    with_hump_separators = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    single_separators = re.sub(r"[^A-Za-z0-9]+", "_", with_hump_separators)
    return single_separators.lower().strip("_")


def _key_looks_secret_named(key: str) -> bool:
    normalized = _normalize_key_for_matching(key)
    return bool(_SECRET_KEY_COMPONENT_PATTERN.search(f"_{normalized}_"))


# --- .env-style `KEY=value` / `KEY: value` assignments embedded in free text --------
#
# Distinct from key-name-aware dict redaction above: this catches a secret-named
# assignment *inside a string value* — a pasted `.env` file, a shell `export` dump, a
# config snippet — where there is no dict key to inspect, only text. The key name and
# `=`/`:` are preserved in the output; only the value is replaced, so the redacted
# result stays legible (`DB_PASSWORD=[REDACTED]`).

_ENV_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^([ \t]*(?:export[ \t]+)?"
    r"(?=[A-Za-z0-9_]*(?:pass(?:word|wd)?|secret|api_?key|access_?key|auth_?token|"
    r"session_?token|client_?secret|private_?key|credentials?|token)[A-Za-z0-9_]*[ \t]*[:=])"
    r"[A-Za-z][A-Za-z0-9_]*[ \t]*[:=][ \t]*)(\S+)"
)


def _redact_string(value: str) -> tuple[str, bool]:
    """Redact any secret-shaped substring in `value`. Returns (result, found_any)."""
    found = False

    def _sub(pattern: re.Pattern[str], text: str) -> str:
        nonlocal found
        new_text, n = pattern.subn(REDACTED_MARKER, text)
        if n:
            found = True
        return new_text

    def _sub_env_assignment(text: str) -> str:
        nonlocal found

        def repl(m: re.Match[str]) -> str:
            nonlocal found
            found = True
            return m.group(1) + REDACTED_MARKER

        return _ENV_ASSIGNMENT_PATTERN.sub(repl, text)

    def _sub_generic(text: str) -> str:
        nonlocal found

        def repl(m: re.Match[str]) -> str:
            nonlocal found
            token = m.group(0)
            if _looks_secret_shaped(token):
                found = True
                return REDACTED_MARKER
            return token

        return _GENERIC_SECRET_RUN.sub(repl, text)

    result = value
    result = _sub(_PEM_BLOCK_PATTERN, result)
    for pattern in _VENDOR_KEY_PATTERNS:
        result = _sub(pattern, result)
    result = _sub(_BASIC_AUTH_PATTERN, result)
    result = _sub_env_assignment(result)
    result = _sub_generic(result)
    return result, found


def _redact_key(key: str) -> tuple[str, bool]:
    """Keys get the same secret-pattern scan as values, plus the length guard the
    Phase 0 spike's own defect requires (`jsonl_format_notes.md` §Spike hygiene)."""
    if len(key) > _MAX_PLAUSIBLE_KEY_LENGTH:
        return f"[REDACTED-LONG-KEY:{len(key)}-chars]", True
    return _redact_string(key)


def redact_json(value: Any) -> tuple[Any, bool]:
    """Recursively redact secret-shaped content from a JSON-like structure (nested
    dicts, lists, strings, and scalars). Returns `(redacted_value, found_any)` — the
    boolean lets a caller log/count redaction events without re-scanning.

    Three things are scanned, not one: dict **values** by shape, dict **keys** by shape
    and length, and — since the Session 003 fix — dict **values by their key's name**,
    unconditionally, regardless of the value's own shape. A secret-named key
    (`password`, `api_key`, `db_password`, `AWS_SECRET_ACCESS_KEY`, …) has its scalar
    value replaced outright; if the value is itself a nested dict/list, redaction
    recurses into it instead of blanking the whole structure, so a container key like
    `credentials` doesn't erase non-secret siblings alongside the actual secret nested
    inside it — the nested secret-named field gets caught on its own turn.
    """
    found_any = False

    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str):
                redacted_key, key_found = _redact_key(k)
                key_is_secret_named = _key_looks_secret_named(k)
            else:
                redacted_key, key_found = k, False
                key_is_secret_named = False

            if key_is_secret_named and not isinstance(v, (dict, list)) and v not in (None, ""):
                redacted_value, value_found = REDACTED_MARKER, True
            else:
                redacted_value, value_found = redact_json(v)

            redacted[redacted_key] = redacted_value
            found_any = found_any or key_found or value_found
        return redacted, found_any

    if isinstance(value, list):
        redacted_list = []
        for item in value:
            redacted_item, item_found = redact_json(item)
            redacted_list.append(redacted_item)
            found_any = found_any or item_found
        return redacted_list, found_any

    if isinstance(value, str):
        redacted_str, str_found = _redact_string(value)
        return redacted_str, str_found

    # int, float, bool, None — nothing to redact.
    return value, False

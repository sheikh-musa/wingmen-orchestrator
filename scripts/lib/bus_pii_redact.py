"""bus_pii_redact.py — hash-redact PII shapes out of a bus message BODY before
insert (fable-audit E-client-lanes-quality.md item 5, hc#359 / bus #64729).

Evidence that justified this (same report): 8 Emirates-ID rows, 4 NRIC rows,
and 29 real-address rows landed in agent_messages over a 7-day window, with
the console as the top emitter. A bus row is durable and widely readable
(every lane with DB access can SELECT agent_messages) — unlike a vault page,
there is no separate "generated mirror" step to catch this at, so the
redaction has to run at insert time, in bus_send.py itself.

Shapes redacted (mirrors scripts/stage_client_file.py's already-reviewed
PII-shape patterns, bus #52465/#51094, so the same value is classified the
same way whether it is about to be filed or about to be posted):
  - Emirates ID       784-YYYY-NNNNNNN-N
  - NRIC/FIN/BC       (SG) letter + 7 digits + letter
  - 3-token Arabic name   "<Name> bin/binti/ibn/bint <Name>" patronymic shape
  - non-system email  everything except fleet/service/operator addresses

HASHED, not masked: each matched value is replaced by a short, deterministic
sha256-derived token (`[REDACTED:<kind>:<hash10>]`) rather than a bare
`[REDACTED:<kind>]` marker — the same real value always hashes to the same
token, so two bus rows about the same person stay correlatable without ever
re-exposing the value itself (one-way; nothing here can recover the input
from the token).

Escape: a caller who genuinely needs the raw value in the bus body passes
`bus_send.py --allow-pii <reason>`, which skips this module entirely and logs
the reason via bus_send._log_warn — never a silent bypass.
"""
from __future__ import annotations

import hashlib
import re

NRIC_FIN_BC_RE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
EMIRATES_ID_RE = re.compile(r"\b784-\d{4}-\d{7}-\d\b")
# "<Capitalised word> bin/binti/ibn/bint <Capitalised word>" — the same
# 3-token Malay/Arabic patronymic shape as stage_client_file.py's
# _ARABIC_NAME_PARTICLE_RE (bus #52465).
ARABIC_NAME_RE = re.compile(r"\b[A-Z][a-z]+\s+(?:bin|binti|ibn|bint)\s+[A-Z][a-z]+\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Fleet/system addresses a bus body is EXPECTED to carry (bot accounts, CI,
# service roles, docs examples) — never a real person's email, so never
# redacted. Mirrors the allowlist shape in second-brain's _meta/redact.py
# (FLEET_DOMAINS/SERVICE_LOCALPARTS), kept as its own copy here since the two
# repos don't share a module.
_FLEET_DOMAINS_RE = re.compile(
    r"^(wingmen\.(dev|sg)|github\.com|supabase\.co|vercel\.(app|com)|"
    r"anthropic\.com|openai\.com|z\.ai|npmjs\.(com|org)|googleapis\.com|"
    r"[a-z0-9.-]+\.gserviceaccount\.com|resend\.dev|docker\.com|"
    r"example\.(com|org|net)|.*\.(test|demo|invalid|localhost)|localhost)$",
    re.IGNORECASE,
)
_SERVICE_LOCALPARTS_RE = re.compile(r"^(noreply|no-reply|postmaster|root|delivered)$", re.IGNORECASE)
# Musa's own addresses are expected to appear in plenty of legitimate bus
# chatter about him — redacting them would make his own threads unreadable.
OPERATOR_EMAILS = frozenset({"musa.bagushair@gmail.com", "sheikh.musa@outlook.com"})


def _is_system_email(addr: str) -> bool:
    local, _, domain = addr.partition("@")
    if addr.lower() in OPERATOR_EMAILS:
        return True
    if _SERVICE_LOCALPARTS_RE.match(local):
        return True
    return bool(_FLEET_DOMAINS_RE.match(domain))


def _hash_token(kind: str, value: str) -> str:
    h = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"[REDACTED:{kind}:{h}]"


def redact_pii(body: str) -> tuple[str, dict[str, int]]:
    """Hash-redact every PII-shaped value in `body`. Returns (redacted_body,
    counts) where counts maps kind -> number of values redacted (0 entries
    omitted). Idempotent: a token this function already produced does not
    itself look like any of the four shapes, so re-running on already-
    redacted text is a no-op."""
    counts: dict[str, int] = {}

    def _sub(pattern: re.Pattern, kind: str, text: str, keep=lambda v: False) -> str:
        def repl(m: re.Match) -> str:
            v = m.group(0)
            if keep(v):
                return v
            counts[kind] = counts.get(kind, 0) + 1
            return _hash_token(kind, v)

        return pattern.sub(repl, text)

    out = body
    out = _sub(EMIRATES_ID_RE, "emirates_id", out)
    out = _sub(NRIC_FIN_BC_RE, "nric", out)
    out = _sub(ARABIC_NAME_RE, "name", out)
    out = _sub(EMAIL_RE, "email", out, keep=_is_system_email)
    return out, counts

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
same way whether it is about to be filed or about to be posted; phone shapes
mirror second-brain's _meta/redact.py, kept in sync by hand since the two
repos don't share a module):
  - Emirates ID       784-YYYY-NNNNNNN-N, with or without separators/spaces
  - NRIC/FIN/BC       (SG) letter + 7 digits + letter
  - Phone             UAE local (0[2-9]XXXXXXXX), UAE intl (+971/00971,
                      spaces/dashes ok), SG intl (+65), SG mobile (8-digit,
                      8/9-led) — the UAE-local form is the exact gap that let
                      a real ADCDA trainer's number leak into second-brain's
                      git history (bus #64698/#64769)
  - 3-token Arabic name   "<Name> bin/binti/ibn/bint <Name>" patronymic shape
  - non-system email  everything except fleet/service/operator addresses

KNOWN LIMIT — addresses: the audit (hc#359) counted 29 real free-text address
rows in the 7-day window, but a free-text street address has no reliable
regex shape (unlike the structured ids/phones above) without an unacceptable
false-positive rate. Not covered here; do not read the absence of an
"address" kind as meaning bus bodies are address-safe.

HASHED, not masked: each matched value is replaced by a short, deterministic
token (`[REDACTED:<kind>:<hash10>]`) rather than a bare `[REDACTED:<kind>]`
marker — the same real value always hashes to the same token, so two bus rows
about the same person stay correlatable without ever re-exposing the value.

The token is HMAC-SHA256(key=BUS_PII_HMAC_KEY, msg=value)[:10], not plain
sha256(value)[:10] — a plain hash of a low-entropy value (an Emirates ID is
~10^8 candidates, an NRIC ~10^8, a phone ~10^9) is brute-forceable offline in
minutes, which defeats the point of redacting it (orch-console bus #64805).
BUS_PII_HMAC_KEY must come from the environment, never the repo. If it is
unset, this module does NOT fall back to a reversible hash — it falls back
to a bare `[REDACTED:<kind>]` marker with no token at all (same-value
correlation is lost in that mode, but nothing reversible is ever emitted).

Escape: a caller who genuinely needs the raw value in the bus body passes
`bus_send.py --allow-pii <reason>`, which skips this module entirely and logs
the reason via bus_send._log_warn — never a silent bypass.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

NRIC_FIN_BC_RE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
# 784-YYYY-NNNNNNN-N with optional dash/space separators, or none at all —
# orch-console bus #64805 point 3 (784 1990 1234567 1, 784199012345671).
EMIRATES_ID_RE = re.compile(r"\b784[- ]?\d{4}[- ]?\d{7}[- ]?\d\b")
# "<Capitalised word> bin/binti/ibn/bint <Capitalised word>" — the same
# 3-token Malay/Arabic patronymic shape as stage_client_file.py's
# _ARABIC_NAME_PARTICLE_RE (bus #52465).
ARABIC_NAME_RE = re.compile(r"\b[A-Z][a-z]+\s+(?:bin|binti|ibn|bint)\s+[A-Z][a-z]+\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone shapes — mirrors second-brain's _meta/redact.py PHONE_INTL_RE /
# MOBILE_SG_RE / PHONE_LOCAL_UAE_RE (orch-console bus #64805 point 1), plus
# 00-prefixed international dialing (00971 as well as +971).
PHONE_INTL_RE = re.compile(
    r"(?:\+|00)(?:65[ -]?\d{4}[ -]?\d{4}|971[ -]?\d{1,2}[ -]?\d{3}[ -]?\d{3,4}|971\d{8,9})"
)
MOBILE_SG_RE = re.compile(r"(?<![\dA-Za-z-])[89]\d{7}(?![\dA-Za-z-])")
PHONE_LOCAL_UAE_RE = re.compile(r"(?<![\dA-Za-z-])0[2-9]\d{8}(?![\dA-Za-z-])")

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
    key = os.environ.get("BUS_PII_HMAC_KEY")
    if not key:
        # No key -> no token at all, rather than a brute-forceable plain
        # hash. Loses cross-row correlation in this mode; never leaks the
        # value either way.
        return f"[REDACTED:{kind}]"
    h = hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()[:10]
    return f"[REDACTED:{kind}:{h}]"


def redact_pii(body: str) -> tuple[str, dict[str, int]]:
    """Hash-redact every PII-shaped value in `body`. Returns (redacted_body,
    counts) where counts maps kind -> number of values redacted (0 entries
    omitted). Idempotent: a token this function already produced does not
    itself look like any of the redacted shapes, so re-running on already-
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
    out = _sub(PHONE_INTL_RE, "phone", out)
    out = _sub(MOBILE_SG_RE, "phone", out)
    out = _sub(PHONE_LOCAL_UAE_RE, "phone", out)
    out = _sub(ARABIC_NAME_RE, "name", out)
    out = _sub(EMAIL_RE, "email", out, keep=_is_system_email)
    return out, counts

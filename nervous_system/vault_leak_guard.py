"""vault_leak_guard.py — defensive scan-and-redact pass for the operator_log
logging path (bus #44378).

Motivating incident: scripts/oeh_send.sh sent the OEH preview password to a
client and then logged the RAW text into operator_messages verbatim (row
22824), because it had no redaction pass of ANY kind — unlike
nazim_send.sh/tg_send.sh's PATTERN-based nervous_system.secret_redact, which
only catches fixed regex SHAPES (a DSN, a bot token, a JWT...). A hand-picked
plaintext password has no fixed shape to match, so even the sibling scripts'
existing redaction pass would not have caught it. This is the second
occurrence of this exact class of defect (op#22696, 2026-09-27).

This module is the backstop for BOTH failure modes: (a) a send script that
skipped or forgot the {{SECRET}} placeholder mechanism
(scripts/lib/vault_placeholder_send.py) entirely, and (b) a raw secret value
pasted directly into an outgoing message by hand. It is wired into
operator_log.log() — the single choke point every send script's durable-log
call goes through — so it protects every current AND future caller in code,
not by each script separately remembering to call it.

Deliberately an ALLOWLIST of specific vault key names, not "every vault
value": decrypting every stored secret on every single outbound/inbound log
call would be an unbounded DB+KEK round trip per secret per message, and most
vault secrets (pg DSNs, API keys, bot tokens) are already caught by
nervous_system.secret_redact's fixed-shape patterns. This allowlist exists
for exactly the residual class that bit us: a plain-text credential (no
recognizable shape) that is routinely SENT to a client/user (so redacting it
from the send itself would break the legitimate use) but must never be
persisted into the durable log.

Each key is scoped to the operator_messages.tag value(s) it is legitimately
shared on (orch-console review, bus #44388, fix 2): vault.get() writes a
vault_access_log row on EVERY call with no cache, so an unscoped fleet-wide
scan would decrypt oeh_preview_password on every hub/Nazim/console/oeh
outbound message, burying the handful of real accesses of that secret under
hundreds of scanner rows a day. operator_messages has no dedicated
"channel" narrower than the always-'telegram' `channel` column, so `tag` —
the column oeh_send.sh already stamps 'oeh' on by default — is the real
discriminator available here; scoping by it is the (a) option from the
review (vs. (b) a fleet-wide scan behind an audit-exempt reason), chosen
because it directly bounds the vault_access_log volume at the source instead
of just re-labelling it for reviewers to filter out after the fact.
"""
from __future__ import annotations

from nervous_system.vault import vault

# Vault key name -> the operator_messages.tag value(s) whose outbound/inbound
# text may legitimately carry this secret's value. A key's value is only
# decrypted (vault.get()) for a message tagged with one of its tags — never
# fleet-wide. Add a key+tag pair here the same time you introduce a new
# --secret-vault-key use of it — tests/test_vault_leak_guard.py asserts this
# stays a real, bounded allowlist, not a stand-in for "every vault value".
CLIENT_SHAREABLE_VAULT_KEYS: dict[str, tuple[str, ...]] = {
    "oeh_preview_password": ("oeh",),
}


def defensive_redact(text: str, tag: str | None = None) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Returns (possibly-redacted text, keys that FIRED, keys that could NOT be
    checked as (key, reason_class) pairs).

    Only keys scoped to `tag` (see CLIENT_SHAREABLE_VAULT_KEYS) are looked up
    at all — a message tagged e.g. 'nazim-console' never triggers a vault.get()
    for 'oeh_preview_password'.

    "Could not check" is reported distinctly from "checked, clean" (orch-console
    review, bus #44388, fixes 1b and 1c): a vault lookup failure for an
    IN-SCOPE key is never silently treated as if the scan ran and found
    nothing — it comes back in the third element so the caller can record
    "skipped: <reason>" on the row instead. The per-key catch is a broad
    `Exception`, not `VaultError` — vault.get() only raises VaultError for its
    OWN checks (not-found, wrong host, KEK missing); a psycopg
    OperationalError from a dead connection, a cryptography InvalidTag from
    _aead_decrypt, or an _audit() write failure all propagate as raw,
    non-VaultError exceptions (bus #44388 round 2: catching only VaultError
    made the "never raises" claim below false). Best-effort per key: one
    key's failure never blocks checking the rest. Never raises.
    """
    if not text:
        return text, [], []
    redacted = text
    matched: list[str] = []
    skipped: list[tuple[str, str]] = []
    for key, tags in CLIENT_SHAREABLE_VAULT_KEYS.items():
        if tag not in tags:
            continue
        try:
            value = vault.get(key, reason="vault_leak_guard defensive log-scan (bus #44378)").value
        except Exception as exc:
            skipped.append((key, type(exc).__name__))
            continue
        if value and value in redacted:
            redacted = redacted.replace(value, f"[REDACTED: {key}]")
            matched.append(key)
    return redacted, matched, skipped

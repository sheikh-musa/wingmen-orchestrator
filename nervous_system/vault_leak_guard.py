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
"""
from __future__ import annotations

from nervous_system.vault import VaultError, vault

# Vault key names whose VALUE is routinely shared over an outbound channel (so
# nervous_system.secret_redact's fixed-shape patterns don't and shouldn't
# catch it) but must never be persisted into operator_messages. Add a key here
# the same time you introduce a new --secret-vault-key use of it —
# tests/test_vault_leak_guard.py asserts this stays a real allowlist, not a
# stand-in for "every vault value".
CLIENT_SHAREABLE_VAULT_KEYS: tuple[str, ...] = (
    "oeh_preview_password",
)


def defensive_redact(text: str) -> tuple[str, list[str]]:
    """Returns (possibly-redacted text, vault key names that fired). Checks
    `text` for a literal, exact match of each CLIENT_SHAREABLE_VAULT_KEYS
    value and replaces any hit with "[REDACTED: <key>]".

    Best-effort per key: a vault lookup failure (no DB, no KEK, key doesn't
    exist yet, etc.) must never block logging the rest of the text — it just
    means that one key wasn't checked this time. Never raises.
    """
    if not text:
        return text, []
    redacted = text
    matched: list[str] = []
    for key in CLIENT_SHAREABLE_VAULT_KEYS:
        try:
            value = vault.get(key, reason="vault_leak_guard defensive log-scan (bus #44378)").value
        except VaultError:
            continue
        if value and value in redacted:
            redacted = redacted.replace(value, f"[REDACTED: {key}]")
            matched.append(key)
    return redacted, matched

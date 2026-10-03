"""system_remediation_secret.py — CAI-1380 secret-release-gate.

Mints a short-lived JWT for the ONE permanent system-remediation identity
(CAI-RESP-1380) WITHOUT the raw login password ever crossing the boundary to
the caller. This is the app-side half of the gate (the GoTrue password-grant
is an HTTP call, which cannot be made from inside a SQL SECURITY DEFINER
function — orch-console's own framing, CAI-1380 co-design).

CUSTODY (orch-console's ruling, option (a), accepted): the decryption key
(MINT_KEY_ENV_VAR below) lives ONLY in the restricted console/cai credential
class — the SAME class as write_dsn.env — and is NEVER present in the shared
lane `.env` every agent sources. `mint_remediation_session()` therefore only
ever runs meaningfully inside console's restricted execution context; calling
it from an ordinary lane environment will simply fail closed (no key
present), which is the correct, structural (not promised) enforcement of
that custody boundary. The encrypted blob itself (system_remediation_
identity_secret, migration 328) is stored in a normal table — useless
without this key — so the key is the ONLY thing that needs restricted
custody (orch-console's own framing: "only the KEY is custody-restricted").

CAI-RESP-1389(i)'s condition on the whole ephemeral-login mechanism: it may
NOT be used for any real correction until this gate is actually built and
landed. Building it does not itself constitute using it — no mint password
has been provisioned anywhere yet (the identity's own auth.users row does not
exist yet either, CAI-RESP-1389(iii) execution not granted), so every call
this module makes will fail closed at the "no mint-password provisioned"
step regardless of key presence, until that separate provisioning act
happens under its own authorization.

GRANT-LIVENESS CHECK reuses mig326's own at-use invariant (system_
remediation_grants: decision_ref + org_id + actor_user_id match, revoked_at
IS NULL, expires_at > now()) — a fresh, server-executed query every call, per
orch-console's "enforced server-side, not just app logic" requirement. No
caching, no trusting an earlier read.

ENCRYPTION mirrors this codebase's own established convention (persons.
nric_encrypted, AES-256) in spirit — a standard, well-reviewed AES-256-GCM
scheme via the `cryptography` package, not a bespoke cipher. There is no
cross-language interop requirement: both encrypt (at provisioning time,
console-side) and decrypt (here, at mint time) are Python, so this does not
need to byte-match the TypeScript NRIC functions — only the AT-REST posture
(AES-256, key held separately) needs to match the platform's convention.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Callable

MINT_KEY_ENV_VAR = "SYSTEM_REMEDIATION_MINT_KEY"  # console/cai-restricted only, NEVER shared lane .env


@dataclass
class MintResult:
    ok: bool
    reason: str
    access_token: str | None = None  # ONLY the JWT — never the password, never logged either


def grant_is_live(
    decision_ref: str,
    org_id: str,
    actor_user_id: str,
    target_dsn: str,
) -> bool:
    """Fresh, server-executed check against the live system_remediation_grants
    table (mig327, PR#671) — mirrors mig326 rev4's own at-use invariant
    exactly. No caching: called fresh on every mint attempt."""
    import psycopg

    with psycopg.connect(target_dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM system_remediation_grants "
            "WHERE decision_ref = %s AND org_id = %s AND actor_user_id = %s "
            "AND revoked_at IS NULL AND expires_at > now()",
            (decision_ref, org_id, actor_user_id),
        )
        return cur.fetchone() is not None


def _fetch_encrypted_password(actor_user_id: str, target_dsn: str) -> str | None:
    import psycopg

    with psycopg.connect(target_dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT encrypted_password FROM system_remediation_identity_secret WHERE actor_user_id = %s",
            (actor_user_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def encrypt_mint_password(plaintext_password: str, key_b64: str) -> str:
    """AES-256-GCM encrypt. `key_b64` is a base64-encoded 32-byte key from the
    restricted console/cai credential store. Used ONLY by console's own
    one-time provisioning step (not called from mint_remediation_session) —
    included here so the encrypt/decrypt pair lives in one reviewed place."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = base64.b64decode(key_b64)
    if len(key) != 32:
        raise ValueError("mint key must decode to exactly 32 bytes (AES-256)")
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext_password.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def _decrypt_mint_password(encrypted_b64: str, key_b64: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = base64.b64decode(key_b64)
    blob = base64.b64decode(encrypted_b64)
    nonce, ciphertext = blob[:12], blob[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")


def _default_password_grant(project_ref: str, anon_key: str, email: str, password: str) -> tuple[int, dict]:
    """The real GoTrue HTTP call — isolated behind a function so tests can
    inject a fake without ever needing a real password or network access."""
    import requests

    resp = requests.post(
        f"https://{project_ref}.supabase.co/auth/v1/token?grant_type=password",
        headers={"apikey": anon_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=15,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def mint_remediation_session(
    *,
    decision_ref: str,
    org_id: str,
    actor_user_id: str,
    email: str,
    project_ref: str,
    anon_key: str,
    target_dsn: str,
    # Injectable seams (mirrors auth_write_gate.py's resolve_users/grant_fn
    # pattern) — production defaults to the real DB/HTTP calls above; tests
    # inject fakes so the fail-closed branches are provable with NO DB, NO
    # network, and NO real password ever constructed.
    grant_is_live_fn: Callable[[str, str, str, str], bool] | None = None,
    fetch_password_fn: Callable[[str, str], "str | None"] | None = None,
    decrypt_fn: Callable[[str, str], str] | None = None,
    password_grant_fn: Callable[[str, str, str, str], tuple[int, dict]] | None = None,
    mint_key: str | None = None,
) -> MintResult:
    """The whole gate in one call. Fails closed at every step — no partial
    credit. On success, returns ONLY a short-lived access token; the raw
    password is decrypted in-process, used once for the GoTrue call, and
    never returned, logged, or printed anywhere."""
    grant_check = grant_is_live_fn or grant_is_live
    fetch_password = fetch_password_fn or _fetch_encrypted_password
    decrypt = decrypt_fn or _decrypt_mint_password
    password_grant = password_grant_fn or _default_password_grant

    if not grant_check(decision_ref, org_id, actor_user_id, target_dsn):
        return MintResult(False, f"no live system_remediation_grants row for decision_ref={decision_ref!r} org_id={org_id!r} actor={actor_user_id!r} — refusing to mint")

    key_b64 = mint_key if mint_key is not None else os.environ.get(MINT_KEY_ENV_VAR)
    if not key_b64:
        return MintResult(
            False,
            f"{MINT_KEY_ENV_VAR} not set in this process's environment — this function must run only in "
            "console's restricted execution context, never with the shared lane .env",
        )

    encrypted = fetch_password(actor_user_id, target_dsn)
    if encrypted is None:
        return MintResult(False, "no mint-password provisioned for this identity yet — nothing to decrypt")

    try:
        password = decrypt(encrypted, key_b64)
    except Exception as e:  # noqa: BLE001 — never let a decrypt error leak key/ciphertext material in the message
        return MintResult(False, f"decrypt failed ({type(e).__name__}) — refusing to proceed")

    try:
        status, body = password_grant(project_ref, anon_key, email, password)
    finally:
        password = None  # best-effort local scrub; never referenced again below

    if status != 200:
        # Deliberately do not include the response body — GoTrue error
        # bodies can sometimes echo request fields back.
        return MintResult(False, f"GoTrue password-grant failed with status {status}")

    access_token = body.get("access_token")
    if not access_token:
        return MintResult(False, "GoTrue response had no access_token")

    return MintResult(True, "session minted", access_token=access_token)

"""vault.py — fleet secrets vault (op#21338 phase 1).

Motivating incident: the gzb sudo password was hand-delivered by Musa twice
(2026-09-12, 2026-09-14), used ephemerally, deliberately never persisted, and
had to be re-asked for a third time on 2026-09-20 (see
reports/wingmen-core-drain-cutover-plan-op20655.md § "CREDENTIAL SEARCH").
This is the permanent fix: a credential is stored ONCE, every trusted agent
retrieves it at runtime, and it never has to transit the agent_messages /
operator_log bus again.

Design: reports/fleet-secrets-vault-design-op21338.md (gated by orch-console
bus #41841). Two-tier envelope encryption:
  - DEK (data-encryption-key): random per secret, AES-256-GCM, encrypts the
    value. Stored in Postgres wrapped (never in the clear).
  - KEK (key-encryption-key): one per TRUSTED host, lives only in that host's
    OS-native secret store (macOS Keychain on the Mini, a root-only 0600 file
    on Linux hosts) -- never in Postgres, never in .env, never in git. gzb is
    deliberately never issued a KEK (residency: a fleet-wide secret store must
    not be decryptable on a client's own premises) -- a trusted host (the Mini
    or wingmen-core/hub-vps) calls vault.get() locally and carries the
    plaintext across whatever SSH/VPN hop it already makes, same model
    orch-console used for itself in bus #41816.

Usage (mirrors load_dotenv(".env") ergonomics):
    from nervous_system.vault import vault
    secret = vault.get("gzb_sudo_password", reason="wingmen-core cutover op20655 Part A")
    if secret.leak_flagged:
        ...caller must explicitly decide whether to proceed...
    use(secret.value)

    vault.put("some_new_secret", "the-value", reason="onboarding X",
              leak_flagged=False)

API contract this module enforces on itself (callers must still not print or
forward secret.value anywhere it could be logged -- this module cannot stop
that, only refuses to do it itself):
  - never writes a plaintext secret value to disk, stdout/stderr, or any log
  - never includes a plaintext secret value in an exception message
  - every get()/put()/rotate() writes exactly one metadata-only row to
    vault_access_log (secret name, calling agent_id, the caller's `reason`
    string, success/failure, timestamp) -- never the value itself

Phase 1 scope: get/put/rotate + Mini (macOS) and Linux root-file KEK sources.
allowed_agents ACL enforcement is NOT implemented (Phase 2+, bus #41841 note 3)
-- the column exists on the table but this module does not read or enforce it.
"""
from __future__ import annotations

import base64
import os
import platform
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")

# macOS Keychain generic-password entry this module expects for the Mini's
# KEK. orch-console bus #41841 bootstraps this with:
#   security add-generic-password -s wingmen-vault-kek -a mini -w <32-byte-key-as-base64>
_KEYCHAIN_SERVICE = "wingmen-vault-kek"

# Linux hosts read their KEK from a root-only file instead of a keyring.
# Matches the existing /etc/gzb-vpn.conf convention (root-only, single host,
# documented inline).
_LINUX_KEK_PATH = Path("/etc/wingmen-vault-kek")

_AESGCM_NONCE_LEN = 12  # bytes, per AES-GCM's standard 96-bit nonce


class VaultError(RuntimeError):
    """Base class for every vault failure. Never includes a secret value."""


class KekNotFoundError(VaultError):
    """Raised when this host's KEK cannot be located. Message names the exact
    lookup that failed so whoever bootstraps it knows precisely what to
    create -- this is a feature (actionable signal), not a bug to stub
    around."""


class UnknownHostError(VaultError):
    """Raised when the current host isn't a recognized trusted KEK host.
    Fail-loud rather than silently guessing a host identity -- an unrecognized
    host must never be handed a KEK path it wasn't deliberately given."""


class SecretNotFoundError(VaultError):
    """Raised when no row exists for the requested secret name."""


class WrongHostError(VaultError):
    """Raised when a secret is wrapped for a KEK host other than the one
    calling get() -- this host structurally cannot decrypt it."""


@dataclass(frozen=True)
class VaultSecret:
    """Returned by vault.get(). leak_flagged is surfaced explicitly (not just
    folded into .value) so a caller cannot accidentally use a known-
    compromised credential without seeing the flag."""

    value: str
    leak_flagged: bool
    leak_reason: Optional[str]


def _local_host_id() -> str:
    """The logical KEK-host identity for the machine this process is running
    on. Deliberately explicit/allowlisted rather than derived by guessing --
    an unrecognized host must fail loud, never silently fall back to some
    default KEK path."""
    system = platform.system()
    if system == "Darwin":
        # This fleet has exactly one Mac -- the Mini. hostname is not used as
        # the KEK identity (it can change); 'mini' is the fixed logical name
        # the design doc + bus #41841 both use.
        return "mini"
    if system == "Linux":
        hostname = socket.gethostname()
        # wingmen-core / hub-vps is the only Linux host issued a KEK in phase
        # 1. gzb is deliberately NEVER issued one (residency, see module
        # docstring) -- if this ever runs there, _linux_kek() will correctly
        # fail with a missing-file error, not a wrong-secret decrypt.
        return "hub-vps"
    raise UnknownHostError(
        f"vault: unrecognized platform.system()={system!r} -- this host has no "
        "defined KEK identity. Trusted hosts today: Mini (Darwin -> 'mini'), "
        "wingmen-core/hub-vps (Linux -> 'hub-vps'). gzb is intentionally never "
        "issued a KEK."
    )


def _agent_id() -> str:
    """Calling agent's identity for the audit log. AGENT_ID / CC_BASE_AGENT_ID
    are the real identity envs in this fleet -- ORCH_AGENT_ID is NOT (see
    reference_agent_id_not_orch_agent_id_for_identity). Fails loud rather than
    auditing under a blank/guessed identity."""
    agent_id = os.environ.get("CC_BASE_AGENT_ID") or os.environ.get("AGENT_ID")
    if not agent_id:
        raise VaultError(
            "vault: no CC_BASE_AGENT_ID or AGENT_ID in this process's environment -- "
            "refusing to audit a vault access under an unknown identity."
        )
    return agent_id


def _macos_kek(host_id: str) -> bytes:
    try:
        out = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                _KEYCHAIN_SERVICE,
                "-a",
                host_id,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise KekNotFoundError(
            f"vault: no Keychain entry for service={_KEYCHAIN_SERVICE!r} "
            f"account={host_id!r}. Bootstrap with:\n"
            f"  security add-generic-password -s {_KEYCHAIN_SERVICE} -a {host_id} "
            "-w <base64-32-byte-key>\n"
            f"(security exit {exc.returncode}: {exc.stderr.strip()!r})"
        ) from None
    key_b64 = out.stdout.strip()
    try:
        key = base64.b64decode(key_b64, validate=True)
    except Exception as exc:  # noqa: BLE001 -- re-raised as a typed VaultError below
        raise VaultError(
            f"vault: Keychain entry {_KEYCHAIN_SERVICE!r}/{host_id!r} did not "
            "decode as base64 -- was it bootstrapped correctly?"
        ) from exc
    if len(key) != 32:
        raise VaultError(
            f"vault: Keychain entry {_KEYCHAIN_SERVICE!r}/{host_id!r} decoded to "
            f"{len(key)} bytes, expected 32 (AES-256 key) -- was it bootstrapped "
            "correctly?"
        )
    return key


def _linux_kek(host_id: str) -> bytes:
    if not _LINUX_KEK_PATH.exists():
        raise KekNotFoundError(
            f"vault: no KEK file at {_LINUX_KEK_PATH} for host {host_id!r}. "
            f"Bootstrap with (root, 0600): base64 32 random bytes into "
            f"{_LINUX_KEK_PATH}."
        )
    raw = _LINUX_KEK_PATH.read_text().strip()
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise VaultError(f"vault: {_LINUX_KEK_PATH} did not decode as base64.") from exc
    if len(key) != 32:
        raise VaultError(
            f"vault: {_LINUX_KEK_PATH} decoded to {len(key)} bytes, expected 32."
        )
    return key


def _local_kek(host_id: str) -> bytes:
    system = platform.system()
    if system == "Darwin":
        return _macos_kek(host_id)
    if system == "Linux":
        return _linux_kek(host_id)
    raise UnknownHostError(f"vault: no KEK source implemented for platform {system!r}.")


def _aead_encrypt(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(_AESGCM_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct


def _aead_decrypt(key: bytes, blob: bytes) -> bytes:
    nonce, ct = blob[:_AESGCM_NONCE_LEN], blob[_AESGCM_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ct, None)


def _connect() -> psycopg.Connection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise VaultError("vault: DATABASE_URL not set in this process's environment.")
    return psycopg.connect(dsn)


def _audit(conn: psycopg.Connection, secret_name: str, reason: str, success: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.vault_access_log (secret_name, accessed_by, reason, success) "
            "VALUES (%s, %s, %s, %s)",
            (secret_name, _agent_id(), reason, success),
        )


class Vault:
    """Thin wrapper so `from nervous_system.vault import vault; vault.get(...)`
    reads like load_dotenv() usage elsewhere in this fleet. Stateless beyond
    that -- every call opens its own connection, no cached plaintext."""

    def get(self, name: str, reason: str) -> VaultSecret:
        if not reason:
            raise VaultError("vault.get() requires a non-empty reason (goes to the audit log).")
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ciphertext, wrapped_dek, kek_host, leak_flagged, leak_reason "
                    "FROM public.vault_secrets WHERE name = %s",
                    (name,),
                )
                row = cur.fetchone()
            if row is None:
                _audit(conn, name, reason, success=False)
                conn.commit()
                raise SecretNotFoundError(f"vault: no secret named {name!r}.")

            ciphertext, wrapped_dek, kek_host, leak_flagged, leak_reason = row
            local_host = _local_host_id()
            if kek_host != local_host:
                _audit(conn, name, reason, success=False)
                conn.commit()
                raise WrongHostError(
                    f"vault: secret {name!r} is wrapped for kek_host={kek_host!r}, "
                    f"this host is {local_host!r} -- cannot decrypt here."
                )

            kek = _local_kek(local_host)
            dek = _aead_decrypt(kek, bytes(wrapped_dek))
            plaintext = _aead_decrypt(dek, bytes(ciphertext)).decode("utf-8")

            _audit(conn, name, reason, success=True)
            conn.commit()
            return VaultSecret(value=plaintext, leak_flagged=bool(leak_flagged), leak_reason=leak_reason)
        finally:
            conn.close()

    def put(
        self,
        name: str,
        value: str,
        reason: str,
        leak_flagged: bool = False,
        leak_flagged_reason: Optional[str] = None,
    ) -> None:
        if not reason:
            raise VaultError("vault.put() requires a non-empty reason (goes to the audit log).")
        if leak_flagged and not leak_flagged_reason:
            raise VaultError("vault.put(leak_flagged=True) requires leak_flagged_reason.")

        conn = _connect()
        try:
            local_host = _local_host_id()
            kek = _local_kek(local_host)
            dek = AESGCM.generate_key(bit_length=256)
            ciphertext = _aead_encrypt(dek, value.encode("utf-8"))
            wrapped_dek = _aead_encrypt(kek, dek)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.vault_secrets
                        (name, ciphertext, wrapped_dek, kek_host, leak_flagged, leak_reason, created_by_agent)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        ciphertext = EXCLUDED.ciphertext,
                        wrapped_dek = EXCLUDED.wrapped_dek,
                        kek_host = EXCLUDED.kek_host,
                        leak_flagged = EXCLUDED.leak_flagged,
                        leak_reason = EXCLUDED.leak_reason,
                        rotated_at = now()
                    """,
                    (name, ciphertext, wrapped_dek, local_host, leak_flagged, leak_flagged_reason, _agent_id()),
                )
            _audit(conn, name, reason, success=True)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
                _audit(conn, name, reason, success=False)
                conn.commit()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def rotate(self, name: str, new_value: str, reason: str) -> None:
        """Re-encrypts under a fresh DEK, archives the superseded ciphertext
        to vault_secrets_history, and clears any leak_flagged state -- per
        design §3, rotation is how a leak flag gets cleared, never a silent
        UPDATE that just drops the flag without a new value actually landing."""
        if not reason:
            raise VaultError("vault.rotate() requires a non-empty reason (goes to the audit log).")

        conn = _connect()
        try:
            local_host = _local_host_id()
            kek = _local_kek(local_host)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ciphertext, wrapped_dek, kek_host FROM public.vault_secrets WHERE name = %s FOR UPDATE",
                    (name,),
                )
                row = cur.fetchone()
                if row is None:
                    raise SecretNotFoundError(f"vault: no secret named {name!r} to rotate.")
                old_ciphertext, old_wrapped_dek, old_kek_host = row

                cur.execute(
                    "INSERT INTO public.vault_secrets_history (secret_name, ciphertext, wrapped_dek, kek_host) "
                    "VALUES (%s, %s, %s, %s)",
                    (name, bytes(old_ciphertext), bytes(old_wrapped_dek), old_kek_host),
                )

                new_dek = AESGCM.generate_key(bit_length=256)
                new_ciphertext = _aead_encrypt(new_dek, new_value.encode("utf-8"))
                new_wrapped_dek = _aead_encrypt(kek, new_dek)

                cur.execute(
                    """
                    UPDATE public.vault_secrets
                    SET ciphertext = %s, wrapped_dek = %s, kek_host = %s,
                        leak_flagged = false, leak_reason = NULL, rotated_at = now()
                    WHERE name = %s
                    """,
                    (new_ciphertext, new_wrapped_dek, local_host, name),
                )
            _audit(conn, name, reason, success=True)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
                _audit(conn, name, reason, success=False)
                conn.commit()
            except Exception:
                pass
            raise
        finally:
            conn.close()


vault = Vault()

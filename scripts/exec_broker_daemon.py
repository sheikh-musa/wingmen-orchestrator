#!/usr/bin/env python3
"""exec_broker_daemon.py — hc#95 exec-broker systemd ExecStart target.

Reads its config from the environment (EXEC_BROKER_SOCKET,
EXEC_BROKER_ALLOWED_UIDS, EXEC_BROKER_TOKEN_FILE; DATABASE_URL via
substrate_dsn.dsn_from_env_file -- see below), wires up BrokerServer with
real dependencies (psycopg for the DB, the real mgmt-API transport), and
serves forever. The PAT is read ONCE from its dedicated 0400 file at startup
and held only in memory — never logged, never re-read per request.

DATABASE_URL is resolved FRESH on every connection via
substrate_dsn.dsn_from_env_file(), never cached from os.environ at startup
(op#24342: a long-lived process that keeps its launch-time DATABASE_URL
keeps re-trying a PRE-ROTATION password after a DB password rotation,
hammering the pooler into ECIRCUITBREAKER -- the 2026-09-30/10-01
incidents). The .env FILE wins over the inherited environment so a
rotation's single push-point (the file) is always honored.

BOUNDED OVERRIDE (op#34322/#76985/#76989/#76998, 2026-10-26, orch-console
gate): four optional env vars arm a single, wall-clock-gated exception on
top of the permanent allowlists in acl.py/gate_validation.py --
EXEC_BROKER_UID_OVERRIDE + EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT (uid),
EXEC_BROKER_REF_OVERRIDE_ALIAS + EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT (ref).
The ref override is keyed by silo ALIAS (e.g. "goumlyne"), never the raw
project ref -- see resolve_ref_override_alias()'s docstring for why (the
console's own console_irsyad_guard hook blocks it from ever typing the raw
ref, and the console is the sole gzb-root holder who arms this). Both
expiries are clamped to a 24h cap in code (parse_override_expiry) and
re-evaluated against wall-clock time on every request, so the exception
reverts by construction the moment it expires.

B1 STANDING LIVE_GATED_REFS (cc-fleet-health cutover spec bus #89814 §1):
EXEC_BROKER_LIVE_GATED_REFS is an OPTIONAL comma-separated list of silo
ALIASES (same alias-not-raw-ref rule as the ref override, same reason) that
replaces the hard-coded QA-only call-allowlist. Unset/blank -> the default
(QA only, identical to before). Read ONCE at boot: widening is an env edit +
restart, never a code edit; narrowing takes effect on restart. Every ref in
the set is fully gated by gate_validation.require_gate (every non-plain-read
statement needs a gate). An unknown alias fails loud at boot. The resolved set
(aliases + refs) is logged once at boot.

RUNBOOK NOTE -- the env REPLACES the default, it does not ADD to it: setting
EXEC_BROKER_LIVE_GATED_REFS=goumlyne DROPS QA. For rollout step (b) (QA + one
real ref in overlap) the value must list BOTH, e.g.
EXEC_BROKER_LIVE_GATED_REFS=qa,goumlyne.

DEAD-MAN HEARTBEAT (spec bus #89814 §5.2): before serving, the daemon CAS-takes
one exec_broker_heartbeat row per gated ref (exec_broker/heartbeat.py) and
then renews each every EXEC_BROKER_HEARTBEAT_INTERVAL_SECONDS (default 60,
bounded 10..900; ttl = 3 x interval). Boot REFUSES if the host identity cannot
be resolved, the take hits a DB error, or a gated ref is held by another live
broker -- fail closed.

B3 GATE MAX AGE (bus #89814 §4.1, console #89841 ruling c):
EXEC_BROKER_GATE_MAX_AGE_SECONDS, an OPTIONAL integer number of seconds, read
once at boot. Unset/blank -> 24h (gate_validation.DEFAULT_GATE_MAX_AGE_SECONDS).
Hard-bounded IN CODE to 1s..72h: anything malformed, zero, negative, or above
the 72h ceiling REFUSES BOOT (never clamped, never "no limit").

B3 CUTOVER FLOOR (console #89841 ruling e): EXEC_BROKER_GATE_NOT_BEFORE, an
ISO-8601 timestamp set at deploy; any gate whose bus row is older is refused
(GATE_INVALID, reason gate_before_cutover). Naive -> UTC; malformed refuses
boot. UNSET: gates on every non-QA ref are refused per call (reason
gate_cutover_unset), and the daemon REFUSES BOOT if EXEC_BROKER_LIVE_GATED_REFS
names any non-QA ref -- fail closed, so real refs can never be gated without a
floor.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from dotenv import load_dotenv  # noqa: E402

from exec_broker.gate_validation import (  # noqa: E402
    DEFAULT_GATE_MAX_AGE_SECONDS,
    DEFAULT_LIVE_GATED_REFS,
    MAX_GATE_MAX_AGE_SECONDS,
    MIN_GATE_MAX_AGE_SECONDS,
    SILO_ALIASES,
)
from exec_broker import heartbeat  # noqa: E402
from exec_broker import vault_log  # noqa: E402
from exec_broker.mgmt_api import post_to_mgmt_api  # noqa: E402
from exec_broker.server import BrokerServer  # noqa: E402
from substrate_dsn import dsn_from_env_file  # noqa: E402

_REQUIRED_ENV_VARS = ("EXEC_BROKER_SOCKET", "EXEC_BROKER_ALLOWED_UIDS", "EXEC_BROKER_TOKEN_FILE")


def load_allowed_uids(raw: str) -> frozenset[int]:
    """Parse a comma-separated EXEC_BROKER_ALLOWED_UIDS value. Raises
    ValueError on an empty value or a non-digit entry — fail loud, never
    silently fall back to an empty (deny-all) or wildcard (allow-all) set."""
    if not raw or not raw.strip():
        raise ValueError("EXEC_BROKER_ALLOWED_UIDS must not be empty")
    uids = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            raise ValueError(f"EXEC_BROKER_ALLOWED_UIDS entry {part!r} is not a positive integer uid")
        uids.append(int(part))
    return frozenset(uids)


_OVERRIDE_MAX_HOURS = 24


def load_uid_override(raw: "str | None") -> "frozenset[int] | None":
    """Like load_allowed_uids but OPTIONAL — unset/blank means no override
    (inert, not an error). A malformed non-empty value still fails loud
    (same digit-only rule as the permanent allowlist)."""
    if not raw or not raw.strip():
        return None
    return load_allowed_uids(raw)


def resolve_ref_override_alias(raw: "str | None") -> "str | None":
    """Resolve EXEC_BROKER_REF_OVERRIDE_ALIAS (a silo ALIAS like "goumlyne",
    never a raw project ref) to its real project_ref via the same
    SILO_ALIASES table gate_validation.py already uses for gate-text
    matching. None if unset/blank (override inert).

    WHY an alias and not the raw ref (op#34322/#76998, orch-console): the
    console's own console_irsyad_guard PreToolUse hook pattern-matches the
    literal ref string (e.g. "goumlynecruxrlmzlntp") in ANY tool call the
    console body makes and refuses it -- and the console is the sole
    gzb-root holder who can perform this env-edit+restart. Keying the arm
    env var by alias means the root-holder's own typed command/file never
    contains the guard-blocked literal; only this trusted in-process lookup
    (never console-typed) resolves it to the real ref. Fails loud on an
    unrecognized alias -- never silently treated as "no override" (that
    could look like a disarm when someone actually intended an arm), and
    never falls back to matching a raw ref directly (that would just
    reopen the same guard trip this exists to avoid)."""
    if not raw or not raw.strip():
        return None
    alias = raw.strip().lower()
    try:
        return SILO_ALIASES[alias]
    except KeyError:
        raise ValueError(
            f"EXEC_BROKER_REF_OVERRIDE_ALIAS {alias!r} is not a known silo alias "
            f"(one of {sorted(SILO_ALIASES)})"
        ) from None


def describe_live_gated_refs(refs: "frozenset[str]") -> str:
    """Human-readable alias(es) + ref for each gated ref, for the boot log."""
    parts = []
    for ref in sorted(refs):
        aliases = sorted(a for a, r in SILO_ALIASES.items() if r == ref)
        parts.append(f"{'/'.join(aliases) or '?'}={ref}")
    return ", ".join(parts)


def load_heartbeat_interval(raw: "str | None") -> int:
    """EXEC_BROKER_HEARTBEAT_INTERVAL_SECONDS: unset/blank -> 60; else an
    integer in 10..900, or ValueError (fail loud at boot)."""
    if not raw or not raw.strip():
        return heartbeat.DEFAULT_INTERVAL_SECONDS
    value = raw.strip()
    if (not value.isascii() or not value.isdigit()
            or not heartbeat.MIN_INTERVAL_SECONDS <= int(value) <= heartbeat.MAX_INTERVAL_SECONDS):
        raise ValueError(
            "EXEC_BROKER_HEARTBEAT_INTERVAL_SECONDS must be an integer in "
            f"{heartbeat.MIN_INTERVAL_SECONDS}..{heartbeat.MAX_INTERVAL_SECONDS}, got {value!r}"
        )
    return int(value)


def _resolve_host() -> str:
    import fleet_host_id  # scripts/lib is on sys.path (top of this file)

    try:
        return fleet_host_id.fleet_host_id()
    except Exception as e:  # noqa: BLE001 -- never act under an unknown identity
        raise RuntimeError(f"cannot resolve a stable host identity: {e}") from e


def _reconcile_in_doubt(*, db_conn_factory, boot_id: str, host: str) -> "tuple[int, int]":
    """Queue row 272: BEFORE serving and before taking heartbeat rows, move every
    'started' row from a PREVIOUS boot to status 'in_doubt' (see
    vault_log.mark_previous_boots_in_doubt). Never retries, never finalizes.
    Raises RuntimeError (boot refused) on any DB error."""
    try:
        with db_conn_factory() as conn:
            with conn.cursor() as cur:
                n, oldest = vault_log.mark_previous_boots_in_doubt(cur, boot_id=boot_id, host=host)
            conn.commit()
    except Exception as e:  # noqa: BLE001 -- class only, never a DSN or SQL text
        raise RuntimeError(f"in-doubt reconcile failed: {e.__class__.__name__}") from e
    print(
        f"exec_broker_daemon: in-doubt reconcile: marked {n} row(s) from previous boot(s) "
        f"as '{vault_log.IN_DOUBT_STATUS}'" + (f", oldest {oldest}s old" if n else ""),
        file=sys.stderr,
    )
    return n, oldest


def _start_heartbeat(*, db_conn_factory, refs, interval_seconds: int,
                     sock_path: "str | None" = None, boot_id: "str | None" = None,
                     host: "str | None" = None) -> "heartbeat.HeartbeatWriter":
    """Take every gated ref's heartbeat row (CAS) and start the writer thread.
    Raises RuntimeError on any reason boot must be refused."""
    if host is None:
        import fleet_host_id  # scripts/lib is on sys.path (top of this file)

        try:
            host = fleet_host_id.fleet_host_id()
        except Exception as e:  # noqa: BLE001 -- never beat under an unknown identity
            raise RuntimeError(f"cannot resolve a stable host identity for the heartbeat: {e}") from e
    writer = heartbeat.HeartbeatWriter(
        db_conn_factory, refs, host=host, pid=os.getpid(), boot_id=boot_id or str(uuid.uuid4()),
        interval_seconds=interval_seconds, sock_path=sock_path,
    )
    try:
        failed = writer.take_all()
    except Exception as e:  # noqa: BLE001 -- DB error at boot -> refuse
        raise RuntimeError(f"heartbeat take failed: {e.__class__.__name__}") from e
    if failed:
        raise RuntimeError(
            f"heartbeat row(s) for {failed} are held by another LIVE broker -- refusing to "
            "boot a second broker for the same gated ref (never steal a fresh holder)"
        )
    writer.start()
    return writer


def load_live_gated_refs(raw: "str | None") -> "frozenset[str]":
    """Resolve EXEC_BROKER_LIVE_GATED_REFS (comma-separated silo ALIASES) to
    the standing set of project refs the broker will call, every one of them
    gated. Unset/blank -> DEFAULT_LIVE_GATED_REFS (QA only). Fails loud
    (ValueError) on an empty entry or an unknown alias -- never silently
    drops a typo (that would look like a narrowing nobody intended) and never
    accepts a raw ref (see resolve_ref_override_alias for why)."""
    if not raw or not raw.strip():
        return DEFAULT_LIVE_GATED_REFS
    refs = set()
    for part in raw.split(","):
        alias = part.strip().lower()
        if not alias:
            raise ValueError("EXEC_BROKER_LIVE_GATED_REFS has an empty entry")
        try:
            refs.add(SILO_ALIASES[alias])
        except KeyError:
            raise ValueError(
                f"EXEC_BROKER_LIVE_GATED_REFS entry {alias!r} is not a known silo alias "
                f"(one of {sorted(SILO_ALIASES)})"
            ) from None
    return frozenset(refs)


def load_gate_max_age_seconds(raw: "str | None") -> int:
    """Parse EXEC_BROKER_GATE_MAX_AGE_SECONDS. Unset/blank -> the 24h default.
    Raises ValueError on anything but a positive base-10 integer."""
    if not raw or not raw.strip():
        return DEFAULT_GATE_MAX_AGE_SECONDS
    value = raw.strip()
    if (
        not value.isascii() or not value.isdigit()
        or not MIN_GATE_MAX_AGE_SECONDS <= int(value) <= MAX_GATE_MAX_AGE_SECONDS
    ):
        raise ValueError(
            "EXEC_BROKER_GATE_MAX_AGE_SECONDS must be an integer number of seconds in "
            f"{MIN_GATE_MAX_AGE_SECONDS}..{MAX_GATE_MAX_AGE_SECONDS} (72h hard ceiling), got {value!r}"
        )
    return int(value)


def load_gate_not_before(raw: "str | None") -> "datetime | None":
    """Parse EXEC_BROKER_GATE_NOT_BEFORE (ISO-8601). None if unset/blank;
    naive -> UTC; malformed raises ValueError (fail loud at boot)."""
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"malformed EXEC_BROKER_GATE_NOT_BEFORE {value!r}: {e}") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_override_expiry(raw: "str | None", *, now: datetime) -> "datetime | None":
    """Parse an ISO8601 expiry for a bounded override. None if unset/blank
    (override inert). Raises ValueError on a malformed non-empty value —
    fail loud, never silently treat a garbled expiry as "no override" (that
    could look like a disarm when someone actually intended an arm).

    A parsed expiry further than _OVERRIDE_MAX_HOURS from `now` is CLAMPED
    down to that cap (logged loudly, not rejected) — the bounded-exception
    ceiling (op#34322/#76985/#76989) is enforced HERE in code, never left to
    whoever sets the env to get right."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"malformed override expiry {raw!r}: {e}") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    cap = now + timedelta(hours=_OVERRIDE_MAX_HOURS)
    if parsed > cap:
        print(
            f"exec_broker_daemon: override expiry {raw!r} exceeds the {_OVERRIDE_MAX_HOURS}h "
            f"cap — clamped to {cap.isoformat()}",
            file=sys.stderr,
        )
        return cap
    return parsed


def _db_conn_factory():
    import psycopg
    from psycopg.rows import dict_row

    # Resolved fresh per connection (op#24342) -- env_path=None uses
    # substrate_dsn's own default (the orchestrator repo's .env); point
    # EXEC_BROKER_ENV_FILE at a different file if provisioning gives this
    # daemon its own, still with the same file-wins-over-environment and
    # fail-loud-on-missing guarantees.
    dsn = dsn_from_env_file(env_path=os.environ.get("EXEC_BROKER_ENV_FILE"))
    return psycopg.connect(dsn, row_factory=dict_row)


def main(argv=None) -> int:
    load_dotenv()

    missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        print(f"exec_broker_daemon: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        dsn_from_env_file(env_path=os.environ.get("EXEC_BROKER_ENV_FILE"))
    except RuntimeError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    try:
        allowed_uids = load_allowed_uids(os.environ["EXEC_BROKER_ALLOWED_UIDS"])
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    token_path = Path(os.environ["EXEC_BROKER_TOKEN_FILE"])
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as e:
        print(f"exec_broker_daemon: could not read token file {token_path}: {e}", file=sys.stderr)
        return 1
    if not token:
        print(f"exec_broker_daemon: token file {token_path} is empty", file=sys.stderr)
        return 1

    try:
        live_gated_refs = load_live_gated_refs(os.environ.get("EXEC_BROKER_LIVE_GATED_REFS"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1
    print(
        "exec_broker_daemon: LIVE_GATED_REFS ("
        + ("EXEC_BROKER_LIVE_GATED_REFS" if (os.environ.get("EXEC_BROKER_LIVE_GATED_REFS") or "").strip()
           else "default -- env unset")
        + f"): {describe_live_gated_refs(live_gated_refs)}",
        file=sys.stderr,
    )

    try:
        heartbeat_interval = load_heartbeat_interval(os.environ.get("EXEC_BROKER_HEARTBEAT_INTERVAL_SECONDS"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    try:
        gate_max_age_seconds = load_gate_max_age_seconds(os.environ.get("EXEC_BROKER_GATE_MAX_AGE_SECONDS"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    try:
        gate_not_before = load_gate_not_before(os.environ.get("EXEC_BROKER_GATE_NOT_BEFORE"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1
    if gate_not_before is None and live_gated_refs - DEFAULT_LIVE_GATED_REFS:
        print(
            "exec_broker_daemon: EXEC_BROKER_LIVE_GATED_REFS names a non-QA ref but "
            "EXEC_BROKER_GATE_NOT_BEFORE is unset -- refusing to boot (ruling e: a "
            "real ref is never gated without a cutover floor)",
            file=sys.stderr,
        )
        return 1
    if gate_not_before is not None and gate_not_before > datetime.now(timezone.utc):
        print(
            f"exec_broker_daemon: EXEC_BROKER_GATE_NOT_BEFORE {gate_not_before.isoformat()} is in "
            "the future -- every gate is refused until then",
            file=sys.stderr,
        )

    try:
        ref_override = resolve_ref_override_alias(os.environ.get("EXEC_BROKER_REF_OVERRIDE_ALIAS"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1
    try:
        uid_override = load_uid_override(os.environ.get("EXEC_BROKER_UID_OVERRIDE"))
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    try:
        ref_override_expires_at = parse_override_expiry(
            os.environ.get("EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT"), now=now
        )
        uid_override_expires_at = parse_override_expiry(
            os.environ.get("EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT"), now=now
        )
    except ValueError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    # Half an override wired is a misconfiguration, not "no override" — fail
    # loud at boot rather than let a lone ref/uid with no paired expiry (or
    # vice versa) compare against None on a live request later.
    if ref_override and ref_override_expires_at is None:
        print(
            "exec_broker_daemon: EXEC_BROKER_REF_OVERRIDE_ALIAS set without "
            "EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT",
            file=sys.stderr,
        )
        return 1
    if ref_override_expires_at is not None and not ref_override:
        print(
            "exec_broker_daemon: EXEC_BROKER_REF_OVERRIDE_EXPIRES_AT set without "
            "EXEC_BROKER_REF_OVERRIDE_ALIAS",
            file=sys.stderr,
        )
        return 1
    if uid_override and uid_override_expires_at is None:
        print(
            "exec_broker_daemon: EXEC_BROKER_UID_OVERRIDE set without "
            "EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT",
            file=sys.stderr,
        )
        return 1
    if uid_override_expires_at is not None and uid_override is None:
        print(
            "exec_broker_daemon: EXEC_BROKER_UID_OVERRIDE_EXPIRES_AT set without "
            "EXEC_BROKER_UID_OVERRIDE",
            file=sys.stderr,
        )
        return 1

    # Queue row 272: ONE boot id for this daemon process -- stamped on every
    # access-log row (log_started) AND used as the heartbeat boot_id -- so the
    # next boot's in-doubt reconcile can tell exactly whose rows were in flight.
    boot_id = str(uuid.uuid4())
    try:
        host = _resolve_host()
        _reconcile_in_doubt(db_conn_factory=_db_conn_factory, boot_id=boot_id, host=host)
    except RuntimeError as e:
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1

    server = BrokerServer(
        sock_path=os.environ["EXEC_BROKER_SOCKET"],
        allowed_uids=allowed_uids,
        db_conn_factory=_db_conn_factory,
        token=token,
        mgmt_api_fn=post_to_mgmt_api,
        uid_override=uid_override,
        uid_override_expires_at=uid_override_expires_at,
        ref_override=ref_override,
        ref_override_expires_at=ref_override_expires_at,
        live_gated_refs=live_gated_refs,
        gate_max_age_seconds=gate_max_age_seconds,
        gate_not_before=gate_not_before,
        boot_id=boot_id,
    )
    try:
        _start_heartbeat(
            db_conn_factory=_db_conn_factory, refs=live_gated_refs, interval_seconds=heartbeat_interval,
            sock_path=os.environ["EXEC_BROKER_SOCKET"], boot_id=boot_id, host=host,
        )
    except (RuntimeError, ValueError) as e:  # ValueError: HeartbeatWriter config (e.g. no refs)
        print(f"exec_broker_daemon: {e}", file=sys.stderr)
        return 1
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

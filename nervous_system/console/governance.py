"""governance.py — per-project governance registry, console read + write path
(op#20702 Stage E, fc-v65; design: reports/per-project-governance-design-op20702.md).

THE REGISTRY (migration 063, substrate DB tscuymavysscrvoberrr):
  public.project_governance      — one row per project: cai_enabled, operators
                                   [{name, chat_id, internal}], channels [chat_id],
                                   money_clearance_enabled (default FALSE),
                                   residency_ack, updated_by, reason, updated_at.
  public.project_governance_audit — APPEND-ONLY before/after snapshots, written by
                                   the AFTER INSERT OR UPDATE trigger
                                   `trg_project_governance_audit`
                                   (project_governance_audit_trigger()). Nothing
                                   writes the audit table directly; DELETE on the
                                   registry is refused by trg_project_governance_
                                   forbid_delete.

THE SANCTIONED WRITE PATH mig 063 defines is exactly: an UPDATE of ONE row on
project_governance (as postgres/service_role) that sets the toggle + `updated_by`
+ `reason`, and lets the trigger append the audit row (changed_by = NEW.updated_by,
reason = NEW.reason, before/after = full-row JSONB). There is no RPC. This module
does ONLY that — never an INSERT (a new project is a migration-level decision),
never a DELETE (refused by the DB anyway), never a direct audit write.

WHY A SUBPROCESS MODULE, NOT db.py:
  * The console's DB session is the SELECT-only `console_readonly` role
    (CAI-RESP-264 cond 2), and migration 063 grants SELECT on the registry to
    `authenticated` only — verified 2026-09-17: console_readonly gets
    InsufficientPrivilege on project_governance. So BOTH the read and the write
    run here, invoked by app.py as `python -m nervous_system.console.governance`
    (the same vetted-script pattern /api/assign, /api/ask-close and /api/backlog
    use: the script loads the writable orchestrator env itself; the console
    process never holds the write DSN). Lives INSIDE the console package so the
    deploy gate's content hash + cc-quality review cover it.
  * Every value reaching the UPDATE is validated HERE (allowlisted column, typed
    value, positive user-id operators only, group-id channels only) — argv from
    app.py is re-validated, never trusted.

STRUCTURAL FAIL-CLOSED RULES (the governance analogue of the apply-armed
gazzabyte-fp rule — enforced in code, not by the UI):
  * an operator chat_id must be a POSITIVE Telegram user id (a group/channel id
    is negative and can never authorize — same rule Stage C's
    project_operator_authorization._is_authorizable_chat_id applies on read);
  * a channel must be a NEGATIVE (group) chat id — a DM never needs listing;
  * money_clearance_enabled -> true additionally requires the literal
    acknowledgement phrase MONEY_ACK_PHRASE (typed by the operator in the UI,
    passed as --money-ack); OFF never needs it;
  * `reason` is mandatory (it is what the audit row records);
  * the audit row is VERIFIED in the same transaction — if the trigger did not
    append one, the write is rolled back (a governance change without its audit
    row must never commit).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Tuple

PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_INT_RE = re.compile(r"^-?[0-9]{1,20}$")

# The ONLY writable columns (allowlist — a column name is never taken from input).
FIELDS = ("cai_enabled", "money_clearance_enabled", "operators", "channels")
BOOL_FIELDS = ("cai_enabled", "money_clearance_enabled")
MONEY_ACK_PHRASE = "ENABLE MONEY CLEARANCE"
MAX_OPERATORS = 16
MAX_CHANNELS = 16
MAX_REASON = 500
MAX_NAME = 64

# subprocess exit codes app.py maps to HTTP: 2 -> 404, 3 -> 400, else 500
EXIT_NOT_FOUND = 2
EXIT_INVALID = 3


class GovernanceError(ValueError):
    """A validation failure (maps to HTTP 400 / exit 3)."""


class NotFound(LookupError):
    """No project_governance row for that project (HTTP 404 / exit 2)."""


# ------------------------------------------------------------------ pure validators
def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    raise GovernanceError("value must be true or false")


def is_user_chat_id(v: Any) -> bool:
    """A Telegram USER id: a positive integer (as int or digit-string). Group /
    channel ids are negative and can never be an operator — refused by construction."""
    s = str(v).strip() if v is not None else ""
    return bool(_INT_RE.match(s)) and int(s) > 0


def is_group_chat_id(v: Any) -> bool:
    """A Telegram GROUP/channel id: a negative integer. Only these belong in
    `channels` (a DM, where chat_id == from_user_id, is always acceptable)."""
    s = str(v).strip() if v is not None else ""
    return bool(_INT_RE.match(s)) and int(s) < 0


def normalize_operators(v: Any) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            raise GovernanceError("operators must be a JSON array")
    if not isinstance(v, list):
        raise GovernanceError("operators must be an array")
    if len(v) > MAX_OPERATORS:
        raise GovernanceError(f"too many operators (max {MAX_OPERATORS})")
    out, seen = [], set()
    for i, op in enumerate(v):
        if not isinstance(op, dict):
            raise GovernanceError(f"operator #{i + 1}: must be an object")
        name = str(op.get("name") or "").strip()
        chat_id = str(op.get("chat_id") if op.get("chat_id") is not None else "").strip()
        internal = op.get("internal", False)
        if not name or len(name) > MAX_NAME:
            raise GovernanceError(f"operator #{i + 1}: name required (1-{MAX_NAME} chars)")
        if not is_user_chat_id(chat_id):
            raise GovernanceError(
                f"operator '{name}': chat_id must be a positive Telegram USER id "
                "(a group id can never authorize)")
        if not isinstance(internal, bool):
            raise GovernanceError(f"operator '{name}': internal must be true/false")
        if chat_id in seen:
            raise GovernanceError(f"operator '{name}': duplicate chat_id {chat_id}")
        seen.add(chat_id)
        out.append({"name": name, "chat_id": chat_id, "internal": internal})
    return out


def normalize_channels(v: Any) -> list:
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                v = json.loads(s)
            except ValueError:
                raise GovernanceError("channels must be a JSON array")
        else:
            v = [x.strip() for x in s.split(",") if x.strip()]
    if not isinstance(v, list):
        raise GovernanceError("channels must be an array")
    if len(v) > MAX_CHANNELS:
        raise GovernanceError(f"too many channels (max {MAX_CHANNELS})")
    out = []
    for c in v:
        s = str(c).strip()
        if not is_group_chat_id(s):
            raise GovernanceError(
                f"channel '{s}': must be a negative Telegram GROUP chat id "
                "(a DM never needs listing)")
        if s not in out:
            out.append(s)
    return out


def normalize_value(field: str, value: Any) -> Any:
    """Validate + normalize a toggle value for `field`. Raises GovernanceError."""
    if field not in FIELDS:
        raise GovernanceError(f"unknown field '{field}'")
    if field in BOOL_FIELDS:
        return _as_bool(value)
    if field == "operators":
        return normalize_operators(value)
    return normalize_channels(value)


def validate_write(project: str, field: str, value: Any, reason: str,
                   money_ack: str = "") -> Tuple[str, Any, str]:
    """The full pre-write check, shared by the CLI (authoritative) and app.py
    (so a bad request is refused before anything reaches argv). Returns
    (field, normalized_value, reason) or raises GovernanceError."""
    if not project or not PROJECT_RE.match(project):
        raise GovernanceError("bad project name")
    norm = normalize_value(field, value)
    reason = (reason or "").strip()
    if not reason:
        raise GovernanceError("a reason is required (it is recorded in the audit row)")
    if len(reason) > MAX_REASON:
        raise GovernanceError(f"reason too long (max {MAX_REASON})")
    if field == "money_clearance_enabled" and norm is True and (money_ack or "").strip() != MONEY_ACK_PHRASE:
        raise GovernanceError(f"enabling money clearance requires the typed acknowledgement '{MONEY_ACK_PHRASE}'")
    return field, norm, reason


def audit_changed_fields(before: Any, after: Any) -> list:
    """Which of the 4 toggles differ between two audit snapshots (display only)."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ["created"] if before is None else []
    return [f for f in FIELDS if before.get(f) != after.get(f)]


# ------------------------------------------------------------------ DB
def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        # launchd minimal env: load the orchestrator .env (same as console_assign.py).
        try:
            from dotenv import load_dotenv  # type: ignore
            here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            load_dotenv(os.path.join(here, ".env"))
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        except Exception:  # noqa: BLE001
            dsn = None
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return dsn


_ROW_COLS = ("project, cai_enabled, operators, channels, money_clearance_enabled, "
             "(residency_ack IS NOT NULL) AS residency_ack_on_file, updated_by, reason, updated_at")


def _row(cols, r) -> dict:
    d = dict(zip(cols, r))
    if hasattr(d.get("updated_at"), "isoformat"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


def fetch_all(dsn: str, audit_limit: int = 30) -> dict:
    """Every registry row + the most recent audit entries (display shape)."""
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = 8000")
        cur.execute(f"SELECT {_ROW_COLS} FROM project_governance ORDER BY project")
        cols = [d[0] for d in cur.description]
        projects = [_row(cols, r) for r in cur.fetchall()]
        cur.execute(
            "SELECT id, project, before, after, changed_by, reason, changed_at "
            "FROM project_governance_audit ORDER BY id DESC LIMIT %s",
            (max(1, min(int(audit_limit), 200)),))
        audit = []
        for (aid, project, before, after, changed_by, reason, changed_at) in cur.fetchall():
            audit.append({
                "id": aid, "project": project, "changed_by": changed_by, "reason": reason,
                "changed_at": changed_at.isoformat() if hasattr(changed_at, "isoformat") else changed_at,
                "changed": audit_changed_fields(before, after),
                "money_clearance_enabled": (after or {}).get("money_clearance_enabled") if isinstance(after, dict) else None,
            })
    return {"projects": projects, "audit": audit, "fields": list(FIELDS),
            "money_ack_phrase": MONEY_ACK_PHRASE}


def apply_set(dsn: str, project: str, field: str, value: Any, updated_by: str,
              reason: str, money_ack: str = "") -> dict:
    """The write: ONE UPDATE on project_governance, audit row verified, one txn."""
    import psycopg
    from psycopg.types.json import Jsonb
    field, norm, reason = validate_write(project, field, value, reason, money_ack)
    updated_by = (updated_by or "").strip()[:120] or "console"
    param = Jsonb(norm) if field in ("operators", "channels") else norm
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 8000")
            cur.execute(f"SELECT {_ROW_COLS} FROM project_governance WHERE project = %s FOR UPDATE", (project,))
            cols = [d[0] for d in cur.description]
            r = cur.fetchone()
            if r is None:
                raise NotFound(f"no project_governance row for '{project}'")
            before = _row(cols, r)
            # column name from the FIELDS allowlist only (checked in validate_write)
            cur.execute(
                f"UPDATE project_governance SET {field} = %s, updated_by = %s, reason = %s, "
                f"updated_at = now() WHERE project = %s RETURNING {_ROW_COLS}",
                (param, updated_by, reason, project))
            after = _row(cols, cur.fetchone())
            # now() is fixed for the whole transaction, so the trigger's audit row
            # (changed_at = now()) is matched exactly to THIS update, never an older one.
            cur.execute(
                "SELECT id, changed_by, reason FROM project_governance_audit "
                "WHERE project = %s AND changed_at = now() ORDER BY id DESC LIMIT 1",
                (project,))
            a = cur.fetchone()
            if a is None or a[1] != updated_by or a[2] != reason:
                conn.rollback()
                raise RuntimeError("audit trigger did not record this change — rolled back (fail-closed)")
            conn.commit()
    return {"ok": True, "project": project, "field": field, "value": norm,
            "before": before.get(field), "after": after.get(field),
            "audit_id": a[0], "updated_by": updated_by}


# ------------------------------------------------------------------ CLI (what app.py spawns)
_DSN_RE = re.compile(r"postgres(?:ql)?://\S+|password=\S+", re.I)


def _scrub(msg: str) -> str:
    """A driver error can echo connection details; never let a DSN-shaped token
    (or a password= fragment) reach stderr -> app.py -> the browser."""
    return _DSN_RE.sub("<dsn>", msg or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="project_governance read/write (op#20702 Stage E)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("set")
    s.add_argument("--project", required=True)
    s.add_argument("--field", required=True, choices=FIELDS)
    s.add_argument("--value", required=True, help="true|false, or a JSON array for operators/channels")
    s.add_argument("--updated-by", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--money-ack", default="")
    a = ap.parse_args(argv)
    try:
        dsn = _dsn()
        if a.cmd == "list":
            print(json.dumps(fetch_all(dsn), default=str))
            return 0
        res = apply_set(dsn, a.project, a.field, a.value, a.updated_by, a.reason, a.money_ack)
        print(json.dumps(res, default=str))
        return 0
    except NotFound as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return EXIT_NOT_FOUND
    except GovernanceError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return EXIT_INVALID
    except Exception as e:  # noqa: BLE001 — never print the DSN
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {_scrub(str(e))[:200]}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""exec_prod_ledger_shipper.py — ship exec_prod's append-only JSONL ledger to the
fleet bus (queue row 271; hc#95 B4 direct-path half).

WHY: cc-fleet-health's daily reconcile compares the exec broker's
exec_broker_access_log against the DIRECT-path exec_prod ledger for the same
window. That ledger lives only on the exec host
(~/.local/state/wingmen/agent_exec_ledger.jsonl, written by ihsanos-irsyad
scripts/db/exec_prod.py _append_ledger). This copies it, line for line, into
the fleet-bus table exec_prod_ledger (migrations/drafts/exec_prod_ledger.sql)
so the reconcile can row-match them.

RECONCILE KEY (state it exactly; cc-fleet-health bus #89909):
  (exec_prod_ledger.exec_id == exec_broker_access_log.exec_ledger_id,
   project_ref, transport) AND sql_sha256.
sql_sha256 on BOTH sides is the hash of the bytes exec_prod SENT == the bytes
the broker RECEIVED (ihsanos #1220 hashes the exact guarded_sql it sends;
broker #821 keeps sql_sha256 over the received bytes). NEVER compare against
exec_broker_access_log.executed_sql_sha256 -- that is the SEPARATE hash of
what the broker actually ran (received bytes + its read-only prologue on a
gate-free read), and it intentionally differs.

RUN HEARTBEAT: every run that gets past argument parsing upserts ONE row per
host into exec_prod_ledger_shipper_runs at the END of the run -- empty runs
and failed runs included where the DB is reachable (last_run_at, last_ok_at
only on exit 0, cursor_offset, ledger_size, exit_code, shipped_last,
malformed_last, conflicted_last). If the DB itself is the failure the run
exits 1 and the row simply goes stale: a stale/missing run row is the
watchdog's signal.

LEDGER SCHEMA (one JSON object per line): exec_id, ts_start, ts_end, actor,
project_ref, sql_sha256, reason, gate_id, gate_ref, transport, outcome. Every
exec writes a START line (ts_end null) and an END line (ts_end set) sharing
exec_id; line_kind is derived from that.

GUARANTEES
  * Idempotent: INSERT ... ON CONFLICT (host, exec_id, line_kind) DO NOTHING,
    so re-shipping any range is a no-op.
  * Byte-offset cursor (JSON file: offset + inode), written atomically AFTER
    each batch commits. A crash between commit and cursor write just re-ships
    the batch (no-op). Truncation/rotation (cursor offset > file size, or a
    different inode) restarts from 0 -- still idempotent.
  * Only COMPLETE lines (ending in "\\n") are consumed; a partially written
    last line is left for the next run.
  * Malformed lines (bad JSON, not an object, missing/invalid exec_id,
    unparseable timestamp, wrong types) are COUNTED and REPORTED by byte
    offset (never by content -- a line may carry free text), skipped, and make
    the run exit 2. They never crash the run and are never silently dropped.
  * `reason` (operator free text already present in the ledger) is shipped
    truncated to 200 characters; nothing else free-text is added.
  * conflicted = lines attempted - rows actually inserted (sum of
    cur.rowcount), i.e. lines that were already in the table. Reported in the
    summary line and the run row.
  * One run at a time per cursor: an exclusive non-blocking flock on
    <cursor>.lock; a second concurrent run exits 0 without touching anything
    (the running one writes the run row).

CLI (one-shot; schedule with deploy/wingmen-exec-prod-ledger-shipper.timer):
  exec_prod_ledger_shipper.py [--ledger PATH] [--cursor PATH] [--dry-run]
Exit codes: 0 shipped cleanly, 2 shipped but malformed lines were found,
1 refused to run (missing DSN / host identity / DB error).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

DEFAULT_LEDGER = Path.home() / ".local" / "state" / "wingmen" / "agent_exec_ledger.jsonl"
DEFAULT_CURSOR = Path.home() / ".local" / "state" / "wingmen" / "exec_prod_ledger_shipper.cursor.json"
REASON_MAX = 200
BATCH_LINES = 500

_TEXT_FIELDS = ("actor", "project_ref", "sql_sha256", "gate_id", "gate_ref", "transport", "outcome")

INSERT_SQL = (
    "INSERT INTO exec_prod_ledger "
    "(host, exec_id, line_kind, ts_start, ts_end, actor, project_ref, sql_sha256, "
    " reason, gate_id, gate_ref, transport, outcome) "
    "VALUES (%(host)s, %(exec_id)s, %(line_kind)s, %(ts_start)s, %(ts_end)s, %(actor)s, "
    " %(project_ref)s, %(sql_sha256)s, %(reason)s, %(gate_id)s, %(gate_ref)s, %(transport)s, "
    " %(outcome)s) "
    "ON CONFLICT (host, exec_id, line_kind) DO NOTHING"
)


RUN_UPSERT_SQL = (
    "INSERT INTO exec_prod_ledger_shipper_runs "
    "(host, last_run_at, last_ok_at, cursor_offset, ledger_size, exit_code, "
    " shipped_last, malformed_last, conflicted_last) "
    "VALUES (%(host)s, now(), CASE WHEN %(exit_code)s = 0 THEN now() END, %(cursor_offset)s, "
    " %(ledger_size)s, %(exit_code)s, %(shipped)s, %(malformed)s, %(conflicted)s) "
    "ON CONFLICT (host) DO UPDATE SET "
    "  last_run_at = now(), "
    "  last_ok_at = CASE WHEN EXCLUDED.exit_code = 0 THEN now() "
    "                    ELSE exec_prod_ledger_shipper_runs.last_ok_at END, "
    "  cursor_offset = EXCLUDED.cursor_offset, ledger_size = EXCLUDED.ledger_size, "
    "  exit_code = EXCLUDED.exit_code, shipped_last = EXCLUDED.shipped_last, "
    "  malformed_last = EXCLUDED.malformed_last, conflicted_last = EXCLUDED.conflicted_last"
)

# A ledger timestamp must carry a TIME component (cc-fleet-health #89909): a
# date-only value would silently become midnight and mis-window the reconcile.
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class Malformed(ValueError):
    pass


def _ts(value, name: str, *, required: bool) -> "str | None":
    if value is None:
        if required:
            raise Malformed(f"{name} missing")
        return None
    if not isinstance(value, str):
        raise Malformed(f"{name} not a string")
    if not _DATETIME_RE.match(value):
        raise Malformed(f"{name} has no time component")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Malformed(f"{name} not ISO-8601") from None
    return value


def parse_line(raw: bytes, host: str) -> dict:
    """One ledger line -> an INSERT row dict. Raises Malformed."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Malformed("not valid UTF-8 JSON") from None
    if not isinstance(obj, dict):
        raise Malformed("not a JSON object")
    exec_id = obj.get("exec_id")
    if not isinstance(exec_id, str) or not exec_id.strip():
        raise Malformed("exec_id missing or not a non-empty string")
    row = {
        "host": host,
        "exec_id": exec_id,
        "ts_start": _ts(obj.get("ts_start"), "ts_start", required=True),
        "ts_end": _ts(obj.get("ts_end"), "ts_end", required=False),
    }
    row["line_kind"] = "start" if row["ts_end"] is None else "end"
    for key in _TEXT_FIELDS:
        value = obj.get(key)
        if value is not None and not isinstance(value, str):
            raise Malformed(f"{key} not a string")
        row[key] = value
    reason = obj.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise Malformed("reason not a string")
    row["reason"] = reason[:REASON_MAX] if reason is not None else None
    return row


# ── cursor ────────────────────────────────────────────────────────────────────

def read_cursor(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("offset"), int) and data["offset"] >= 0:
            return {"offset": data["offset"], "inode": data.get("inode")}
    except (OSError, ValueError):
        pass
    return {"offset": 0, "inode": None}


def write_cursor(path: Path, offset: int, inode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".shipper-cursor-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"offset": offset, "inode": inode}, f)
    os.replace(tmp, path)


def start_offset(cursor: dict, size: int, inode: int) -> "tuple[int, str | None]":
    """Where to resume, and why a restart happened (None if resuming)."""
    if cursor["inode"] is not None and cursor["inode"] != inode:
        return 0, "rotated (inode changed)"
    if cursor["offset"] > size:
        return 0, "truncated (cursor beyond end of file)"
    return cursor["offset"], None


# ── ship ──────────────────────────────────────────────────────────────────────

@dataclass
class Report:
    shipped_lines: int = 0      # lines attempted (parsed OK and sent to INSERT)
    inserted: int = 0           # rows actually inserted (sum of cur.rowcount)
    ledger_size: "int | None" = None   # None until the ledger was stat()ed
    malformed: list = field(default_factory=list)   # [(byte_offset, reason)]
    restarted: "str | None" = None
    start: int = 0
    end: int = 0

    @property
    def conflicted(self) -> int:
        return self.shipped_lines - self.inserted


def iter_complete_lines(data: bytes, base: int):
    """Yield (absolute_offset, line_bytes_without_newline, next_offset) for every
    COMPLETE line in data; a trailing partial line is not yielded."""
    pos = 0
    while True:
        nl = data.find(b"\n", pos)
        if nl == -1:
            return
        yield base + pos, data[pos:nl], base + nl + 1
        pos = nl + 1


def ship(*, ledger: Path, cursor_path: Path, host: str, conn_factory, dry_run: bool = False,
         report: "Report | None" = None) -> Report:
    """Ship new complete lines. Pass `report` to keep the partial state if this
    raises: report.end is only ever advanced to the end of a COMMITTED batch."""
    report = report if report is not None else Report()
    try:
        st = ledger.stat()
    except FileNotFoundError:
        return report  # nothing written yet -- not an error
    report.ledger_size = st.st_size
    cursor = read_cursor(cursor_path)
    offset, why = start_offset(cursor, st.st_size, st.st_ino)
    report.restarted, report.start, report.end = why, offset, offset
    with ledger.open("rb") as f:
        f.seek(offset)
        data = f.read()
    batch, batch_end = [], offset

    def flush():
        nonlocal batch
        if batch and not dry_run:
            with conn_factory() as conn:
                inserted = 0
                with conn.cursor() as cur:
                    for row in batch:
                        cur.execute(INSERT_SQL, row)
                        inserted += max(getattr(cur, "rowcount", 0) or 0, 0)
                conn.commit()
            report.inserted += inserted
        if not dry_run:
            write_cursor(cursor_path, batch_end, st.st_ino)
        report.shipped_lines += len(batch)
        report.end = batch_end
        batch = []

    for line_offset, raw, next_offset in iter_complete_lines(data, offset):
        if raw.strip():
            try:
                batch.append(parse_line(raw, host))
            except Malformed as e:
                report.malformed.append((line_offset, str(e)))
        batch_end = next_offset
        if len(batch) >= BATCH_LINES:
            flush()
    flush()
    return report


def _conn_factory():
    import psycopg
    from substrate_dsn import dsn_from_env_file

    dsn = dsn_from_env_file(env_path=os.environ.get("EXEC_PROD_LEDGER_SHIPPER_ENV_FILE"))
    return psycopg.connect(dsn)


def write_run_row(conn_factory, *, host: str, exit_code: int, rep: "Report | None") -> bool:
    """Upsert this host's run-heartbeat row. Best effort: returns False (never
    raises) if the DB is unreachable -- the stale row is then the signal."""
    seen = rep is not None and rep.ledger_size is not None
    params = {
        "host": host, "exit_code": exit_code,
        # cursor_offset = end of the last COMMITTED batch (never past a failed one)
        "cursor_offset": rep.end if seen else None, "ledger_size": rep.ledger_size if seen else None,
        "shipped": rep.shipped_lines if rep else None,
        "malformed": len(rep.malformed) if rep else None,
        "conflicted": rep.conflicted if rep else None,
    }
    try:
        with conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(RUN_UPSERT_SQL, params)
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"exec_prod_ledger_shipper: could not write the run row ({e.__class__.__name__})",
              file=sys.stderr)
        return False


def _acquire_lock(cursor_path: Path):
    """Exclusive non-blocking lock next to the cursor; None if another run holds it."""
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(str(cursor_path) + ".lock", "a")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ship exec_prod's JSONL ledger to the fleet bus.")
    ap.add_argument("--ledger", type=Path,
                    default=Path(os.environ.get("AGENT_EXEC_LEDGER_PATH") or DEFAULT_LEDGER))
    ap.add_argument("--cursor", type=Path, default=DEFAULT_CURSOR)
    ap.add_argument("--dry-run", action="store_true", help="parse + report only; no DB write, no cursor move")
    a = ap.parse_args(argv)
    try:
        import fleet_host_id
        host = fleet_host_id.fleet_host_id()
    except Exception as e:  # noqa: BLE001 -- never ship under an unknown identity
        print(f"exec_prod_ledger_shipper: cannot resolve host identity ({e}) -- refusing", file=sys.stderr)
        return 1
    lock = None
    if not a.dry_run:
        lock = _acquire_lock(a.cursor)
        if lock is None:
            print("exec_prod_ledger_shipper: another run holds the lock -- skipping (it writes the run row)")
            return 0
    try:
        rep = Report()
        try:
            ship(ledger=a.ledger, cursor_path=a.cursor, host=host,
                 conn_factory=_conn_factory, dry_run=a.dry_run, report=rep)
        except Exception as e:  # noqa: BLE001 -- report the class only (never a DSN)
            print(f"exec_prod_ledger_shipper: ship failed ({e.__class__.__name__}) -- cursor not advanced "
                  "past the failed batch", file=sys.stderr)
            if not a.dry_run:
                # whatever is already known: committed cursor_offset, ledger_size, counts so far
                write_run_row(_conn_factory, host=host, exit_code=1, rep=rep)
            return 1
        code = 2 if rep.malformed else 0
        print(f"exec_prod_ledger_shipper: host={host} bytes {rep.start}..{rep.end} "
              f"ledger_size={rep.ledger_size} shipped_lines={rep.shipped_lines} "
              # dry-run attempts no INSERT, so inserted/conflicted are not knowable
              + ("inserted=n/a conflicted=n/a" if a.dry_run
                 else f"inserted={rep.inserted} conflicted={rep.conflicted}")
              + f" malformed={len(rep.malformed)}"
              + (f" restart={rep.restarted}" if rep.restarted else "")
              + (" (dry-run)" if a.dry_run else ""))
        for off, why in rep.malformed:
            print(f"exec_prod_ledger_shipper: MALFORMED line at byte {off}: {why}", file=sys.stderr)
        if not a.dry_run and not write_run_row(_conn_factory, host=host, exit_code=code, rep=rep):
            return 1
        return code
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())

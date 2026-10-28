"""snapshot.py — the verified-snapshot half of #80298 condition 1.

The rendered revert refuses to drop client tables that hold rows unless a
marker row in public.provisioner_revert_snapshots matches the LIVE row
counts. This module is the only writer of that marker, and it earns the
write: it exports each table via COPY (deterministic ORDER BY), writes the
bytes to a 0600 file inside a 0700 dir, RE-READS the file from disk, and
requires (a) byte-identical re-read, (b) sha256 of those bytes, (c) the
COPY line count to equal the SQL count — all before the marker INSERT,
which lands in the SAME store transaction as the revert itself. Anything
short of that raises and the revert never runs.

The files are tenant data on disk (that is their job — they are the
recovery copy); the marker row carries ops metadata ONLY (counts, sha256s,
path, timestamp — TENANT-RESIDENCY-001 posture, never a DSN).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

from .spec import ClientSpec

# psycopg3's COPY TO surfaces the raw row stream WITHOUT the "\." trailer
# (an empty table reads as zero bytes), and COPY FROM accepts a
# trailer-less stream — so the row count is simply the newline count.
def _count_lines(data: bytes) -> int:
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    return len(lines)


class SnapshotRefused(Exception):
    """Fail-closed: the revert cannot proceed without a verified snapshot."""


def snapshot_dir(base: Path) -> Path:
    d = base / "provisioner_snapshots"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (d.stat().st_mode & 0o777) != 0o700:
        os.chmod(d, 0o700)
    return d


def _copy_bytes(cur: psycopg.Cursor, table: str, order_by: str) -> bytes:
    with cur.copy(f"COPY (SELECT * FROM public.{table} ORDER BY {order_by}) TO STDOUT") as cp:
        # iterate, not read(): Copy.read() returns one chunk and leaves the
        # protocol busy — iteration drains the whole stream
        return b"".join(bytes(chunk) for chunk in cp)


def _count_lines(data: bytes) -> int:
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    return len(lines)


def _write_0600(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def ensure_verified_snapshot(cur: psycopg.Cursor, spec: ClientSpec,
                             base_dir: Path) -> dict:
    """Called by the daemon inside the revert's store transaction, right
    before bundle.revert_sql executes (whose own guard re-checks the marker
    against live counts). No-op when the client tables hold no rows.

    Returns {'content_rows': n, 'history_rows': n, 'skipped': bool} — ops
    metadata, safe to log."""
    counts: dict[str, int] = {}
    for table in (spec.content_table, spec.history_table):
        exists = cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)).fetchone()[0]
        counts[table] = (cur.execute(
            f"SELECT count(*) FROM public.{table}").fetchone()[0]  # noqa: S608 - fixed identifier
            if exists else 0)
    content_rows, history_rows = counts[spec.content_table], counts[spec.history_table]
    if content_rows == 0 and history_rows == 0:
        return {"content_rows": 0, "history_rows": 0, "skipped": True}

    order = {spec.content_table: "org_id, key", spec.history_table: "id"}
    shas: dict[str, str] = {}
    # O_EXCL filenames must be collision-proof: two snapshots of the same
    # client inside one epoch-second (a retried revert) would otherwise
    # collide and turn a verified write into an OSError.
    stamp = (f"{spec.client_key}_{int(cur.execute('SELECT extract(epoch from now())').fetchone()[0])}"
             f"_{os.urandom(4).hex()}")
    out_dir = snapshot_dir(base_dir)
    for table in (spec.content_table, spec.history_table):
        if counts[table] == 0:
            shas[table] = hashlib.sha256(b"").hexdigest()
            continue
        data = _copy_bytes(cur, table, order[table])
        path = out_dir / f"{stamp}.{table}.copy"
        _write_0600(path, data)
        # the verification IS the re-read: bytes on disk must equal the
        # export, and its line count must equal the SQL count
        on_disk = path.read_bytes()
        if on_disk != data:
            raise SnapshotRefused(f"snapshot re-read mismatch for {table} — refusing")
        if _count_lines(on_disk) != counts[table]:
            raise SnapshotRefused(
                f"snapshot line count {_count_lines(on_disk)} != SQL count "
                f"{counts[table]} for {table} — refusing")
        shas[table] = hashlib.sha256(on_disk).hexdigest()

    cur.execute(
        "INSERT INTO public.provisioner_revert_snapshots "
        "(client_key, content_rows, history_rows, content_sha256, history_sha256, snapshot_path) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (spec.client_key, content_rows, history_rows, shas[spec.content_table],
         shas[spec.history_table], str(out_dir / stamp)))
    return {"content_rows": content_rows, "history_rows": history_rows, "skipped": False}

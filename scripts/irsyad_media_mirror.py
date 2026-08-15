#!/usr/bin/env python3
"""irsyad_media_mirror.py — pull hub-downloaded client media onto the Mini.

WHY (cross-host locality — see reports/media-download-fix.md):
The `gazzabyte-irsyad` Telegram channel is polled by the HUB's ingest (on the VPS),
so a client PDF/screenshot/xlsx is DOWNLOADED to the HUB's disk and the durable
`operator_messages` row records the HUB path
(`/home/wingmen/wingmen/orchestrator/logs/tg_media/<name>`). But the reader agent
`cc-irsyad-coord` runs on the MINI, where that Linux path does not exist — so coord
cannot open the client's attachments. The row carries ONLY that path string; there is
NO Telegram `file_id` persisted anywhere (schema: id, direction, channel, chat_id,
tag, text, delivered, created_at, handled_at, from_user_id, from_username, from_name,
cos_triage), so a Mini-side getFile re-fetch is not possible without a hub change.

WHAT IT DOES: for each new inbound `gazzabyte-irsyad` media row it extracts the hub
filename from the row text and `scp`s the file from the VPS over tailscale
(root@$WINGMEN_VPS_HOST, key $WINGMEN_VPS_KEY — same creds as reset_hub_remote.sh)
into a COLLISION-SAFE Mini path:

    logs/tg_media/gazzabyte-irsyad/<msg_id>-<basename>

The subdir + `<msg_id>-` prefix are deliberate: the hub's counter-based names
(`photos_file_117.jpg`) collide with the Mini's own nazim-console downloads, so a
naive same-dir copy would clobber local files (reports/media-download-fix.md §3).
It NEVER writes to the shared `logs/tg_media/` root and NEVER touches nazim-console.

Idempotent: skips a file already mirrored at the right non-zero size. Best-effort:
a scp failure is logged and skipped, never fatal — the client-wake path (the shadow
watcher that calls this) must survive any mirror problem.

HAND-OFF TO coord: mirroring is automatic, but coord resolves a hub path from a row
to the readable local path with:

    scripts/irsyad_media_mirror.py --resolve '<hub path OR msg_id>'

which prints the local path (pulling on-demand if the auto-mirror has not caught up
yet). This respects the count-only nudge contract (CAI-RESP-357): no payload rides
the nudge and the durable row is never rewritten (the hub still reads its own path).

CLI:
    irsyad_media_mirror.py                 # mirror new rows since the saved offset
    irsyad_media_mirror.py --backfill 20   # mirror the last N media rows now
    irsyad_media_mirror.py --resolve X     # print/pull the local path for a hub path or msg id
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

import psycopg

ORCH = pathlib.Path(os.environ.get("ORCH_DIR", pathlib.Path.home() / "wingmen/orchestrator"))
CLIENT_TAG = "gazzabyte-irsyad"

# Same VPS creds as scripts/reset_hub_remote.sh (root@ over tailscale, dedicated key).
VPS_HOST = os.environ.get("WINGMEN_VPS_HOST", "91.107.235.77")
VPS_KEY = os.environ.get("WINGMEN_VPS_KEY", str(pathlib.Path.home() / ".ssh/wingmen_vps"))
VPS_USER = os.environ.get("WINGMEN_VPS_USER", "root")
# Canonical hub tg_media dir. Row paths embed a `nervous_system/../logs/tg_media`
# relative hop; we anchor on the basename and rebuild the dir to avoid `..` in scp.
HUB_MEDIA_DIR = os.environ.get(
    "WINGMEN_HUB_MEDIA_DIR", "/home/wingmen/wingmen/orchestrator/logs/tg_media")

MIRROR_DIR = ORCH / "logs" / "tg_media" / CLIENT_TAG
MANIFEST = MIRROR_DIR / "_mirror_manifest.jsonl"
OFFSET = ORCH / "logs" / ".irsyad_mirror_offset"

# Anchor on the media dir substring and capture the filename up to the caption
# delimiter ("  | caption:", added by ingest) or end-of-string. Filenames may
# contain spaces (e.g. "DMS - Dec 2025 _Example_.xlsx"), so we cannot stop at a space.
_PATH_RE = re.compile(r"/logs/tg_media/(?P<name>.+?)(?:\s+\|\s+caption:.*)?\Z", re.S)

# SECURITY: a document's filename is CLIENT-CONTROLLED (Telegram forwards the sender's
# file_name), and it ends up in the scp REMOTE path, which the remote shell on the VPS
# (root) expands. A basename with shell metacharacters (`;`, `$`, backtick, `|`, `&`,
# quotes, newlines, `*?[]`, ...) could inject into that remote command. We therefore
# (a) allowlist the basename to a safe set before it can reach scp, skipping anything
# else, and (b) shlex.quote the remote path as defense-in-depth (also fixes spaces —
# an unquoted space would word-split in the remote shell). Allowed: letters, digits,
# space, and . _ - ( ) — covers real hub names ("photos_file_117.jpg", "RCP-000002.pdf",
# "DMS - Dec 2025 _Example_.xlsx") while rejecting every shell-active character.
_SAFE_BASENAME_RE = re.compile(r"\A[A-Za-z0-9 ._()\-]+\Z")


def _is_safe_basename(name: str) -> bool:
    return bool(name) and ".." not in name and _SAFE_BASENAME_RE.match(name) is not None


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.exit("DATABASE_URL not set")
    return dsn


def log(msg: str) -> None:
    # stderr so `--resolve` keeps stdout clean (only the local path) for callers.
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} irsyad-mirror: {msg}",
          file=sys.stderr, flush=True)


def parse_hub_basename(text: str) -> str | None:
    """Extract the media FILENAME from a row's text, or None if it carries no media.

    Only the basename is used — the hub dir is rebuilt from HUB_MEDIA_DIR — so a
    `nervous_system/../logs/tg_media` relative hop in the row cannot break scp.
    A failed-download marker ("download failed: …") has no path and returns None.
    """
    if "/logs/tg_media/" not in text or "download failed" in text:
        return None
    m = _PATH_RE.search(text)
    if not m:
        return None
    name = m.group("name").splitlines()[0].strip()
    # Guard against any stray path separators leaking into the basename.
    name = os.path.basename(name)
    if not name:
        return None
    # SECURITY gate: never let a client-controlled name with shell-active characters
    # reach the scp remote path. Skip (loudly) rather than mirror an unsafe name.
    if not _is_safe_basename(name):
        log(f"SKIP unsafe basename (rejected by allowlist): {name!r}")
        return None
    return name


def local_dest(msg_id: int, basename: str) -> pathlib.Path:
    return MIRROR_DIR / f"{msg_id}-{basename}"


def _scp_pull(basename: str, dest: pathlib.Path, timeout: int = 90) -> bool:
    # Remote path is passed RAW (not shell-quoted): the VPS runs modern OpenSSH scp
    # (SFTP protocol, no remote shell), so (a) there is NO remote-shell path expansion
    # to inject into — verified empirically — and (b) spaces in the name work as-is,
    # whereas adding shlex quotes makes them LITERAL and breaks the fetch. Injection
    # safety therefore rests on the _is_safe_basename allowlist in parse_hub_basename
    # (protocol-independent — it rejects every shell-active char before scp is reached),
    # not on quoting. `-O` is deliberately NOT set (that would force the legacy remote-
    # shell protocol and reintroduce the split/inject concern). Belt: re-assert safety.
    if not _is_safe_basename(basename):
        log(f"REFUSE scp for unsafe basename: {basename!r}")
        return False
    remote = f"{VPS_USER}@{VPS_HOST}:{HUB_MEDIA_DIR}/{basename}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    cmd = ["scp", "-i", VPS_KEY, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
           remote, str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log(f"scp error for {basename!r}: {exc}")
        _cleanup(tmp)
        return False
    if r.returncode != 0:
        log(f"scp failed rc={r.returncode} for {basename!r}: {r.stderr.strip()}")
        _cleanup(tmp)
        return False
    if not tmp.exists() or tmp.stat().st_size == 0:
        log(f"scp produced empty/no file for {basename!r}")
        _cleanup(tmp)
        return False
    tmp.replace(dest)  # atomic swap so a reader never sees a partial file
    return True


def _cleanup(p: pathlib.Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _manifest_append(row: dict) -> None:
    try:
        MIRROR_DIR.mkdir(parents=True, exist_ok=True)
        with MANIFEST.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        log(f"manifest append failed: {exc}")


def _manifest_lookup(msg_id: int | None, basename: str | None) -> pathlib.Path | None:
    if not MANIFEST.exists():
        return None
    hit = None
    try:
        for line in MANIFEST.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if msg_id is not None and rec.get("msg_id") == msg_id:
                hit = rec
            elif msg_id is None and basename and rec.get("basename") == basename:
                hit = rec  # last one wins
    except OSError:
        return None
    if hit:
        p = pathlib.Path(hit["local_path"])
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def mirror_row(msg_id: int, text: str) -> pathlib.Path | None:
    """Mirror one row's media to the Mini. Idempotent; returns local path or None."""
    basename = parse_hub_basename(text)
    if not basename:
        return None
    dest = local_dest(msg_id, basename)
    if dest.exists() and dest.stat().st_size > 0:
        return dest  # already mirrored
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    if not _scp_pull(basename, dest):
        return None
    size = dest.stat().st_size
    _manifest_append({
        "msg_id": msg_id,
        "basename": basename,
        "hub_path": f"{HUB_MEDIA_DIR}/{basename}",
        "local_path": str(dest),
        "size": size,
        "md5": hashlib.md5(dest.read_bytes()).hexdigest(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    log(f"mirrored row {msg_id}: {basename} ({size} bytes) -> {dest}")
    return dest


def _fetch_rows(conn, where: str, params: tuple) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, text FROM operator_messages "
            f"WHERE direction='inbound' AND tag=%s AND {where} ORDER BY id",
            (CLIENT_TAG, *params))
        return cur.fetchall()


def mirror_since(conn, since_id: int) -> tuple[int, int]:
    """Mirror every media row with id > since_id. Returns (n_mirrored, max_id_seen)."""
    rows = _fetch_rows(conn, "id > %s", (since_id,))
    n = 0
    max_id = since_id
    for msg_id, text in rows:
        max_id = max(max_id, msg_id)
        try:
            if mirror_row(msg_id, text or ""):
                n += 1
        except Exception as exc:  # noqa: BLE001 — one bad row never stops the batch
            log(f"mirror_row {msg_id} raised (skipped): {exc}")
    return n, max_id


def read_offset() -> int:
    try:
        return int(OFFSET.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_offset(value: int) -> None:
    try:
        OFFSET.write_text(str(value))
    except OSError as exc:
        log(f"offset write failed: {exc}")


def run_since_offset(conn) -> tuple[int, int]:
    """Mirror new rows since the persisted offset and advance it. Used by the
    shadow watcher each loop. First-ever run seeds the offset to the current max
    so we mirror only NEW media going forward (parity with the watcher's own
    'don't replay history' boot). Use --backfill to pull recent history on demand."""
    off = read_offset()
    if off == 0:
        with conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(id),0) FROM operator_messages WHERE tag=%s",
                        (CLIENT_TAG,))
            off = cur.fetchone()[0]
        write_offset(off)
        log(f"first run — mirror offset seeded at id {off} (no history backfill)")
        return 0, off
    n, max_id = mirror_since(conn, off)
    if max_id > off:
        write_offset(max_id)
    if n:
        log(f"mirrored {n} new media file(s) through id {max_id}")
    return n, max_id


def backfill(conn, limit: int) -> int:
    """Mirror the last `limit` inbound media rows regardless of offset (on-demand)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, text FROM operator_messages WHERE direction='inbound' AND tag=%s "
            "AND text LIKE '%%/logs/tg_media/%%' AND text NOT LIKE '%%download failed%%' "
            "ORDER BY id DESC LIMIT %s", (CLIENT_TAG, limit))
        rows = list(reversed(cur.fetchall()))
    n = 0
    for msg_id, text in rows:
        if mirror_row(msg_id, text or ""):
            n += 1
    log(f"backfill: mirrored {n}/{len(rows)} recent media row(s)")
    return n


def resolve(conn, token: str) -> pathlib.Path | None:
    """Resolve a hub path or msg id to a readable local path, pulling on-demand
    if not yet mirrored. This is coord's hand-off entry point."""
    token = token.strip()
    msg_id = int(token) if token.isdigit() else None
    basename = None if msg_id is not None else parse_hub_basename(token) or os.path.basename(token)

    hit = _manifest_lookup(msg_id, basename)
    if hit:
        return hit

    # Not mirrored yet — pull it now from the row (authoritative text) or the token.
    if msg_id is not None:
        rows = _fetch_rows(conn, "id = %s", (msg_id,))
        if rows:
            return mirror_row(rows[0][0], rows[0][1] or "")
        return None
    # Given a raw hub path with no id: find the newest row carrying that basename.
    rows = _fetch_rows(conn, "text LIKE %s", (f"%/logs/tg_media/{basename}%",))
    if rows:
        rid = rows[-1][0]
        return mirror_row(rid, rows[-1][1] or "")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Mirror gazzabyte-irsyad hub media to the Mini.")
    ap.add_argument("--backfill", type=int, metavar="N",
                    help="mirror the last N media rows now (ignores offset)")
    ap.add_argument("--resolve", metavar="HUBPATH_OR_ID",
                    help="print the local path for a hub path or msg id (pull on-demand)")
    args = ap.parse_args()
    with psycopg.connect(_dsn(), connect_timeout=15) as conn:
        if args.resolve:
            p = resolve(conn, args.resolve)
            if p:
                print(p)
                sys.exit(0)
            print("", file=sys.stderr)
            sys.exit(4)
        elif args.backfill:
            backfill(conn, args.backfill)
        else:
            run_since_offset(conn)


if __name__ == "__main__":
    main()

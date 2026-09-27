"""test_ingest_channels_enabled_false.py — enforce-in-code guard against the
dual-poller 409 class (op#22521, bus #43775/#43833).

nervous_system/ingest.py's load_channels(): if INGEST_CHANNELS is set, that
daemon polls exactly those channel_keys REGARDLESS of bot_channels.enabled;
otherwise it polls `SELECT ... WHERE enabled`. The Mini's nazim-ingest
(scripts/boot_nazim_ingest.sh) sets INGEST_CHANNELS to a pinned list. Any
channel in that pinned list must ship enabled=false in its migration, or the
Mini (which polls it regardless) and the gzb hub (which polls every `enabled`
row, no INGEST_CHANNELS scoping) both long-poll the same bot token at once --
the exact 409 class 072 originally shipped for 'oeh' (bus #43775's
enabled=true), corrected at #43833.

This is a STATIC check over migrations/*.sql -- no DB, no ephemeral harness.
It parses only the specific, consistent bot_channels INSERT shape all three
migrations that create these rows (069/070/072) share: an explicit column
list followed by a single-row VALUES tuple in the same column order. A
migration using a different shape won't be found here and this test will
silently miss it -- if that ever happens, extend the parser rather than
trust the gap away.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
INGEST_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "boot_nazim_ingest.sh"

_INSERT_RE = re.compile(
    r"INSERT INTO public\.bot_channels\s*"
    r"\(\s*channel_key\s*,\s*token_env_key\s*,\s*mode\s*,\s*inject_target\s*,\s*"
    r"inject_prefix\s*,\s*responder_ref\s*,\s*allowed_chat_ids\s*,\s*allowed_usernames\s*,\s*"
    r"group_routing\s*,\s*channel_tag\s*,\s*log_target\s*,\s*enabled\s*,\s*poll_offset\s*\)\s*"
    r"VALUES\s*\(\s*'([^']+)'",
    re.IGNORECASE,
)


def _pinned_ingest_channels() -> frozenset[str]:
    text = INGEST_SCRIPT.read_text()
    m = re.search(r'INGEST_CHANNELS="([^"]+)"', text)
    assert m, "boot_nazim_ingest.sh no longer sets INGEST_CHANNELS -- update this test"
    return frozenset(t.strip() for t in m.group(1).split(",") if t.strip())


def _channel_key_and_enabled(sql_text: str, channel_key: str) -> bool | None:
    """Find the bot_channels INSERT for `channel_key` and return its `enabled`
    literal (True/False), or None if this file doesn't insert that channel."""
    m = _INSERT_RE.search(sql_text)
    while m:
        if m.group(1) == channel_key:
            tail = sql_text[m.end():m.end() + 400]
            enabled_m = re.search(r"\btrue\b|\bfalse\b", tail, re.IGNORECASE)
            assert enabled_m, (
                f"could not locate the `enabled` literal after the '{channel_key}' "
                "bot_channels INSERT -- update the parser, don't skip the check"
            )
            return enabled_m.group(0).lower() == "true"
        m = _INSERT_RE.search(sql_text, m.end())
    return None


def test_every_pinned_ingest_channel_ships_enabled_false():
    pinned = _pinned_ingest_channels()
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert sql_files, "no migrations found -- MIGRATIONS_DIR is wrong"

    checked = set()
    for channel_key in pinned:
        if channel_key == "nazim-console":
            # Nazim's own DM channel -- no bot_channels row (not agent-session
            # injected into a client lane); boot_nazim_ingest.sh's own header
            # already documents this one's enabled=false-by-convention directly.
            continue
        found_enabled = None
        for path in sql_files:
            result = _channel_key_and_enabled(path.read_text(), channel_key)
            if result is not None:
                found_enabled = result  # last write wins if a later migration touches it
        if found_enabled is None:
            # Not every pinned channel necessarily has a bot_channels row from
            # this exact INSERT shape (e.g. it may predate this migration
            # convention) -- nothing to assert against, so skip rather than fail.
            continue
        checked.add(channel_key)
        assert found_enabled is False, (
            f"'{channel_key}' is pinned in boot_nazim_ingest.sh INGEST_CHANNELS "
            f"but ships enabled=true in its migration -- this creates the "
            f"dual-poller 409 class (Mini polls it via INGEST_CHANNELS regardless "
            f"of `enabled`; the gzb hub polls every `enabled` row with no "
            f"INGEST_CHANNELS scoping, so both would long-poll the same bot "
            f"token). Ship enabled=false (bus #43833)."
        )

    # Sanity: this test isn't vacuous -- it must have actually checked at least
    # the channels we know ship via this INSERT shape (oeh, cosem-tdu, angullia).
    assert {"oeh", "cosem-tdu", "angullia"} <= checked

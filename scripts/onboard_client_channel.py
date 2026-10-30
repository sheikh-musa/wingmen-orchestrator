#!/usr/bin/env python3
"""onboard_client_channel.py — render a gated bot_channels onboarding migration
(+ INGEST_CHANNELS pin diff when --host mini) for a new client/internal Telegram
channel, in ONE request instead of hand-authoring SQL + hand-checking every
invariant every time (TG2.0 onboarding-automation, op#31450/#31473, design at
reports/tg2.0-client-onboarding-design-20261017/design.md, section 1).

This tool RENDERS and VALIDATES only — it never applies, enables, or posts a
gate request itself. The human/lane running it still takes the printed
migration + sha256 to orch-console's gate, then runs the usual
`apply_migration.py --gate` on approval. No new approval mechanism (reusing the
one that already has a paper trail).

Checks enforced here (refuse, don't warn — a config gap must never be the
thing that exposes an outside party):

  - channel_key must not already exist.
  - SHARED-TOKEN PER-CHANNEL ISOLATION (Musa's settled model, op#33571,
    2026-10-24 — supersedes the 2026-10-19 dedicated-token ruling bus
    #71813): ONE shared bot, tenant isolation PER-CHANNEL in bot_channels.
    A client channel MAY share a token with siblings; what this tool now
    enforces instead of token dedication:
      * the new channel's allowed_chat_ids must not overlap ANY sibling
        channel's allowed_chat_ids on the same token (two channels bound to
        the same chat is the cross-wire class);
      * --self-description, when given, must not contain any other
        channel_key (mechanical sibling-name-leak guard — the leak the old
        ruling existed to kill; Refinement #4: the responder describes
        itself from the channel's own row, so the row must be clean).
  - PINNED/ENABLED CONSISTENCY per token_env_key: every channel sharing a
    token must be uniformly Mini-pinned (INGEST_CHANNELS) or uniformly
    hub-managed (enabled=true, no pin) -- never mixed, which is the exact
    dual-poller 409 class (bus #43775/#70399/#70656).
  - --phase direct is blocked when len(chat_ids) > 1 (lane_reply.sh's direct
    mode always sends to chat_ids[0] today -- bus #70469 dependency, not yet
    fixed) and, for --audience client, requires the separate
    --override-to-direct flag + its own --override-gate-citation
    (Refinement #2: a client-facing floor change needs its own cited grant,
    not just a flag flip).
  - --store is validated against docs/data-store-registry.md (a project ref
    token must appear verbatim), or the literal 'none' opt-out for a
    pure-routing client with no product data store yet (Refinement #3;
    TENANT-RESIDENCY-001/LAYER-VOCAB-001).
  - The tool has NO --enabled flag at all -- every rendered row ships
    enabled=false; enabling is a deliberate, separate step after the gate
    grants it (same two-step pattern migrations 110->111->112 proved out).

Still open / NOT enforced here (flagged, not hidden):
  - Section 3's PreToolUse `powers` guard does not exist yet -- `powers` is
    recorded in group_routing as the correct least-privilege value for when
    it ships, but nothing reads/enforces it today.
  - Section 6's auto-notify + one-tap-approve flow is separate, unbuilt.
  - Refinement #2's audit-log requirement for --override-to-direct is
    satisfied here via --override-gate-citation baked into the rendered
    migration's own header + group_routing (an append-only, ledgered
    artifact once applied) -- NOT a write into the client-silo `audit_log`
    table, which is org-scoped (NOT NULL org_id uuid) and does not fit a
    substrate-level routing-config change. If a dedicated substrate-level
    append-only log is wanted for this, that's a separate decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path

ORCH_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ORCH_DIR / "migrations"
INGEST_SCRIPT = ORCH_DIR / "scripts" / "boot_nazim_ingest.sh"
OPERATOR_LOG = ORCH_DIR / "nervous_system" / "operator_log.py"
REGISTRY_DOC = ORCH_DIR / "docs" / "data-store-registry.md"
SUBSTRATE_SILO = "tscuymavysscrvoberrr"
LIVE_CHECKOUT = Path.home() / "wingmen" / "orchestrator"

# op#34846: t.me deep links need the BOT's public username. The map covers
# the bots this fleet actually shares today; anything else must pass
# --bot-username explicitly (refuse rather than render a link-less invite).
KNOWN_BOT_USERNAMES = {
    "WINGMENDEVBOT_TOKEN": "wingmendevbot",
}

_INSERT_COLUMNS = (
    "channel_key, token_env_key, mode, inject_target, inject_prefix, responder_ref,\n"
    "   allowed_chat_ids, allowed_usernames, group_routing, channel_tag, log_target, enabled, poll_offset,\n"
    "   audience, owner_lane"
)


def fail(msg: str) -> None:
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel-key", required=True)
    p.add_argument("--chat-id", type=int, action="append", default=None, dest="chat_ids",
                   help="allowed chat id (repeatable; positive = DM, negative = group); OMIT with --invite — the first /start on the deep link binds the chat")
    p.add_argument("--token-env-key", required=False)
    p.add_argument("--tag", help="channel_tag; defaults to --channel-key")
    p.add_argument("--mode", choices=["log-and-route", "agent-session"], required=False)
    p.add_argument("--phase", choices=["drill", "supervised", "direct"], default=None,
                    help="group_routing.agent_phase; defaults to supervised for client, direct for internal")
    p.add_argument("--audience", choices=["client", "internal"], required=False)
    p.add_argument("--owner-lane", default=None)
    p.add_argument("--store", required=False, help="exact store name + project ref, or literal 'none'")
    p.add_argument("--powers", default="reply_text", help="comma list, default least-privilege reply_text")
    p.add_argument("--self-description", default=None,
                    help="per-channel responder self-description (Refinement #4); refused if it names any sibling channel")
    p.add_argument("--agent-reviewer", default=None)
    p.add_argument("--host", choices=["mini", "hub"], required=False)
    p.add_argument("--override-to-direct", action="store_true")
    p.add_argument("--override-gate-citation", default=None, help="bus id of the SEPARATE gate granting the override")
    p.add_argument("--inject-target", default=None)
    p.add_argument("--inject-prefix", default=None)
    p.add_argument("--responder-ref", default=None)
    p.add_argument("--sensitive-data", choices=["true", "false"], default="true")
    p.add_argument("--dry-run", action="store_true", help="print the rendered migration, write nothing")
    p.add_argument("--invite", action="store_true",
                   help="op#34846 self-serve onboarding: render with an EMPTY allowlist + a "
                        "single-use, expiring invite (hash at rest only) the first '/start <token>' "
                        "on the bot binds the chat to")
    p.add_argument("--invite-ttl-hours", type=int, default=72,
                   help="invite expiry in hours from APPLY time (default 72)")
    p.add_argument("--reinvite", action="store_true",
                   help="mint a FRESH single-use invite for an EXISTING channel and change nothing "
                        "else: no migration, no channel-row write, no boot-script edit. Only "
                        "--channel-key (and optionally --invite-ttl-hours / --owner-lane / "
                        "--bot-username) are read; every other onboarding flag is ignored")
    p.add_argument("--bot-username", default=None,
                   help="bot's public t.me username for the invite deep link; required for "
                        "token env keys not in the known map")
    args = p.parse_args(argv)
    if args.reinvite and args.invite:
        p.error("--reinvite and --invite are mutually exclusive: --invite onboards a NEW channel, "
                "--reinvite mints another link for an EXISTING one")
    if not args.reinvite:
        # the onboarding flags stay mandatory for every other mode; argparse
        # cannot express "required unless --reinvite", so validate here and
        # keep the same loud refusal a missing flag has always produced
        missing = [f"--{name.replace('_', '-')}"
                   for name in ("token_env_key", "mode", "audience", "store", "host")
                   if getattr(args, name) in (None, "")]
        if missing:
            p.error("the following arguments are required: " + ", ".join(missing))
    return args


def connect():
    import psycopg2
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        fail("DATABASE_URL not set")
    return psycopg2.connect(dsn)


def check_channel_key_free(cur, channel_key: str) -> None:
    cur.execute("SELECT 1 FROM bot_channels WHERE channel_key=%s", (channel_key,))
    if cur.fetchone():
        fail(f"channel_key {channel_key!r} already exists in bot_channels")


def check_token_isolation(cur, token_env_key: str, channel_key: str, chat_ids: list[int]) -> None:
    """op#33571 settled model: shared token allowed; isolation is per-channel.

    Enforces (a) no allowed_chat_ids overlap between channels sharing a token,
    (b) caller-side sibling-leak guard for self-descriptions.
    """
    cur.execute(
        "SELECT channel_key, allowed_chat_ids FROM bot_channels WHERE token_env_key=%s AND channel_key != %s",
        (token_env_key, channel_key),
    )
    new_set = set(chat_ids)
    for sib_key, sib_chats in cur.fetchall():
        overlap = new_set & set(sib_chats or [])
        if overlap:
            fail(
                f"allowed_chat_ids {sorted(overlap)} already bound to sibling channel "
                f"{sib_key!r} on {token_env_key} -- two channels on one token may never "
                f"share a chat (per-channel isolation, Musa op#33571)."
            )


def check_self_description(cur, channel_key: str, self_description: str) -> None:
    cur.execute("SELECT channel_key FROM bot_channels WHERE channel_key != %s", (channel_key,))
    siblings = [k for (k,) in cur.fetchall()]
    leaked = [s for s in siblings if s and s in self_description.lower()]
    if leaked:
        fail(
            f"--self-description names sibling channel(s) {leaked} -- the responder "
            f"describes itself from this row (Refinement #4); sibling names in it are "
            f"exactly the leak class op#33571's per-channel isolation exists to kill."
        )


def pinned_channels() -> list[str]:
    text = INGEST_SCRIPT.read_text()
    m = re.search(r'INGEST_CHANNELS="([^"]+)"', text)
    if not m:
        fail("boot_nazim_ingest.sh has no INGEST_CHANNELS= line -- update this tool, invariant may have changed")
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def check_host_token_consistency(cur, token_env_key: str, host: str, channel_key: str) -> None:
    cur.execute(
        "SELECT channel_key, enabled FROM bot_channels WHERE token_env_key=%s AND channel_key != %s",
        (token_env_key, channel_key),
    )
    siblings = cur.fetchall()
    pinned = set(pinned_channels())
    sibling_pinned = [k for k, _ in siblings if k in pinned]
    sibling_hub_enabled = [k for k, en in siblings if k not in pinned and en]
    if host == "hub" and sibling_pinned:
        fail(
            f"{token_env_key} has Mini-pinned sibling channel(s) {sibling_pinned} -- a new "
            f"--host hub channel on this token would dual-poll against the Mini. Use "
            f"--host mini to join the existing pin instead."
        )
    if host == "mini" and sibling_hub_enabled:
        fail(
            f"{token_env_key} has hub-enabled (not pinned) sibling channel(s) "
            f"{sibling_hub_enabled} -- pinning a new channel to the Mini on this token would "
            f"dual-poll against the hub. Either give this channel its own token, or move the "
            f"existing sibling(s) onto the pin first (a separate change)."
        )


def resolve_phase(args) -> str:
    phase = args.phase
    if phase is None:
        phase = "supervised" if args.audience == "client" else "direct"
    if phase == "direct":
        if len(args.chat_ids) > 1:
            fail(
                "--phase direct is blocked for a channel with more than one allowed_chat_ids "
                "entry -- lane_reply.sh's direct mode always sends to chat_ids[0] today (bus "
                "#70469 dependency, not yet fixed). Single-chat_id direct is unaffected."
            )
        if args.audience == "client" and not args.override_to_direct:
            fail(
                "--audience client defaults to supervised; --phase direct requires the "
                "separate --override-to-direct flag (its own gate citation, stricter sign-off "
                "-- design section 2)."
            )
        if args.override_to_direct and not args.override_gate_citation:
            fail(
                "--override-to-direct requires --override-gate-citation <bus-id> -- "
                "Refinement #2 (orch-console, 2026-10-17): the override is a client-facing "
                "floor change and needs its own cited grant, not just a flag flip."
            )
    return phase


def validate_store(store: str) -> None:
    if store.strip().lower() == "none":
        return
    text = REGISTRY_DOC.read_text()
    refs = set(re.findall(r"`([^`\s]+)`", text))
    if not any(ref in store for ref in refs):
        fail(
            f"--store {store!r} doesn't contain any known project ref from "
            f"{REGISTRY_DOC.relative_to(ORCH_DIR)} -- per Refinement #3 the ref must be "
            f"validated, not just non-empty. Use the exact alias + project ref from the "
            f"registry, or 'none' for a pure-routing client with no product data store yet."
        )


def validate_mode_target(mode: str, inject_target: str | None) -> None:
    if mode == "log-and-route" and inject_target:
        fail("--mode log-and-route takes no --inject-target (log-only, nothing is injected)")
    if mode == "agent-session" and not inject_target:
        fail("--mode agent-session requires --inject-target <lane>")


def validate_chat_ids(args) -> None:
    """op#34846: --invite renders with an EMPTY allowlist — the first
    '/start <token>' on the deep link binds the chat (ingest.py
    try_invite_bind), so no chat ids may be pinned at render time. Without
    --invite the pre-op#34846 flow stands: at least one --chat-id is
    mandatory."""
    if args.invite:
        if args.chat_ids:
            fail("--invite fills allowed_chat_ids at bind time (first /start on the "
                 "deep link) -- drop --chat-id; the bind owns the allowlist")
    elif not args.chat_ids:
        fail("--chat-id is required (or pass --invite for a self-serve deep-link "
             "channel whose allowlist fills at bind time)")


def mint_invite() -> str:
    """Single-use invite token. token_urlsafe(24) = 32 chars of URL-safe
    base64 — fits Telegram's 64-byte deep-link payload cap with room to
    spare, ~192 bits of entropy."""
    return secrets.token_urlsafe(24)


def bot_username_for(token_env_key: str, override: str | None) -> str:
    if override:
        return override
    username = KNOWN_BOT_USERNAMES.get(token_env_key)
    if not username:
        fail(f"--bot-username is required for {token_env_key} (not in the known map) -- "
             "an invite without a t.me deep link is unusable, and guessing a username "
             "would render a link to the wrong bot")
    return username


def write_invite_link(path: Path, username: str, token: str) -> None:
    """The ONLY two places the plaintext token ever exists: this 0600 file
    and the render's stdout. Never the migration (hash only), never the bus,
    never git (.secrets/ is gitignored)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"https://t.me/{username}?start={token}\n")
    path.chmod(0o600)


def reinvite(args) -> int:
    """Mint a FRESH single-use invite for a channel that already exists
    (orch-console #84015, after the bariq re-mint had to be hand-rolled at
    bus #83995/#84008).

    check_channel_key_free makes onboarding one-shot on purpose; a channel that
    is already bound keeps needing fresh links (the client lets one expire, the
    bot is re-onboarded to a new phone). This is the narrow second path: it
    mints + records an invite and returns. It does NOT render a migration,
    touch the channel row's allowlist/enabled/group_routing, edit the
    INGEST_CHANNELS pin, or apply anything -- a re-mint must be unable to
    change how the channel behaves, only what key opens it.
    """
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT token_env_key FROM bot_channels WHERE channel_key=%s",
                    (args.channel_key,))
        row = cur.fetchone()
        if not row:
            fail(f"channel_key {args.channel_key!r} does not exist -- --reinvite is for an "
                 f"EXISTING channel; onboard a new one with --invite")
        token_env_key = row[0]

        token = mint_invite()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        ttl = args.invite_ttl_hours or 72
        created_by = args.owner_lane or "onboard_client_channel --reinvite"
        cur.execute(
            "INSERT INTO public.bot_channel_invites\n"
            "  (channel_key, token_hash, expires_at, created_by)\n"
            "VALUES (%s, %s, now() + make_interval(hours => %s), %s)\n"
            "ON CONFLICT (token_hash) DO NOTHING\n"
            "RETURNING id, expires_at",
            (args.channel_key, token_hash, ttl, created_by))
        inserted = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    if not inserted:
        fail("invite insert hit a token_hash collision and wrote nothing -- re-run")
    invite_id, expires_at = inserted
    username = bot_username_for(token_env_key, args.bot_username)
    link_path = ORCH_DIR / ".secrets" / f"invite_{slugify(args.channel_key)}.link"
    write_invite_link(link_path, username, token)

    print(f"\n# Reinvited {args.channel_key}: invite id {invite_id}, "
          f"expires {expires_at.isoformat()} (+{ttl}h from now)")
    print(f"# token_hash sha256 prefix: {token_hash[:12]}")
    print(f"# Link written 0600 to {link_path} (gitignored) -- deliberately NOT printed "
          f"here: read it from the file and relay it out-of-band.")
    print(f"# Earlier invites for this channel were left untouched; they stay valid until "
          f"their own expiry.")
    return 0


def next_migration_number() -> int:
    # Scan BOTH this worktree's migrations/ and the live checkout's -- the
    # live checkout can hold applied-but-uncommitted migrations ahead of any
    # branch (bus #63425: 103_front_desk.sql picked 097, unaware 097-102
    # already existed untracked in the live checkout). Picking a number only
    # from this worktree risks exactly that collision.
    dirs = [MIGRATIONS_DIR]
    live_dir = LIVE_CHECKOUT / "migrations"
    if live_dir.resolve() != MIGRATIONS_DIR.resolve() and live_dir.is_dir():
        dirs.append(live_dir)
    nums = []
    for d in dirs:
        for f in d.glob("*.sql"):
            m = re.match(r"(\d+)_", f.name)
            if m:
                nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def build_group_routing(args, phase: str) -> dict:
    powers_list = [p.strip() for p in args.powers.split(",") if p.strip()]
    routing: dict = {"agent_phase": phase}
    reviewer = args.agent_reviewer
    if reviewer is None and phase == "supervised":
        reviewer = "orch-console"
    if reviewer:
        routing["agent_reviewer"] = reviewer
    routing["powers"] = powers_list
    if args.self_description:
        routing["self_description"] = args.self_description
    if args.override_to_direct:
        routing["direct_override_grant"] = args.override_gate_citation
    return routing


def render_migration(args, number: int, phase: str) -> tuple[str, str]:
    key = args.channel_key
    slug = slugify(key)
    name = f"{number:03d}_{slug}_onboarding.sql"
    tag = args.tag or key
    chat_ids_sql = "{" + ",".join(str(c) for c in args.chat_ids) + "}"
    routing = build_group_routing(args, phase)
    routing_sql = json.dumps(routing).replace("'", "''")

    def sqlstr(v):
        return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"

    comment_lines = [
        f"-- {name}",
        f"-- ledger: silo={SUBSTRATE_SILO}",
        "-- (orchestrator substrate)",
        "--",
        "-- Rendered by scripts/onboard_client_channel.py (TG2.0 onboarding-automation,",
        "-- op#31450/#31473). NOT applied yet -- bring this + its sha256 to orch-console's",
        "-- gate before apply_migration.py --gate runs it.",
        "--",
        f"-- audience={args.audience}, host={args.host}, phase={phase}, "
        f"token={args.token_env_key}",
    ]
    if args.audience == "client":
        comment_lines.append(
            "-- SHARED token per Musa's settled one-bot model (op#33571, 2026-10-24; supersedes"
        )
        comment_lines.append(
            "-- bus #71813) -- isolation is per-channel: private chat_id binding (no overlap"
        )
        comment_lines.append(
            "-- with siblings on this token), self-description free of sibling names."
        )
    if args.override_to_direct:
        comment_lines.append(
            f"-- --override-to-direct GRANTED, cited gate={args.override_gate_citation} "
            "(Refinement #2 -- client-facing floor change, own cited grant)."
        )
    comment_lines.append(f"-- --store {args.store}")
    comment_lines.append("--")

    invite = bool(getattr(args, "invite", False)) and getattr(args, "invite_token", None)
    if invite:
        token_hash = hashlib.sha256(args.invite_token.encode()).hexdigest()
        ttl = getattr(args, "invite_ttl_hours", 72) or 72
        comment_lines += [
            "-- op#34846 SELF-SERVE INVITE: the channel ships with an EMPTY allowlist;",
            "-- the first '/start <token>' on the t.me deep link binds the chat"
            " (ingest.py",
            "-- try_invite_bind), consumes the single-use invite and replies with this",
            "-- channel's own self_description. The PLAINTEXT token is NOT in this file",
            "-- (sha256 at rest only); it lives solely in the render's stdout + a 0600",
            "-- link file under .secrets/ (gitignored). Relay it out-of-band, never",
            "-- over the bus. Expiry anchors at APPLY time (now()+interval), not render.",
            "--",
            "-- Asserts (lint: the REVOKE below requires them):",
            "-- assert: no_table_privilege anon public.bot_channel_invites SELECT",
            "-- assert: no_table_privilege anon public.bot_channel_invites INSERT",
            "-- assert: no_table_privilege anon public.bot_channel_invites UPDATE",
            "-- assert: no_table_privilege anon public.bot_channel_invites DELETE",
            "-- assert: no_table_privilege anon public.bot_channel_invites TRUNCATE",
            "-- assert: no_table_privilege authenticated public.bot_channel_invites SELECT",
            "-- assert: no_table_privilege authenticated public.bot_channel_invites INSERT",
            "-- assert: no_table_privilege authenticated public.bot_channel_invites UPDATE",
            "-- assert: no_table_privilege authenticated public.bot_channel_invites DELETE",
            "-- assert: no_table_privilege authenticated public.bot_channel_invites TRUNCATE",
        ]
    comment_lines.append(
        f"-- REVERT: DELETE FROM public.bot_channels WHERE channel_key = '{key}';")
    if invite:
        comment_lines.append(
            f"-- REVERT: DELETE FROM public.bot_channel_invites WHERE channel_key = '{key}';")
    comment_lines.append("")

    insert_sql = (
        "INSERT INTO public.bot_channels\n"
        f"  ({_INSERT_COLUMNS})\n"
        "VALUES\n"
        f"  ({sqlstr(key)}, {sqlstr(args.token_env_key)}, {sqlstr(args.mode)}, "
        f"{sqlstr(args.inject_target)},\n"
        f"   {sqlstr(args.inject_prefix)}, {sqlstr(args.responder_ref)},\n"
        f"   '{chat_ids_sql}', '{{}}',\n"
        f"   '{routing_sql}'::jsonb, {sqlstr(tag)}, 'substrate', false, NULL,\n"
        f"   {sqlstr(args.audience)}, {sqlstr(args.owner_lane)})\n"
        "ON CONFLICT (channel_key) DO NOTHING;\n"
    )
    invite_sql = ""
    if invite:
        created_by = args.owner_lane or "onboard_client_channel"
        invite_sql = (
            "\n"
            "-- Single-use, expiring invite keyed to this channel (op#34846). The\n"
            "-- lock-down block is the 014 substrate pattern: service-role only, RLS\n"
            "-- + deny-all + REVOKEs (new tables get anon/authenticated ALL by\n"
            "-- pg_default_acl implicitly — see the assert header above). This table/\n"
            "-- policy is shared across EVERY --invite channel, so every render after\n"
            "-- the first hits it already existing (op#35542/orch-console: the bare\n"
            "-- CREATE POLICY below used to fail the migration outright on the second\n"
            "-- channel onboarded -- guard it, never DROP+recreate a live policy).\n"
            "CREATE TABLE IF NOT EXISTS public.bot_channel_invites (\n"
            "  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,\n"
            "  channel_key    TEXT NOT NULL REFERENCES public.bot_channels(channel_key)\n"
            "                 ON DELETE CASCADE,\n"
            "  token_hash     TEXT NOT NULL UNIQUE,\n"
            "  expires_at     TIMESTAMPTZ NOT NULL,\n"
            "  used_at        TIMESTAMPTZ,\n"
            "  bound_chat_id  BIGINT,\n"
            "  created_by     TEXT NOT NULL,\n"
            "  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()\n"
            ");\n"
            "ALTER TABLE public.bot_channel_invites ENABLE ROW LEVEL SECURITY;\n"
            "DO $$\n"
            "BEGIN\n"
            "  IF NOT EXISTS (\n"
            "    SELECT 1 FROM pg_policies\n"
            "    WHERE schemaname = 'public' AND tablename = 'bot_channel_invites'\n"
            "      AND policyname = 'deny_all_bot_channel_invites'\n"
            "  ) THEN\n"
            "    CREATE POLICY deny_all_bot_channel_invites ON public.bot_channel_invites\n"
            "      FOR ALL TO PUBLIC USING (false) WITH CHECK (false);\n"
            "  END IF;\n"
            "END $$;\n"
            "REVOKE ALL ON public.bot_channel_invites FROM PUBLIC, anon, authenticated;\n"
            "\n"
            "INSERT INTO public.bot_channel_invites\n"
            "  (channel_key, token_hash, expires_at, created_by)\n"
            "VALUES\n"
            f"  ({sqlstr(key)}, '{token_hash}', "
            f"now() + make_interval(hours => {ttl}), {sqlstr(created_by)})\n"
            "ON CONFLICT (token_hash) DO NOTHING;\n"
        )
    return name, "\n".join(comment_lines) + insert_sql + invite_sql


def render_ingest_diff(channel_key: str) -> tuple[str, str] | None:
    text = INGEST_SCRIPT.read_text()
    pinned = pinned_channels()
    if channel_key in pinned:
        return None
    new_line = text.replace(
        f'INGEST_CHANNELS="{",".join(pinned)}"',
        f'INGEST_CHANNELS="{",".join(pinned + [channel_key])}"',
    )
    return text, new_line


def render_polled_tags_diff(tag: str) -> tuple[str, str] | None:
    """op#22517 lockstep (console-revamp #78815 fast-follow a): a client
    channel's Mini pin (INGEST_CHANNELS) and its _CONSOLE_POLLED_CLIENT_TAGS
    entry move in the SAME render pass -- the drift guard
    (tests/test_operator_log_scope.py) fails CI on half a pair, which is
    exactly what the 126_bariq_onboarding render shipped and console-revamp
    had to fix as 7699f50. This renders the operator_log.py half.

    Returns (old_text, new_text), or None when the tag is already present
    (idempotent, checked independently of the pin so a render also HEALS a
    tag-side gap). Refuses when the constant block can't be found: the
    invariant changed shape and this tool must be taught the new one first."""
    text = OPERATOR_LOG.read_text()
    m = re.search(r"_CONSOLE_POLLED_CLIENT_TAGS = frozenset\(\{(.*?)\n\}\)", text, re.DOTALL)
    if not m:
        fail("nervous_system/operator_log.py has no _CONSOLE_POLLED_CLIENT_TAGS frozenset "
             "block -- update this tool, the op#22517 lockstep invariant may have changed")
    if f'"{tag}"' in m.group(1):
        return None
    date = datetime.now().strftime("%Y-%m-%d")
    block = m.group(0)
    entry = f'    "{tag}",'
    new_block = block.replace(
        "\n})",
        f"\n{entry}" + " " * max(1, 44 - len(entry))
        + f"# {date} auto lockstep (onboard render)\n}})",
    )
    return text, text.replace(block, new_block)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.reinvite:
        return reinvite(args)
    if args.chat_ids is None:            # --chat-id omitted (--invite flow)
        args.chat_ids = []

    cwd = Path.cwd().resolve()
    if args.host == "mini" and cwd == LIVE_CHECKOUT.resolve():
        fail(
            "running with --host mini from the LIVE checkout (~/wingmen/orchestrator) -- this "
            "would edit boot_nazim_ingest.sh's and operator_log.py's working tree directly, "
            "and launchd jobs execute the boot script from it every few minutes. Run from a "
            "git worktree PR branch instead."
        )

    validate_mode_target(args.mode, args.inject_target)
    validate_store(args.store)
    validate_chat_ids(args)
    phase = resolve_phase(args)
    if args.invite:
        args.invite_token = mint_invite()

    conn = connect()
    try:
        cur = conn.cursor()
        check_channel_key_free(cur, args.channel_key)
        check_token_isolation(cur, args.token_env_key, args.channel_key, args.chat_ids)
        check_host_token_consistency(cur, args.token_env_key, args.host, args.channel_key)
        if args.self_description:
            check_self_description(cur, args.channel_key, args.self_description)
    finally:
        conn.close()

    number = next_migration_number()
    name, sql_text = render_migration(args, number, phase)
    sha = hashlib.sha256(sql_text.encode()).hexdigest()

    print(sql_text)
    print(f"-- sha256: {sha}")
    print(f"-- sha256 (12-char prefix for gate citation): {sha[:12]}")

    if args.invite:
        username = bot_username_for(args.token_env_key, args.bot_username)
        link = f"https://t.me/{username}?start={args.invite_token}"
        print(f"\n# CLIENT INVITE LINK (single-use, expires at apply+{args.invite_ttl_hours}h):")
        print(f"#   {link}")
        print("#   The plaintext token exists ONLY here and (post-render) in the 0600")
        print("#   link file — never in the migration (sha256 only), never in a bus body,")
        print("#   never in git. Relay to the client out-of-band (CA/Musa), not via agents.")

    ingest_diff = None
    polled_tags_diff = None
    if args.host == "mini":
        ingest_diff = render_ingest_diff(args.channel_key)
        if ingest_diff is None:
            print(f"\n# {args.channel_key} is already pinned in INGEST_CHANNELS -- no boot-script diff needed.")
        if args.audience == "client":
            # op#22517 lockstep (console-revamp #78815 fast-follow a): the
            # boot-script pin and the _CONSOLE_POLLED_CLIENT_TAGS entry move
            # in the same render pass, so client #2 can't ship the half-pair
            # the 126 render did. Internal Mini pins are lane-owned tags
            # (operator_log._LANE_OWNED_TAGS) and deliberately never land
            # here -- adding one would break the drift-guard equality itself.
            polled_tags_diff = render_polled_tags_diff(args.tag or args.channel_key)
            if polled_tags_diff is None:
                print(f"# {args.tag or args.channel_key} is already in "
                      "_CONSOLE_POLLED_CLIENT_TAGS -- no operator_log diff needed.")

    if args.dry_run:
        edits = []
        if ingest_diff:
            edits.append("edit scripts/boot_nazim_ingest.sh")
        if polled_tags_diff:
            edits.append("edit nervous_system/operator_log.py "
                         "(_CONSOLE_POLLED_CLIENT_TAGS, op#22517 lockstep)")
        print(f"\n# --dry-run: would write migrations/{name}"
              + (f" and {' and '.join(edits)}" if edits else ""))
        return 0

    out_path = MIGRATIONS_DIR / name
    out_path.write_text(sql_text)
    print(f"\n# Wrote {out_path.relative_to(ORCH_DIR)}")

    if args.invite:
        username = bot_username_for(args.token_env_key, args.bot_username)
        link_path = ORCH_DIR / ".secrets" / f"invite_{slugify(args.channel_key)}.link"
        write_invite_link(link_path, username, args.invite_token)
        print(f"# Wrote the client link 0600 to {link_path.relative_to(ORCH_DIR)} "
              "(gitignored; relay out-of-band).")

    if ingest_diff:
        old_text, new_text = ingest_diff
        INGEST_SCRIPT.write_text(new_text)
        print(f"# Updated {INGEST_SCRIPT.relative_to(ORCH_DIR)}'s INGEST_CHANNELS pin (same PR).")

    if polled_tags_diff:
        _, new_ol = polled_tags_diff
        OPERATOR_LOG.write_text(new_ol)
        print(f"# Updated {OPERATOR_LOG.relative_to(ORCH_DIR)}'s _CONSOLE_POLLED_CLIENT_TAGS "
              "(op#22517 lockstep -- pin + tag in the same PR, never half a pair).")

    print(
        f"\n# Next: post migrations/{name} + sha256 {sha[:12]} to orch-console's gate "
        f"(same `apply_migration.py --gate` paper trail as every prior onboarding), apply only "
        f"on grant, enable only as the deliberate separate step this tool never exposes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

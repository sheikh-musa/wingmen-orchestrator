"""Unit tests for scripts/onboard_client_channel.py's validation logic.

Pure-logic tests only (stub DB cursors) -- no live DATABASE_URL touch, so
these run under the static/no-DB test lane same as
tests/migrations/test_ingest_channels_enabled_false.py.
"""
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ORCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ORCH_DIR / "scripts"))

import onboard_client_channel as tool  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self._result = None

    def execute(self, sql, params=None):
        self._result = self._rows

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result or []


def test_check_token_isolation_refuses_overlapping_chat_id():
    # op#33571 settled model: sharing a token is fine; sharing a CHAT is not.
    cur = FakeCursor([("angullia", [111])])
    with pytest.raises(SystemExit):
        tool.check_token_isolation(cur, "WINGMENDEVBOT_TOKEN", "zz-new", [111, 222])


def test_check_token_isolation_allows_disjoint_chats():
    cur = FakeCursor([("angullia", [111])])
    tool.check_token_isolation(cur, "WINGMENDEVBOT_TOKEN", "zz-new", [222])  # no raise


def test_check_self_description_refuses_sibling_name():
    cur = FakeCursor([("angullia",)])
    with pytest.raises(SystemExit):
        tool.check_self_description(cur, "zz-new", "Ask angullia for help")


def test_check_self_description_allows_clean_description():
    cur = FakeCursor([("angullia",)])
    tool.check_self_description(cur, "zz-new", "Site-edit channel for zz")  # no raise


def test_check_channel_key_free_refuses_existing():
    cur = FakeCursor([(1,)])
    with pytest.raises(SystemExit):
        tool.check_channel_key_free(cur, "coffeemedia")


def test_check_channel_key_free_allows_new():
    cur = FakeCursor([])
    tool.check_channel_key_free(cur, "zz-brand-new")  # no raise


def test_host_consistency_refuses_hub_when_sibling_pinned():
    cur = FakeCursor([("coffeemedia", False)])
    with mock.patch.object(tool, "pinned_channels", return_value=["coffeemedia"]):
        with pytest.raises(SystemExit):
            tool.check_host_token_consistency(cur, "WINGMENDEVBOT_TOKEN", "hub", "zz-new")


def test_host_consistency_refuses_mini_when_sibling_hub_enabled():
    cur = FakeCursor([("some-hub-channel", True)])
    with mock.patch.object(tool, "pinned_channels", return_value=[]):
        with pytest.raises(SystemExit):
            tool.check_host_token_consistency(cur, "SOME_TOKEN", "mini", "zz-new")


def test_host_consistency_allows_matching_mini_pin():
    cur = FakeCursor([("coffeemedia", False)])
    with mock.patch.object(tool, "pinned_channels", return_value=["coffeemedia"]):
        tool.check_host_token_consistency(cur, "WINGMENDEVBOT_TOKEN", "mini", "zz-new")  # no raise


def test_host_consistency_allows_fresh_token_no_siblings():
    cur = FakeCursor([])
    with mock.patch.object(tool, "pinned_channels", return_value=[]):
        tool.check_host_token_consistency(cur, "ZZ_FRESH_TOKEN", "hub", "zz-new")  # no raise


def make_args(**overrides):
    base = dict(
        channel_key="zz", chat_ids=[1], token_env_key="ZZ_TOKEN", tag=None,
        mode="agent-session", phase=None, audience="internal", owner_lane=None,
        store="none", powers="reply_text", agent_reviewer=None, host="hub",
        override_to_direct=False, override_gate_citation=None,
        inject_target="zz", inject_prefix=None, responder_ref=None,
        sensitive_data="true", self_description=None,
        invite=False, invite_ttl_hours=72, invite_token=None, bot_username=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_resolve_phase_defaults_supervised_for_client():
    args = make_args(audience="client", phase=None)
    assert tool.resolve_phase(args) == "supervised"


def test_resolve_phase_defaults_direct_for_internal():
    args = make_args(audience="internal", phase=None)
    assert tool.resolve_phase(args) == "direct"


def test_resolve_phase_blocks_direct_with_multiple_chat_ids():
    args = make_args(audience="internal", phase="direct", chat_ids=[1, 2])
    with pytest.raises(SystemExit):
        tool.resolve_phase(args)


def test_resolve_phase_blocks_client_direct_without_override():
    args = make_args(audience="client", phase="direct", chat_ids=[1], override_to_direct=False)
    with pytest.raises(SystemExit):
        tool.resolve_phase(args)


def test_resolve_phase_blocks_override_without_citation():
    args = make_args(
        audience="client", phase="direct", chat_ids=[1],
        override_to_direct=True, override_gate_citation=None,
    )
    with pytest.raises(SystemExit):
        tool.resolve_phase(args)


def test_resolve_phase_allows_override_with_citation():
    args = make_args(
        audience="client", phase="direct", chat_ids=[1],
        override_to_direct=True, override_gate_citation="#12345",
    )
    assert tool.resolve_phase(args) == "direct"


def test_resolve_phase_allows_single_chat_direct_internal():
    args = make_args(audience="internal", phase="direct", chat_ids=[1])
    assert tool.resolve_phase(args) == "direct"


def test_validate_store_accepts_none():
    tool.validate_store("none")  # no raise
    tool.validate_store("NONE")  # no raise


def test_validate_store_accepts_known_ref():
    tool.validate_store(f"substrate `{tool.SUBSTRATE_SILO}`")  # no raise


def test_validate_store_refuses_unknown_ref():
    with pytest.raises(SystemExit):
        tool.validate_store("some made up store with no backticked ref")


def test_validate_mode_target_refuses_inject_target_on_log_and_route():
    with pytest.raises(SystemExit):
        tool.validate_mode_target("log-and-route", "somelane")


def test_validate_mode_target_requires_inject_target_on_agent_session():
    with pytest.raises(SystemExit):
        tool.validate_mode_target("agent-session", None)


def test_validate_mode_target_allows_valid_combos():
    tool.validate_mode_target("log-and-route", None)
    tool.validate_mode_target("agent-session", "somelane")


def test_build_group_routing_defaults_reviewer_for_supervised():
    args = make_args(agent_reviewer=None)
    routing = tool.build_group_routing(args, "supervised")
    assert routing["agent_reviewer"] == "orch-console"
    assert routing["agent_phase"] == "supervised"
    assert routing["powers"] == ["reply_text"]


def test_build_group_routing_no_reviewer_for_direct():
    args = make_args(agent_reviewer=None)
    routing = tool.build_group_routing(args, "direct")
    assert "agent_reviewer" not in routing


def test_build_group_routing_respects_explicit_reviewer():
    args = make_args(agent_reviewer="custom-reviewer")
    routing = tool.build_group_routing(args, "direct")
    assert routing["agent_reviewer"] == "custom-reviewer"


def test_build_group_routing_records_override_citation():
    args = make_args(override_to_direct=True, override_gate_citation="#999")
    routing = tool.build_group_routing(args, "direct")
    assert routing["direct_override_grant"] == "#999"


def test_render_migration_shape_matches_112_column_order():
    args = make_args(audience="client", chat_ids=[-555])
    name, sql_text = tool.render_migration(args, 200, "supervised")
    assert name == "200_zz_onboarding.sql"
    assert "-- ledger: silo=" + tool.SUBSTRATE_SILO in sql_text
    assert tool._INSERT_COLUMNS.replace("\n", " ").replace("  ", " ") in sql_text.replace("\n", " ").replace("  ", " ")
    assert "enabled, poll_offset" in sql_text
    assert ", false, NULL," in sql_text  # enabled always false, never a flag to flip it


# ── op#34846 self-serve invite deep links (Musa #78263) ──────────────────────

def test_validate_chat_ids_invite_xor_chat_id():
    # --invite means the allowlist fills at bind time: no chat ids allowed
    with pytest.raises(SystemExit):
        tool.validate_chat_ids(make_args(invite=True, chat_ids=[1]))
    tool.validate_chat_ids(make_args(invite=True, chat_ids=[]))
    # without --invite the chat id is still mandatory (the pre-op#34846 flow)
    with pytest.raises(SystemExit):
        tool.validate_chat_ids(make_args(invite=False, chat_ids=[]))
    tool.validate_chat_ids(make_args(invite=False, chat_ids=[1]))


def test_mint_invite_shape():
    import re as _re
    a, b = tool.mint_invite(), tool.mint_invite()
    for tok in (a, b):
        assert 20 <= len(tok) <= 64            # Telegram deep-link payload cap
        assert _re.fullmatch(r"[A-Za-z0-9_-]+", tok)
    assert a != b


def test_render_migration_invite_hash_never_plaintext():
    args = make_args(audience="client", token_env_key="WINGMENDEVBOT_TOKEN",
                     invite=True, invite_token="super-secret-plaintext",
                     chat_ids=[], owner_lane="cc-bariq")
    name, sql_text = tool.render_migration(args, 200, "supervised")
    expected_hash = hashlib.sha256(b"super-secret-plaintext").hexdigest()
    assert expected_hash in sql_text                    # only the hash at rest
    assert "super-secret-plaintext" not in sql_text     # plaintext NEVER lands
    assert "CREATE TABLE IF NOT EXISTS public.bot_channel_invites" in sql_text
    assert "token_hash" in sql_text
    assert "make_interval(hours => 72)" in sql_text     # expiry, SQL-side
    # channel ships with an EMPTY allowlist — no {0} placeholder (op#34846)
    assert "'{}', '{}'," in sql_text
    assert "{0}" not in sql_text
    # 014-style lockdown: RLS + deny-all + REVOKEs + assert lines (lint: a
    # REVOKE without asserts is refused outright)
    assert "ENABLE ROW LEVEL SECURITY" in sql_text
    assert "REVOKE ALL ON public.bot_channel_invites FROM PUBLIC, anon, authenticated" in sql_text
    assert "-- assert: no_table_privilege anon public.bot_channel_invites SELECT" in sql_text


def test_render_migration_without_invite_has_no_invite_block():
    _, sql_text = tool.render_migration(make_args(audience="client", chat_ids=[-555]),
                                        200, "supervised")
    assert "bot_channel_invites" not in sql_text


# ── op#35542 (orch-console, 2026-10-29): bot_channel_invites + its deny-all
# policy are process-wide -- created by the FIRST --invite channel ever
# onboarded (bariq, migration 126). Every LATER --invite channel's render
# (e.g. faizul, migration 133) hits both already existing. A bare
# CREATE POLICY there made that second channel's migration fail outright
# ("policy already exists") and roll back the whole apply. ──────────────

def test_render_migration_invite_policy_guard_is_idempotent_shape():
    # the policy creation must be guarded (checked against pg_policies, never
    # a bare CREATE POLICY) so a second --invite render can't collide with a
    # sibling's already-applied policy of the same name.
    _, sql_text = tool.render_migration(
        make_args(audience="client", token_env_key="WINGMENDEVBOT_TOKEN",
                  invite=True, invite_token="tok", chat_ids=[], owner_lane="coffeemedia"),
        200, "supervised",
    )
    assert "IF NOT EXISTS (\n    SELECT 1 FROM pg_policies" in sql_text
    assert "policyname = 'deny_all_bot_channel_invites'" in sql_text
    # never drop a live security policy to re-create it (orch-console directive)
    assert "DROP POLICY" not in sql_text


def test_render_migration_invite_block_applies_cleanly_for_a_second_channel(pg_dsn):
    """Real-Postgres proof, not just a string-shape check: apply two
    different channels' FULL rendered migrations back to back against a
    DB that starts empty -- the second must not raise, because it hits
    bot_channel_invites (and its policy) already created by the first."""
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT USAGE ON SCHEMA public TO public")
        for role in ("anon", "authenticated"):
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if not cur.fetchone():
                cur.execute(f"CREATE ROLE {role}")
        cur.execute(
            """
            CREATE TABLE public.bot_channels (
                channel_key       TEXT PRIMARY KEY,
                token_env_key     TEXT, mode TEXT, inject_target TEXT,
                inject_prefix     TEXT, responder_ref TEXT,
                allowed_chat_ids  BIGINT[], allowed_usernames TEXT[],
                group_routing     JSONB, channel_tag TEXT, log_target TEXT,
                enabled           BOOLEAN, poll_offset BIGINT,
                audience          TEXT, owner_lane TEXT
            )
            """
        )
        for key, token in (("bariq", "bariq-secret"), ("faizul", "faizul-secret")):
            args = make_args(
                channel_key=key, audience="client", token_env_key="WINGMENDEVBOT_TOKEN",
                invite=True, invite_token=token, chat_ids=[], owner_lane="coffeemedia",
                self_description=f"Site-edit channel for {key}",
            )
            _, sql_text = tool.render_migration(args, 200, "supervised")
            cur.execute(sql_text)  # the SECOND channel's apply must not raise

        cur.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
            "AND tablename='bot_channel_invites' AND policyname='deny_all_bot_channel_invites'"
        )
        assert cur.fetchone()[0] == 1  # created once, not duplicated, never dropped+recreated
        cur.execute("SELECT channel_key FROM public.bot_channel_invites ORDER BY channel_key")
        assert [r[0] for r in cur.fetchall()] == ["bariq", "faizul"]


def test_bot_username_for_known_token_and_refusal():
    assert tool.bot_username_for("WINGMENDEVBOT_TOKEN", None) == "wingmendevbot"
    assert tool.bot_username_for("MYSTERY_TOKEN", "custombot") == "custombot"
    with pytest.raises(SystemExit):
        tool.bot_username_for("MYSTERY_TOKEN", None)    # can't build the link


def test_write_invite_link_file_is_secret(tmp_path):
    import os
    import stat
    tok = tool.mint_invite()
    link = tmp_path / "invite_zz.link"
    tool.write_invite_link(link, "wingmendevbot", tok)
    assert link.read_text().strip() == f"https://t.me/wingmendevbot?start={tok}"
    assert stat.S_IMODE(link.stat().st_mode) == 0o600


# ── op#22517 lockstep render (console-revamp #78815 fast-follow a) ───────────
# A client channel's Mini pin (boot_nazim_ingest.sh INGEST_CHANNELS) and its
# _CONSOLE_POLLED_CLIENT_TAGS entry must move in the SAME render pass -- the
# half-pair my 126 render shipped broke the drift guard in CI until
# console-revamp's 7699f50. These tests pin the renderer's side of that pair.

_OPERATOR_LOG_FIXTURE = '''\
# (fixture shaped like the real constant block, nervous_system/operator_log.py)
_LANE_OWNED_TAGS = ("hk-editor", "mamadah", "coffeemedia")

_CONSOLE_POLLED_CLIENT_TAGS = frozenset({
    "cosem-caai", "cosem-exams", "alderei",  # 2026-08-03
    "cosem-tdu", "angullia",                  # 2026-09-27 (op#22517 fix)
    "oeh",                                     # 2026-09-27 (op#22521/bus#43713)
})

def _after(): pass
'''


def _write_operator_log(tmp_path, extra_tag=None):
    text = _OPERATOR_LOG_FIXTURE
    if extra_tag:
        text = text.replace(
            "})\n\ndef _after",
            f'\n    "{extra_tag}",'
            + " " * max(1, 37 - len(extra_tag))
            + "# 2026-10-27 (test fixture)\n})\n\ndef _after",
        )
    ol = tmp_path / "operator_log.py"
    ol.write_text(text)
    return ol


def _frozenset_block(text):
    import re as _re
    m = _re.search(r"_CONSOLE_POLLED_CLIENT_TAGS = frozenset\(\{(.*?)\n\}\)", text, _re.DOTALL)
    assert m, "fixture lost its _CONSOLE_POLLED_CLIENT_TAGS block"
    return m.group(1)


def test_render_polled_tags_diff_adds_client_tag(tmp_path):
    ol = _write_operator_log(tmp_path)
    with mock.patch.object(tool, "OPERATOR_LOG", ol):
        diff = tool.render_polled_tags_diff("zz-new")
    assert diff is not None
    old_text, new_text = diff
    block = _frozenset_block(new_text)
    # the tag lands INSIDE the frozenset, with the date-commented entry shape
    assert f'    "zz-new",' in block
    from datetime import datetime
    assert f'# {datetime.now().strftime("%Y-%m-%d")} auto lockstep (onboard render)' in block
    # idempotence input: every existing entry survives untouched
    for existing in ("cosem-caai", "angullia", "oeh"):
        assert f'"{existing}"' in block


def test_render_polled_tags_diff_idempotent_when_tag_present(tmp_path):
    ol = _write_operator_log(tmp_path, extra_tag="bariq")
    with mock.patch.object(tool, "OPERATOR_LOG", ol):
        assert tool.render_polled_tags_diff("bariq") is None
        assert tool.render_polled_tags_diff("zz-new") is not None  # heals a tag-side gap


def test_render_polled_tags_diff_refuses_missing_constant(tmp_path):
    ol = tmp_path / "operator_log.py"
    ol.write_text("def _after(): pass\n")  # no frozenset block at all
    with mock.patch.object(tool, "OPERATOR_LOG", ol):
        with pytest.raises(SystemExit):
            tool.render_polled_tags_diff("zz-new")


def _fake_conn(rows=()):
    conn = mock.MagicMock()
    conn.cursor.return_value = FakeCursor(list(rows))
    return conn


def _render_via_main(tmp_path, audience):
    """Run tool.main() end-to-end against tmp fixtures for both lockstep
    files, the way a lane actually renders a Mini client onboarding."""
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    boot = tmp_path / "boot_nazim_ingest.sh"
    boot.write_text('#!/bin/bash\nINGEST_CHANNELS="nazim-console"\n')
    ol = _write_operator_log(tmp_path)
    argv = [
        "--channel-key", "zz-new", "--chat-id", "123",
        "--token-env-key", "ZZ_TOKEN", "--mode", "agent-session",
        "--inject-target", "zz-new", "--audience", audience,
        "--owner-lane", "cc-bariq", "--host", "mini", "--store", "none",
        "--self-description", "Site-edit channel for the zz-new client",
    ]
    with mock.patch.object(tool, "connect", return_value=_fake_conn()), \
         mock.patch.object(tool, "ORCH_DIR", tmp_path), \
         mock.patch.object(tool, "MIGRATIONS_DIR", migrations), \
         mock.patch.object(tool, "INGEST_SCRIPT", boot), \
         mock.patch.object(tool, "OPERATOR_LOG", ol), \
         mock.patch.object(tool, "LIVE_CHECKOUT", tmp_path):
        rc = tool.main(argv)
    assert rc == 0
    return boot, ol, migrations


def test_main_mini_client_pins_boot_script_and_polled_tag_in_one_pass(tmp_path):
    boot, ol, migrations = _render_via_main(tmp_path, "client")
    # the migration rendered...
    assert list(migrations.glob("*_zz_new_onboarding.sql"))
    # ...AND the boot-script pin...
    import re as _re
    pinned = _re.search(r'INGEST_CHANNELS="([^"]+)"', boot.read_text()).group(1)
    assert "zz-new" in pinned.split(",")
    # ...AND the op#22517 lockstep partner, in the SAME run -- never half a pair
    assert '"zz-new"' in _frozenset_block(ol.read_text())


def test_main_mini_internal_does_not_touch_polled_client_tags(tmp_path):
    # internal Mini pins are lane-owned tags (operator_log._LANE_OWNED_TAGS),
    # NOT console-polled client tags -- adding one to _CONSOLE_POLLED_CLIENT_TAGS
    # would break the drift-guard equality itself.
    boot, ol, migrations = _render_via_main(tmp_path, "internal")
    import re as _re
    pinned = _re.search(r'INGEST_CHANNELS="([^"]+)"', boot.read_text()).group(1)
    assert "zz-new" in pinned.split(",")          # pin still happens
    assert '"zz-new"' not in _frozenset_block(ol.read_text())  # tag add must not


# ── --reinvite (orch-console #84015): a fresh invite for an EXISTING channel ──
#
# check_channel_key_free refuses any channel_key that exists, which is right for
# onboarding and wrong for a re-mint: on 2026-10-30 the bariq invite had to be
# re-minted by hand (bus #83995/#84008) because the fleet's only invite minter
# had no existing-channel path. --reinvite is that path: it mints a NEW
# single-use token for a channel that already exists and changes NOTHING about
# the channel row -- no allowlist write, no enabled flip, no migration.

import re as _re
from datetime import datetime, timezone


class _RecordingCursor:
    """Records every (sql, params) the reinvite flow issues and answers by the
    shape of the statement, so a test can prove what the flow did and did not
    touch."""

    def __init__(self, channel_row=("ZZ_TOKEN",)):
        self.calls = []
        self._channel_row = channel_row
        self._last = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        up = sql.upper()
        if "FROM BOT_CHANNELS" in up:
            self._last = self._channel_row
        elif "INSERT INTO PUBLIC.BOT_CHANNEL_INVITES" in up:
            self._last = (7, datetime.now(timezone.utc))
        else:
            self._last = None

    def fetchone(self):
        return self._last

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run_reinvite(tmp_path, channel_row=("ZZ_TOKEN",), extra=None):
    cur = _RecordingCursor(channel_row)
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    argv = ["--reinvite", "--channel-key", "zz", "--invite-ttl-hours", "168",
            "--bot-username", "zzbot"] + (extra or [])
    with mock.patch.object(tool, "connect", return_value=conn), \
         mock.patch.object(tool, "ORCH_DIR", tmp_path), \
         mock.patch.object(tool, "LIVE_CHECKOUT", tmp_path):
        rc = tool.main(argv)
    return rc, cur, conn


def test_reinvite_inserts_one_invite_and_never_touches_the_channel_row(tmp_path):
    rc, cur, _conn = _run_reinvite(tmp_path)
    assert rc == 0
    shapes = [sql.upper() for sql, _ in cur.calls]
    # exactly one invite INSERT and one channel lookup
    assert sum("INSERT INTO PUBLIC.BOT_CHANNEL_INVITES" in s for s in shapes) == 1
    assert sum("FROM BOT_CHANNELS" in s for s in shapes) == 1
    # and NOTHING that mutates the channel: a re-mint must never widen the
    # allowlist, flip enabled, or rewrite group_routing
    for s in shapes:
        assert "UPDATE" not in s, f"reinvite must not UPDATE anything: {s}"
        assert "DELETE" not in s, f"reinvite must not DELETE anything: {s}"
        if "BOT_CHANNELS" in s:
            assert "INSERT" not in s, f"reinvite must not INSERT a channel row: {s}"


def test_reinvite_writes_a_0600_link_whose_token_hashes_to_the_inserted_row(tmp_path, capsys):
    _rc, cur, _conn = _run_reinvite(tmp_path)
    link = tmp_path / ".secrets" / "invite_zz.link"
    assert link.exists(), "reinvite must leave the plaintext in the 0600 link file"
    assert oct(link.stat().st_mode & 0o777) == "0o600"
    token = link.read_text().strip().split("start=", 1)[1]
    insert = next(p for s, p in cur.calls if "INSERT INTO PUBLIC.BOT_CHANNEL_INVITES" in s.upper())
    assert hashlib.sha256(token.encode()).hexdigest() in [str(x) for x in insert]
    # the plaintext token is NOT reproduced on stdout -- a re-mint is run over
    # a bus/session far more often than an onboarding is
    out = capsys.readouterr().out
    assert token not in out
    assert hashlib.sha256(token.encode()).hexdigest()[:12] in out, "reporter needs the prefix"


def test_reinvite_refuses_an_unknown_channel(tmp_path):
    cur = _RecordingCursor(channel_row=None)
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    with mock.patch.object(tool, "connect", return_value=conn), \
         mock.patch.object(tool, "ORCH_DIR", tmp_path):
        with pytest.raises(SystemExit):
            tool.main(["--reinvite", "--channel-key", "nope"])
    assert not any("INSERT INTO PUBLIC.BOT_CHANNEL_INVITES" in s.upper() for s, _ in cur.calls)
    assert not (tmp_path / ".secrets" / "invite_nope.link").exists()


def test_reinvite_and_invite_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        tool.parse_args(["--reinvite", "--invite", "--channel-key", "zz"])


def test_parse_args_reinvite_needs_only_a_channel_key():
    # the whole point: no --host/--store/--mode/--audience/--token-env-key
    args = tool.parse_args(["--reinvite", "--channel-key", "zz"])
    assert args.reinvite is True and args.channel_key == "zz"
    assert args.invite_ttl_hours == 72          # explicit 7-day runs pass their own


def test_parse_args_onboarding_still_requires_its_flags(capsys):
    # making the onboarding flags optional for --reinvite must not let a plain
    # onboarding render start with half a config: it still refuses, naming the
    # missing flags exactly as argparse did before the change
    with pytest.raises(SystemExit):
        tool.parse_args(["--channel-key", "zz"])
    err = capsys.readouterr().err
    assert "--host" in err and "--store" in err

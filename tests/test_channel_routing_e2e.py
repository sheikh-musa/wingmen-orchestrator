"""TG2.0 section 4: the routing checklist, mechanised.

Design report §4 ("Testing a new client's routing, zero-409, zero-manual-poll")
lists six steps. Steps 1-2 (capture the real chat_id from an unclaimed inbound)
and 4-5 (prove the second message routes to the NEW tag; prove no cross-wire
with siblings sharing the token) are what a human kept re-inventing by hand for
brandit-coffeemedia. This module pins them as code:

  capture  — the unclaimed inbound chats a token group has logged (under the
             group's fallback tag), so onboarding reads the chat_id from the
             row instead of someone typing it.
  check    — the independent-verification half: the row resolves, the evidence
             exists, and no message crossed wires with a sibling tag. Reads the
             DB directly, never a relayed claim.

RED first: scripts/verify_channel_routing.py did not exist when these were
written.

The e2e half uses a throwaway PostgreSQL cluster with a minimal schema the test
creates itself (never the fleet DB), walks the checklist end to end, and then
injects each cross-wire fault to prove the check catches it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "verify_channel_routing.py"

T0 = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
TOK = "WINGMENDEVBOT_TOKEN"


def vr():
    spec = importlib.util.spec_from_file_location("verify_channel_routing", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(key, tag, ids=(), users=(), created=None, enabled=False):
    return {"channel_key": key, "token_env_key": TOK, "channel_tag": tag,
            "allowed_chat_ids": list(ids), "allowed_usernames": list(users),
            "enabled": enabled, "created_at": created or T0}


def msg(id, chat_id, tag, created=None):
    return {"id": id, "chat_id": str(chat_id), "tag": tag,
            "created_at": created or T0 + timedelta(minutes=5)}


HEALTHY_ROWS = [
    row("alderei", "alderei", ids=[100]),
    row("coffeemedia", "coffeemedia", ids=[200]),
    row("newclient", "newclient", ids=[555], created=T0 + timedelta(hours=1)),
]
HEALTHY_MSGS = [
    msg(1, 555, "alderei", created=T0 + timedelta(minutes=10)),      # step 1: pre-onboarding, fallback tag
    msg(2, 555, "newclient", created=T0 + timedelta(hours=2)),       # step 4: routed to the new tag
    msg(3, 100, "alderei", created=T0 + timedelta(minutes=1)),       # a sibling's own traffic
]


# ── step 1-2: capture the chat_id from the unclaimed row ────────────────────

def test_fallback_tag_is_the_groups_first_row():
    assert vr().fallback_tag(HEALTHY_ROWS) == "alderei"
    assert vr().fallback_tag(list(reversed(HEALTHY_ROWS))) == "alderei"


def test_unmatched_rows_are_the_unclaimed_chats_under_the_fallback_tag():
    v = vr()
    rows = HEALTHY_ROWS[:2]                       # before the new row exists
    out = v.unmatched_rows(rows, [msg(1, 555, "alderei"),
                                  msg(2, 100, "alderei"),
                                  msg(3, 777, "coffeemedia")])
    assert [m["chat_id"] for m in out] == ["555", "777"]


def test_unmatched_rows_ignore_an_empty_chat_id():
    out = vr().unmatched_rows(HEALTHY_ROWS[:2], [{"id": 9, "chat_id": None,
                                                  "tag": "alderei", "created_at": T0}])
    assert out == []


def test_capture_summarises_the_fallback_and_the_candidate_chats():
    v = vr()
    payload = v.summarise_capture(TOK, HEALTHY_ROWS[:2],
                                  [msg(1, 555, "alderei"), msg(2, 555, "alderei")])
    assert payload["token_env_key"] == TOK
    assert payload["fallback_tag"] == "alderei"
    assert payload["unmatched"] == [{"chat_id": "555", "tag": "alderei",
                                     "first_seen": (T0 + timedelta(minutes=5)).isoformat(),
                                     "messages": 2}]


# ── steps 4-5: the routing check ────────────────────────────────────────────

def _check(rows, messages, key):
    return vr().evaluate(rows, messages, key)


def _by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_a_healthy_channel_passes_every_check():
    report = _check(HEALTHY_ROWS, HEALTHY_MSGS, "newclient")
    assert report["ok"] is True, json.dumps(report, indent=2)
    names = _by_name(report)
    for expected in ("channel_exists", "allowlist_declared", "no_overlap",
                     "resolves_to_self", "routed_evidence",
                     "no_foreign_rows_under_this_tag",
                     "crosswire_after_onboarding"):
        assert names[expected]["ok"] is True, expected


def test_an_unknown_channel_fails_loudly():
    report = _check(HEALTHY_ROWS, HEALTHY_MSGS, "nope")
    assert report["ok"] is False
    assert _by_name(report)["channel_exists"]["ok"] is False


def test_an_empty_allowlist_fails():
    rows = HEALTHY_ROWS[:2] + [row("newclient", "newclient", ids=[], created=T0 + timedelta(hours=1))]
    report = _check(rows, HEALTHY_MSGS, "newclient")
    assert report["ok"] is False
    assert _by_name(report)["allowlist_declared"]["ok"] is False


def test_two_rows_claiming_the_same_chat_fail():
    rows = HEALTHY_ROWS[:2] + [row("newclient", "newclient", ids=[100], created=T0 + timedelta(hours=1))]
    report = _check(rows, HEALTHY_MSGS, "newclient")
    assert report["ok"] is False
    assert _by_name(report)["no_overlap"]["ok"] is False


def test_no_routed_message_yet_fails_routed_evidence():
    report = _check(HEALTHY_ROWS, HEALTHY_MSGS[:1], "newclient")
    assert report["ok"] is False
    assert _by_name(report)["routed_evidence"]["ok"] is False


def test_a_sibling_chats_message_under_the_new_tag_is_a_cross_wire():
    msgs = HEALTHY_MSGS + [
        msg(4, 100, "newclient", created=T0 + timedelta(hours=2))]   # alderei's chat
    report = _check(HEALTHY_ROWS, msgs, "newclient")
    assert report["ok"] is False
    names = _by_name(report)
    assert names["no_foreign_rows_under_this_tag"]["ok"] is False
    assert "100" in names["no_foreign_rows_under_this_tag"]["detail"]


def test_the_new_chats_message_under_a_sibling_tag_after_onboarding_is_a_cross_wire():
    msgs = HEALTHY_MSGS + [
        msg(5, 555, "alderei", created=T0 + timedelta(hours=3))]
    report = _check(HEALTHY_ROWS, msgs, "newclient")
    assert report["ok"] is False
    assert _by_name(report)["crosswire_after_onboarding"]["ok"] is False


def test_the_new_chats_pre_onboarding_rows_are_reported_as_evidence():
    report = _check(HEALTHY_ROWS, HEALTHY_MSGS, "newclient")
    assert report["evidence"]["pre_onboarding_rows"] == 1
    assert report["evidence"]["pre_onboarding_tags"] == ["alderei"]


def test_a_pre_onboarding_row_under_a_sibling_tag_is_reported_not_failed():
    """The brandit-coffeemedia run, from the live DB: chat -5328962086's first
    messages landed under the sibling tag 'coffeemedia' 30 minutes BEFORE its
    own row existed. Nobody claimed that chat at arrival time, so it cannot be
    a cross-wire — it is the step-1 capture evidence. Asserting it must sit
    under today's fallback tag failed on real data, so the rule is time-aware:
    unclaimed-at-arrival is reported, never a gate failure."""
    msgs = HEALTHY_MSGS + [
        msg(6, 555, "coffeemedia", created=T0 + timedelta(minutes=30))]  # own row exists at T0+1h
    report = _check(HEALTHY_ROWS, msgs, "newclient")
    assert report["ok"] is True, json.dumps(report, indent=2)
    assert report["evidence"]["pre_onboarding_rows"] == 2          # 555 under alderei + coffeemedia
    assert report["evidence"]["pre_onboarding_tags"] == ["alderei", "coffeemedia"]


def test_an_unclaimed_chats_foreign_row_does_not_fail_the_gate():
    """An arrival from a chat NO row had claimed yet is the unclaimed-inbound
    path working as designed, whichever tag it landed under."""
    msgs = HEALTHY_MSGS + [
        msg(7, 909, "newclient", created=T0 + timedelta(minutes=30))]
    report = _check(HEALTHY_ROWS, msgs, "newclient")
    assert _by_name(report)["no_foreign_rows_under_this_tag"]["ok"] is True
    assert report["ok"] is True, json.dumps(report, indent=2)
    assert report["evidence"]["unclaimed_arrival_chats"] == ["909"]


def test_a_foreign_row_arriving_while_a_sibling_claimed_the_chat_is_a_cross_wire():
    """The live faizul case: chat 1578424630 was claimed by row 'faizul' at
    05:41, yet its message at 06:45 was logged under the sibling tag
    'coffeemedia'. Claimed-by-a-sibling-at-arrival is the cross-wire."""
    msgs = HEALTHY_MSGS + [
        msg(7, 909, "newclient", created=T0 + timedelta(minutes=30)),   # unclaimed, fine
        msg(8, 100, "newclient", created=T0 + timedelta(hours=2)),      # alderei claimed it since T0
    ]
    report = _check(HEALTHY_ROWS, msgs, "newclient")
    check = _by_name(report)["no_foreign_rows_under_this_tag"]
    assert check["ok"] is False
    assert "100" in check["detail"] and "alderei" in check["detail"]
    assert report["evidence"]["cross_wire_rows"] == 1
    assert report["evidence"]["unclaimed_arrival_chats"] == ["909"]


def test_an_invite_bind_time_is_the_real_claim_time():
    """The live faizul case, exactly: its row was created 05:41 but the invite
    bound chat 1578424630 at 06:45:39, and the tap itself is the message that
    arrived at 06:45. A chat is claimed from the BIND moment, not from the
    row's created_at — otherwise every invite onboarding reports its own
    deep-link tap as a cross-wire."""
    bound = row("newclient", "newclient", ids=[555], created=T0 + timedelta(hours=1))
    bound["bound_chats"] = {"555": T0 + timedelta(hours=2)}      # invite bound at T0+2h
    rows = HEALTHY_ROWS[:2] + [bound]
    msgs = HEALTHY_MSGS + [
        msg(3, 200, "coffeemedia", created=T0 + timedelta(minutes=5)),      # coffeemedia's own
        msg(9, 555, "coffeemedia", created=T0 + timedelta(hours=1, minutes=30)),  # the tap, pre-bind
    ]
    report = _check(rows, msgs, "coffeemedia")
    assert _by_name(report)["no_foreign_rows_under_this_tag"]["ok"] is True, \
        json.dumps(report, indent=2)


def test_a_message_under_this_tag_after_the_bind_is_still_a_cross_wire():
    bound = row("newclient", "newclient", ids=[555], created=T0 + timedelta(hours=1))
    bound["bound_chats"] = {"555": T0 + timedelta(hours=2)}
    rows = HEALTHY_ROWS[:2] + [bound]
    msgs = HEALTHY_MSGS + [
        msg(3, 200, "coffeemedia", created=T0 + timedelta(minutes=5)),
        msg(10, 555, "coffeemedia", created=T0 + timedelta(hours=3)),   # after the bind
    ]
    report = _check(rows, msgs, "coffeemedia")
    check = _by_name(report)["no_foreign_rows_under_this_tag"]
    assert check["ok"] is False
    assert "newclient" in check["detail"]


def test_json_output_is_stable_and_machine_readable():
    report = _check(HEALTHY_ROWS, HEALTHY_MSGS, "newclient")
    assert json.loads(json.dumps(report)) == report


# ── e2e: the checklist against a real database ──────────────────────────────

psycopg = pytest.importorskip("psycopg")


def _server_bindir():
    candidates = []
    found = shutil.which("initdb")
    if found:
        candidates.append(Path(found).parent)
    for prefix in ("/opt/homebrew/opt", "/usr/local/opt"):
        candidates.extend(sorted(Path(prefix).glob("postgresql*/bin"), reverse=True))
    for d in candidates:
        if (d / "postgres").exists() and (d / "initdb").exists():
            return d
    return None


@pytest.fixture(scope="module")
def cluster():
    bindir = _server_bindir()
    if bindir is None:
        pytest.skip("no postgres SERVER binaries on this host")
    psql = shutil.which("psql") or str(bindir / "psql")
    initdb, pg_ctl = str(bindir / "initdb"), str(bindir / "pg_ctl")
    tmp = tempfile.mkdtemp(prefix="tgroute-")
    data, sock = Path(tmp) / "data", Path(tmp) / "sock"
    sock.mkdir()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    schema = """
    CREATE TABLE bot_channels (
        channel_key text PRIMARY KEY,
        token_env_key text NOT NULL,
        channel_tag text NOT NULL,
        allowed_chat_ids bigint[],
        allowed_usernames text[],
        enabled boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE operator_messages (
        id bigserial PRIMARY KEY,
        direction text NOT NULL,
        channel text,
        chat_id text,
        tag text,
        text text,
        delivered boolean,
        created_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE bot_channel_invites (
        id bigserial PRIMARY KEY,
        channel_key text NOT NULL,
        token_hash text,
        bound_chat_id bigint,
        used_at timestamptz,
        expires_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now());
    """
    try:
        subprocess.run([initdb, "-D", str(data), "-U", "postgres", "-A", "trust",
                        "--no-sync", "-E", "UTF8"],
                       check=True, capture_output=True, text=True, env=env)
        subprocess.run([pg_ctl, "-D", str(data), "-l", str(Path(tmp) / "log"),
                        "-o", f"-k {sock} -p {port} -c listen_addresses=127.0.0.1",
                        "-w", "start"], check=True, capture_output=True, text=True, env=env)
        conninfo = f"host={sock} port={port} dbname=postgres user=postgres"
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(schema)
        yield conninfo, psql
    finally:
        subprocess.run([pg_ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
                       check=False, capture_output=True, text=True, env=env)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def db(cluster):
    conninfo, _ = cluster
    c = psycopg.connect(conninfo)
    c.execute("TRUNCATE bot_channels, operator_messages, bot_channel_invites RESTART IDENTITY")
    c.execute("""INSERT INTO bot_channels
        (channel_key, token_env_key, channel_tag, allowed_chat_ids, created_at)
        VALUES ('alderei', %s, 'alderei', ARRAY[100], now() - interval '30 days'),
               ('coffeemedia', %s, 'coffeemedia', ARRAY[200], now() - interval '30 days')""",
              (TOK, TOK))
    c.commit()
    yield c
    c.close()


def _capture(conn, token=TOK):
    v = vr()
    rows = v.load_group(conn, token)
    return v.summarise_capture(token, rows, v.load_group_messages(conn, rows))


def test_e2e_capture_reads_the_chat_id_from_the_unclaimed_row(db):
    db.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, delivered)
                  VALUES ('inbound','telegram','555','alderei',true)""")
    db.commit()
    cap = _capture(db)
    assert cap["fallback_tag"] == "alderei"
    assert [u["chat_id"] for u in cap["unmatched"]] == ["555"]


def test_e2e_a_capped_scan_keeps_the_NEWEST_rows(db):
    """`ORDER BY id LIMIT n` returns the OLDEST n — a capped capture would
    then never see today's unclaimed arrivals and report 'no unclaimed chats'
    forever."""
    v = vr()
    for i in range(5):
        db.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, delivered)
                      VALUES ('inbound','telegram','555','alderei',true)""")
    db.commit()
    rows = v.load_group(db, TOK)
    ids = [m["id"] for m in v.load_group_messages(db, rows, limit=2)]
    all_ids = [r[0] for r in db.execute(
        "SELECT id FROM operator_messages ORDER BY id").fetchall()]
    assert ids == all_ids[-2:], f"capped scan returned {ids}, not the newest two of {all_ids}"


def test_e2e_an_invite_bound_chat_is_only_claimed_from_the_bind(db):
    """The live faizul shape: row created an hour ago with an invite minted, the
    very tap that binds the chat logged under a sibling tag shortly after the
    row appeared, then the first real message under the new tag."""
    v = vr()
    db.execute("""INSERT INTO bot_channels
                  (channel_key, token_env_key, channel_tag, allowed_chat_ids, created_at)
                  VALUES ('newclient', %s, 'newclient', ARRAY[555], now() - interval '1 hour')""",
               (TOK,))
    db.execute("""INSERT INTO bot_channel_invites (channel_key, bound_chat_id, used_at)
                  VALUES ('newclient', 555, now() - interval '10 minutes')""")
    db.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, delivered, created_at)
                  VALUES ('inbound','telegram','555','alderei',true, now() - interval '30 minutes'),
                         ('inbound','telegram','555','newclient',true, now() - interval '5 minutes')""")
    db.commit()

    report = v.check(db, "newclient")
    assert report["ok"] is True, json.dumps(report, indent=2)
    assert report["evidence"]["pre_onboarding_rows"] == 1
    assert report["evidence"]["pre_onboarding_tags"] == ["alderei"]


def test_e2e_check_proves_the_second_message_routed_and_no_cross_wire(db):
    v = vr()
    db.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, delivered, created_at)
                  VALUES ('inbound','telegram','555','alderei',true, now() - interval '2 hours')""")
    # step 3: the onboarding row exists, gated + applied (enabled=false here is fine)
    db.execute("""INSERT INTO bot_channels
                  (channel_key, token_env_key, channel_tag, allowed_chat_ids, created_at)
                  VALUES ('newclient', %s, 'newclient', ARRAY[555], now() - interval '1 hour')""", (TOK,))
    # step 4: the second message, after the restart
    db.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, delivered)
                  VALUES ('inbound','telegram','555','newclient',true)""")
    db.commit()

    report = v.check(db, "newclient")
    assert report["ok"] is True, json.dumps(report, indent=2)
    assert report["evidence"]["routed_rows"] == 1
    assert report["evidence"]["pre_onboarding_rows"] == 1

    # and the tool catches a real cross-wire injected after the fact
    db.execute("""INSERT INTO operator_messages (direction, channel, chat_id, tag, delivered)
                  VALUES ('inbound','telegram','100','newclient',true)""")
    db.commit()
    bad = v.check(db, "newclient")
    assert bad["ok"] is False
    failed = [c["name"] for c in bad["checks"] if not c["ok"]]
    assert "no_foreign_rows_under_this_tag" in failed

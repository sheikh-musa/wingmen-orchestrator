"""fleet_health step 8 -- idle-in-transaction watch (console #78501/#78690/#78695).

pid 490867 sat idle in transaction ~2d21h holding a lock on agent_messages: an
ad-hoc in-session bus-watch loop that never committed. Step A is MEASURE mode:
log every session idle in tx past 5 min, WARN past 30, page console P2 once per
episode past 1 h, and NEVER terminate anything. Pure-logic tests, no live DB.
"""
import importlib

fh = importlib.import_module("scripts.fleet_health")


def sess(pid=490867, idle_s=4000, xact_epoch=1_700_000_000.0, query="select 1"):
    return {"pid": pid, "idle_s": idle_s, "xact_epoch": xact_epoch, "tx_age_s": idle_s + 5,
            "usename": "postgres", "app": "Supavisor", "client_addr": "10.0.0.1",
            "query": query}


def test_thresholds_are_consoles_ruling():
    assert (fh.IDLE_TX_MEASURE_S, fh.IDLE_TX_WARN_S, fh.IDLE_TX_PAGE_S) == (300, 1800, 3600)


def test_short_idle_is_ignored_entirely():
    p = fh.idle_tx_plan([sess(idle_s=60)], {})
    assert p["measure"] == [] and p["warn"] == [] and p["page"] == []


def test_past_5_min_is_measured_only():
    p = fh.idle_tx_plan([sess(idle_s=400)], {})
    assert [s["pid"] for s in p["measure"]] == [490867]
    assert p["warn"] == [] and p["page"] == []


def test_past_30_min_warns_but_does_not_page():
    p = fh.idle_tx_plan([sess(idle_s=1900)], {})
    assert [s["pid"] for s in p["warn"]] == [490867] and p["page"] == []


def test_past_1h_pages_once():
    p = fh.idle_tx_plan([sess(idle_s=4000)], {})
    assert [s["pid"] for s in p["page"]] == [490867]


def test_already_paged_episode_is_not_repaged():
    s = sess(idle_s=9000)
    p1 = fh.idle_tx_plan([s], {})
    state = dict(p1["state"])
    state[fh.idle_tx_key(s)] = "paged"
    p2 = fh.idle_tx_plan([s], state)
    assert p2["page"] == []
    assert p2["state"][fh.idle_tx_key(s)] == "paged"


def test_reused_pid_with_new_transaction_is_a_new_episode():
    old = sess(xact_epoch=1_700_000_000.0)
    new = sess(xact_epoch=1_700_009_999.0)
    p = fh.idle_tx_plan([new], {fh.idle_tx_key(old): "paged"})
    assert [s["xact_epoch"] for s in p["page"]] == [1_700_009_999.0]


def test_episode_that_ended_is_dropped_from_state():
    s = sess()
    p = fh.idle_tx_plan([], {fh.idle_tx_key(s): "paged"})
    assert p["state"] == {}


def test_query_literals_are_masked_so_a_secret_in_sql_never_reaches_the_bus():
    q = "ALTER ROLE x PASSWORD 'hunter2-very-secret'; select * from t where a = 'abc'"
    m = fh.mask_query(q)
    assert "hunter2" not in m and "abc" not in m
    assert "ALTER ROLE x PASSWORD '?'" in m


def test_query_prefix_is_bounded_and_whitespace_collapsed():
    m = fh.mask_query("select\n   id,\n  subject  from agent_messages " + "x" * 500)
    assert m.startswith("select id, subject from agent_messages")
    assert len(m) <= fh.IDLE_TX_QUERY_CHARS


def test_the_490867_shape_is_identifiable_from_the_page_body():
    s = sess(query="select id, priority, message_type, from_agent, subject from agent_messages "
                   "where id > 7859 and to_agent in ('cc-cosem-platform-4','cc-cosem-platform','*')")
    body = fh.idle_tx_page_body(s)
    assert "490867" in body and "agent_messages" in body and "Supavisor" in body
    assert "cc-cosem-platform-4" not in body          # literals masked, shape kept
    assert "never terminated" in body.lower()

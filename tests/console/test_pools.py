"""op#20684 — "which lanes are on which key", on BOTH consoles.

LOCKS:
  * the HOSTED (phone) payload carries the pool NICKNAME per lane/coordinator/
    bloat row and NEVER the raw auth_fp (cond-2 field list + the nickname).
  * the LOCAL /api/fleet lane row carries `pool` too (same key on both surfaces).
  * fp -> pool is ONE set: pools.py (backend SSOT) == panes._KNOWN_ACCOUNTS ==
    fleet.js POOL_FP == irsyad.js acctForFp — musa2 included everywhere.
"""
import pathlib
import re

from nervous_system.console import hosted_view, panes, pools

_STATIC = pathlib.Path(__file__).resolve().parents[2] / "nervous_system" / "console" / "static"
_MUSA, _MUSA2, _SYED = "68142948c003", "e1dfa48eec85", "582043088eae"


def test_pool_for_fp_full_prefix_and_unknown():
    assert pools.pool_for_fp(_MUSA) == "Musa"
    assert pools.pool_for_fp(_MUSA2) == "musa2"
    assert pools.pool_for_fp(_SYED) == "Syed"
    assert pools.pool_for_fp("e1dfa48") == "musa2"          # fleet.js-length prefix
    assert pools.pool_for_fp("deadbeef0000") == ""          # unknown -> "", NEVER the fp
    assert pools.pool_for_fp("e1d") == ""                    # too short to trust
    assert pools.pool_for_fp(None) == "" and pools.pool_for_fp("") == ""


def test_backend_map_is_the_ssot_for_panes_fleet_js_and_irsyad_js():
    assert panes._KNOWN_ACCOUNTS == pools.KNOWN_POOLS
    assert panes._KNOWN_ACCOUNTS[_MUSA2] == "musa2"        # the stale-copy bug
    fleet = (_STATIC / "fleet.js").read_text()
    m = re.search(r"var POOL_FP = \[(.*?)\];", fleet)
    assert m, "fleet.js must carry POOL_FP"
    js_pairs = dict(re.findall(r'\["([0-9a-f]+)",\s*"([^"]+)"\]', m.group(1)))
    assert set(js_pairs.values()) == set(pools.KNOWN_POOLS.values())
    for prefix, name in js_pairs.items():
        assert pools.pool_for_fp(prefix) == name, (prefix, name)
    irsyad = (_STATIC / "irsyad.js").read_text()
    for full, name in pools.KNOWN_POOLS.items():
        assert re.search(r'fp\.indexOf\("' + full[:7] + r'[0-9a-f]*"\) === 0\) return "' + name + '"', irsyad), name


class _Cur:
    def __init__(self, rows): self.sql, self._rows = None, rows
    def execute(self, sql, *a): self.sql = sql
    def fetchall(self): return self._rows


def _lane_row(fp, agent="cc-hifz", sess="hifz"):
    # agent_id, base_agent_id, status, current_task, tmux_session, auth_fp, host,
    # heartbeat_age_s, desired_state, lane, activity, activity_age_s
    return (agent, agent, "working", "building", sess, fp, "mini", 10, "up", sess, "ship it", 30)


def test_hosted_lane_rows_carry_pool_nickname_never_raw_fp():
    rows = hosted_view._clone_lanes(_Cur([
        _lane_row(_MUSA2, "cc-irsyad", "irsyad"),
        _lane_row(_MUSA, "cc-hifz", "hifz"),
        _lane_row("deadbeef0000", "cc-x", "x"),
        _lane_row(None, "cc-y", "y"),
    ]))
    by = {r["tmux_session"]: r for r in rows}
    assert by["irsyad"]["pool"] == "musa2" and by["hifz"]["pool"] == "Musa"
    assert by["x"]["pool"] == "" and by["y"]["pool"] == ""
    for r in rows:
        assert "auth_fp" not in r and "auth_account" not in r
        # the raw id must not ride along under ANY key
        assert _MUSA not in str(r) and _MUSA2 not in str(r) and "deadbeef" not in str(r)


def test_hosted_coordinator_and_bloat_rows_carry_pool_not_fp(monkeypatch):
    # coordinators: agent_id, short, role_label, tmux_session, activity, activity_age_s,
    #               ctx_tokens, auth_fp, host, last_seen_s, peekable
    cur = _Cur([("cai", "cai", "governance", "cai", "ruling", 5, 120000, _SYED, "mini", 5, True)])
    monkeypatch.setattr(hosted_view, "_CTX_WINDOW", 1_000_000, raising=False)
    coords = hosted_view._clone_coordinators(cur)
    assert coords and coords[0]["pool"] == "Syed" and "auth_fp" not in coords[0]
    # context bloat: cc_identity, sub_tag, ctx_tokens, age_s, auth_fp, host
    bloat = hosted_view._clone_context_bloat(_Cur([("cc-hifz", None, 500000, 10, _MUSA2, "mini")]))
    assert bloat and bloat[0]["pool"] == "musa2" and "auth_fp" not in bloat[0]


def test_hosted_cloned_payload_has_no_auth_fp_anywhere():
    class _Conn:
        def cursor(self): return _AnyCur()

    class _AnyCur(_Cur):
        def __init__(self): super().__init__([])
        def fetchall(self):
            return [_lane_row(_MUSA2)] if "FROM agent_status s" in (self.sql or "") else []

    payload = hosted_view.build_cloned_payload(_Conn())
    assert payload["lanes"] and payload["lanes"][0]["pool"] == "musa2"

    def walk(n):
        if isinstance(n, dict):
            assert "auth_fp" not in n
            for v in n.values(): walk(v)
        elif isinstance(n, list):
            for v in n: walk(v)
        elif isinstance(n, str):
            assert _MUSA2 not in n and _MUSA not in n and _SYED not in n
    walk(payload)


def test_local_fleet_lane_rows_carry_pool_alongside_fp():
    """app.py: every finalised /api/fleet lane row gets `pool` from its auth_fp so
    fleet.js reads ONE key on both consoles."""
    src = (pathlib.Path(__file__).resolve().parents[2] / "nervous_system" / "console" / "app.py").read_text()
    assert 'l["pool"] = pools.pool_for_fp(l.get("auth_fp"))' in src
    assert '"pool": pools.pool_for_fp(r.get("auth_fp"))' in src   # context_bloat rows too


def test_fleet_html_carries_the_key_rollup_row():
    html = (_STATIC / "fleet.html").read_text()
    assert 'id="keyRoll"' in html and ".keyroll:empty { display: none; }" in html
    assert ".tile.offpool" in html
    fleet = (_STATIC / "fleet.js").read_text()
    assert "function poolRollup(" in fleet and 'renderKeyRoll(' in fleet
    assert "poolRollup: poolRollup" in fleet   # exported for the node test

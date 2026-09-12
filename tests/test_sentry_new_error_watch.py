"""Sentry new-error watch — pure decision core (no network).

Musa directive op#19859 (Nazim 39189/39191): a standing watch that surfaces each
genuinely-new SERVER/app Sentry issue ONCE, filters client-side browser noise
(MetaMask / extension / <unknown>), and escalates ONLY the material ones — paging
the operator once per material issue (re-escalate only on reopened / fresh spike,
never every tick). These tests pin the pure core: is_noise, is_material, decide.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import sentry_new_error_watch as sw  # noqa: E402


def _issue(id, title, culprit="", level="error", count=1):
    return {"id": str(id), "title": title, "culprit": culprit, "level": level, "count": count}


def _state(reported=None, escalated=None):
    return {"reported_ids": list(reported or []), "escalated": dict(escalated or {})}


# ── is_noise: client-side browser offenders are noise; server/app errors are not ──
def test_is_noise_matches_metamask():
    assert sw.is_noise("i: Failed to connect to MetaMask", "/dashboard/tabung/reports/:id")


def test_is_noise_matches_extension_receiving_end():
    assert sw.is_noise("Error: Could not establish connection. Receiving end does not exist.", "/dashboard")


def test_is_noise_matches_unknown():
    assert sw.is_noise("<unknown>", "/dashboard")


def test_is_noise_false_for_real_server_error():
    assert not sw.is_noise("Error: academic_admin student-profile audit-log write failed",
                           "GET /dashboard/school/students/[id]")


# ── is_material: fatal / server-error / spike are material; benign new is not ──
def test_is_material_fatal():
    assert sw.is_material(_issue(1, "Error: aborted", "abortIncoming(node:_http_server)", level="fatal"))


def test_is_material_server_error_pattern():
    assert sw.is_material(_issue(2, "Error: academic_admin student-profile audit-log write failed",
                                 "GET /dashboard/school/students/[id]"))


def test_is_material_high_count_spike():
    assert sw.is_material(_issue(3, "Error: Load failed", "/dashboard", count=sw.MATERIAL_SPIKE_COUNT + 1))


def test_is_material_false_for_benign_low_count():
    assert not sw.is_material(_issue(4, "Error: minor render warning", "/dashboard", level="error", count=1))


# ── decide: surface each NEW non-noise issue once ──
def test_decide_surfaces_new_non_noise_once():
    issues = [_issue(10, "Error: audit-log write failed", "GET /x"),
              _issue(11, "i: Failed to connect to MetaMask", "/y")]  # noise
    out = sw.decide(_state(), issues)
    assert [i["id"] for i in out["surface"]] == ["10"]     # noise (11) not surfaced
    assert "10" in out["state"]["reported_ids"]
    assert "11" not in out["state"]["reported_ids"]        # noise never tracked


def test_decide_skips_already_reported():
    issues = [_issue(10, "Error: audit-log write failed", "GET /x")]
    out = sw.decide(_state(reported=["10"]), issues)
    assert out["surface"] == []                             # already reported -> not surfaced again


def test_decide_noise_never_escalated_even_if_high_count():
    issues = [_issue(30, "i: Failed to connect to MetaMask", "/z", count=999)]
    out = sw.decide(_state(), issues)
    assert out["surface"] == [] and out["escalate"] == []


# ── decide: escalate ONLY material, and dedupe (page once) ──
def test_decide_escalates_only_material():
    issues = [_issue(20, "Error: audit-log write failed", "GET /x"),        # material (server)
              _issue(21, "Error: minor render warning", "/y", count=1)]     # non-noise, not material
    out = sw.decide(_state(), issues)
    assert {i["id"] for i in out["surface"]} == {"20", "21"}   # both surfaced (non-noise, new)
    assert {i["id"] for i in out["escalate"]} == {"20"}         # only the material one escalated
    assert "20" in out["state"]["escalated"]                    # escalation tracked


def test_decide_escalate_dedups_recurring_material():
    # already escalated at count=6; same issue at count=6 next tick -> NOT re-escalated
    issues = [_issue(20, "Error: audit-log write failed", "GET /x", count=6)]
    out = sw.decide(_state(reported=["20"], escalated={"20": 6}), issues)
    assert out["escalate"] == []


def test_decide_reescalates_on_fresh_spike():
    # escalated at count=6; count jumps by >= spike delta -> re-escalate
    issues = [_issue(20, "Error: audit-log write failed", "GET /x",
                     count=6 + sw.MATERIAL_SPIKE_COUNT)]
    out = sw.decide(_state(reported=["20"], escalated={"20": 6}), issues)
    assert {i["id"] for i in out["escalate"]} == {"20"}
    assert out["state"]["escalated"]["20"] == 6 + sw.MATERIAL_SPIKE_COUNT   # mark advanced


def test_decide_prunes_resolved_so_reopened_refires():
    # id 20 was reported+escalated; it is NO LONGER in the unresolved list (resolved)
    # -> pruned from state, so a later reopened occurrence re-enters as new.
    out = sw.decide(_state(reported=["20", "10"], escalated={"20": 6}), [_issue(10, "Error: x", "/x")])
    assert "20" not in out["state"]["reported_ids"]     # pruned (resolved)
    assert "20" not in out["state"]["escalated"]         # pruned
    assert "10" in out["state"]["reported_ids"]          # still-unresolved kept

"""Caller audit for operator_log's scoped read/stamp API (gate condition on PR #226,
orch-console bus #46674): with '' no longer an unscoped role, any process that calls
operator_log.unprocessed()/mark_handled_through() with ORCH_BODY_ROLE unset and no tag=
RAISES. This pins the audit's findings so a future caller can't regress them silently:

  * the ONLY programmatic caller is nervous_system/hub_self_recovery.py, which runs under
    deploy/wingmen-hub-self-recovery.service with Environment=ORCH_BODY_ROLE=hub (verified
    live on gzb 2026-09-30: unit + process env both 'hub'); it also self-gates on the role.
  * the two interactive singleton bodies (hub claude via orch_supervisor.sh `export
    ORCH_BODY_ROLE=hub`; Nazim via the Mini .env ORCH_BODY_ROLE=console) call it from
    python heredocs — covered by their launchers, not by code here.
  * lanes never call operator_log directly; they use scripts/lane_operator_reconcile.py --tag.
  * ingest.py / tg_out.py / agent_wake_subscriber.py (role UNSET on gzb after the #46268
    identity strip) only WRITE via operator_log.log() — no scoped read/stamp.
"""
import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import ast

_SCOPED = {"unprocessed", "mark_handled_through", "mark_handled"}
# Reviewed programmatic callers (path relative to repo root). Adding one here = a review
# that its runtime ORCH_BODY_ROLE (or tag=) is set by its launcher/unit.
_REVIEWED = {
    "nervous_system/hub_self_recovery.py",   # role=hub pinned by its unit (test below)
}


def _py_files():
    for top in ("nervous_system", "scripts"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, top)):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.relpath(os.path.join(dirpath, f), ROOT)


def _calls_scoped_api(src: str) -> bool:
    """True if the module contains a REAL call `<x>.unprocessed(...)` / `.mark_handled*(...)`
    on a name bound to operator_log (attribute call), or a bare call after
    `from ... operator_log import unprocessed|mark_handled...`. Strings/comments don't count."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("operator_log"):
            imported |= {a.asname or a.name for a in node.names if a.name in _SCOPED}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in _SCOPED:
            base = f.value
            if isinstance(base, ast.Name) and base.id in ("operator_log", "ol"):
                return True
        if isinstance(f, ast.Name) and f.id in imported:
            return True
    return False


def test_only_reviewed_modules_call_the_scoped_read_or_stamp():
    offenders = []
    for rel in _py_files():
        if rel in ("nervous_system/operator_log.py", "scripts/lane_operator_reconcile.py"):
            continue
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        if _calls_scoped_api(src):
            offenders.append(rel)
    assert set(offenders) <= _REVIEWED, (
        f"new programmatic caller(s) of operator_log's scoped API: "
        f"{sorted(set(offenders) - _REVIEWED)} — with ORCH_BODY_ROLE unset they RAISE. Set the "
        f"role in the launcher/unit (or pass tag=) and add the path to _REVIEWED after review.")


def test_hub_self_recovery_unit_pins_the_hub_role():
    unit = os.path.join(ROOT, "deploy", "wingmen-hub-self-recovery.service")
    with open(unit, encoding="utf-8") as fh:
        text = fh.read()
    assert re.search(r"^Environment=ORCH_BODY_ROLE=hub\s*$", text, re.M), (
        "wingmen-hub-self-recovery.service must pin Environment=ORCH_BODY_ROLE=hub: the gzb "
        "secrets bundle no longer carries identity vars (#46268), so without this the unit's "
        "operator_log.unprocessed() read would run as a tag-less LANE and raise.")


def test_hub_self_recovery_reader_refuses_without_hub_role(monkeypatch):
    """Belt: even if the unit lost its Environment line, the reader raises loudly (a lane
    without tag=) rather than reading an unscoped queue."""
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from nervous_system import hub_self_recovery as hsr
    with pytest.raises(ValueError) as exc:
        hsr.fetch_operator_unprocessed(limit=1)
    assert "tag" in str(exc.value)

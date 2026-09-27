"""_open_asks_block() (op#22669, piece 5): surfaces standing operator_asks
delegated to orch-console at boot (SessionStart reconstitution), so a fresh
context can't lose sight of a live ask. Fail-safe by construction — any error
(DB down, migration not applied yet, ...) yields an empty block, never a
boot-blocking exception. PURE formatting tests — operator_log.open_asks_for is
monkeypatched, no live DB."""
import importlib

import pytest

ssr = importlib.import_module("scripts.session_start_reconstitute")


def _patch_open_asks_for(monkeypatch, fn):
    from nervous_system import operator_log
    monkeypatch.setattr(operator_log, "open_asks_for", fn)


def test_empty_when_no_open_asks(monkeypatch):
    _patch_open_asks_for(monkeypatch, lambda body: [])
    assert ssr._open_asks_block() == ""


def test_empty_on_any_exception(monkeypatch):
    def _boom(body):
        raise RuntimeError("db down")
    _patch_open_asks_for(monkeypatch, _boom)
    assert ssr._open_asks_block() == ""


def test_lists_each_open_ask_with_id_and_text(monkeypatch):
    rows = [
        (1, "ship the thing", "orch-console", "2026-09-27T10:00:00Z", False, None),
        (2, "approve the migration", "orch-console", "2026-09-27T11:00:00Z", False, None),
    ]
    _patch_open_asks_for(monkeypatch, lambda body: rows)
    block = ssr._open_asks_block()
    assert "#1" in block and "ship the thing" in block
    assert "#2" in block and "approve the migration" in block
    assert "orch-console" in block


def test_flags_waiting_on_operator_row(monkeypatch):
    rows = [(3, "waiting on musa ask", "orch-console", "2026-09-27T12:00:00Z", True, None)]
    _patch_open_asks_for(monkeypatch, lambda body: rows)
    block = ssr._open_asks_block()
    assert "[WAITING ON MUSA]" in block


def test_non_waiting_row_has_no_waiting_flag(monkeypatch):
    rows = [(4, "just an open ask", "orch-console", "2026-09-27T13:00:00Z", False, None)]
    _patch_open_asks_for(monkeypatch, lambda body: rows)
    block = ssr._open_asks_block()
    assert "[WAITING ON MUSA]" not in block


def test_includes_chase_by_when_set(monkeypatch):
    rows = [(5, "chase-by ask", "orch-console", "2026-09-27T14:00:00Z", True, "2026-09-27T20:00:00Z")]
    _patch_open_asks_for(monkeypatch, lambda body: rows)
    block = ssr._open_asks_block()
    assert "chase by 2026-09-27T20:00:00Z" in block


def test_omits_chase_clause_when_not_set(monkeypatch):
    rows = [(6, "no deadline ask", "orch-console", "2026-09-27T15:00:00Z", True, None)]
    _patch_open_asks_for(monkeypatch, lambda body: rows)
    block = ssr._open_asks_block()
    assert "chase by" not in block


def test_calls_open_asks_for_with_orch_console(monkeypatch):
    seen = {}

    def _fake(body):
        seen["body"] = body
        return []
    _patch_open_asks_for(monkeypatch, _fake)
    ssr._open_asks_block()
    assert seen["body"] == "orch-console"

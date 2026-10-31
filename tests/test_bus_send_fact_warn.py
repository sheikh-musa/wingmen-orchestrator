"""Memory 2.0 Part 2 PR C (op#36768, backlog #127): bus_send.py WARNS when a
message asserts it is waiting on Musa for something fleet_memory records as
done.

Console's ruling (bus #87068) splits the two call sites deliberately:
asks_open.py REFUSES, because an ask row is a durable claim on the operator's
attention; bus_send.py only WARNS, because the bus is how the fleet talks --
a refusal there would block reporting, corrections and handoffs, and the
sentence being flagged is often somebody else's, quoted.

WARN must never cost a send: no live DB in these tests, the matcher is
injected, and a failing matcher is silent.
"""
from __future__ import annotations

import io

from scripts import bus_send as bs

from scripts.lib.fleet_memory import Fact
from datetime import datetime, timezone

BODY = "DeepSeek login still waiting on Musa's approval before I can continue."


def _fact(fid=7, text="DeepSeek console login done 30 Oct, profile in place"):
    now = datetime.now(timezone.utc)
    return Fact(fid, "fleet", None, text, "op#36768", "cc-seed", now, now, None)


def run(subject="status", body=BODY, mtype="update", matching=None, monkeypatch=None):
    out = io.StringIO()
    logged = []
    if monkeypatch is not None:
        monkeypatch.setattr(bs, "_log_warn", lambda kind, **f: logged.append((kind, f)))
    bs.warn_if_asserting_pending_over_a_known_fact(
        mtype, subject, body, stream=out,
        matching=matching if matching is not None else (lambda t: []),
    )
    return out.getvalue(), logged


def test_waiting_on_musa_over_a_known_fact_warns(monkeypatch):
    msg, logged = run(matching=lambda t: [_fact()], monkeypatch=monkeypatch)
    assert "WARNING" in msg
    assert "fleet_memory" in msg and "#7" in msg
    assert logged and logged[0][0] == "asserting_pending_over_fact"


def test_the_warning_shows_the_fact_so_the_sender_can_settle_it(monkeypatch):
    msg, _ = run(matching=lambda t: [_fact()], monkeypatch=monkeypatch)
    assert "DeepSeek console login done" in msg


def test_an_ordinary_update_never_looks_at_the_database(monkeypatch):
    """The overwhelming majority of bus traffic claims nothing is pending.
    Those sends must not pay a DB round-trip, so the matcher is not called."""
    calls = []

    def matcher(text):
        calls.append(text)
        return [_fact()]

    msg, _ = run(body="PR#748 is rebased and green.", matching=matcher,
                 monkeypatch=monkeypatch)
    assert msg == ""
    assert calls == []


def test_a_subject_that_waits_on_musa_is_checked_too(monkeypatch):
    msg, _ = run(subject="Waiting on Musa: DeepSeek login", body="nothing more to say",
                 matching=lambda t: [_fact()], monkeypatch=monkeypatch)
    assert "WARNING" in msg


def test_pending_language_without_a_match_is_quiet(monkeypatch):
    msg, logged = run(matching=lambda t: [], monkeypatch=monkeypatch)
    assert msg == "" and logged == []


def test_a_failing_matcher_is_silent_and_still_sends(monkeypatch):
    """A dead DB, a missing table, a 139 that has not been applied -- none of
    them may add a traceback to every send on the box."""

    def boom(text):
        raise RuntimeError("relation fleet_memory does not exist")

    msg, logged = run(matching=boom, monkeypatch=monkeypatch)
    assert msg == "" and logged == []


def test_it_warns_rather_than_refusing(monkeypatch):
    """The refusal/refuse split is console's ruling: this function can only
    print. It returns None and raises nothing even on a definite match."""
    assert run(matching=lambda t: [_fact()], monkeypatch=monkeypatch)[0] != ""
    assert bs.warn_if_asserting_pending_over_a_known_fact(
        "update", "s", BODY, stream=io.StringIO(), matching=lambda t: [_fact()]
    ) is None


def test_main_still_completes_a_dry_run_with_a_matching_fact(monkeypatch):
    """Wired into main(): the send path is unchanged, the warning is extra."""
    monkeypatch.setattr(bs, "resolve_from_agent", lambda env: "cc-second-brain")
    monkeypatch.setattr(bs, "_log_warn", lambda kind, **f: None)
    monkeypatch.setattr(bs, "_default_fact_match", lambda text: [_fact()])
    rc = bs.main(["--to", "orch-console", "--type", "update", "--subject", "status",
                  "--priority", "P2", "--dry-run", "--body", BODY])
    assert rc == 0

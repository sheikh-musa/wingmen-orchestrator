"""console ruling #78298 A: an orch-console delegation to a lane (fresh thread) whose
subject has no op#N token gets a bus_send WARN, so the slow-ask net can tie the lane's
ack to Musa's ask. WARN only: never blocks a send. No live DB (is_lane injected)."""
import io

from scripts import bus_send as bs


def run(frm="orch-console", to="cc-cosem-adcda-2", subject="PR#490: rebase now",
        thread=None, reply_to=None, is_lane=lambda t: True, monkeypatch=None):
    out = io.StringIO()
    logged = []
    if monkeypatch is not None:
        monkeypatch.setattr(bs, "_log_warn", lambda kind, **f: logged.append((kind, f)))
    bs.warn_if_delegation_without_op(frm, to, subject, thread, reply_to,
                                     stream=out, is_lane=is_lane)
    return out.getvalue(), logged


def test_console_delegation_to_lane_without_op_warns_and_logs(monkeypatch):
    msg, logged = run(monkeypatch=monkeypatch)
    assert "no op number" in msg
    assert logged and logged[0][0] == "delegation_without_op"


def test_subject_citing_op_is_quiet(monkeypatch):
    for subj in ("Musa op#34818: remove SCDF Theory", "MUSA RULED Slice 2 (op#34816): x",
                 "OP# 34839 rebase"):
        assert run(subject=subj, monkeypatch=monkeypatch)[0] == ""


def test_other_senders_are_never_checked(monkeypatch):
    assert run(frm="cc-fleet-health", monkeypatch=monkeypatch)[0] == ""


def test_replies_in_an_existing_thread_are_not_delegations(monkeypatch):
    assert run(reply_to=78234, monkeypatch=monkeypatch)[0] == ""
    assert run(thread="b9d7bb61", monkeypatch=monkeypatch)[0] == ""


def test_non_lane_recipient_is_quiet(monkeypatch):
    assert run(to="cai", is_lane=lambda t: False, monkeypatch=monkeypatch)[0] == ""


def test_lane_lookup_failure_never_blocks_the_send(monkeypatch):
    def boom(t):
        raise RuntimeError("db down")

    msg, logged = run(is_lane=boom, monkeypatch=monkeypatch)
    assert "could not run" in msg and logged == []


def test_bare_pr_or_bus_numbers_do_not_count_as_an_op_token(monkeypatch):
    assert "no op number" in run(subject="PR#768 + bus #78259 follow-up",
                                 monkeypatch=monkeypatch)[0]

"""Console #78250: (1) an overdue page to a BASE id with 2+ live instances goes to the
instance that last worked the ask, else to orch-console — never refused into a log (#2141
-> cc-cosem-platform failed daily); (2) any asks launchd job exiting non-zero pages
orch-console once per failure episode. Pure-logic, no live DB.
"""
import datetime as dt
import importlib

ard = importlib.import_module("scripts.asks_reconcile_daily")
fh = importlib.import_module("scripts.fleet_health")

T = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)


# ── choose_instance ──────────────────────────────────────────────────────────
def test_picks_the_instance_that_most_recently_worked_the_ask():
    got = ard.choose_instance(["cc-x-2", "cc-x-4"],
                              {"cc-x-2": T, "cc-x-4": T + dt.timedelta(hours=1)})
    assert got == "cc-x-4"


def test_instance_that_never_cited_the_ask_is_not_chosen():
    assert ard.choose_instance(["cc-x-2", "cc-x-4"], {"cc-x-2": T}) == "cc-x-2"


def test_no_instance_worked_it_returns_none_so_console_gets_the_page():
    assert ard.choose_instance(["cc-x-2", "cc-x-4"], {}) is None


def test_citations_from_non_live_instances_are_ignored():
    assert ard.choose_instance(["cc-x-2"], {"cc-x-9": T}) is None


# ── launchctl parsing ────────────────────────────────────────────────────────
LAUNCHCTL = (
    "PID\tStatus\tLabel\n"
    "-\t0\tdev.wingmen.asks-daily-digest\n"
    "-\t1\tdev.wingmen.asks-reconcile\n"
    "-\t0\tdev.wingmen.commitment-sweeper\n"
    "18577\t-15\tdev.wingmen.fleet-health-bus-notify\n"
    "-\t1\tcom.apple.something\n"
)


def test_parse_keeps_only_asks_jobs_and_the_commitment_sweeper():
    assert fh.parse_launchctl_list(LAUNCHCTL) == {
        "dev.wingmen.asks-daily-digest": 0,
        "dev.wingmen.asks-reconcile": 1,
        "dev.wingmen.commitment-sweeper": 0,
    }


# ── exit_watch_plan: once per episode ────────────────────────────────────────
def test_nonzero_exit_pages_once():
    to_page, state = fh.exit_watch_plan({"dev.wingmen.asks-reconcile": 1}, {})
    assert to_page == [("dev.wingmen.asks-reconcile", 1)]
    # caller stamps state only on a successful send; once stamped, no re-page
    again, _ = fh.exit_watch_plan({"dev.wingmen.asks-reconcile": 1},
                                  {"dev.wingmen.asks-reconcile": 1})
    assert again == []


def test_recovery_clears_so_the_next_failure_pages_again():
    _, state = fh.exit_watch_plan({"dev.wingmen.asks-reconcile": 0},
                                  {"dev.wingmen.asks-reconcile": 1})
    assert state == {}
    to_page, _ = fh.exit_watch_plan({"dev.wingmen.asks-reconcile": 1}, state)
    assert to_page == [("dev.wingmen.asks-reconcile", 1)]


def test_a_different_nonzero_status_is_a_new_episode():
    to_page, _ = fh.exit_watch_plan({"dev.wingmen.commitment-sweeper": 3},
                                    {"dev.wingmen.commitment-sweeper": 1})
    assert to_page == [("dev.wingmen.commitment-sweeper", 3)]


def test_failed_send_leaves_label_unstamped_for_retry(monkeypatch, tmp_path):
    import scripts.bus_send as bs

    def boom(*a, **k):
        raise SystemExit("bus_send: REFUSED")

    monkeypatch.setattr(fh, "EXIT_WATCH_STATE", tmp_path / "s.json")
    monkeypatch.setattr(bs, "send", boom)
    monkeypatch.setattr(fh.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": LAUNCHCTL})())
    assert fh.asks_job_exit_watch() == []
    assert (tmp_path / "s.json").read_text() == "{}"


def test_successful_send_stamps_state(monkeypatch, tmp_path):
    import json
    import scripts.bus_send as bs
    sent = []
    monkeypatch.setattr(fh, "EXIT_WATCH_STATE", tmp_path / "s.json")
    monkeypatch.setattr(bs, "send", lambda *a, **k: sent.append(a) or (1, "t"))
    monkeypatch.setattr(fh.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": LAUNCHCTL})())
    assert fh.asks_job_exit_watch() == ["dev.wingmen.asks-reconcile"]
    (frm, to, mtype, subj, body, prio), = sent
    assert (frm, to, prio) == ("cc-fleet-health", "orch-console", "P1")
    assert json.loads((tmp_path / "s.json").read_text()) == {"dev.wingmen.asks-reconcile": 1}

"""console #78388 condition 3: a chase net with due > 0 and sent == 0 for 3 consecutive
ARMED scans pages orch-console ONCE (client-asks-chase sat at 0/33 for 15+h unseen)."""
import importlib

w = importlib.import_module("scripts.priority_sla_watchdog")
T = [{"id": 4054, "delegated_to": "cosem-port"}]


def run_scans(results, page_ok=True):
    state, pages = {}, []

    def page(net, due, scans, sample):
        pages.append((net, due, sample))
        return page_ok

    for targets, sent in results:
        w.zero_send_check(state, "client-asks-chase", targets, sent, page=page)
    return state, pages


def test_three_zero_send_scans_page_once():
    _, pages = run_scans([(T, 0)] * 3)
    assert pages == [("client-asks-chase", 1, "#4054->cosem-port")]


def test_it_does_not_repage_while_the_streak_continues():
    _, pages = run_scans([(T, 0)] * 6)
    assert len(pages) == 1


def test_two_scans_is_not_enough():
    assert run_scans([(T, 0)] * 2)[1] == []


def test_a_successful_send_resets_the_streak():
    _, pages = run_scans([(T, 0), (T, 0), (T, 1), (T, 0), (T, 0)])
    assert pages == []


def test_nothing_due_resets_and_rearms():
    _, pages = run_scans([(T, 0)] * 3 + [([], 0)] + [(T, 0)] * 3)
    assert len(pages) == 2


def test_failed_page_is_retried_next_scan():
    state, pages = run_scans([(T, 0)] * 4, page_ok=False)
    assert len(pages) == 2 and state["zero_send_streak"]["client-asks-chase"]["paged"] is False


def test_default_threshold_is_three_scans():
    assert w.ZERO_SEND_ALARM_SCANS == 3

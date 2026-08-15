"""A wake must reach the instance it was addressed to, not a sibling in the same family.

cc-irsyad-3 reported this from the inside (bus #22646) after being woken five consecutive
times by bus traffic addressed to its siblings: "Fresh SELECTs on to_agent in {cc-irsyad-3,
cc-ihsanos} = 0 unread every time. The wake trigger is not scoped to my agent id, so a
stood-down instance keeps burning the context it was recycled to free."

It was right. `_candidate_sessions` discarded the exact agent_id and queried
`WHERE base_agent_id=%s`, so a wake for cc-irsyad-2 resolved to whichever of the six
cc-irsyad instances was live first. The per-instance rows existed and were correct all along:

    cc-irsyad-1 -> irsyad          cc-irsyad-4 -> irsyad-prog1
    cc-irsyad-2 -> irsyad-history  cc-irsyad-5 -> irsyad-cisco-recon
    cc-irsyad-3 -> irsyad-prog2    cc-irsyad-6 -> irsyad-tabung-jumaat

This is the leak that undoes recycling: a lane cleared to free 635k gets re-woken by traffic
that turns out not to be for it, and re-inflates reading messages it will discard.

The family fallback is kept on purpose and is tested here too — removing it would trade this
bug for missed wakes on instances that have no row of their own (the op#11297 coverage
property). Preference, not restriction.
"""
from nervous_system.agent_wake import rank_candidates


IRSYAD = [
    ("cc-irsyad-1", "irsyad"),
    ("cc-irsyad-2", "irsyad-tabung-history"),
    ("cc-irsyad-3", "irsyad-prog2"),
    ("cc-irsyad-4", "irsyad-prog1"),
    ("cc-irsyad-5", "irsyad-cisco-recon"),
    ("cc-irsyad-6", "irsyad-tabung-jumaat"),
]


def test_wake_goes_to_the_addressed_instance_first():
    # THE REPORTED BUG. Before the fix this returned 'irsyad' — a different lane entirely.
    assert rank_candidates(IRSYAD, "cc-irsyad-3")[0] == "irsyad-prog2"


def test_every_instance_resolves_to_its_own_session():
    for agent, session in IRSYAD:
        assert rank_candidates(IRSYAD, agent)[0] == session, agent


def test_stood_down_lane_is_not_first_for_a_siblings_wake():
    # prog2's actual complaint: traffic for cc-irsyad-2 must not land on it.
    assert rank_candidates(IRSYAD, "cc-irsyad-2")[0] != "irsyad-prog2"


# ── the fallback must survive: preference, not restriction ───────────────────
def test_family_is_still_reachable_when_the_instance_has_no_row():
    # An instance with no row of its own still reaches live siblings (op#11297 coverage).
    out = rank_candidates(IRSYAD, "cc-irsyad-9")
    assert out and out[0] == "irsyad"
    assert len(out) == len(IRSYAD)


def test_family_sessions_are_retained_behind_the_exact_match():
    out = rank_candidates(IRSYAD, "cc-irsyad-3")
    assert out[0] == "irsyad-prog2"
    assert set(out) == {s for _, s in IRSYAD}  # nothing dropped, only reordered


def test_db_ordering_is_preserved_within_each_group():
    # The SQL already orders by non-offline then freshest heartbeat; ranking must not
    # scramble that — it only lifts the exact match above the rest.
    out = rank_candidates(IRSYAD, "cc-irsyad-4")
    assert out == ["irsyad-prog1", "irsyad", "irsyad-tabung-history", "irsyad-prog2",
                   "irsyad-cisco-recon", "irsyad-tabung-jumaat"]


# ── robustness: this sits in the wake path, it must never raise ──────────────
def test_empty_and_none_are_safe():
    assert rank_candidates([], "cc-irsyad-3") == []
    assert rank_candidates(None, "cc-irsyad-3") == []


def test_null_sessions_are_skipped_not_returned():
    assert rank_candidates([("cc-irsyad-3", None), ("cc-irsyad-1", "irsyad")],
                           "cc-irsyad-3") == ["irsyad"]


def test_duplicate_sessions_are_deduped_keeping_the_first():
    rows = [("cc-irsyad-1", "irsyad"), ("cc-irsyad-3", "irsyad-prog2"),
            ("cc-irsyad-7", "irsyad-prog2")]
    assert rank_candidates(rows, "cc-irsyad-3") == ["irsyad-prog2", "irsyad"]


def test_single_column_rows_still_work():
    # Tolerate a session-only row shape so an older/!=2-column query cannot crash the wake.
    assert rank_candidates([("irsyad",), ("irsyad-prog2",)], "cc-irsyad-3") == [
        "irsyad", "irsyad-prog2"]

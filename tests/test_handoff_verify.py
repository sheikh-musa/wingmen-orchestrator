"""handoff_verify: every reset gate on this fleet asks "does the file exist", and on
2026-08-15 that question passed four restore points it should have failed.

The cases below are the real ones from that night, not invented fixtures:
  cc-irsyad-coord   claimed two handoffs on the bus; neither file existed
  cc-fleet-health   restore point ~18h old, from before a full day of work
  cc-irsyad-receipt "no further action pending" while its runbook PR sat unmerged

The last one is the limit of what this module can do, and the tests say so explicitly: a
handoff can be fresh, substantial, well-formed and fully resolving, and still be wrong. This
makes restore points CHECKABLE, not trustworthy.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import handoff_verify as hv  # noqa: E402


GOOD = """# Lane handoff — FINAL STATE

## RIGHT NOW / mid-flight
Routing cai apply-grants to hub. NEXT STEP: merge PR #309 once cai's post-apply verify lands.

## Only-in-my-context — the recycle would destroy this class
1. Shuk's dated schedule — PROMISED to the client, UNSENT.
2. C3 v2 monitor, bg id bluj71gsz, script scripts/lib/context_truth.py — RE-LAUNCH if killed.

## Verified vs relayed
[VERIFIED] first-hand: the 990 backfill, queried at source myself.
[BUS] everything else — I did not check it myself.
Open items: bus 22613, bus 22618.
""" + "padding to clear the size floor. " * 30


def _resolver_all_ok(kind, value):
    return True


# ── the coord case: the file was never there ─────────────────────────────────
def test_absent_file_is_refused_not_warned():
    r = hv.verify("reports/claimed.md", None)
    assert not r.ok and "does not exist" in r.failures[0]


# ── the SRE case: an 18h-old file passes "does it exist" ─────────────────────
def test_written_before_the_request_is_stale():
    r = hv.verify("h.md", GOOD, mtime=1000.0, requested_at=1000.0 + 18 * 3600,
                  resolver=_resolver_all_ok)
    assert not r.ok
    assert any("stale" in f for f in r.failures)


def test_written_after_the_request_is_fresh():
    r = hv.verify("h.md", GOOD, mtime=2000.0, requested_at=1000.0, resolver=_resolver_all_ok)
    assert r.ok, r.failures


def test_freshness_is_measured_against_the_request_not_the_clock():
    # An 18h-old file "touched recently" must still fail; only "since I asked" is meaningful.
    old = hv.verify("h.md", GOOD, mtime=500.0, requested_at=900.0, resolver=_resolver_all_ok)
    assert not old.ok


# ── thinness and shape ───────────────────────────────────────────────────────
def test_one_line_all_done_is_refused():
    r = hv.verify("h.md", "all done, nothing pending", mtime=2.0, requested_at=1.0)
    assert not r.ok and any("too thin" in f for f in r.failures)


def test_missing_only_in_context_section_is_refused():
    text = "## Right now\nNext step: merge PR #309.\n[VERIFIED] I checked it.\n" + "pad " * 300
    r = hv.verify("h.md", text, mtime=2.0, requested_at=1.0, resolver=_resolver_all_ok)
    assert not r.ok
    assert any("only_in_context" in f for f in r.failures)


def test_missing_verified_split_is_refused():
    text = ("## Right now\nNext step: merge it.\n"
            "## Only-in-my-context\nAn unsent client message.\n" + "pad " * 300)
    r = hv.verify("h.md", text, mtime=2.0, requested_at=1.0, resolver=_resolver_all_ok)
    assert not r.ok and any("verified_split" in f for f in r.failures)


# ── the feature that would have caught coord: references must resolve ────────
def test_reference_that_does_not_exist_is_a_failure():
    def resolver(kind, value):
        return False if kind == "path" else True
    r = hv.verify("h.md", GOOD, mtime=2.0, requested_at=1.0, resolver=resolver)
    assert not r.ok
    assert any("does not exist" in f for f in r.failures)


def test_unresolvable_reference_warns_but_does_not_fail():
    # A false alarm here would train people to ignore the checker, which costs more than the
    # check is worth. "Could not tell" must read differently from "checked and fine".
    def resolver(kind, value):
        return None
    r = hv.verify("h.md", GOOD, mtime=2.0, requested_at=1.0, resolver=resolver)
    assert r.ok
    assert r.unresolved and any("could not be checked" in w for w in r.warnings)


def test_no_resolver_says_so_rather_than_implying_checked():
    r = hv.verify("h.md", GOOD, mtime=2.0, requested_at=1.0)
    assert r.ok and any("NOT checked" in w for w in r.warnings)


# ── extraction ───────────────────────────────────────────────────────────────
def test_extracts_the_claim_types_that_matter():
    refs = hv.extract_refs(GOOD)
    assert "scripts/lib/context_truth.py" in refs["path"]
    assert "309" in refs["pr"]
    assert "22613" in refs["bus"] and "22618" in refs["bus"]


def test_extraction_dedupes_and_keeps_order():
    refs = hv.extract_refs("PR #309 then PR #309 again, then PR #310.")
    assert refs["pr"] == ["309", "310"]


# ── the honest limit, stated as a test so nobody oversells the module ────────
def test_a_wellformed_but_untrue_handoff_still_passes():
    # The receipt lane's exact failure: fresh, substantial, correctly sectioned, every
    # reference resolving — and "nothing further pending" was false, because PR #310 was
    # still open. No checker catches this. The defence is first-hand authorship and
    # verifying at source on the other side, not this module.
    lying = GOOD.replace("PROMISED to the client, UNSENT.", "nothing further pending.")
    r = hv.verify("h.md", lying, mtime=2.0, requested_at=1.0, resolver=_resolver_all_ok)
    assert r.ok

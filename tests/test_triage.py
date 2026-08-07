"""Unit tests for nervous_system/triage.py — the PASSIVE CoS triage classifier.

Pure-function tests: no DB, no network, no LLM. They pin the key routing cases,
prove determinism, and prove the classifier is total (never raises, always
returns a well-formed result) — the properties that make it safe to run on every
inbound as a read-only annotation.
"""
from nervous_system import triage
from nervous_system.triage import (
    DIRECT_ANSWER, DELEGATE_LANE, DELEGATE_HUB_PEN, DELEGATE_DOMAIN_AGENT,
    GOVERNANCE_FORK, R_DIRECT, R_CAI, R_COSEM, R_STOREFRONT, R_IRSYAD, R_QR,
    R_SCHOLAR, R_SHIPFORGE, R_MAMADAH,
)

_CATEGORIES = {DIRECT_ANSWER, DELEGATE_LANE, DELEGATE_HUB_PEN,
               DELEGATE_DOMAIN_AGENT, GOVERNANCE_FORK}
_ROUTES = {R_DIRECT, R_CAI, R_COSEM, R_STOREFRONT, "ihsanos", R_IRSYAD, R_QR,
           R_SCHOLAR, R_SHIPFORGE, R_MAMADAH, "nutri-study", "ray-ai", "hub"}


# (text, tag, expected_category, expected_route) — representative operator traffic.
CASES = [
    # ── Governance forks (highest priority — money / irreversible / residency) ──
    ("please transfer funds to the vendor today", None, GOVERNANCE_FORK, R_CAI),
    ("go ahead and deploy to prod", None, GOVERNANCE_FORK, R_CAI),
    ("run supabase db push on the storefront", None, GOVERNANCE_FORK, R_CAI),
    ("is this residency-compliant for the UAE client?", None, GOVERNANCE_FORK, R_CAI),
    ("drop table operator_messages", None, GOVERNANCE_FORK, R_CAI),
    ("@cosem but first make the payment to the supplier", None, GOVERNANCE_FORK, R_CAI),

    # ── Commerce is NOT a money fork (the key false-positive guard) ──────────────
    ("add a payment button to the storefront checkout", None, DELEGATE_HUB_PEN, R_STOREFRONT),
    ("@ihsanos wire up the payment UI on the product page", None, DELEGATE_HUB_PEN, R_STOREFRONT),

    # ── Explicit @tags ──────────────────────────────────────────────────────────
    ("@qr the dynamic code isn't scanning", None, DELEGATE_LANE, R_QR),
    ("@cosem the namelist PDF is off by one", None, DELEGATE_LANE, R_COSEM),
    ("@adcda the course video deck", None, DELEGATE_LANE, R_COSEM),
    ("@scholar review the mizan rubric", None, DELEGATE_LANE, R_SCHOLAR),
    ("@irsyad tweak the monthly report layout", None, DELEGATE_HUB_PEN, R_IRSYAD),
    ("@fleet how many lanes are up", None, DIRECT_ANSWER, R_DIRECT),

    # ── Definitive channel tags ─────────────────────────────────────────────────
    ("what did i note about tajweed?", "mamadah", DELEGATE_DOMAIN_AGENT, R_MAMADAH),
    ("macros for today", "nutri-study", DELEGATE_DOMAIN_AGENT, "nutri-study"),
    ("the report looks good, thanks", "gazzabyte-irsyad", DELEGATE_HUB_PEN, R_IRSYAD),

    # ── Keyword heuristics (no tag) ─────────────────────────────────────────────
    ("the merchant catalog import failed on checkout", None, DELEGATE_HUB_PEN, R_STOREFRONT),
    ("cosem assessor onboarding is incomplete", None, DELEGATE_LANE, R_COSEM),
    ("shipforge clone for sushi tei", None, DELEGATE_LANE, R_SHIPFORGE),
    ("the ingest watchdog looks wedged", None, DIRECT_ANSWER, R_DIRECT),

    # ── Chatter / status → direct-answer ────────────────────────────────────────
    ("status?", None, DIRECT_ANSWER, R_DIRECT),
    ("hey, how's it going", None, DIRECT_ANSWER, R_DIRECT),
    ("thanks!", None, DIRECT_ANSWER, R_DIRECT),

    # ── Ambiguous / default → low-confidence direct-answer (flag-not-guess) ──────
    ("some vague musing with no clear domain", None, DIRECT_ANSWER, R_DIRECT),
    ("", None, DIRECT_ANSWER, R_DIRECT),
]


def test_routing_table():
    for text, tag, exp_cat, exp_route in CASES:
        r = triage.classify(text, tag=tag)
        assert r.category == exp_cat, (text, tag, "cat", r.category, "!=", exp_cat)
        assert r.suggested_route == exp_route, (text, tag, "route", r.suggested_route, "!=", exp_route)


def test_governance_beats_domain_tag():
    """A money/irreversible signal must flag even when a domain @tag is present —
    the worst error is MISSING a governance fork."""
    r = triage.classify("@storefront transfer funds to acct 123")
    assert r.category == GOVERNANCE_FORK and r.suggested_route == R_CAI


def test_governance_false_positive_guard():
    """'payment'/'invoice' as a build TOPIC must not read as a money fork."""
    for text in ("build an invoice tracker for adcda",
                 "add a payment method selector",
                 "the payments page needs a redesign"):
        r = triage.classify(text)
        assert r.category != GOVERNANCE_FORK, text


def test_determinism():
    """Same input → identical output, every time (pure function)."""
    for text, tag, _, _ in CASES:
        a = triage.classify(text, tag=tag).to_dict()
        b = triage.classify(text, tag=tag).to_dict()
        c = triage.classify(text, tag=tag).to_dict()
        assert a == b == c, (text, tag)


def test_result_is_well_formed_and_total():
    """Classifier is TOTAL: never raises, always returns a valid, bounded result
    — the safety property for running on every inbound."""
    weird = [None, "", "   ", "🚀🔥", "@", "@@@ nonsense @@@",
             "a" * 5000, "\n\n\t", "SELECT * FROM x; DROP TABLE y",
             "@unknowntag hello", "MiXeD CaSe TrAnSfEr FuNdS now"]
    for text in weird:
        r = triage.classify(text)
        d = r.to_dict()
        assert r.category in _CATEGORIES, (text, r.category)
        assert r.suggested_route in _ROUTES, (text, r.suggested_route)
        assert 0.0 <= r.confidence <= 1.0, (text, r.confidence)
        assert isinstance(r.rationale, str) and r.rationale, text
        assert d["v"] == triage.TRIAGE_VERSION and d["tier"] == "t1"
        assert set(d) == {"category", "suggested_route", "confidence",
                          "rationale", "domain", "tier", "v"}


def test_reply_prefix_does_not_break_routing():
    """ingest prepends a `↩️ re "…":` reply prefix — routing still works."""
    r = triage.classify('↩️ re "the qr build": @qr still failing')
    assert r.suggested_route == R_QR


def test_annotate_matches_classify():
    assert triage.annotate("@cosem fix the pdf") == triage.classify("@cosem fix the pdf").to_dict()


def test_case_insensitivity():
    assert triage.classify("@COSEM Fix It").suggested_route == R_COSEM
    assert triage.classify("TRANSFER FUNDS NOW").category == GOVERNANCE_FORK

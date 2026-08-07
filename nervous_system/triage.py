"""triage.py — PASSIVE Chief-of-Staff triage classifier (CoS Step 1).

Phase 2 / Step 1 of `reports/chief-of-staff-one-front-door-spec.md`. This is the
*passive* first ship: a pure-function classifier that, given an inbound operator
message, suggests a route. It is surfaced as a READ-ONLY annotation that Nazim
(the operator conduit) simply reads when he reconciles — it does NOT route, does
NOT auto-act, does NOT change any send/route/gate behavior. Its whole purpose is
to de-risk the routing brain against real traffic at zero risk before any later
step (Step 3) lets it act.

Design (spec §4.1 "Tier 1 — deterministic, no LLM"):
  - PURE: no I/O, no network, no DB, no LLM, no global state. Deterministic — the
    same inputs always produce the same output. This is what makes it unit-
    testable and safe to run on every inbound.
  - It reuses signals that already exist on an `operator_messages` row: the
    `@tag` vocabulary (CLAUDE.md), the channel `tag`, and the message text.
  - Ambiguity is a FLAG, not a guess (`feedback_ingestion_orientation_agnostic`):
    low confidence defaults to `direct-answer` (the CoS/Nazim handles it), NEVER
    a silent mis-route.

Return shape (`TriageResult` / `.to_dict()`):
    {category, suggested_route, confidence, rationale, tier, domain, v}

`category`        — the disposition (direct-answer vs which kind of delegate vs
                    governance-fork).
`suggested_route` — the concrete executor/domain it likely belongs to.
`confidence`      — 0.0..1.0; how strong the signal is.
`rationale`       — short human-readable why (Nazim reads this).
`tier`            — "t1" (deterministic). Tier-2 LLM classify is a LATER step
                    (spec §4.1) and is intentionally NOT called here.
`domain`          — topical domain (cosem/storefront/…), for grouping.
`v`               — classifier version, so a stored annotation is auditable
                    across classifier changes (recompute-at-analysis-time).

Tier-2 note: the spec's LLM fallback for ambiguous free-text is deliberately out
of scope for Step 1 — Step 1 must stay pure/deterministic. Ambiguous messages
here resolve to a low-confidence `direct-answer` (flag-not-guess), which is
exactly the safe default an LLM tier would later refine.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

TRIAGE_VERSION = "1"

# ── Categories (the disposition) ──────────────────────────────────────────────
DIRECT_ANSWER = "direct-answer"            # CoS/Nazim handles it itself
DELEGATE_LANE = "delegate-lane"            # a build/engineering ask → tmux lane
DELEGATE_HUB_PEN = "delegate-hub-pen"      # needs a hub singleton pen (storefront/fleet-status)
DELEGATE_DOMAIN_AGENT = "delegate-domain-agent"  # a persona (mamadah, Ray-AI, nutri)
GOVERNANCE_FORK = "governance-fork"        # money/irreversible/residency → escalate to cai

# ── Routes (the concrete executor / domain) ───────────────────────────────────
R_DIRECT = "direct"        # the CoS voice (Nazim) answers
R_HUB = "hub"              # the Studio hub body (pen holder)
R_CAI = "cai"             # the strategic/governance node
R_COSEM = "cosem"          # cosem / adcda / tdu lane family
R_STOREFRONT = "storefront"  # ihsanos storefront / merchant (hub-owned)
R_IHSANOS = "ihsanos"      # ihsanos repo work
R_IRSYAD = "irsyad"        # irsyad silo (Gazzabyte/Elly)
R_QR = "qr"               # branditqr dynamic-QR app
R_SCHOLAR = "scholar"      # scholar / mizan
R_SHIPFORGE = "shipforge"  # shipforge site cloner
R_MAMADAH = "mamadah"      # second-brain persona
R_NUTRI = "nutri-study"    # nutri-study persona
R_RAY_AI = "ray-ai"        # cosem domain agent (AI-CA)


@dataclass(frozen=True)
class TriageResult:
    category: str
    suggested_route: str
    confidence: float
    rationale: str
    domain: str = "general"
    tier: str = "t1"
    v: str = TRIAGE_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


# ── Signal tables (deterministic, ordered by priority in classify()) ──────────

# Governance ACTION signals: money-movement / irreversible / residency phrases
# that warrant a cai gate (CLAUDE.md money/irreversible/residency doctrine).
# NOTE these are deliberately ACTION-oriented ("transfer funds", "deploy to
# prod"), NOT topic words like "payment" — the whole product is commerce, so a
# storefront "add a payment button" must NOT read as a money fork (tested).
_GOVERNANCE_PATTERNS = [
    r"\btransfer\s+funds\b", r"\bwire\s+(the\s+)?(money|funds|payment)\b",
    r"\bsend\s+(the\s+)?money\b", r"\bpay\s*out\b", r"\bpayout\b",
    r"\bdisburse\b", r"\bgiro\s+upload\b", r"\bmake\s+(the\s+)?payment\b",
    r"\bpay\s+the\s+(invoice|vendor|supplier)\b", r"\bissue\s+a?\s*refund\b",
    r"\bdrop\s+table\b", r"\bdelete\s+(the\s+|all\s+)", r"\bwipe\b",
    r"\bdb\s+push\b", r"\bsupabase\s+db\s+push\b",
    r"\bdeploy\s+to\s+prod", r"\bpush\s+to\s+prod", r"\bproduction\s+deploy\b",
    r"\bgo\s+live\s+with\b", r"\birreversible\b",
    r"\bresidency\b", r"\bgov(ernment)?\s+(pii|id|data)\b",
    r"\bmilitary\s+id\b", r"\breal\s+(gov|government|client)\s+(pii|data)\b",
]

# Explicit @tag vocabulary → (category, route, domain). CLAUDE.md tag list.
# The tag is a strong operator-supplied signal (confidence 0.9).
_AT_TAGS = {
    "cai": (GOVERNANCE_FORK, R_CAI, "governance"),
    "qr": (DELEGATE_LANE, R_QR, "qr"),
    "cosem": (DELEGATE_LANE, R_COSEM, "cosem"),
    "adcda": (DELEGATE_LANE, R_COSEM, "cosem"),
    "tdu": (DELEGATE_LANE, R_COSEM, "cosem"),
    "ihsanos": (DELEGATE_HUB_PEN, R_STOREFRONT, "storefront"),
    "irsyad": (DELEGATE_HUB_PEN, R_IRSYAD, "irsyad"),
    "scholar": (DELEGATE_LANE, R_SCHOLAR, "scholar"),
    "mizan": (DELEGATE_LANE, R_SCHOLAR, "scholar"),
    "fleet": (DIRECT_ANSWER, R_DIRECT, "fleet"),
    "console": (DIRECT_ANSWER, R_DIRECT, "fleet"),
}

# Structural channel `tag` (the row's channel_tag) → (category, route, domain,
# confidence). The channel is a HARD structural signal — a mamadah/nutri channel
# only ever carries that persona's topic (confidence 0.95). Operator-DM channels
# (nazim-console / tmux-console / orch-channel) carry no structural domain, so
# they are deliberately absent here and fall through to text analysis.
_CHANNEL_TAGS = {
    "mamadah": (DELEGATE_DOMAIN_AGENT, R_MAMADAH, "second-brain", 0.95),
    "nutri-study": (DELEGATE_DOMAIN_AGENT, R_NUTRI, "nutri", 0.95),
    "cai-channel": (GOVERNANCE_FORK, R_CAI, "governance", 0.9),
    "gazzabyte-irsyad": (DELEGATE_HUB_PEN, R_IRSYAD, "irsyad", 0.8),
}

# Domain keyword heuristics (no tag): ordered list of (compiled-regex, category,
# route, domain). First match wins. Lower confidence than an explicit tag.
_KEYWORD_RULES = [
    (r"\b(storefront|merchant|catalog|dookana|woocommerce|checkout|shopping\s+cart|product\s+listing|onboard\s+a?\s*merchant)\b",
     DELEGATE_HUB_PEN, R_STOREFRONT, "storefront"),
    (r"\b(cosem|adcda|namelist|assessor|verifier|course\s+video|certificate)\b",
     DELEGATE_LANE, R_COSEM, "cosem"),
    (r"\b(branditqr|dynamic\s+qr|qr\s+code)\b",
     DELEGATE_LANE, R_QR, "qr"),
    (r"\b(shipforge|clone\s+(the\s+)?site|website\s+clone|site\s+cloner|redesign)\b",
     DELEGATE_LANE, R_SHIPFORGE, "shipforge"),
    (r"\b(irsyad|gazzabyte|elly|tabung\s+fajr)\b",
     DELEGATE_HUB_PEN, R_IRSYAD, "irsyad"),
    (r"\b(scholar|mizan|quran|hifz|tajweed|tarbiyah)\b",
     DELEGATE_LANE, R_SCHOLAR, "scholar"),
    (r"\b(watchdog|tmux|ingest|daemon|launchd|restart\s+(the\s+)?(orch|fleet)|fleet\s+status|lane\b)\b",
     DIRECT_ANSWER, R_DIRECT, "fleet"),
]

# Cheap greeting / ack / status chatter → direct-answer (moderate confidence).
_DIRECT_CHATTER = re.compile(
    r"^\s*(hi|hey|hello|salam|assalam|thanks|thank\s+you|ok|okay|got\s+it|"
    r"noted|status|update\??|ping|how('?s| is)\s+it\s+going|what('?s| is)\s+up)\b",
    re.IGNORECASE)

_GOVERNANCE_RE = re.compile("|".join(_GOVERNANCE_PATTERNS), re.IGNORECASE)
_AT_TAG_RE = re.compile(r"@([a-z][a-z0-9_]*)", re.IGNORECASE)


def _strip_reply_prefix(text: str) -> str:
    """ingest prepends `↩️ re "<quoted>": ` for replies. Keep the whole string
    for scanning (the referent is also signal), but this helper is available if a
    caller wants just the operator's own words."""
    m = re.match(r'^↩️?\s*re\s+".*?":\s*(.*)$', text, re.DOTALL)
    return m.group(1) if m else text


def classify(text: str | None, tag: str | None = None,
             channel: str | None = None,
             sender_label: str | None = None,
             source: str | None = None) -> TriageResult:
    """Classify one inbound operator message → TriageResult. PURE + deterministic.

    Args mirror the columns available on an `operator_messages` inbound row:
      text          — the message content (may carry a `↩️ re "…":` reply prefix).
      tag           — the channel_tag (e.g. 'nazim-console', 'mamadah', 'orch-channel').
      channel       — 'telegram' | 'tmux-console' | … (unused for routing today,
                      accepted so callers can pass the whole row).
      sender_label  — 'Musa' / a name (unused for routing today; accepted for parity).
      source        — 'DM' | 'group' (unused for routing today; accepted for parity).

    Precedence (worst-error-first): governance fork → definitive channel tag →
    explicit @tag → keyword heuristic → chatter → default direct-answer.
    """
    body = (text or "").strip()
    if not body:
        return TriageResult(DIRECT_ANSWER, R_DIRECT, 0.0,
                            "empty/non-text message — CoS handles (e.g. media only)",
                            domain="general")

    low = body.lower()

    # 1. GOVERNANCE FORK (highest priority — the worst error is MISSING one).
    #    Passive flag with the matched phrase in the rationale so Nazim judges.
    gmatch = _GOVERNANCE_RE.search(low)
    if gmatch:
        return TriageResult(
            GOVERNANCE_FORK, R_CAI, 0.72,
            f"money/irreversible/residency signal ('{gmatch.group(0).strip()}') "
            f"— flag for cai gate before any action",
            domain="governance")

    # 2. DEFINITIVE CHANNEL TAG (structural — the channel fixes the domain).
    if tag and tag in _CHANNEL_TAGS:
        cat, route, domain, conf = _CHANNEL_TAGS[tag]
        return TriageResult(cat, route, conf,
                            f"channel '{tag}' is domain-specific", domain=domain)

    # 3. EXPLICIT @tag (strong operator-supplied context).
    for m in _AT_TAG_RE.finditer(low):
        key = m.group(1).lower()
        if key in _AT_TAGS:
            cat, route, domain = _AT_TAGS[key]
            return TriageResult(cat, route, 0.9,
                                f"explicit @{key} tag", domain=domain)

    # 4. DOMAIN KEYWORD HEURISTIC (first match wins).
    for pattern, cat, route, domain in _KEYWORD_RULES:
        if re.search(pattern, low):
            return TriageResult(cat, route, 0.6,
                                f"keyword match ({domain})", domain=domain)

    # 5. GREETING / STATUS CHATTER → direct-answer.
    if _DIRECT_CHATTER.match(body):
        return TriageResult(DIRECT_ANSWER, R_DIRECT, 0.55,
                            "greeting/status/ack — CoS answers directly",
                            domain="general")

    # 6. DEFAULT: ambiguous → low-confidence direct-answer (flag-not-guess).
    return TriageResult(DIRECT_ANSWER, R_DIRECT, 0.2,
                        "no strong routing signal — CoS handles / clarify",
                        domain="general")


def classify_row(row: dict) -> TriageResult:
    """Convenience: classify from a dict of operator_messages fields."""
    return classify(row.get("text"), tag=row.get("tag"),
                    channel=row.get("channel"),
                    sender_label=row.get("sender_label"),
                    source=row.get("source"))


def annotate(text: str | None, tag: str | None = None,
             channel: str | None = None) -> dict:
    """The passive annotation dict written/read alongside an inbound. Never
    raises for a classifiable input; callers still guard for total safety."""
    return classify(text, tag=tag, channel=channel).to_dict()


if __name__ == "__main__":  # tiny manual smoke: `python -m nervous_system.triage "text"`
    import json
    import sys
    t = " ".join(sys.argv[1:]) or "status?"
    print(json.dumps(classify(t).to_dict(), indent=2))

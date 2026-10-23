"""provider_policy — per-client model-provider policy resolver (Musa op#29321/#29324),
plus the fail-closed FLIP GATE (orch-console #74584/#74588, bus #74586 spec,
reports/provider-policy-flip-gate-spec-20261023.md) that wires it into actual lane
flips.

Answers "for this client_key, which providers are allowed and is text_only
(vision/image MCP off) required?" from `provider_policy` rows. The gate the
GLM/non-Anthropic routing reads before placing a lane on z.ai.

SAFETY (fail-closed, orch-console residency posture): never default to allowing
a non-Anthropic provider. An unknown client_key resolves to Anthropic-only.

THE FLIP GATE (`assert_glm_allowed`): until 2026-10-23 this module was pure reference
data — resolve_policy() was called nowhere in the actual flip tooling
(switch_lane_token.sh, glm_overflow_switch.py, glm_peak_pause_resume.py), so the
table was only as good as whoever remembered to read it before flipping a lane.
That's how cc-mamadah got flipped onto GLM against a standing lane-specific
residency ruling (#67914) that had never been synced into the table (bus #74420/
#74563/#74577). `assert_glm_allowed` is the thing that actually gets called, and it
RAISES rather than returning a code a caller could forget to check (orch-console
Q3: "an exception cannot be silently ignored").

LANE -> client_key resolution (`resolve_client_key`) is DATA-DRIVEN: most-specific
match against provider_policy's own client_key set, never a hardcoded family list
(orch-console Q1 — a second hardcoded list is exactly the two-sources-of-truth
problem this closes, one level down). Verified empirically against the real fleet
this would have mis-mapped `cc-cosem-adcda-1` and `cosem-exams` to the coarser
`cosem` key if a naive prefix-split (`lane_token_resolver.family_of`-style) were
reused instead.

provider_policy_lane_override (migration 116) is the exact-lane override layer that
replaces glm_peak_pause_resume.py's old hardcoded HARD_EXCLUDE_LANES constant (run
in PARALLEL with it for one deploy cycle, warn-on-divergence, before that constant
is deleted — orch-console Q5). An override may only NARROW its resolved client-level
policy, never widen it (orch-console Q2) — checked on every READ here, not just at
write time, so a future hand-edit that violates this is still caught.
"""
from __future__ import annotations

import os
import re
import sys

ANTHROPIC_ONLY = {"allowed_providers": ["anthropic"], "text_only": False}


def resolve_policy(rows, client_key):
    """PURE. `rows` = provider_policy rows (dicts: client_key, allowed_providers,
    text_only). Returns {allowed_providers, text_only} for client_key, or the
    fail-closed Anthropic-only default when no row matches."""
    for r in rows:
        if r.get("client_key") == client_key:
            providers = list(r.get("allowed_providers") or ["anthropic"])
            text_only = bool(r.get("text_only"))
            # HARD invariant (Musa op#29321/#29324): a non-Anthropic provider is
            # permitted ONLY with text_only=true. A row that allows one without it
            # is a misconfiguration — fail closed rather than leak images to z.ai.
            if any(p != "anthropic" for p in providers) and not text_only:
                return dict(ANTHROPIC_ONLY)
            return {"allowed_providers": providers, "text_only": text_only}
    return dict(ANTHROPIC_ONLY)


class ProviderNotAllowedError(Exception):
    """Raised by assert_glm_allowed when `session` may not use `provider`. Carries
    the resolved client_key + the policy that refused it so a caller's loud bus
    escalation can cite the exact ruling without a second DB round-trip."""

    def __init__(self, session, provider, resolved, client_key=None):
        self.session = session
        self.provider = provider
        self.resolved = resolved
        self.client_key = client_key
        super().__init__(
            f"{session}: provider {provider!r} not allowed "
            f"(resolved client_key={client_key!r}, allowed={resolved['allowed_providers']})")


class ProviderPolicyMisconfigured(Exception):
    """Raised when a provider_policy_lane_override row would WIDEN its lane's
    client-level resolution. An override may only narrow, never widen — this is a
    configuration-integrity failure, never silently honored (orch-console Q2)."""


def resolve_client_key(session, client_keys):
    """PURE. Most-specific match of `session` against the known `client_keys`
    (provider_policy's OWN keys — data-driven, never a hardcoded family list;
    orch-console Q1). `session` is normalized by stripping a leading 'cc-' and a
    trailing '-<digits>' instance suffix, then matched against each key as either
    an EXACT match or a '<key>-' prefix (segment-based: 'hc9' must never match
    'hc95-broker'). Returns the LONGEST matching key. If more than one key ties at
    that longest length, the match is genuinely ambiguous — returns None so the
    caller fails CLOSED (Anthropic-only) rather than guessing between them."""
    if not session:
        return None
    s = session[3:] if session.startswith("cc-") else session
    s = re.sub(r"-\d+$", "", s)
    if not s:
        return None
    matches = [k for k in client_keys if s == k or s.startswith(k + "-")]
    if not matches:
        return None
    maxlen = max(len(k) for k in matches)
    best = [k for k in matches if len(k) == maxlen]
    return best[0] if len(best) == 1 else None


def lookup_lane_override(override_rows, session):
    """PURE. EXACT-match (never a prefix/family match — orch-console Q2) lookup
    against provider_policy_lane_override rows. Returns {allowed_providers,
    text_only} for `session`, or None if no row matches."""
    for r in override_rows or []:
        if r.get("lane") == session:
            return {"allowed_providers": list(r.get("allowed_providers") or ["anthropic"]),
                    "text_only": bool(r.get("text_only"))}
    return None


def is_narrower_or_equal(candidate, reference):
    """PURE. True iff candidate's allowed_providers is a subset of reference's —
    the one relation a lane override is permitted to have with the client-level
    policy under it. Equal sets count (an override that merely restates the
    client-level policy is fine, not just a strictly tighter one)."""
    return set(candidate["allowed_providers"]) <= set(reference["allowed_providers"])


def assert_glm_allowed(session, *, policy_rows, override_rows=None, provider="zai"):
    """Resolve whether `session` may use `provider` (default 'zai', i.e. GLM).

    Order: (1) provider_policy_lane_override exact match, if any, validated as a
    NARROWING of (2) the client-level resolution (resolve_client_key against
    policy_rows' own keys, then resolve_policy) — a widening override raises
    ProviderPolicyMisconfigured rather than being silently honored. The EFFECTIVE
    policy is the override if present and valid, else the client-level one.

    Raises ProviderNotAllowedError if `provider` is not in the effective
    allowed_providers. Returns None (no raise) if it is allowed.

    Fail-closed throughout: an unmapped or genuinely-ambiguous session
    (resolve_client_key returns None) resolves to Anthropic-only via
    resolve_policy's own unknown-key default — an unrecognized lane is refused a
    non-Anthropic provider, never silently allowed one."""
    override_rows = override_rows or []
    client_keys = [r.get("client_key") for r in policy_rows]
    key = resolve_client_key(session, client_keys)
    client_level = resolve_policy(rows=policy_rows, client_key=key) if key is not None else dict(ANTHROPIC_ONLY)

    override = lookup_lane_override(override_rows, session)
    if override is not None:
        if not is_narrower_or_equal(override, client_level):
            raise ProviderPolicyMisconfigured(
                f"{session}: lane override {override['allowed_providers']} is WIDER than its "
                f"resolved client_key={key!r} policy {client_level['allowed_providers']} — an "
                f"override may only narrow, never widen; refusing to honor it.")
        effective = override
    else:
        effective = client_level

    if provider not in effective["allowed_providers"]:
        raise ProviderNotAllowedError(session, provider, effective, client_key=key)


def _default_fetch_rows():
    """Impure: the real policy_rows/override_rows from the live DB. Factored out
    so gate_or_post_refusal is unit-testable with an injected fetch_rows, never
    needing a real DB in a test."""
    import psycopg
    from scripts.bus_send import dburl
    with psycopg.connect(dburl(os.environ)) as conn, conn.cursor() as cur:
        cur.execute("SELECT client_key, allowed_providers, text_only FROM provider_policy")
        cols = [d[0] for d in cur.description]
        policy_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT lane, allowed_providers, text_only FROM provider_policy_lane_override")
        cols = [d[0] for d in cur.description]
        override_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return policy_rows, override_rows


def _default_escalate(subject: str, body: str, priority: str) -> None:
    """Impure: one loud bus row to orch-console about a refused flip. Factored
    out so gate_or_post_refusal is unit-testable with an injected escalate,
    never needing a real DB in a test."""
    import psycopg
    from scripts.bus_send import dburl
    with psycopg.connect(dburl(os.environ)) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_agent_id','cc-fleet-health',true)")
        cur.execute(
            "INSERT INTO agent_messages (from_agent,to_agent,message_type,subject,body,priority,"
            "requires_response) VALUES ('cc-fleet-health','orch-console','blocker',%s,%s,%s,false)",
            (subject, body, priority))
        conn.commit()


def gate_or_post_refusal(session: str, *, actor: str = "cli", armed: bool = False,
                          break_glass: bool = False, provider: str = "zai",
                          fetch_rows=None, escalate=None) -> None:
    """The actual flip-time gate — wired into switch_lane_token.sh --model-apply
    for a glm-* model (orch-console #74584/#74588, bus #74586 spec). Resolves
    policy_rows/override_rows (via `fetch_rows`, defaulting to a real DB read)
    and calls assert_glm_allowed(); on refusal, posts ONE loud bus escalation
    (via `escalate`, defaulting to a real bus post) then RAISES SystemExit(1) —
    the exception shape the caller cannot forget to check (orch-console Q3), and
    the shell wrapper's exit code the caller DOES check.

    Priority split (orch-console Q3): P1 if an armed/break-glass (operator-
    directed) path was refused — a human intended this and needs eyes; P2 for a
    routine automated attempt correctly blocked (system working as intended)."""
    fetch_rows = fetch_rows or _default_fetch_rows
    escalate = escalate or _default_escalate
    policy_rows, override_rows = fetch_rows()
    try:
        assert_glm_allowed(session, policy_rows=policy_rows, override_rows=override_rows, provider=provider)
    except (ProviderNotAllowedError, ProviderPolicyMisconfigured) as e:
        priority = "P1" if (armed or break_glass) else "P2"
        subject = f"[provider-policy-refused] {session}: GLM flip hard-refused ({type(e).__name__})"
        body = (f"{e}\n\nactor={actor} armed={armed} break_glass={break_glass}. "
                f"No kill/relaunch happened — lane state is UNCHANGED.")
        escalate(subject, body, priority)
        print(f"REFUSED: {e}", file=sys.stderr)
        raise SystemExit(1)

"""Tests for the provider_policy resolver (Musa op#29321/#29324).

The resolver answers "for this client_key, which model providers are allowed,
and is text_only (vision/image MCP off) required?" from provider_policy rows.
Core safety property: fail-closed — an unknown key, or a row that allows a
non-Anthropic provider without text_only, resolves to Anthropic-only.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib"))
import provider_policy as pp  # noqa: E402


def test_unknown_client_fails_closed_to_anthropic_only():
    # No matching row must NEVER default to "safe to use a non-Anthropic provider".
    result = pp.resolve_policy(rows=[], client_key="totally-unknown")
    assert result == {"allowed_providers": ["anthropic"], "text_only": False}


def test_zai_without_text_only_is_refused_fail_closed():
    # Musa's HARD condition (op#29321/#29324): a non-Anthropic provider is allowed
    # only WITH text_only=true (vision/image MCP off, the 10-07 image-store finding).
    # A row that allows zai but forgets text_only must NOT silently route to z.ai —
    # it fails closed to Anthropic-only rather than leaking images to z.ai.
    rows = [{"client_key": "misconfigured", "allowed_providers": ["anthropic", "zai"], "text_only": False}]
    result = pp.resolve_policy(rows=rows, client_key="misconfigured")
    assert result == {"allowed_providers": ["anthropic"], "text_only": False}


def test_irsyad_is_anthropic_only():
    # Musa op#29321: irsyad = anthropic-only, HARD. No z.ai, ever.
    rows = [{"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False}]
    result = pp.resolve_policy(rows=rows, client_key="irsyad")
    assert result == {"allowed_providers": ["anthropic"], "text_only": False}


def test_cosem_adcda_is_glm_allowed_text_only():
    # Musa op#29324: ADCDA allowed on GLM, text_only=true (data via Singapore).
    rows = [{"client_key": "cosem-adcda", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    result = pp.resolve_policy(rows=rows, client_key="cosem-adcda")
    assert result == {"allowed_providers": ["anthropic", "zai"], "text_only": True}


# ── resolve_client_key: most-specific, data-driven lane->client_key match ─────
# (orch-console Q1, bus #74588) — verified against the real fleet names that
# a naive family_of()-style prefix-split would have mis-mapped.

_KEYS = ["cosem", "cosem-adcda", "cosem-exams", "mamadah", "finance", "hc95-broker", "bariq"]


def test_resolve_client_key_picks_the_more_specific_of_two_prefix_matches():
    assert pp.resolve_client_key("cc-cosem-adcda-1", _KEYS) == "cosem-adcda"
    assert pp.resolve_client_key("cosem-adcda", _KEYS) == "cosem-adcda"


def test_resolve_client_key_cosem_exams_does_not_collapse_into_cosem():
    assert pp.resolve_client_key("cosem-exams", _KEYS) == "cosem-exams"


def test_resolve_client_key_plain_cosem_lane_resolves_to_cosem():
    assert pp.resolve_client_key("cosem-platform-calendar", _KEYS) == "cosem"
    assert pp.resolve_client_key("cc-cosem-platform-2", _KEYS) == "cosem"


def test_resolve_client_key_exact_single_segment_match():
    assert pp.resolve_client_key("cc-mamadah-1", _KEYS) == "mamadah"
    assert pp.resolve_client_key("mamadah", _KEYS) == "mamadah"
    assert pp.resolve_client_key("finance", _KEYS) == "finance"


def test_resolve_client_key_full_lane_name_matches_its_own_row():
    # hc95-broker has its OWN provider_policy row — must not collapse to a
    # shorter "hc95" that isn't even in the key set here, and must not falsely
    # match some unrelated shorter key.
    assert pp.resolve_client_key("hc95-broker", _KEYS) == "hc95-broker"


def test_resolve_client_key_never_matches_a_bare_substring():
    # 'hc9' must never match 'hc95-broker' — segment-based only.
    assert pp.resolve_client_key("hc9", _KEYS) is None
    assert pp.resolve_client_key("hc9-something", _KEYS) is None


def test_resolve_client_key_unknown_lane_returns_none():
    assert pp.resolve_client_key("totally-unmapped-lane", _KEYS) is None


def test_resolve_client_key_empty_session_returns_none():
    assert pp.resolve_client_key("", _KEYS) is None
    assert pp.resolve_client_key(None, _KEYS) is None


def test_resolve_client_key_duplicate_key_entries_are_ambiguous_fails_closed():
    # A single prefix string can only ever match ONE distinct key at the longest
    # length by construction (two different strings of equal length can't both
    # be a prefix of the same session) -- the only way to hit a genuine tie is a
    # malformed key LIST with a duplicate entry (the real table's client_key is a
    # PRIMARY KEY, so this guards a pure function against a caller that doesn't
    # honor that invariant). Must fail closed (None), never pick either copy.
    dup_keys = ["cosem", "cosem"]
    assert pp.resolve_client_key("cosem-platform-calendar", dup_keys) is None


# ── lookup_lane_override: exact match only ─────────────────────────────────────

def test_lookup_lane_override_exact_match():
    rows = [{"lane": "cosem-adcda-photos", "allowed_providers": ["anthropic"], "text_only": False}]
    assert pp.lookup_lane_override(rows, "cosem-adcda-photos") == {
        "allowed_providers": ["anthropic"], "text_only": False}


def test_lookup_lane_override_no_prefix_matching():
    rows = [{"lane": "cosem-adcda-photos", "allowed_providers": ["anthropic"], "text_only": False}]
    assert pp.lookup_lane_override(rows, "cosem-adcda-photos-2") is None
    assert pp.lookup_lane_override(rows, "cosem-adcda") is None


def test_lookup_lane_override_none_when_no_rows():
    assert pp.lookup_lane_override([], "anything") is None
    assert pp.lookup_lane_override(None, "anything") is None


# ── assert_glm_allowed: the actual flip gate ────────────────────────────────────

def test_assert_glm_allowed_passes_silently_when_allowed():
    rows = [{"client_key": "cosem", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    assert pp.assert_glm_allowed("cosem-platform-calendar", policy_rows=rows) is None


def test_assert_glm_allowed_raises_for_unmapped_lane():
    rows = [{"client_key": "cosem", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    try:
        pp.assert_glm_allowed("totally-unmapped-lane", policy_rows=rows)
        assert False, "expected ProviderNotAllowedError"
    except pp.ProviderNotAllowedError as e:
        assert e.session == "totally-unmapped-lane"
        assert e.client_key is None


def test_assert_glm_allowed_raises_for_the_exact_mamadah_incident_shape():
    # the real bug this closes: mamadah's table row was the OLD broad op#29321
    # clearance; this proves a FIXED (anthropic-only) row now correctly refuses.
    rows = [{"client_key": "mamadah", "allowed_providers": ["anthropic"], "text_only": False}]
    try:
        pp.assert_glm_allowed("cc-mamadah-1", policy_rows=rows)
        assert False, "expected ProviderNotAllowedError"
    except pp.ProviderNotAllowedError as e:
        assert e.client_key == "mamadah"


def test_assert_glm_allowed_lane_override_narrows_silently_honored():
    rows = [{"client_key": "cosem-adcda", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    overrides = [{"lane": "cosem-adcda-photos", "allowed_providers": ["anthropic"], "text_only": False}]
    try:
        pp.assert_glm_allowed("cosem-adcda-photos", policy_rows=rows, override_rows=overrides)
        assert False, "expected ProviderNotAllowedError (override narrows to anthropic-only)"
    except pp.ProviderNotAllowedError as e:
        assert e.client_key == "cosem-adcda"  # override is on top of this resolution
        assert e.resolved["allowed_providers"] == ["anthropic"]


def test_assert_glm_allowed_sibling_lane_unaffected_by_strict_override():
    # cosem-adcda-photos being hard-excluded must not affect its sibling lane.
    rows = [{"client_key": "cosem-adcda", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    overrides = [{"lane": "cosem-adcda-photos", "allowed_providers": ["anthropic"], "text_only": False}]
    assert pp.assert_glm_allowed("cc-cosem-adcda-1", policy_rows=rows, override_rows=overrides) is None


def test_assert_glm_allowed_widening_override_is_misconfiguration_not_honored():
    rows = [{"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False}]
    overrides = [{"lane": "irsyad-worker-1", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    try:
        pp.assert_glm_allowed("irsyad-worker-1", policy_rows=rows, override_rows=overrides)
        assert False, "expected ProviderPolicyMisconfigured"
    except pp.ProviderPolicyMisconfigured:
        pass


def test_assert_glm_allowed_anthropic_always_allowed():
    rows = [{"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False}]
    assert pp.assert_glm_allowed("irsyad-worker-1", policy_rows=rows, provider="anthropic") is None


# ── gate_or_post_refusal: the actual flip-time gate wired into switch_lane_token.sh ──
# fetch_rows/escalate are injected so these never touch a real DB.

def _fetch(policy_rows, override_rows):
    return lambda: (policy_rows, override_rows)


# ── _default_escalate must go through bus_send.send(), never a hand-written INSERT
# (CLAUDE.md binding rule, bus #43651; orch-console #74628 caught the earlier
# hand-written version on this exact PR before merge). ───────────────────────────

def test_default_escalate_calls_bus_send_send_not_a_raw_insert(monkeypatch):
    calls = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return (1, "thread")

    from scripts import bus_send
    monkeypatch.setattr(bus_send, "send", fake_send)
    pp._default_escalate("subj", "body text", "P1", True)
    assert len(calls) == 1
    kw = calls[0]
    assert kw["from_agent"] == "cc-fleet-health"
    assert kw["to"] == "orch-console"
    assert kw["mtype"] == "blocker"
    assert kw["subject"] == "subj"
    assert kw["body"] == "body text"
    assert kw["priority"] == "P1"
    assert kw["req"] is True


def test_gate_allows_silently_when_allowed():
    rows = [{"client_key": "cosem", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    calls = []
    pp.gate_or_post_refusal("cosem-platform-calendar", fetch_rows=_fetch(rows, []),
                            escalate=lambda *a: calls.append(a))
    assert calls == []


def test_gate_refuses_and_escalates_and_exits_nonzero():
    rows = [{"client_key": "mamadah", "allowed_providers": ["anthropic"], "text_only": False}]
    calls = []
    try:
        pp.gate_or_post_refusal("cc-mamadah-1", fetch_rows=_fetch(rows, []),
                                escalate=lambda *a: calls.append(a))
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 1
    assert len(calls) == 1
    subject, body, priority, requires_response = calls[0]
    assert "cc-mamadah-1" in subject
    assert priority == "P2"  # routine, not armed/break-glass
    assert requires_response is False  # routine P2 is fire-and-forget, not an ask


def test_gate_priority_is_p1_when_armed():
    rows = [{"client_key": "mamadah", "allowed_providers": ["anthropic"], "text_only": False}]
    calls = []
    try:
        pp.gate_or_post_refusal("cc-mamadah-1", armed=True, fetch_rows=_fetch(rows, []),
                                escalate=lambda *a: calls.append(a))
    except SystemExit:
        pass
    assert calls[0][2] == "P1"
    assert calls[0][3] is True  # orch-console #74628: an armed/break-glass refusal needs eyes


def test_gate_priority_is_p1_when_break_glass():
    rows = [{"client_key": "mamadah", "allowed_providers": ["anthropic"], "text_only": False}]
    calls = []
    try:
        pp.gate_or_post_refusal("cc-mamadah-1", break_glass=True, fetch_rows=_fetch(rows, []),
                                escalate=lambda *a: calls.append(a))
    except SystemExit:
        pass
    assert calls[0][2] == "P1"
    assert calls[0][3] is True


def test_gate_escalates_on_misconfigured_override_too():
    rows = [{"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False}]
    overrides = [{"lane": "irsyad-worker-1", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    calls = []
    try:
        pp.gate_or_post_refusal("irsyad-worker-1", fetch_rows=_fetch(rows, overrides),
                                escalate=lambda *a: calls.append(a))
    except SystemExit:
        pass
    assert len(calls) == 1
    assert "ProviderPolicyMisconfigured" in calls[0][0]


# ── provider_for_model: the flip gate is TOTAL, not a list (orch-console #81004) ──
# Every non-Anthropic model id maps to a provider name the gate checks; only a
# recognised Anthropic id (or the empty fleet default) skips it; anything else
# fails CLOSED as an unknown provider no policy row can allow.

def test_provider_for_model_anthropic_ids_skip_the_gate():
    for m in ("", "claude-sonnet-5", "claude-opus-5-5", "claude-sonnet-5[1m]",
              "opus", "sonnet", "haiku", "sonnet[1m]", "claude-haiku-5-5"):
        assert pp.provider_for_model(m) is None, m


def test_provider_for_model_maps_each_routed_prefix():
    assert pp.provider_for_model("glm-5.3") == "zai"
    assert pp.provider_for_model("deepseek-flash") == "deepseek"
    assert pp.provider_for_model("deepseek-v4-pro") == "deepseek"
    assert pp.provider_for_model("kimi-k2") == "moonshot"
    assert pp.provider_for_model("moonshot-v1") == "moonshot"
    assert pp.provider_for_model("qwen3-coder") == "qwen"


def test_provider_for_model_openrouter_vendor_ids_map_to_openrouter():
    for m in ("moonshotai/kimi-k2", "deepseek/deepseek-chat", "qwen/qwen3-coder", "z-ai/glm-4.6"):
        assert pp.provider_for_model(m) == "openrouter", m


def test_provider_for_model_unknown_id_fails_closed():
    p = pp.provider_for_model("some-new-model-9")
    assert p is not None and p.startswith("unknown:")


_DS_ROWS = [
    {"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False},
    {"client_key": "mamadah", "allowed_providers": ["anthropic"], "text_only": False},
    {"client_key": "ffsfulfilment", "allowed_providers": ["anthropic", "zai", "deepseek"], "text_only": True},
]


def test_gate_refuses_deepseek_for_irsyad_and_mamadah():
    for sess in ("cc-irsyad-coord-1", "irsyad-coord", "cc-mamadah-1", "mamadah"):
        calls = []
        try:
            pp.gate_or_post_refusal(sess, provider="deepseek", fetch_rows=_fetch(_DS_ROWS, []),
                                    escalate=lambda *a: calls.append(a))
            assert False, f"expected SystemExit for {sess}"
        except SystemExit as e:
            assert e.code == 1
        assert len(calls) == 1 and "deepseek" in calls[0][0], sess


def test_gate_allows_deepseek_where_the_row_allows_it():
    calls = []
    pp.gate_or_post_refusal("cc-ffsfulfilment", provider="deepseek", fetch_rows=_fetch(_DS_ROWS, []),
                            escalate=lambda *a: calls.append(a))
    assert calls == []


def test_gate_refuses_unknown_provider_even_for_a_permissive_row():
    rows = [{"client_key": "ffsfulfilment", "allowed_providers": ["anthropic", "zai", "deepseek"], "text_only": True}]
    try:
        pp.gate_or_post_refusal("cc-ffsfulfilment", provider=pp.provider_for_model("some-new-model-9"),
                                fetch_rows=_fetch(rows, []), escalate=lambda *a: None)
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 1


def test_gate_main_cli_gates_by_model():
    # the shell calls `python -m scripts.lib.provider_policy --gate-model <model> <session>`
    calls = []
    rc = pp.gate_model_main(["--gate-model", "deepseek-flash", "cc-mamadah-1"],
                            fetch_rows=_fetch(_DS_ROWS, []), escalate=lambda *a: calls.append(a))
    assert rc == 1 and len(calls) == 1
    assert pp.gate_model_main(["--gate-model", "claude-sonnet-5", "cc-mamadah-1"],
                              fetch_rows=_fetch(_DS_ROWS, []), escalate=lambda *a: calls.append(a)) == 0
    assert len(calls) == 1  # an Anthropic model never consults the gate


# ── gate and routing must agree (orch-console #81044) ────────────────────────
# provider_for_model() once checked '/' (openrouter) BEFORE the native prefixes,
# while provider_routing.sh checks glm-*/kimi-*/moonshot-*/deepseek-* first, so
# 'glm-4.6/x' GATED as openrouter but RAN on z.ai: fail-closed while no row
# allows openrouter, a bypass the day one does. This test runs BOTH classifiers
# over one corpus so they cannot drift again.
import subprocess  # noqa: E402

_ROUTING_SH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib" / "provider_routing.sh"
_HOST_TO_PROVIDER = {"api.z.ai": "zai", "api.moonshot.ai": "moonshot", "api.deepseek.com": "deepseek",
                     "openrouter.ai": "openrouter", "coding-intl.dashscope.aliyuncs.com": "qwen"}
_CORPUS = ["glm-5.3", "glm-4.6/x", "GLM-5", "kimi-k2", "kimi-k2/preview", "moonshot-v1", "moonshot-v1/x",
           "deepseek-flash", "deepseek-v4-pro", "deepseek-flash/x", "moonshotai/kimi-k2",
           "deepseek/deepseek-chat", "qwen/qwen3-coder", "z-ai/glm-4.6", "qwen3-coder", "qwen3.6-flash",
           "openai/gpt-5", "claude-sonnet-5", "claude-opus-5-5", "claude-sonnet-5[1m]", "sonnet", "opus",
           "", "mystery-model"]


def _routing_provider(model):
    """What provider_routing.sh's _provider_for_model actually routes `model` to (None = not routed)."""
    r = subprocess.run(["bash", "-c", 'source "$1" >/dev/null 2>&1; _provider_for_model "$2"', "_",
                        str(_ROUTING_SH), model], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    host = r.stdout.strip().split("|")[1].split("//", 1)[1].split("/", 1)[0]
    return _HOST_TO_PROVIDER[host]


def test_native_prefix_wins_over_slash_like_routing():
    assert pp.provider_for_model("glm-4.6/x") == "zai"
    assert pp.provider_for_model("kimi-k2/preview") == "moonshot"
    assert pp.provider_for_model("deepseek-flash/x") == "deepseek"
    assert pp.provider_for_model("z-ai/glm-4.6") == "openrouter"
    assert pp.provider_for_model("qwen/qwen3-coder") == "openrouter"


def test_gate_and_routing_agree_on_every_routed_model():
    for m in _CORPUS:
        routed = _routing_provider(m)
        gated = pp.provider_for_model(m)
        if routed is not None:
            assert gated == routed, f"{m!r}: routing runs it on {routed} but the gate checks {gated}"
        else:
            # routing leaves it on Anthropic: the gate may skip (Anthropic id) or be STRICTER, never laxer
            assert gated is None or gated.startswith("unknown:") or gated in _HOST_TO_PROVIDER.values(), m

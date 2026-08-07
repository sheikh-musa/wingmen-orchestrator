# LLM-Agnosticism PoC Harness (internal lanes)

_2026-08-05 · op#10377 "proceed" · runs the moment a DeepSeek API key lands. Pairs with reports/llm-agnosticism-scoping-20260805.md._

## Setup
- **Test lane:** one internal tmux lane, DeepSeek V4-Flash via NATIVE endpoint (no proxy):
  `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`, `ANTHROPIC_AUTH_TOKEN=<deepseek key>`, model=deepseek-v4-flash (confirm current id). Non-client-data worktree only.
- **Control lane:** identical internal lane on Claude **Sonnet 5** (native) — the realistic "cheaper Claude" comparator, not Opus.
- Same prompts, clean context each task. Freeze the suite BEFORE running (no moving goalposts).

## Frozen 10-task internal suite (all SAFE: read-only or throwaway-branch, NO client data, NO prod writes)
Low-fidelity / short-horizon (expected DeepSeek WIN):
1. Summarize the last 20 wedge-watchdog bus alerts into a one-paragraph incident digest.
2. Aggregate `cc_session_costs` → top-5 token-consuming identities this week (SQL + prose).
3. Extract all TODO/FIXME across `nervous_system/*.py` into a grouped list.
4. Write a docstring for a chosen undocumented function (acceptance: describes args/return/side-effects correctly).
5. Generate a README section describing what `scripts/sre_lane_recycle.py` does (acceptance: matches the code).
6. Draft a changelog entry from a given git diff.
Single-file mechanical (expected WIN / borderline):
7. Rename a local variable consistently within one file (acceptance: tests green, no stray refs).
8. Extract a repeated inline block into a helper within one file (acceptance: behavior identical, tests green).
Long-horizon / multi-file (expected DEGRADE — the edge case we WANT to see):
9. Thread a new optional param through 3 call sites in a small module (acceptance: all callers updated, tests green).
10. Change a helper's signature + update every caller across the module (acceptance: tests green, no missed caller).

## Scoreboard (per task, both lanes)
completion (pass/fail on acceptance check) · #turns/tool-calls · tokens in/out · wall-clock · reviewer-correction count (edits a human had to make to accept) · $ cost · failure mode (tool-call-chain fail / ran-but-wrong / incomplete).
Headline metric = **cost-per-COMPLETED-task** = total $ / tasks that passed.

## Pass/fail bar
- **PASS**: on the mechanical/throwaway subset (1–8) — completion ≥90% of Claude, cost-per-completed-task ≤40% of Claude, review burden not materially higher.
- **CONDITIONAL**: passes only on 1–6 → agnosticism for throwaway lanes only.
- **FAIL**: even 1–6 needs babysitting → drop the abstraction; bank the Opus→Sonnet downshift.

## Parallel (no key needed)
- Price the **GLM Z.ai Coding Plan** flat-rate ($10–80/mo, Anthropic-native) as an always-on-lane cost model.
- Quantify the **zero-risk Opus→Sonnet 5 downshift** on throwaway lanes as the baseline saving.

## Ownership
Console (Nazim) coordinates + defines suite; the RUN is delegated to a lane once the key is staged (console ≠ IC). DeepSeek key needs operator's payment (prepaid API) — the one blocking input.

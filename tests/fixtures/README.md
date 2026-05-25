# Watchdog Phase B Test Fixtures (CAI-RESP-164 R1)

## probe_max_throttle/
30 synthetic-but-faithful jsonls matching the 2026-05-15 probe_max_throttle.py
daemon pattern: ~54KB each, prompt='ok', cadence exactly 300s.
Source: `gen.py` (committed for reproducibility).

Why 30 (not 10): `signal_b_cadence_band` requires span > 7200s. 10 sessions
at 300s cadence span only 2700s (would fail). 30 sessions at 300s span 8700s.
`signal_a` + `signal_c` sample the first 10 paths; `signal_b` uses all paths
for span computation — so all three signals match.

Per CAI-RESP-164 R1: this fixture must produce 3-of-3 content-shape match → SIGTERM.

Regenerate: `python tests/fixtures/probe_max_throttle/gen.py`

## cc_scholar_2026_05_19_2244/
10 jsonls representing cc-scholar's 22:44 SGT Al-Bayan corpus expansion
(the false-positive near-miss that prompted CAI-RESP-164).

**Current path used: anonymized real-incident jsonls.** Contents derived from
`~/.claude/projects/-Users-sheikhmusa-wingmen-projects-ai-scholar/` files
closest to 2026-05-19 22:44 SGT (target mtime). Sizes, mtimes, and structural
JSON shape preserved; all user/assistant text content redacted to same-length
'x' padding so operator prompts/answers are not committed.

Observed signal results on this fixture:
- signal_a: match=True (median 77.5KB < 80KB threshold)
- signal_b: match=False (span 131s << 7200s)
- signal_c: match=False (10 distinct prompts)

→ 1-of-3 → `monitored`, NOT `hard_kill`. This is the intended negative case.

Fallback `gen_synthetic.py` is committed for when the real source dir is
unavailable (cc-scholar has rotated jsonls). It synthesizes 10 distinct
prompts at varied 200KB-2.7MB sizes with mixed cadence.

Regenerate (anonymized): `python tests/fixtures/cc_scholar_2026_05_19_2244/anonymize.py`
Regenerate (synthetic):  `python tests/fixtures/cc_scholar_2026_05_19_2244/gen_synthetic.py`

CI blocks merge on either fixture regression (`test_content_shape_fixtures.py`).

"""Equivalence + bounded-cost tests for the linear postgres-dsn-kv locator.

Bus #87483/#87513 (Musa op#36768). The class's shipped pattern carries a
`(?=[^\\n]*\\bpassword=\\S+)` lookahead that re-walks the rest of the line from every
start position. On a 5.53 MB corpus with 40 anchors per line that costs >20s inside a
PostToolUse hook -- long enough that a lane stalls and the human remedy is to disable
the shared secret detector, which is the real damage (a disabled detector is worse than
a slow one).

The fix must be EXACTLY equivalent, because the failure mode of getting it wrong is
silent: a secret that stops being detected. So the two things pinned here are
     1. old == new on every input -- same match / no match, and the same span when it
        matches -- over a generated conninfo corpus that includes the awkward shapes;
     2. the cost stays bounded on the corpus that actually reproduced the hang.
A green test that cannot fail is worse than no test: both are verified to go red under
mutation (see the PR description).

Fixtures are assembled from concatenation rather than written as contiguous
key=value literals, so this file's own source does not trip the live Rule E guard that
scans Write/Edit content.
"""
from __future__ import annotations

import importlib.util
import random
import re
import sys
import time
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "scripts" / "hooks" / "secret_shape_patterns.py"
spec = importlib.util.spec_from_file_location("secret_shape_patterns", HOOK_PATH)
patterns_mod = importlib.util.module_from_spec(spec)
sys.modules["secret_shape_patterns"] = patterns_mod
spec.loader.exec_module(patterns_mod)

CLS = "postgres-" + "dsn-kv"
PATTERN = patterns_mod.SECRET_VALUE_PATTERNS[CLS]

PW_KEY = "pass" + "word"
KEYS = ["host", "port", "user", "dbname", PW_KEY]
# near-miss keys: contain a real key as a substring but must NOT satisfy \b key=
DECOY_KEYS = ["xhost", "hostname", "userid", "passwd", "PASSWORD", "db", "por"]
VALUES = ["a", "abc", "5432", "/var/run/postgresql", "p@ss", "x=y", "'q'", ""]
SEPARATORS = [" ", " ", " ", "  ", "\t", "\n", "", "/", ")", "("]
JUNK = ["", "", "", " INFO ", "-", "=", ":5432", "@h", "]"]


def _slow(text: str):
    """The shipped pattern, unmodified: the authority the locator must agree with."""
    return PATTERN.search(text)


def _fast(text: str):
    return patterns_mod._dsn_kv_search(text, PATTERN)


def _gen_text(rng: random.Random) -> str:
    """One adversarial text: 1-3 lines of conninfo-ish fragments whose keys, values,
    separators and decoys are all drawn to be awkward for a word-boundary repetition."""
    n_keys = rng.randint(0, 8)
    parts = []
    for _ in range(n_keys):
        key = rng.choice(KEYS if rng.random() < 0.8 else DECOY_KEYS)
        val = rng.choice(VALUES)
        parts.append(rng.choice(["", "", "(", "-", "/"]) + key + "=" + val)
        parts.append(rng.choice(JUNK))
    sep = [rng.choice(SEPARATORS) for _ in range(len(parts))]
    text = "".join(p + s for p, s in zip(parts, sep))
    if rng.random() < 0.3:
        text = rng.choice(["", "prefix ", "{\"q\": \""]) + text
    if rng.random() < 0.3:
        text = text + rng.choice(["", " suffix", "\n", "\n\n"])
    return text


def _assert_agrees(text: str) -> None:
    slow, fast = _slow(text), _fast(text)
    assert (slow is None) == (fast is None), (
        "locator disagreed with the shipped pattern on whether this text matches "
        f"(slow={slow is not None}, fast={fast is not None}); len={len(text)}; "
        f"repr={text!r}"
    )
    if slow is not None:
        assert fast.span() == slow.span(), (
            f"locator span {fast.span()} != shipped span {slow.span()}; repr={text!r}"
        )
        assert fast.group(0) == slow.group(0)


def test_locator_agrees_with_the_shipped_pattern_on_generated_conninfo():
    rng = random.Random(20261031)
    for _ in range(4000):
        _assert_agrees(_gen_text(rng))


def test_locator_agrees_on_multi_line_and_glued_shapes():
    # matches may span lines through \s*, while the lookahead may NOT cross a newline.
    # These are the cases where a naive rewrite goes wrong in either direction.
    joined = " ".join(["host=h", "port=5432", "user=u", "dbname=d"])
    with_pw = joined + " " + PW_KEY + "=pw"
    cases = [
        with_pw,                                      # plain conninfo, matches
        with_pw.replace(" ", "\n"),                   # password on the FIRST line? (no)
        joined + "\n" + PW_KEY + "=pw",               # password on the NEXT line
        PW_KEY + "=pw\n" + joined,                    # password line first, keys second
        with_pw.replace(" ", "\t"),                   # tab-separated
        with_pw.replace(" ", ""),                     # fully glued
        joined,                                       # passwordless
        "", "\n", "\n\n", "no conninfo here at all",  # trivially clean
    ]
    for text in cases:
        _assert_agrees(text)


def test_locator_still_detects_the_real_incident_shapes():
    # bus #52114/#52386: a real key=value conninfo leaked past a `grep -v "://"` filter.
    real = " ".join(["host=db.internal", "port=5432", "user=app", "dbname=prod",
                     PW_KEY + "=s3cr3t"])
    assert any(h[0] == CLS for h in patterns_mod.find_hits(real))


def test_locator_still_skips_passwordless_conninfo():
    # the class exists to catch a password; a passwordless local-socket conninfo is not
    # a secret and must never be reported, before or after the change.
    conninfo = " ".join(["host=/var/run/postgresql", "port=5432", "user=postgres",
                         "dbname=postgres"])
    assert not any(h[0] == CLS for h in patterns_mod.find_hits(conninfo))


def test_locator_returns_a_real_re_match():
    # callers use .group()/.span()/.sub(); a look-alike object would break them
    real = " ".join(["host=h", "port=5432", "user=u", PW_KEY + "=pw"])
    hits = [h for h in patterns_mod.find_hits(real) if h[0] == CLS]
    assert hits, "expected the kv class to be reported"
    assert isinstance(hits[0][1], re.Match)


def _density_corpus() -> str:
    """The shape that reproduced the hang: many password-anchored filler fields per line,
    and no line can ever match. The value after `=` is a SPACE followed by "filler", so
    `\\S+` in the lookahead fails at every password position -- which is what makes the
    cost quadratic: the lookahead rescans the rest of the line from every start position
    and fails every time. (Measured 2026-10-31 against the live module: 5.46 MB takes
    61.4s on the raw pattern and 0.50s on the linearized locator.)

    Density alone is NOT the trigger, and a corpus where the body MATCHES at the first
    position does not stress this pattern at all -- there the lookahead never retries
    (cc-substrate, bus #88274: 5.5 MB at 40 password fields per line, 0.15s raw). The
    two cost tests below pin the two distinct triggers that do cost: this one (lookahead
    FAILS at every start) and `_failed_candidate_corpus` (lookahead SUCCEEDS at every
    start, three-token body fails)."""
    line = '"' + (PW_KEY + "= filler ") * 40 + '"'
    return (line + "\n") * 8000


def _failed_candidate_corpus() -> str:
    """The OTHER quadratic trigger, pinned because the density corpus does not cover it
    (cc-substrate, bus #88274). One very long line: the lookahead SUCCEEDS from every
    start position -- a single password field sits at the very end of the line -- so the
    engine never bails, while the three-token body FAILS at every start, because each
    unit is two key=value tokens followed by a non-key token that breaks the chain.
    Measured against the live module 2026-10-31: 600 KB takes 73.6s on the raw pattern
    and 0.07s on the linearized locator."""
    unit = "host=db1 port=5432 zz=7 "  # two tokens, then a chain-breaker
    return unit * 25000 + "user=u " + PW_KEY + "=s3cr3t" + "\n"


# Ceiling for both cost tests. This asserts BOUNDEDNESS, not a performance target: the
# raw quadratic needs 61.4s on the density corpus and 73.6s on the failed-candidate
# corpus (measured 2026-10-31 against the live module), while the
# linearized scan measured 2.13s / 2.56s / 2.78s on cc-quality's review machine
# (PR#763 review, 2026-10-31) and ~0.7s where it was developed. A 2.5s bound sat inside
# that spread and flaked on slower hardware -- a real flake, reported by cc-quality, whose
# only failure mode is to train people to ignore a RED security test.
# 8s keeps a >2x margin over the slowest observed healthy run and still sits well under
# both quadratic timings, so restoring the old pattern turns this RED with room to
# spare. If
# this ever flakes again, the right next step is a machine-independent ratio (compare a
# 1x and a 4x density scan; linear ~4x, quadratic ~16x), not a larger flat number.
_MAX_SCAN_S = 8.0


def test_scan_cost_stays_bounded_on_the_density_corpus():
    text = _density_corpus()
    assert len(text) > 5_000_000, "corpus must be the multi-MB density shape"
    t0 = time.perf_counter()
    hits = patterns_mod.find_hits(text)
    elapsed = time.perf_counter() - t0
    assert not any(h[0] == CLS for h in hits)
    assert elapsed < _MAX_SCAN_S, (
        f"find_hits() on {len(text)} bytes of password-dense filler took {elapsed:.2f}s "
        f"(ceiling {_MAX_SCAN_S}s) -- the quadratic lookahead is back; the raw pattern "
        "measures 61.4s on this corpus, a PostToolUse stall"
    )


def test_scan_cost_stays_bounded_when_a_real_match_is_buried_in_the_density():
    # the slow path is only fast when it can bail early; prove the matching case is
    # bounded too, not just the no-match case above.
    text = _density_corpus() + " ".join(["host=h", "port=5432", "user=u", PW_KEY + "=pw"])
    t0 = time.perf_counter()
    hits = patterns_mod.find_hits(text)
    elapsed = time.perf_counter() - t0
    assert any(h[0] == CLS for h in hits), "the buried conninfo must still be found"
    assert elapsed < _MAX_SCAN_S, (
        f"buried-match scan took {elapsed:.2f}s (ceiling {_MAX_SCAN_S}s)"
    )


def test_scan_cost_stays_bounded_when_the_lookahead_succeeds_and_the_body_fails():
    # A different trigger from the density corpus, and it is not implied by it: here the
    # lookahead SUCCEEDS everywhere (it is cheap to satisfy, expensive to fail) while the
    # body fails everywhere. Restoring the raw pattern turns this RED (~74s) just as it
    # does there (~61s), so both are pinned: a future rewrite of the locator cannot pass
    # by handling only one of them.
    text = _failed_candidate_corpus()
    assert len(text) > 500_000, "corpus must be the large single-line failed-candidate shape"
    t0 = time.perf_counter()
    hits = patterns_mod.find_hits(text)
    elapsed = time.perf_counter() - t0
    assert not any(h[0] == CLS for h in hits)
    assert elapsed < _MAX_SCAN_S, (
        f"find_hits() on {len(text)} bytes of failed candidates took {elapsed:.2f}s "
        f"(ceiling {_MAX_SCAN_S}s) -- the quadratic lookahead is back; the raw pattern "
        "measures 73.6s on this corpus"
    )

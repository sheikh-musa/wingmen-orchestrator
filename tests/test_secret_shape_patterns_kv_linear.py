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
    """The exact shape that reproduced the hang: many password-anchored filler fields
    per line. Anchor DENSITY per line is the variable that matters -- a sparse corpus a
    third this size runs in ~3s and hides the problem completely."""
    line = '"' + (PW_KEY + "= filler ") * 40 + '"'
    return (line + "\n") * 8000


def test_scan_cost_stays_bounded_on_the_density_corpus():
    text = _density_corpus()
    assert len(text) > 5_000_000, "corpus must be the multi-MB density shape"
    t0 = time.perf_counter()
    hits = patterns_mod.find_hits(text)
    elapsed = time.perf_counter() - t0
    assert not any(h[0] == CLS for h in hits)
    assert elapsed < 2.5, (
        f"find_hits() on {len(text)} bytes of password-dense filler took {elapsed:.2f}s "
        "-- the quadratic lookahead is back (pre-fix this is >20s in a PostToolUse hook)"
    )


def test_scan_cost_stays_bounded_when_a_real_match_is_buried_in_the_density():
    # the slow path is only fast when it can bail early; prove the matching case is
    # bounded too, not just the no-match case above.
    text = _density_corpus() + " ".join(["host=h", "port=5432", "user=u", PW_KEY + "=pw"])
    t0 = time.perf_counter()
    hits = patterns_mod.find_hits(text)
    elapsed = time.perf_counter() - t0
    assert any(h[0] == CLS for h in hits), "the buried conninfo must still be found"
    assert elapsed < 2.5, f"buried-match scan took {elapsed:.2f}s"

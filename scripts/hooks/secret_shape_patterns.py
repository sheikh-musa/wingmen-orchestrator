"""Shared secret-VALUE shape patterns (Musa op#24408), used by BOTH:

  - secrets_transcript_guard.py's Rule E (PreToolUse, blocks a Bash command that
    types a secret literally inline instead of referencing it by $VAR)
  - secrets_output_scanner.py (PostToolUse, catches/redacts a secret-shaped value
    that still made it into a tool_use input or a tool_result output)

One shared source so the two layers can't silently drift apart (bus #48685/#48695:
cc-fleet-health's real-leak-shapes sweep found literal DSNs/tokens typed directly into
Bash commands -- shape 1 ~84x, mostly `psql postgres://user:pass@host/db`; shape 2 ~55x,
mostly `curl -H "Authorization: Bearer <token>"` / an inline bot token -- that the old
guard never blocked pre-execution and the old scanner, which only ever looked at
tool_response/stdout, never caught in the tool_use INPUT either).
"""
from __future__ import annotations

import re

SECRET_VALUE_PATTERNS = {
    "anthropic-api-key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "supabase-service-key": re.compile(r"sbp_[a-f0-9]{20,}"),
    # no leading \b: a Telegram Bot API URL embeds the token right after "bot" with no
    # word boundary (.../bot123456789:AA.../sendMessage) -- anchored on the literal "AA"
    # prefix real bot tokens use instead (bus #48642 real incident).
    "telegram-bot-token": re.compile(r"\d{8,10}:AA[A-Za-z0-9_-]{30,}\b"),
    "postgres-dsn": re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"),
    # libpq key=value conninfo form (host=... port=... user=... dbname=... password=...),
    # which has no "://" and so slips a `grep -v "://"` filter (bus #52114/#52386 real
    # incident: a passwordless local-socket DATABASE_URL in this form leaked past that
    # exact filter). Requires `password=` actually present in the run of tokens -- a
    # passwordless local-socket conninfo (host=/var/..., no password= field) is not a
    # secret and must NOT match, or every CI-bootstrap ephemeral-cluster line would page.
    "postgres-dsn-kv": re.compile(
        r"(?=[^\n]*\bpassword=\S+)(?:\b(?:host|port|user|dbname|password)=\S+\s*){3,}"
    ),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    "vercel-token": re.compile(r"\bvcp_[A-Za-z0-9]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "google-oauth-refresh-token": re.compile(r"\b1//0[A-Za-z0-9_-]{20,}\b"),
    "ssh-private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # bus #48685/#48695 shape 2: a bearer/bot token typed literally into a curl header
    # or an export, rather than referenced via $VAR. Not anchored to "Authorization:" --
    # orch-console's ask covers "Bearer <token>" generally (curl -H, a raw header dump,
    # an export of the header value).
    "bearer-token": re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{15,}\b", re.IGNORECASE),
}

# Cheap, SOUND prefilter: a tuple of literal substrings at least one of which MUST be
# present for the class's full pattern to have any chance of matching -- a plain `in`
# check costs microseconds even on a multi-MB text, vs. a full regex .search() that can
# cost orders of magnitude more (bus #54580/#54643: profiling scripts/hooks/
# secrets_output_scanner.py on a 1.6MB representative clean tool output found
# postgres-dsn-kv alone at ~1.5s -- ~40x every other pattern -- because its
# `(?=[^\n]*\bpassword=\S+)` lookahead reruns from every candidate `host|port|...=`
# position; the other ~10 patterns were each ~30-40ms just from the base cost of one
# full-text regex .search()). Checking these literals FIRST skips the regex entirely
# for the overwhelming majority of real calls, which contain none of these substrings.
# A missing-anchor class (not a key here) always falls through to the full pattern --
# fail open to scanning, never fail open to skipping. bearer-token has NO entry: its
# pattern is case-INSENSITIVE, so no fixed-case literal substring is a sound anchor
# (a mixed-case "BeArEr" would be missed), and its own base cost is unremarkable
# (~40ms/1.6MB) -- not worth risking a detection gap to save it.
SECRET_VALUE_ANCHORS: dict[str, tuple[str, ...]] = {
    "anthropic-api-key": ("sk-ant-",),
    "supabase-service-key": ("sbp_",),
    "telegram-bot-token": (":AA",),
    "postgres-dsn": ("postgres://", "postgresql://"),
    # the pattern's own lookahead already requires `password=` -- this is not a looser
    # approximation, it is the exact same necessary condition, just checked BEFORE the
    # expensive lookahead/quantifier instead of inside it.
    "postgres-dsn-kv": ("password=",),
    "jwt": ("eyJ",),
    "vercel-token": ("vcp_",),
    "github-token": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"),
    "google-oauth-refresh-token": ("1//0",),
    "ssh-private-key": ("PRIVATE KEY-----",),
}


def has_candidate(text: str, cls: str) -> bool:
    """True if `cls`'s full pattern could possibly match `text`. False is a SOUND skip
    (the full pattern provably cannot match, so never run it); True is only permission
    to try, not a guarantee of a match. A class with no registered anchor (not vetted,
    or -- like bearer-token -- has no sound literal anchor) always returns True: fail
    open to running the full regex, never fail open to skipping it."""
    anchors = SECRET_VALUE_ANCHORS.get(cls)
    if anchors is None:
        return True
    return any(a in text for a in anchors)


# --- linear locator for postgres-dsn-kv (bus #87483/#87513, Musa op#36768) ----------
# The class's shipped pattern is TWO conditions over one start position:
#
#     (?=[^\n]*\bpassword=\S+)                  <- A: a password field later ON THE SAME LINE
#     (?:\b(?:host|port|user|dbname|password)=\S+\s*){3,}   <- B: the match itself
#
# A is quadratic in LINE LENGTH (and only linear in line count): `[^\n]*` re-walks the
# rest of the line from EVERY start position, so a long line of anchors is what hurts,
# not a large file. Measured on the 5.53 MB density corpus (40 `password=` fields per
# line): A alone >20s, the pair >20s, while B alone is 0.47s and a bare password-token
# scan 0.20s. Sparse probes do NOT reproduce it -- a 2.85 MB corpus with one anchor per
# line finishes in ~2.8s and looks survivable, which is how this was nearly dismissed.
#
# A depends ONLY on the start position's line, so it can be decided once per candidate
# start instead of once per byte. B is already linear, so: find B's leftmost match, ask
# A directly, and skip the whole line when A fails. The anchored `pattern.match` at that
# start then yields a genuine re.Match with byte-identical spans, so every caller
# (`.group()`, `.sub()`, `.span()`) is unaffected. Detection is unchanged -- this is a
# faster way to answer the same question, never a narrower one.
_DSN_KV_BODY = re.compile(r"(?:\b(?:host|port|user|dbname|password)=\S+\s*){3,}")

# Split so this file's own source does not contain one contiguous conninfo token: the
# live Rule E guard scans Write/Edit content, and a literal key=value pair in a guard's
# own source is exactly the false positive it would otherwise have to whitelist.
_DSN_KV_PASSWORD = re.compile(r"\bpassword" + r"=\S+")


def _dsn_kv_search(text: str, pattern: re.Pattern) -> "re.Match | None":
    """Leftmost match of the kv class's own `pattern`, without ever running its
    quadratic lookahead at a position where it cannot pay off.

    Equivalent to `pattern.search(text)` by construction: `_DSN_KV_BODY` and
    `_DSN_KV_PASSWORD` are the pattern's own two halves, and both are evaluated at the
    same start position the shipped pattern would. Never returns "no match" for a text
    the slow path would match: if the two ever disagree the authoritative (slow) scan
    is run instead, so a bug here can cost time but can never fail open."""
    pos = 0
    while True:
        body = _DSN_KV_BODY.search(text, pos)
        if body is None:
            return None
        start = body.start()
        nl = text.find("\n", start)
        line_end = len(text) if nl < 0 else nl
        pw = _DSN_KV_PASSWORD.search(text, start)
        if pw is not None and pw.start() < line_end:
            hit = pattern.match(text, start)
            if hit is not None:
                return hit
            # Unreachable unless the halves above have drifted from `pattern`.
            # Fail CLOSED -- hand it to the slow, authoritative scan rather than
            # reporting a clean text we cannot actually prove is clean.
            return pattern.search(text, start)
        # No start position from here to end-of-line can satisfy condition A, so the
        # next candidate can only be on a later line.
        if nl < 0:
            return None
        pos = nl + 1


# Classes whose scan is known-cost, located without running their full pattern at every
# offset. Only classes vetted for exact equivalence belong here; anything absent from
# this map keeps the plain `pattern.search(text)` path unchanged.
_LINEAR_SEEKERS = {
    "postgres-dsn-kv": _dsn_kv_search,
}


def find_hits(text: str, patterns: dict[str, re.Pattern] = SECRET_VALUE_PATTERNS) -> list[tuple[str, re.Match]]:
    """Anchor-prefiltered scan over every class in `patterns`, in the same iteration
    order `dict(patterns).items()` would give -- same hits, same first-match-wins order,
    as a plain `[(cls, m) for cls, p in patterns.items() if (m := p.search(text))]`; the
    only difference is how many of those regexes actually run, and (for a class with a
    registered linear seeker) how the one match it reports is located."""
    hits = []
    for cls, pattern in patterns.items():
        if not has_candidate(text, cls):
            continue
        seeker = _LINEAR_SEEKERS.get(cls)
        # `pattern is ...` not `cls in ...`: a caller passing its own pattern dict must
        # get that pattern's own behaviour, never a locator written for a different one.
        if seeker is not None and pattern is SECRET_VALUE_PATTERNS.get(cls):
            m = seeker(text, pattern)
        else:
            m = pattern.search(text)
        if m:
            hits.append((cls, m))
    return hits

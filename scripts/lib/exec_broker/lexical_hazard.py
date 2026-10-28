#!/usr/bin/env python3
"""lexical_hazard.py — the ONE shared lexical-hazard pre-screen for the
vendored ddl_detect lexer's blind spots.

WHY THIS EXISTS: strip_sql_noise/is_ddl_shaped (vendored, sha256-pinned,
NEVER hand-edited) are good-faith single-pass lexers, not a full PG parser,
and have two documented blind spots that can swallow real top-level code:

1. Whole-branch review Critical #1 (2026-10-28): the vendored lexer has NO
   double-quoted-IDENTIFIER state at all -- a bare ', --, /* or $tag$ that
   textually follows a "-looking identifier opens a bogus noise region
   (line comment / block comment / string / dollar body) exactly as if it
   were bare code, because the lexer never knew it was "inside quotes" to
   begin with. A LINE- or BLOCK-COMMENT swallow is never re-scanned by
   anything downstream (unlike a dollar body, which both is_plain_read's
   body-token scan and is_ddl_shaped's own DDL-keyword rescan DO re-examine)
   -- so this is the one PROVEN, permanent blind spot: e.g.
   SELECT "x--y"; DROP TABLE t has is_ddl_shaped() == False, because the
   `--` after the quote char opens a line comment hiding "; DROP TABLE t"
   for good. An E'..' backslash escape similarly closes a plain string
   early relative to true PG semantics, letting what PG still considers
   string DATA be treated as CODE by the vendored lexer -- any further
   noise-opener inside that mis-exposed span cascades into the same hole.

2. PR #569 review's identifier-$ bypass (2026-10-28): $ is a legal PG
   identifier CONTINUATION character, so a$x$ is ONE identifier token to
   PG -- but both this module's own dollar-open regex and the vendored
   one match $x$ mid-identifier, opening a dollar body that swallows real
   code up to the next matching $x$. Unlike class 1, anything swallowed
   this way IS still a dollar body and IS re-scanned for DDL keywords by
   is_ddl_shaped and for write tokens by is_plain_read's body scan -- so
   this class has NOT been demonstrated to defeat is_ddl_shaped on its own
   (only is_plain_read's narrower INTO/FOR check, which this module's
   caller in read_only.py also closes). It is still refused here, fail-
   closed, for the same "cannot safely classify this input -> refuse"
   posture as class 1, and because a caller hitting it should re-quote
   rather than have EITHER consumer try to reason about it further.

REUSE, NEVER REIMPLEMENT: both scripts/lib/exec_broker/read_only.py (the
RO-override plain-read screen) and scripts/lib/exec_broker/gate_validation.py
(require_gate's base DDL-gate path, rollout step (c), bus #79161) import
is_lexically_hazardous from HERE -- never re-derive this scan in either
consumer. A second, divergent copy is exactly the footgun this extraction
closes; the two pre-screens share this ONE definition so they cannot drift,
the same reasoning as gate_validation.ref_override_is_live being the one
liveness definition shared by check_project_ref and check_read_only_override.

FAIL-CLOSED DOCTRINE: false refusals are acceptable; a missed write (or
missed DDL) is not. Concretely, a quoted identifier containing '/--/*/$, an
identifier followed directly by $ (e.g. col$1), and any backslash anywhere
are all treated as hazardous -- the same deliberate over-refusal stance
ddl_detect takes ("a false refusal is fail-safe for a screen"). A caller
hitting one renames or re-quotes; nothing legitimate is silently let
through the other way.
"""
from __future__ import annotations

import re

# Dollar-quote opener: $$ or $tag$ where tag is [A-Za-z_]\w* — the SAME
# pattern the vendored _DOLLAR_OPEN_RE uses, so this pre-screen tracks
# exactly the regions the vendored lexer could mis-read.
_DOLLAR_OPEN_RE = re.compile(r"\$([A-Za-z_]\w*|)\$")


def _is_identifier_char(ch: str) -> bool:
    """Broad/inclusive on purpose (fail-closed): ASCII alnum, underscore,
    dollar, or any Unicode letter/digit (str.isalnum() covers those) -- PG
    identifiers may contain non-ASCII letters, and erring broad only means
    MORE refusals, never a missed one."""
    return ch.isalnum() or ch in ("_", "$")


def _hazard_region_scan(sql: str) -> bool:
    """True iff sql contains a ', --, /* or $tag$ inside a double-quoted
    identifier, OR a $ directly following an identifier character (see
    module docstring classes 1 and 2).

    Single left-to-right pass mirroring the vendored lexer's own region set
    (code / 'string' / "identifier" / -- comment / nestable /* comment */ /
    $tag$ body), so a "..." region is located exactly where PG locates it.
    Divergences are all in the fail-closed direction:
    - unterminated "..." or $tag$ runs to EOS for us; the code it would hide
      is a PG syntax error upstream, never an executed write;
    - a bare $ (e.g. a positional $1) that does NOT immediately follow an
      identifier character is not an opener for us; those are not a broker
      input class (statements arrive fully rendered).
    """
    i, n = 0, len(sql)
    state = "code"
    dollar_tag = ""
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if state == "code":
            if two == "--":
                state = "line_comment"
                i += 2
            elif two == "/*":
                state = "block_comment"
                depth, i = 1, i + 2
                while i < n and depth:
                    if sql[i:i + 2] == "/*":
                        depth, i = depth + 1, i + 2
                    elif sql[i:i + 2] == "*/":
                        depth, i = depth - 1, i + 2
                    else:
                        i += 1
                continue
            elif ch == "'":
                state = "squote"
                i += 1
            elif ch == '"':
                state = "dquote"
                i += 1
            else:
                if ch == "$" and i > 0 and _is_identifier_char(sql[i - 1]):
                    # $ immediately after an identifier char: PG folds this
                    # into the SAME identifier, but naively matching a
                    # $tag$ pattern here (as both lexers do) can open a
                    # bogus dollar body mid-statement. Refuse the whole
                    # input class, fail-closed, rather than try to track
                    # whether a matching close tag would actually follow.
                    return True
                m = _DOLLAR_OPEN_RE.match(sql, i)
                if m:
                    dollar_tag = m.group(0)
                    state = "dollar"
                    i = m.end()
                else:
                    i += 1
        elif state == "squote":
            if ch == "'":
                if two == "''":  # doubled quote: escaped, still in-string
                    i += 2
                else:
                    state = "code"
                    i += 1
            else:
                i += 1
        elif state == "dquote":
            if ch == '"':
                if two == '""':  # doubled quote: escaped, still in-identifier
                    i += 2
                else:
                    state = "code"
                    i += 1
            elif ch in ("'", "$") or two in ("--", "/*"):
                return True  # hazard inside a quoted identifier
            else:
                i += 1
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
            i += 1
        else:  # dollar body — exact-tag close only
            if sql.startswith(dollar_tag, i):
                i += len(dollar_tag)
                state = "code"
            else:
                i += 1
    return False


def is_lexically_hazardous(sql: str) -> bool:
    """True iff sql contains any construct that can blind the vendored
    lexer: any backslash anywhere, OR the quoted-identifier / identifier-$
    hazard region class (see module docstring)."""
    return "\\" in sql or _hazard_region_scan(sql)

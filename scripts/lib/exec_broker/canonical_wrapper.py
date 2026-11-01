#!/usr/bin/env python3
"""canonical_wrapper.py — hc#95 ruling C (console #89827/#89841): the plain-read
gate exemption for exec_prod's WRAPPED reads, decided SERVER-SIDE.

WHY: exec_prod sends every statement wrapped in the canonical agent-actor guard
(ihsanos-irsyad scripts/db/lib/agent-actor-guard.sql, spliced by
agent_actor_guard.wrap()). That wrapper is `SET TRANSACTION ...; DO $..$ ...
set_config(...) ...`, so read_only.is_plain_read(guarded_sql) is ALWAYS False
for exec_prod traffic, and B1 would demand a gate for every wrapped read.

WHAT: is_wrapped_plain_read(sql) is True only when the broker itself proves
  1. sql == PREFIX + inner + SUFFIX, byte-for-byte, where PREFIX/SUFFIX are the
     two halves of the VENDORED wrapper (agent-actor-guard.sql next to this
     file, sha256-pinned below) split exactly the way wrap() splices: at the
     placeholder line, with wrap()'s own "\n" + inner.rstrip() + "\n" framing,
     for the DEFAULT actor only (wrap()'s actor retarget rewrites PREFIX, so a
     retargeted wrapper never matches -- fail closed);
  2. inner is in wrap()'s canonical form (non-empty, no trailing whitespace);
  3. inner is LEXICALLY CLOSED inside the wrapper: the vendored lexer's
     strip_sql_noise over the whole statement equals the concatenation of its
     output over PREFIX, inner and SUFFIX separately. An inner that leaves a
     string/comment/dollar-body open would swallow part of SUFFIX and fails
     this check;
  4. read_only.is_plain_read(inner) -- the SAME classifier as the raw-read path
     (lexical-hazard pre-screen, DDL, write tokens, write functions, read heads).
There is NO caller-supplied flag anywhere in this decision. Anything that does
not match is simply "not a wrapped plain read" and goes through the normal gate.

PIN: a vendored copy whose bytes no longer hash to WRAPPER_SHA256 disables the
exemption entirely (every wrapped read then needs a gate) -- never a crash and
never a looser match. Re-sync = copy the upstream file byte-for-byte, update
WRAPPER_SHA256 and the pin test, and the irsyad-side pin test in the same change.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .ddl_detect import strip_sql_noise

# ihsanos-irsyad main scripts/db/lib/agent-actor-guard.sql @ dd7f9468
# (last changed by 007320b7, PR #870).
WRAPPER_SHA256 = "7ec3ba9167b321db72cdec03c3868c9a5e628deace012f5b74e0dd20561cff90"
WRAPPER_PATH = Path(__file__).resolve().parent / "agent-actor-guard.sql"
PLACEHOLDER = "-- <<< INNER EXEC WORK GOES HERE >>>"


def _load_parts() -> "tuple[str, str] | None":
    """(PREFIX, SUFFIX) of the pinned wrapper, or None if the vendored copy is
    missing, drifted from its pin, or no longer has exactly one placeholder."""
    try:
        raw = WRAPPER_PATH.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != WRAPPER_SHA256:
        return None
    text = raw.decode("utf-8")
    if text.count(PLACEHOLDER) != 1:
        return None
    head, tail = text.split(PLACEHOLDER, 1)
    # wrap(): wrapper.replace(PLACEHOLDER, f"{PLACEHOLDER}\n{inner.rstrip()}\n", 1)
    return head + PLACEHOLDER + "\n", "\n" + tail


_PARTS = _load_parts()


def wrapper_parts() -> "tuple[str, str] | None":
    return _PARTS


def extract_wrapped_inner(sql: str) -> "str | None":
    """The inner work if `sql` is exactly the pinned canonical wrapper around a
    lexically closed, canonical-form inner; else None."""
    if _PARTS is None:
        return None
    prefix, suffix = _PARTS
    if len(sql) <= len(prefix) + len(suffix):
        return None
    if not (sql.startswith(prefix) and sql.endswith(suffix)):
        return None
    inner = sql[len(prefix):len(sql) - len(suffix)]
    if not inner.strip() or inner != inner.rstrip():
        return None
    if strip_sql_noise(sql) != strip_sql_noise(prefix) + strip_sql_noise(inner) + strip_sql_noise(suffix):
        return None
    return inner


def is_wrapped_plain_read(sql: str) -> bool:
    """True iff `sql` is the pinned canonical wrapper around a plain read."""
    inner = extract_wrapped_inner(sql)
    if inner is None:
        return False
    # Lazy import: read_only imports gate_validation, which imports this module.
    from .read_only import is_plain_read

    return is_plain_read(inner)

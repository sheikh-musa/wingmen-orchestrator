"""The remote/sub-shell command text hidden inside a wrapper invocation.

A guard that judges the LEADING command of a statement is blind to anything wrapped
in `ssh host '…'`, `sshpass … ssh host '…'`, or `bash -c '…'`: the leading command is
the wrapper, never the thing that actually runs. Two real incidents live in that
blind spot:

  * 2026-09-24 (cc-fleet-health bus #49064): the real dump trigger (`ps eww`) sat two
    quoting levels deep inside
    `ssh hub-vps "sshpass ... ssh gazzai@<host> 'ps eww -u gazzai -o pid,command'"`.
  * 2026-11-01 (this module's other caller): the 30 Oct Modesty Hut class of leak —
    `ssh modestyhut "wp option list --search='wc_facebook_*'"` was ALLOWED by
    credential_store_guard while the identical local command was refused. The fleet
    reaches client hosts over ssh, so that wrapper is how the work is actually done,
    not an exotic shape.

Extracted from secrets_transcript_guard.py (where it shipped first) so both guards
share ONE parser instead of two that can drift. Stdlib only, no side effects.
"""
from __future__ import annotations

import re
import shlex

# An ABSOLUTE-PATH binary is the same wrapper: `/usr/bin/ssh host '…'` runs exactly what
# `ssh host '…'` runs, and thin-PATH environments (launchd, cron, the Studio breakglass
# heredoc) are where absolute paths get written. Anchoring on the BARE name only was a
# one-word bypass of the whole wrapper handling (cc-quality 89507 observation 2).
SSH_WRAPPER_LEADING_RE = re.compile(r"^(?:\S*/)?(?:ssh|sshpass)\b")
SHELL_DASH_C_LEADING_RE = re.compile(r"^(?:bash|sh|zsh)\s+(?:-\w*\s+)*-\w*c\w*\b")
# ssh flags that consume the NEXT token as their argument; anything else starting
# with "-" is treated as a standalone flag.
SSH_FLAGS_WITH_ARG = {"-p", "-i", "-o", "-l", "-F", "-J", "-L", "-R", "-D", "-c", "-m", "-w", "-B", "-b", "-E", "-e"}


def extract_remote_script(segment: str, lead: str) -> str | None:
    """Best-effort extraction of the remote/sub-shell command text wrapped by
    ssh/sshpass/bash -c/sh -c/zsh -c, whether it arrived as ONE quoted shell token
    (`ssh host 'ps eww'`, the Sep-24 shape) or as several separate UNQUOTED words
    (`ssh host ps eww ...`, bus #49064 gap #3) -- shlex has already stripped quoting
    by the time we see tokens, so both shapes look identical: a run of tokens after
    the host (or after `-c`) is the remote script, rejoined with spaces. Not a full
    ssh-argv parser -- best-effort skip of ssh's own flags and their single-token
    arguments, the same documented-limitation posture the guards' own shell parsing
    takes."""
    if not (SSH_WRAPPER_LEADING_RE.match(lead) or SHELL_DASH_C_LEADING_RE.match(lead)):
        return None
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return None
    if not tokens:
        return None
    if SHELL_DASH_C_LEADING_RE.match(lead):
        last_c_flag = None
        for i, tok in enumerate(tokens):
            if tok.startswith("-") and "c" in tok:
                last_c_flag = i
        if last_c_flag is None or last_c_flag + 1 >= len(tokens):
            return None
        return " ".join(tokens[last_c_flag + 1:])
    # ssh/sshpass: sshpass always wraps a real `ssh` invocation for our purposes --
    # find that `ssh` token, skip its own flags (and single-token flag arguments) and
    # the host argument; everything left is the remote command.
    i = 0
    n = len(tokens)
    # match on the BASENAME so `/usr/bin/ssh` and plain `ssh` are the same wrapper
    while i < n and tokens[i].rsplit("/", 1)[-1] != "ssh":
        i += 1
    if i >= n:
        return None
    i += 1
    while i < n and tokens[i].startswith("-"):
        flag = tokens[i]
        i += 1
        if flag in SSH_FLAGS_WITH_ARG and i < n:
            i += 1
    if i >= n:
        return None
    i += 1  # the host argument itself
    if i >= n:
        return None
    return " ".join(tokens[i:])

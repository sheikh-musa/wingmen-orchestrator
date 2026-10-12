#!/usr/bin/env python3
"""PreToolUse guard: a worker LANE must not reach the cosem-adcda PROD service-
account key — enforced, not remembered.

PART B of the lane prod-data wall (op#29348). Real incident: a worker lane read
ADCDA prod data a THIRD time after an explicit "don't". Part A (the launcher,
scripts/lib/lane_gcloud_isolation.sh) strips the ambient prod ADC default from a
lane's env; this guard closes the re-establish path — a lane re-exporting the
prod SA key, or pointing ADC back at it.

It REFUSES (fail-closed, exit 2 + stderr) any Bash or Read tool call from a
worker lane that:
  (i)  touches a prod SA key file under ~/.wingmen/keys matching *cosem* (the
       cosem-adcda prod service-account key `cosem-sa.json` and the
       `cosem-adcda-bot.env` prod bot creds — both match the *cosem* family),
       in ANY path form ($HOME / ~ / absolute / relative / symlink); OR
  (ii) contains `GOOGLE_APPLICATION_CREDENTIALS=` or `--key-file` in the Bash
       command text — a lane re-pointing ADC / activating an SA key is the exact
       prod-reach we are closing, regardless of which path follows.

Scope / discriminator — a WORKER LANE is `CC_BASE_AGENT_ID` present AND base
!= 'orch-console' (identical shape to console_irsyad_guard / client_media_read_
guard). The CONSOLE (ORCH_BODY_ROLE=console, NO CC_BASE_AGENT_ID) legitimately
runs prod ops and is UNAFFECTED; CI has no CC_BASE_AGENT_ID either, so it is
likewise unaffected. The hook reads the claude process env it inherits, so a
command that re-sources .env cannot change the verdict.

Exit 2 + stderr = the tool call is refused and the reason is shown to the model.
"""
import json
import os
import re
import shlex
import sys

# The operator's prod key store. A prod SA key reference is this dir + a
# `cosem` token — matched in ANY path form because the substring `.wingmen/
# keys/` is present whether the path is written ~/, $HOME/, absolute, or
# relative-from-home. Case-insensitive is harmless and defends a mixed-case ref.
_KEYS_DIR = os.path.join(os.path.expanduser("~"), ".wingmen", "keys")
_PROD_KEY_RE = re.compile(r"\.wingmen/keys/[^\s'\"]*cosem", re.IGNORECASE)
# A lane re-pointing ADC or activating an SA key — refuse the mechanism itself.
_GAC_ASSIGN_RE = re.compile(r"GOOGLE_APPLICATION_CREDENTIALS\s*=")
_KEYFILE_FLAG_RE = re.compile(r"(?:^|\s)--key-file\b")


def is_worker_lane() -> bool:
    base = os.environ.get("CC_BASE_AGENT_ID")
    return bool(base) and base != "orch-console"


def _text_references_prod_key(text: str) -> bool:
    """A prod SA key path appears anywhere in *text* (raw, and with $HOME/~
    expanded so an expanded-abs form is caught too)."""
    if not text:
        return False
    if _PROD_KEY_RE.search(text):
        return True
    try:
        expanded = os.path.expanduser(os.path.expandvars(text))
    except Exception:
        expanded = text
    return bool(_PROD_KEY_RE.search(expanded))


def _resolves_to_prod_key(path: str) -> bool:
    """*path* resolves (via realpath — follows symlinks) to a prod SA key file
    under the keys dir whose basename carries the `cosem` token. Covers a
    symlink whose own spelling does not contain `.wingmen/keys/`."""
    if not path:
        return False
    try:
        rp = os.path.realpath(os.path.expanduser(os.path.expandvars(path)))
    except Exception:
        return False
    rp = rp.replace(os.sep, "/")
    kd = _KEYS_DIR.replace(os.sep, "/")
    return rp.startswith(kd + "/") and "cosem" in os.path.basename(rp).lower()


def _bash_tokens(cmd: str):
    """Best-effort tokenise for per-token symlink resolution; empty on error."""
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return []


def _deny(reason: str) -> int:
    sys.stderr.write(
        "BLOCKED by lane_prod_key_guard: " + reason + " A worker lane must NOT reach the "
        "cosem-adcda PROD service-account key (op#29348 — a lane read ADCDA prod after an "
        "explicit don't). Prod ops run from the console; if a lane genuinely needs data, ask "
        "the console/coord for a staged extract over the bus, never the raw prod key.\n"
    )
    return 2


def main() -> int:
    # Console + CI (no worker-lane base) are unaffected.
    if not is_worker_lane():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(
            "BLOCKED by lane_prod_key_guard: guard could not read the tool input; refusing "
            "(fail-closed for a worker lane — op#29348).\n"
        )
        return 2

    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}

    if tool == "Read":
        path = tool_input.get("file_path") or ""
        if _text_references_prod_key(path) or _resolves_to_prod_key(path):
            return _deny("this Read touches a prod SA key file.")
        return 0

    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if _GAC_ASSIGN_RE.search(cmd):
            return _deny("this command sets GOOGLE_APPLICATION_CREDENTIALS (re-points ADC at prod creds).")
        if _KEYFILE_FLAG_RE.search(cmd):
            return _deny("this command passes --key-file (activates a service-account key).")
        if _text_references_prod_key(cmd):
            return _deny("this command touches a prod SA key path.")
        for tok in _bash_tokens(cmd):
            if _resolves_to_prod_key(tok):
                return _deny("this command touches a prod SA key path (resolved via symlink).")
        return 0

    # Any other tool is out of scope for this guard.
    return 0


if __name__ == "__main__":
    sys.exit(main())

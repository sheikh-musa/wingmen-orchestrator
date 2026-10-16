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
  (i)   touches a prod SA key file under ~/.wingmen/keys matching *cosem* (the
        cosem-adcda prod service-account key `cosem-sa.json` and the
        `cosem-adcda-bot.env` prod bot creds — both match the *cosem* family),
        in ANY path form ($HOME / ~ / absolute / relative / symlink); OR
  (ii)  touches the WELL-KNOWN per-user gcloud ADC under ~/.config/gcloud —
        `application_default_credentials.json`, `credentials.db`,
        `access_tokens.db`, or anything under `legacy_credentials/`. This is the
        reviewer's hole (op#29348): Node's ADC falls back to this path when
        GOOGLE_APPLICATION_CREDENTIALS is unset and IGNORES CLOUDSDK_CONFIG, so
        Part A points GAC at a dead sentinel and THIS guard denies the lane
        reading the well-known files directly. Matched in any path form incl.
        symlink; OR
  (iii) contains `GOOGLE_APPLICATION_CREDENTIALS=`, `CLOUDSDK_CONFIG=`, or
        `--key-file` in the Bash command text — a lane re-pointing ADC /
        re-pointing the gcloud config / activating an SA key is the exact
        prod-reach we are closing, regardless of which path follows; OR
  (iv)  for a Bash call: the INHERITED environment's own GOOGLE_APPLICATION_
        CREDENTIALS already resolves to a prod SA key or the well-known ADC
        file RIGHT NOW, at tool-call time — regardless of the command's own
        text. Round 2 of the reviewer's hole (op#29348, platform-1 #69524/
        #69530): a plain `python3 some_script.py` that internally constructs a
        GCP client library with no explicit credential silently inherits
        whatever ambient GAC is already present and authenticates with it —
        no gcloud/firebase/credential keyword appears anywhere in the visible
        command text, so checks (i)-(iii) never see it. This check reads the
        SAME env the hook itself inherits (same mechanism as is_worker_lane's
        CC_BASE_AGENT_ID read) and refuses ANY Bash call outright if that env
        is already in the dangerous state, independent of what the command
        says — catching the danger regardless of which script is run or how
        it is written. Part A's own fail-closed sentinel path is explicitly
        exempt (that is the correct isolated state, not a danger).

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
# The well-known per-user gcloud ADC dir + its credential-bearing targets. ADC
# resolution falls back HERE when GAC is unset (and ignores CLOUDSDK_CONFIG), so
# a lane must not read these either (op#29348 — the reviewer's hole).
_GCLOUD_DIR = os.path.join(os.path.expanduser("~"), ".config", "gcloud")
_WELLKNOWN_ADC_RE = re.compile(
    r"\.config/gcloud/(?:application_default_credentials\.json|credentials\.db|"
    r"access_tokens\.db|legacy_credentials)",
    re.IGNORECASE,
)
_WELLKNOWN_BASENAMES = (
    "application_default_credentials.json",
    "credentials.db",
    "access_tokens.db",
)
# A lane re-pointing ADC, re-pointing the gcloud config, or activating an SA key
# — refuse the mechanism itself, regardless of the path that follows.
_GAC_ASSIGN_RE = re.compile(r"GOOGLE_APPLICATION_CREDENTIALS\s*=")
_CLOUDSDK_ASSIGN_RE = re.compile(r"CLOUDSDK_CONFIG\s*=")
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


def _text_references_wellknown_adc(text: str) -> bool:
    """A well-known per-user gcloud ADC path appears anywhere in *text* (raw,
    and with $HOME/~ expanded so an expanded-abs form is caught too)."""
    if not text:
        return False
    if _WELLKNOWN_ADC_RE.search(text):
        return True
    try:
        expanded = os.path.expanduser(os.path.expandvars(text))
    except Exception:
        expanded = text
    return bool(_WELLKNOWN_ADC_RE.search(expanded))


def _resolves_to_wellknown_adc(path: str) -> bool:
    """*path* resolves (via realpath — follows symlinks) to a well-known ADC
    credential file/dir under ~/.config/gcloud. Covers a symlink whose own
    spelling does not contain `.config/gcloud/`."""
    if not path:
        return False
    try:
        rp = os.path.realpath(os.path.expanduser(os.path.expandvars(path)))
    except Exception:
        return False
    rp = rp.replace(os.sep, "/")
    gd = _GCLOUD_DIR.replace(os.sep, "/")
    if not rp.startswith(gd + "/"):
        return False
    rel = rp[len(gd) + 1:].lower()
    base = os.path.basename(rp).lower()
    return (
        base in _WELLKNOWN_BASENAMES
        or rel == "legacy_credentials"
        or rel.startswith("legacy_credentials/")
    )


def _ambient_env_has_prod_adc() -> bool:
    """True when THIS process's own inherited GOOGLE_APPLICATION_CREDENTIALS
    already resolves to a prod SA key or the well-known per-user gcloud ADC
    file, right now — the env-STATE check (op#29348 round 2, platform-1
    #69530), independent of the Bash command's own text. Part A's fail-closed
    sentinel (a guaranteed-non-existent per-lane path) is the correct isolated
    state and must NOT trip this — _resolves_to_prod_key/_resolves_to_wellknown_
    adc only match a path that actually resolves under the real prod dirs, so
    a non-existent sentinel path (which resolves nowhere real) already fails
    both checks naturally."""
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not gac:
        return False
    return _resolves_to_prod_key(gac) or _resolves_to_wellknown_adc(gac)


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
        if _text_references_wellknown_adc(path) or _resolves_to_wellknown_adc(path):
            return _deny("this Read touches the well-known per-user gcloud ADC (prod creds).")
        return 0

    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if _ambient_env_has_prod_adc():
            return _deny(
                "the inherited GOOGLE_APPLICATION_CREDENTIALS already resolves to a prod "
                "credential right now, regardless of this command's own text — any "
                "interpreter invocation in this shell could silently authenticate to prod "
                "via a library's own ADC resolution (op#29348 round 2)."
            )
        if _GAC_ASSIGN_RE.search(cmd):
            return _deny("this command sets GOOGLE_APPLICATION_CREDENTIALS (re-points ADC at prod creds).")
        if _CLOUDSDK_ASSIGN_RE.search(cmd):
            return _deny("this command sets CLOUDSDK_CONFIG (re-points gcloud/ADC at the per-user prod config).")
        if _KEYFILE_FLAG_RE.search(cmd):
            return _deny("this command passes --key-file (activates a service-account key).")
        if _text_references_prod_key(cmd):
            return _deny("this command touches a prod SA key path.")
        if _text_references_wellknown_adc(cmd):
            return _deny("this command touches the well-known per-user gcloud ADC (prod creds).")
        for tok in _bash_tokens(cmd):
            if _resolves_to_prod_key(tok) or _resolves_to_wellknown_adc(tok):
                return _deny("this command touches a prod cred path (resolved via symlink).")
        return 0

    # Any other tool is out of scope for this guard.
    return 0


if __name__ == "__main__":
    sys.exit(main())

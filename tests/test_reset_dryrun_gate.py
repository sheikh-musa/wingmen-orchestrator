"""Every reset_*.sh must honor RESET_DRYRUN, and honor it BEFORE it mutates anything.

WHY THIS EXISTS. On 2026-08-15 orch-console recycled three singletons and ran each through
`RESET_DRYRUN=1` first to check readiness. reset_cai.sh and reset_fleet_health.sh honored
it. reset_lane.sh did not — it ignored the variable SILENTLY and cleared cc-quality for
real. The lane had already confirmed it was safe to clear, so nothing was lost, but that
was luck, not design. reset_orch.sh and reset_hub_remote.sh had the same hole, and there
the target is the HUB: a caller checking whether the hub was ready to recycle would have
recycled it.

The failure class is not "a missing feature". It is a safeguard that is ABSENT BUT LOOKS
PRESENT — the caller believes they are protected and acts more boldly because of it. Same
shape as the MUSA_TELEGRAM_ID leg in nazim_send.sh, which ignored the env var that tests
set to stay off the operator's phone and duly paged him twice from a pytest run
(2026-07-26). A disarm switch that does not disarm is worse than no switch at all.

A promise to "remember to add the flag" does not survive the next script or the next
context reset, so this is a test rather than a convention: any reset_*.sh added later
fails here until it carries the gate.

The ordering assertion is the load-bearing half — a RESET_DRYRUN check placed AFTER the
send-keys would pass a naive "does it mention the variable" grep while still clearing the
target, which is precisely the bug it is meant to prevent.
"""
import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_RESET_SCRIPTS = sorted(_SCRIPTS.glob("reset_*.sh"))

# The first line that MUTATES the target. send-keys types into the pane; ssh hands the
# whole operation to another host. Anything at or after this point is past the safe stop.
_MUTATION_RE = re.compile(r"send-keys|^\s*exec ssh|^\s*ssh ", re.MULTILINE)
_DRYRUN_RE = re.compile(r"RESET_DRYRUN")


def test_there_are_reset_scripts_to_check():
    # Guard against the glob silently matching nothing and the whole suite passing vacuously.
    assert _RESET_SCRIPTS, "no reset_*.sh found — the glob or the layout changed"


@pytest.mark.parametrize("script", _RESET_SCRIPTS, ids=lambda p: p.name)
def test_reset_script_honors_dryrun(script):
    assert _DRYRUN_RE.search(script.read_text()), (
        f"{script.name} does not honor RESET_DRYRUN. A caller who sets it believes the run "
        f"is a no-op; this script would clear its target for real."
    )


def _code_only(text: str) -> str:
    """The script with comment lines blanked (length-preserving, so byte offsets still line
    up with the real file). These scripts carry long rationale headers that quote the very
    commands they gate — matching `send-keys` inside a comment would report a mutation at
    byte 278 of a file whose first real send-keys is at byte 4000, i.e. a broken measurement
    dressed up as a finding."""
    out = []
    for line in text.splitlines(keepends=True):
        out.append(" " * (len(line) - 1) + "\n" if line.lstrip().startswith("#") else line)
    return "".join(out)


@pytest.mark.parametrize("script", _RESET_SCRIPTS, ids=lambda p: p.name)
def test_dryrun_gate_precedes_any_mutation(script):
    text = _code_only(script.read_text())
    mutation = _MUTATION_RE.search(text)
    if mutation is None:
        pytest.skip(f"{script.name} performs no send-keys/ssh mutation")
    gate = _DRYRUN_RE.search(text)
    assert gate is not None and gate.start() < mutation.start(), (
        f"{script.name} checks RESET_DRYRUN at byte {gate.start() if gate else None} but "
        f"first mutates at byte {mutation.start()}. A gate after the mutation reads like a "
        f"safeguard and is not one."
    )

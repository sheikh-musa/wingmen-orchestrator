"""test_migration_number_collisions.py — static guard against silently
reusing a migration's leading number for unrelated content (op#22521, bus
#43868/#43869).

2026-09-27: fork `a08e037ddd57741fa` was building the operator-asks-ledger PR
and, mid-build, applied `072_operator_asks_tracking.sql` directly to the live
substrate (no PR, no CI, no gate) at the same time PR #178 was properly
shipping a DIFFERENT `072_oeh_bot_channel.sql` through review. Both landed —
`apply_migration.py`'s ledger keys on the full filename, so the two 072s are
permanently distinct, ledgered rows (orch-console ruling, bus #43869: RATIFY,
do not rename/renumber either file).

A second, older collision predates this: `057_pane_context_pane_k_at.sql`
(this repo's mainline) vs `057_resolution_independence_and_third_path.sql`
(commit c7073d8, branch `nazim/assigned-decision-audit` — never merged into
this directory), disambiguated in the live `migration_ledger` via a
branch-qualified name (`nazim/assigned-decision-audit:057_...`). It never
actually landed as two 057-numbered files in this directory at once, but
orch-console's ruling (bus #43869) names it alongside 072 as a historic pair
to grandfather, so it is allowlisted here too — defensively, in case that
branch is ever merged under its original number.

Building this guard also surfaced two more, unrelated pre-existing
collisions that predate any of this — `037_admin_mark_offline.sql` /
`037_pool_pace.sql` and `043_agents_single_owner_repo_scope_guard.sql` /
`043_pane_context_pct.sql`, both merged long ago by different bodies at
different times, both already ledgered under their full filenames. Not
part of the op#22521 incident; disclosed and grandfathered here rather than
silently excluded, since fixing decades of history isn't this PR's job but
hiding it from the guard would be dishonest. (One collision WAS caught and
avoided this way before: commit ed603bb renumbered a lockdown migration
054->055 after finding cc-fleet-health's 054 had landed first — this test
is that same discipline, enforced in code instead of relying on someone
noticing.)

All four numbers are grandfathered below. Any THIRD file introduced with a
leading number that collides with an EXISTING file's leading number is a NEW
collision and must fail this test — catching a reused number in CI, before an
`apply_migration.py` run against a live silo ever sees it.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

# Historic collisions, ratified after the fact — see module docstring. A
# number in this set is allowed to have more than one file in MIGRATIONS_DIR
# (072) or is reserved against a future one landing from an unmerged branch
# (057). Do NOT add a new number here to silence this test — allowlisting a
# number is a decision, not a workaround, and belongs in a bus-ratified PR
# like this one.
_ALLOWED_HISTORIC_COLLISIONS = frozenset({"037", "043", "057", "072"})

_NUMBER_RE = re.compile(r"^(\d+)_")


def _migration_numbers() -> dict[str, list[str]]:
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _NUMBER_RE.match(path.name)
        if not m:
            continue  # not this repo's numbered-migration convention
        by_number[m.group(1)].append(path.name)
    return by_number


def test_no_new_migration_number_collisions():
    by_number = _migration_numbers()
    assert by_number, "no numbered migrations found -- MIGRATIONS_DIR is wrong"

    unexpected = {
        number: files
        for number, files in by_number.items()
        if len(files) > 1 and number not in _ALLOWED_HISTORIC_COLLISIONS
    }
    assert not unexpected, (
        "NEW migration-number collision(s) found: "
        f"{unexpected}. Two migrations sharing a leading number will both "
        "ledger as distinct rows in migration_ledger (it keys on the FULL "
        "filename, not the number) but this is almost always a mistake --  "
        "renumber the new one before it ships. If this collision is "
        "deliberate and ratified (like 072's), add its number to "
        "_ALLOWED_HISTORIC_COLLISIONS in this file, in a PR that says why."
    )


def test_the_072_collision_is_present_and_allowlisted():
    # Sanity: this test isn't vacuous -- it must actually be checking the
    # real 072 pair this guard exists for, not silently matching nothing.
    by_number = _migration_numbers()
    assert len(by_number.get("072", [])) == 2, (
        "expected both 072_oeh_bot_channel.sql and "
        "072_operator_asks_tracking.sql in migrations/ -- update this test "
        "if that ever changes"
    )
    assert "072" in _ALLOWED_HISTORIC_COLLISIONS

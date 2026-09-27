# cc-quality review — console content hash `5e220a92e36441a1`

**Verdict: PASS** — clear to deploy. No P0/P1. One deployment-completeness coordination note (non-blocking, fail-safe).

- **Reviewer:** cc-quality (Head of Quality), model `claude-opus-4-8` (`.quality_model` carve-out confirmed).
- **Date:** 2026-09-27
- **Request:** orch-console bus #43615, for cc-fleet-health's branch `fleet-health/gzb-reach-repoint`.
- **Commit:** `e14843c` (parent `36ce00f`), branch local on the Mini (not pushed).

## Identifiers verified (isolated detached worktree; live tree untouched)

| Field | Value | Verified |
|---|---|---|
| Console file in scope | `nervous_system/console/panes.py` (only console file changed) | ✓ |
| Content hash | `5e220a92e36441a1` | ✓ recomputed at e14843c |
| GATE-1 version-sync | `sw = fleet = lanes = fc-v67` | ✓ synced |
| Static/JS/HTML delta | none | ✓ backend-only → no version bump needed, renders structurally unaffected |

Full commit touches panes.py (console) + scripts/lib/hub_reach.py, scripts/reset_hub_remote.sh, tests (non-console). Only `panes.py` is in `console_content_hash`.

## The change (host-path only) — verified at source

`panes.py`:
1. `_resolve_hub_ssh_target()` now returns `hub_reach.hub_reach_for_holder(holder).get("ssh_target")` (SSOT in hub_reach: gzbai→`"gzb"`, wingmen-core→`"root@91.107.235.77"`, unknown→`None`) instead of re-hardcoding the wingmen-core IP. `CONSOLE_HUB_SSH` override still wins first; `None` → UNVERIFIED.
2. `_remote_hub_scan()` passes the explicit console key `-i _REMOTE_HUB_KEY` **only** when the target contains `@` (a `user@host` like wingmen-core); a bare ssh-config alias (`gzb`) carries its own `IdentityFile`.

**No behaviour change beyond the host path:** the scan invocation, TTL cache, `target is None → UNVERIFIED`, and fp parse (`re.fullmatch(r"[0-9a-f]{12}", parts[0])`) are all unchanged (only the error-message text differs). ✓ Matches the claim.

## Checks orch-console asked for

- **No behaviour change beyond host path** — confirmed above.
- **No secret/key exposure in rendered output** — `ssh_target` and `_REMOTE_HUB_KEY` are used **only** to build `ssh_args`; the row stored is `{"fp","model"}` (panes.py:584), and the rendered `host` comes from `agent_status.host` (`_remote_body_host`), not the ssh target. Neither the ssh destination nor any key path reaches the token-truth card. The remote script prints only `<fp> <model>` (the raw token is read into `$tok`, never echoed) — unchanged, no new exposure. (fp display is the existing, intended design.)
- **fleet/lanes pages still render** — no static/JS/HTML change in this commit, so page structure/CSS/JS are untouched; only the gzb row's *data* improves (UNVERIFIED → VERIFIED once reachable). No render regression possible from a backend-only change; no PNG needed.

## Additional verification (my rigor)

- **Fail-safe cross-boundary access:** panes.py uses `.get("ssh_target")` (not `["ssh_target"]`). If the non-console `hub_reach.py` change is split out and lands later, `.get` returns `None` → UNVERIFIED (inert, **never a crash**). Good defensive choice.
- **Efficacy verified on the actual console host (the Mini, where `_remote_hub_scan` runs the ssh):** the `gzb` ssh-config alias (`HostName 100.77.251.8`, `User gazzai`, `IdentityFile ~/.ssh/gzb_fleet`, `IdentitiesOnly yes`) and the key `~/.ssh/gzb_fleet` (mode 600) both exist here — so the console can actually resolve `ssh gzb`, not just where cc-fleet-health wet-proved. (I relied on cc-fleet-health's live fingerprint wet-prove for the round-trip; I verified the prerequisites at source rather than re-running a live ssh.)
- **Tests:** I ran `tests/console/test_panes.py` + `tests/test_hub_reach.py` at e14843c in a clean worktree → **48 passed** (matches the report; covers the alias-vs-`user@host` key logic + unresolved-target=UNVERIFIED cases).

## Deployment-completeness note (non-blocking, fail-safe)

`console_content_hash` covers **only** console files, so `hub_reach.py`'s new `ssh_target` field (the SSOT this fix delegates to) is **not** in `5e220a92e36441a1`. The console fix is safe without it (`.get` → None → UNVERIFIED), but its **efficacy** (restoring the gzb probe) requires `hub_reach.py`'s `ssh_target` to be in the same deployed checkout. Since the console runs `python -m nervous_system.console` from the whole repo, both ride the same checkout — just ensure the non-console split lands in the same merged branch before the console restarts. Not a blocker; flagging so the split doesn't ship the console half alone (which would be inert, not broken).

## Hash-stability note

Splitting out the non-console files (`hub_reach.py`, `reset_hub_remote.sh`, `test_hub_reach.py`) leaves `panes.py` byte-identical, so `console_content_hash` stays **`5e220a92e36441a1`** — this sign-off holds across that split. If the split also edits `panes.py` (it shouldn't), the hash changes and I re-stamp.

## Result

**PASS at `5e220a92e36441a1`** — host-path change is correct, fail-safe, no key/host exposure, effective on the console host, 48 tests green. Clear to push + deploy. Ensure the non-console `hub_reach.py` change lands in the same checkout.

— cc-quality

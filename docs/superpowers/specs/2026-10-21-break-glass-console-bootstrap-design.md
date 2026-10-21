# Break-glass console bootstrap — design (op#32771)

**Status:** DESIGN, awaiting orch-console review before the build starts (bus #73265/#73267/#73270). Secrets-source step is deliberately parameterized — Musa hasn't picked GCP Secret Manager vs. an offline bundle yet (op#32773).

## 1. Intent and why

A single command that brings the `orch-console` ("Nazim") body up, authenticated and reconstituted, on **any spare host** (Musa's MacBook today; the desktop later) when the Mini AND gzbai are both unreachable. Today the only paths back to a working console are (a) the Mini itself, or (b) `scripts/break_glass.sh`, which assumes the full repo + secrets are already sitting on the target machine — neither survives the actual DR scenario this is for.

The 2026-09-05 incident is the cautionary precedent: a fresh clone's hub sat at the OAuth login screen for ~1.5h while lease/heartbeat/daemons all read green. A DR path that's never been run lies about being green. This design's proof obligation (§6) exists specifically to not repeat that.

## 2. Goals

- One command, run on an unprovisioned spare host, that ends with a live, authenticated, reconstituted `orch-console` body — or a loud, specific failure, never a silent half-state.
- Survives the Mini AND gzbai both being dead: no step depends on reaching either.
- Never creates a second live console while one already exists elsewhere, except under an explicit forced override.
- Never leaves plaintext secrets at rest, unencrypted, on a borrowed machine's disk.
- Reuses the fleet's existing headless-auth-gate and launcher machinery (`docs/orch-move-runbook.md`, `scripts/launch_dangerous_cc.sh`, `scripts/lib/ensure_cwd_trusted.py`) rather than re-deriving it — this is the same class of cutover the hub already does by hand for gzbai moves, generalized to an arbitrary host and a dead-primary starting condition.

## 3. Non-goals

- Does not pick the secrets-source (GCP Secret Manager vs. an operator-held offline bundle) — that is Musa's call (op#32773), pending. This design makes that step a swappable fetcher behind one interface so the rest can be built and proven now.
- Does not provision a from-scratch OS (apt packages, Tailscale, etc.) the way `scripts/vps_provision.sh` does for a fresh Linux VPS. The target hosts named (Musa's MacBook, the desktop) are machines Musa already uses, with git/python3/tmux already present via Xcode Command Line Tools — this script clones the repo and sets up a venv, it does not turn a bare machine into a dev box.
- Does not touch or duplicate `scripts/break_glass.sh` (that script's contract — repo+secrets already present — stays valid for its own narrower case) or the hub's own gzbai cutover work in bus #73264 (coordination means this generalizes the *pattern*, not that it edits what the hub is doing right now).

## 4. Anti-goals (would make this a failure even if it "ran")

- Declaring "up" on a green heartbeat/lease/daemon state without the one-real-turn + bridge round-trip proof (§6). This is the exact 2026-09-05 failure shape.
- A fetcher that leaves the secrets bundle on the target's disk unencrypted, even transiently, past process lifetime.
- Silently running a second live console when one already exists (split-brain on the operator bridge).

## 5. Constraints

- Target hosts are Darwin (macOS) in the named cases. The script should fail loud (not guess) on an unrecognized `platform.system()`, matching this fleet's existing convention (`nervous_system/vault.py`'s `_local_host_id()`).
- Deploy branch is `fable/substrate-safe-fixes`, never `main` (matches every other build in this fleet).
- `ORCH_BODY_ROLE=console` must land in the fresh host's `.env` — `session_start_reconstitute.py` is a SessionStart hook already scoped to fire only for that role, so reconstitution is **structural once the env var and the repo's `.claude/settings.json` hook registration are both in place** — this design does not need to re-implement reconstitution, only make sure the env var is set before first launch.
- The vault (`nervous_system/vault.py`) is **not** usable as the bootstrap secrets-source as currently built — its KEK is hard-pinned to the Mini's local Keychain (see bus #73270, my read sent to orch-console just before this design). Whatever Musa picks for op#32773 is a *different* mechanism than the fleet vault; this design's fetcher interface must not assume vault semantics.

## 6. The proof gate (per op#32771 requirement 4 — non-negotiable, this is the point of the whole design)

A fresh console is not "up" until **all three** pass; any failure is loud and the script does not declare success:

1. **One real turn**: the launched session reaches the composer authenticated — not a login screen — and responds to a trivial prompt. (Mirrors the exact headless-auth gate's smoke-test step in `docs/orch-move-runbook.md`.)
2. **Ingest round-trip**: the fresh console receives a real ingest nudge (a message actually delivered through the bus/ingest path, not injected locally) and reacts to it.
3. **Operator bridge round-trip**: the fresh console runs `scripts/nazim_send.sh` with a real, distinguishable test message, and that message is confirmed to land on Musa's phone (operator confirms, or an automated delivery-receipt check if one exists for the bot API — to be confirmed with orch-console, who owns the final test-fire proof per their own message).

## 7. Shape of the script (what's decided; implementation detail is left to the builder in §9)

```
break_glass_bootstrap.sh [--force]
  1. Identity guard:
     query agent_status for agent_id='orch-console' — if status != 'offline' AND
     last_heartbeat within the fleet's standard staleness window (30 min, matching
     fleet_health.py's STALE_MIN), REFUSE unless --force. Requires reaching the
     substrate DB first (see step 2) — if the DB itself is unreachable, that is
     itself inconclusive (could mean "primary really is down" OR "this host just
     has no route yet") and must be logged loudly and surfaced to the operator as
     an explicit "could not verify, --force needed and acknowledged" state, never
     silently treated as "safe to proceed."
  2. Secrets bundle fetch (PARAMETERIZED — see §8): obtain, into a RAM-backed
     location only (not /private/tmp on macOS, which is disk-backed — see
     ramdisk note in §9), the .env contents + OAuth token-pointer + anything
     under ~/.wingmen/keys that boot_fleet_health.sh / boot_nazim.sh need.
  3. One-command provisioning: clone wingmen-orchestrator @ fable/substrate-safe-fixes,
     python3.9 -m venv .venv, pip install -r requirements.txt.
  4. Headless-auth gate (reuse, don't reimplement):
     - set ORCH_BODY_ROLE=console in the fetched .env before anything reads it.
     - run scripts/lib/ensure_cwd_trusted.py --cwd <repo-dir> (same call
       launch_dangerous_cc.sh already makes) to seed hasTrustDialogAccepted +
       bypass for this cwd.
     - seed ~/.claude.json's hasCompletedOnboarding=true + a theme, same as the
       runbook's gzb-hub reference fix, if this host has never run claude before.
     - tmux new-session -x 220 -y 50 (never a zero-size detached pane — the
       exact 2026-09-05 failure class).
     - --continue is conditional on a prior conversation existing for this repo
       dir on THIS host; omit on first launch (a fresh clone has none).
  5. Run the §6 proof gate. Any red: print the loud failure, do NOT mark up,
     do NOT write agent_status as working. SETTLED (cc-fleet-health's call,
     builder-check item): on any gate failure, tear down the live session
     (kill the tmux pane / claude process) and scrub the RAM-backed secrets
     dir before exiting — never leave an unmarked, authenticated,
     secret-holding session running on the spare host just because it didn't
     clear the proof gate. A failed attempt must end in the same "nothing
     live, nothing on disk" state as never having run at all.
  6. Only on all-green: write/update agent_status (orch-console, this host,
     working) and post one bus message to the operator-facing channel
     summarizing what came up, where, and the proof-gate result.
```

## 8. Secrets-fetch interface (parameterized, per Musa's op#32773 pick)

A single function/script boundary, so the rest of the bootstrap builds and tests against a stub now:

```
fetch_secrets_bundle() -> path to a RAM-backed dir containing .env + key files
```

Two candidate implementations (either one slots in once Musa decides; this design does not choose):
- **GCP Secret Manager**: a small bootstrap SA key (itself distributed to Musa out-of-band — password manager or printed, never committed/bussed) lets `fetch_secrets_bundle()` call the GCP SM API directly from the fresh host, no pre-registration on any of our own infra required. Project is `cosem-adcda-cb6d9` per orch-console's message; console SAs there can already read it.
- **Offline bundle**: Musa holds an encrypted bundle (e.g. age-encrypted, matching the `SOPS + age` pattern the vault design doc already evaluated and rejected *for the primary store* but noted as reasonable "for a small number of long-lived host-level bootstrap secrets" — this is exactly that case) on a USB drive or similar; `fetch_secrets_bundle()` decrypts it with a passphrase Musa types in.

Either way, **this fetch must also deliver (or the bundle must separately include) a scoped substrate credential** (`DATABASE_URL` or read-sufficient equivalent) — the identity guard (step 1) and the reconciliation/bus-posting (step 6) both need to reach the substrate DB, and that credential is not otherwise available on a fresh host. Flagging this explicitly because it's easy to scope the bundle to "just the DR secrets" and miss that the bootstrap's own early steps need substrate reach too.

## 9. What's left to the builder

- Exact RAM-backed storage choice on macOS: there's no `/dev/shm` by default. Options: `hdiutil attach -nomount ram://<sectors>` to mint a real RAM disk (clean, mirrors the Linux tmpfs intent exactly, needs teardown on exit/trap), or simply hold the bundle in a bash associative array / exported env vars for the process tree's lifetime only (simpler, no disk risk at all since it's never written, but less convenient for consumers that `source .env` from a path). Recommend the ramdisk — closest match to the Linux tmpfs precedent and still lets existing scripts `source` a real `.env` path unmodified.
- Exact ingest round-trip proof mechanism for step 2 of §6 (what counts as "a real nudge," and how the script observes that it was received — likely a message_id check against `agent_messages` after sending a known test nudge).
- Exact delivery-confirmation mechanism for the bridge round-trip (step 3 of §6) — `nazim_send.sh`'s own exit code proves the Telegram API call was accepted, not that Musa's phone displayed it; confirm with orch-console whether exit-code-success is an acceptable proxy or a real receipt/read check is wanted.
- The actual `--force` override's exact logging shape (what gets written, to the bus, when a known-live console is about to be duplicated).
- Idle/no-op path: what happens if the identity guard finds a live console elsewhere and `--force` is NOT passed — does it exit 0 with a clear message, or non-zero? (Recommend: non-zero, so a scripted/automated DR caller can distinguish "refused, console already up" from "built and ready to test.")

## 10. Open items / blockers to flag back (per op#32771's own ask)

**SETTLED (cc-fleet-health's call):** operator communication channel during an inconclusive/failure path (builder-check item) — assumed Musa is physically at the spare host's keyboard when this runs (it's a hands-on break-glass invocation), so loud stdout is sufficient; no separate alerting channel (SMS/push/call) built. Flagging the assumption here since it's cheap to overturn if wrong.

**NEEDS ORCH-CONSOLE/MUSA (not mine to guess on, builder-check items, most important first):**

0. **Split-brain / handback.** The identity guard (§7 step 1) only checks liveness at the instant bootstrap starts — nothing fences off the "dead" primary actually being partitioned and reconnecting later with its own heartbeat still ticking, and two spare hosts bootstrapped concurrently could both pass the guard before either writes `agent_status` (a real check-then-act race). The fleet already has a CAS-lease pattern for exactly this shape (`fleet_health_lease`). Does break-glass need an equivalent lease for the console identity, or is "a human manually retires the old body" the accepted answer? Changes build scope substantially either way.
0b. **`--force` authorization.** Is it only ever typed by a human who has personally judged the primary dead (bare flag, human-gated by construction), or could an automated DR trigger invoke this unattended? If the latter, `--force` needs its own authorization check, not just a flag — per this fleet's own doctrine that irreversible/real-judgment calls are never an agent's/script's to decide alone.
0c. **Temporary bridge vs. permanent relocation.** Undecided whether this build also needs a handback procedure (stand the console back down cleanly when Mini/gzbai returns, parallel to `fleet_health_lease`'s reclaim/handback) or whether that's a deliberate follow-on. Building "stand up" without "stand back down" risks an orphaned authenticated body with live secrets on someone's personal laptop indefinitely.

1. **Secrets-source pick** (Musa, op#32773) — blocks §8 only; everything else in §7 builds and can be dry-run-tested against a stub fetcher now.
2. **MacBook test-fire access** — "DONE" per op#32771 requires test-firing on a real spare machine (Musa's MacBook) with all three §6 proofs green, screenshotted/logged. This needs Musa's coordination (physical or remote access to that machine) before the proof-gate can be exercised for real; the rest of the build and a dry-run/mock of the proof gate can proceed without it.
3. Confirmed via live query: the fleet vault's entire root of trust is the Mini's local Keychain today (sent as a separate finding to orch-console, bus #73270) — not a blocker for THIS design since it deliberately doesn't use the vault, but flagging again here since it's adjacent context for whoever reviews.

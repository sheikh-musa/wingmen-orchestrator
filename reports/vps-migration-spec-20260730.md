# VPS migration spec — flaky Macs → one stable cloud core (2026-07-30)

**Trigger:** Studio's outbound link to Supabase/GitHub flaps (relay-routed tailnet + residential network) → hub lease flapping, false ihsanOS health alerts, ~4h of intermittent degradation on 2026-07-30. Operator: "proceed" (op#8592). This is the natural endgame of the infra-consolidation workstream ([[project_nazim_cos_infra_consolidation]]) — consolidate the always-on core off the two diverged/flaky Mac hosts onto one stable box.

## Recommendation (the one-liner)
Provision **one Hetzner CAX21** (4 vCPU / 8 GB / 80 GB, ARM) as the always-on **stable core**; keep the Mac(s) as **Apple-Silicon compute nodes**. **~€13–15/mo** in Singapore (or €10.49 in EU). This fixes the network-flap class permanently (public IP → direct Tailscale, no relay) and is ~5× cheaper than the BytePlus box for more cores.

### Region: **EU (recommended)** — CAX ARM is NOT sold in Singapore
- **UPDATE (op#8594, Hetzner console):** the ARM/Cost-Optimized line is **"Limited availability" in Singapore** — no CAX21 there. SG only offers the AMD **CPX** line at a heavy surcharge: CPX22 (2c/4GB) **$33.78/mo**, CPX32 (4c/8GB) **$63.21/mo** — 3–5× the EU price, erasing the cost advantage.
- **So go EU: CAX21 (4c/8GB ARM) ~€10.49/mo (~$12).** Residency is unaffected (data stays in Supabase). The only cost is ~150ms VPS→SG-Supabase latency — **acceptable**: the fleet's DB work is async/polling (not live user requests; user-facing apps are on Vercel+Supabase, not the VPS), and the operator (Abu Dhabi) is actually a touch closer to EU than to SG.
- If DB latency ever proves to bite, re-home to SG later (migration is repeatable) — accepting SG CPX22 (~$34/mo) at that point.

## Architecture: VPS core + Mac compute node
| Runs where | What |
|---|---|
| **VPS (stable, always-on)** | the **hub** (cc-orchestrator, tmux `orch`) + **the lease holders** (orch_lease, fleet_health_lease); the always-on daemons — **ingest** (unified, all channels), **tg_out**, **health_check**, **SRE (fleet-health)** + watchdogs (context-health, priority-sla, lane-wedge, repo-context), coord-pane-publisher, session-costs-writer, bus-notify, tripwires, daily-backup, media-cleanup; **most build lanes** (headless). Optionally the **console body (Nazim)** too — it's coordination, not compute. |
| **Mac (compute node)** | Apple-Silicon/MPS work that a VPS can't do: **sovereign OCR** (arabic-ocr, Baidu on MPS — a product differentiator), **Whisper** voice-journal, **video** (ffmpeg/PyAV). Plus any lane that needs local compute. |

**Endgame option (not forced):** once the core is on the VPS, we likely need only ONE Mac as the compute node — retire the second, killing the Mini↔Studio drift that started this whole workstream.

## Auth (no new cost)
Lanes on the VPS run Claude Code **headless via `CLAUDE_CODE_OAUTH_TOKEN`** (Max-backed, NOT metered API — [[project_headless_claude_auth]]). Same posture as today's headless services; the one thing to validate empirically is the token holding across several concurrent lanes on one host.

## Migration phases (reversible, low-risk)
- **P0 — provision + base** (needs operator: create the Hetzner account + box, add SSH key). Ubuntu 24.04 ARM, Python 3.9 venv, clone orchestrator repo, **secure `.env` transfer** (scp/age, never in git), join Tailscale (`tailscale up` → gets a direct, non-relay tailnet node), `gh` auth.
- **P1 — always-on DAEMONS first** (stateless-ish, low blast radius): move ingest / tg-out / **health-check** (this alone kills the false alerts — stable network) / watchdogs / SRE to VPS launchd→systemd; disable on the Macs; verify each. The SRE + fleet_health_lease move together.
- **P2 — the HUB** (the deliberate, loud part): boot `orch` on the VPS, take `orch_lease` from the VPS (planned takeover, one lease, loud CAS — NOT a scramble). **Coordinate with the hub: finish the irsyad Quhs upload FIRST**, then cut over.
- **P3 — lanes** (headless OAuth), as desired. **P4 — decommission** the Studio always-on role (keep as spare/compute).

## Risks / validation
- Headless OAuth across concurrent lanes (validate in P1 with 2–3 lanes before P3).
- `.env` secret transfer must be out-of-band + the VenV DATABASE_URL leak guard preserved ([[reference_substrate_database_url_leaks_into_subagents]]).
- Lease moves must be one-at-a-time + loud to avoid split-brain (the takeover CAS already serializes this).
- ARM64: Claude Code, Python wheels (psycopg etc.) all ship ARM64 — fine.
- **Single-VPS SPOF?** Net MORE reliable than today (datacenter vs residential), AND we keep a Mac as warm fallback — the existing lease-reclaim logic lets a Mac take over if the VPS ever dies. So resilience improves, not regresses.

## What I need from the operator to start
1. **Create the Hetzner Cloud account + a CAX21 box** (SG recommended), add my SSH key — OR authorize me to, if you'll add a payment card + share access.
2. Confirm **SG vs EU**.
Then I execute P0–P1 immediately (I can pre-write the provisioning + service-install scripts now so it's fast).

## Coordination
This is the infra-consolidation workstream's target — loop the **hub** (owns target topology + branch convergence per [[project_nazim_cos_infra_consolidation]]; its `reports/fleet-consolidation-target-topology-20260729.md` gets a VPS target) and **cai FYI** (touches ORCH-TOPOLOGY-001 — the hub body relocating hosts). Not a money/irreversible gate; audit + coordinate, don't block.

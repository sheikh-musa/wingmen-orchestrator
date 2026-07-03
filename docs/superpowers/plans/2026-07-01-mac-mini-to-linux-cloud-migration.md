# Mac Mini → Linux Cloud Migration + Interim Hardening

**Date:** 2026-07-01
**Owner:** cc-orchestrator · **Governance gate:** cai (open thread `764e05d9`, msg #5003)
**Operator decision:** "Full Linux cloud" (cheapest, most porting, Max-on-cloud ToS risk to be cleared by cai)

## 1. Why — three distinct failure classes surfaced in one day

The Mac Mini (Macmini8,1, 2018 Intel, macOS 15.7.5) went down repeatedly. Operator read all of it as
"power died," but the logs show **three separate root causes**, only one of which is power:

1. **WindowServer / GPU watchdog panics (dominant, reproducible).** `panic(... userspace watchdog
   timeout: no successful checkins from WindowServer ... in 120 seconds)`, WATCHDOG namespace,
   "WindowServer main thread" unresponsive, context littered with GPU/Hang/display. WindowServer
   crashes clustered in pairs (11:41:13+11:41:53, 12:26:02+12:26:42) → kernel panic → reboot.
   **Trigger: the Mini is HDMI-connected to a TV.** With the TV off / in standby / on another input,
   macOS sees *no display* (`system_profiler SPDisplaysDataType` returned blank both times checked).
   A display that keeps vanishing/reappearing churns the Intel UHD 630 + WindowServer → GPU hang →
   watchdog panic. Compounded by **two remote-desktop servers running at once** (Chrome Remote
   Desktop `remoting_me2me_host` + macOS `screensharingd`), both driving WindowServer.
2. **Cannot recover unattended after reboot.** FileVault is OFF (good), but **auto-login is OFF** →
   the Mac stops at the login window. AND **all 20+ `dev.wingmen.*` services are user LaunchAgents**
   (`~/Library/LaunchAgents/`), zero system LaunchDaemons → nothing starts until a GUI user logs in.
   Chrome Remote Desktop's host runs in the user session, so at the login window it shows no screen →
   operator must get someone to physically key in the password. **This is the autonomy-killer.**
3. **Power (`SMC shutdown cause: -128` ×2, 05:47 + 11:57).** Genuine abrupt power loss OR the operator
   hard-power-cycling an already-hung machine (both register -128). Real but secondary to (1).

**Throughline:** every failure is a symptom of a **desktop GUI machine pressed into headless-server
duty.** A headless Linux VM has no WindowServer, no GPU display stack, no login window, no FileVault
pre-boot, and services are systemd units at boot. The migration deletes classes (1) and (2) outright.

## 2. Interim Mac hardening — do NOW, before/independent of migration

These stop the bleeding during the migration window. Operator actions marked (OP).

- **(OP) Replace the TV with an HDMI dummy plug** (headless display emulator, ~$10). Gives WindowServer
  a stable framebuffer that never drops. Single highest-leverage fix for failure class (1). Unplug TV.
- **(OP) Enable auto-login** for `sheikhmusa` (FileVault is off, so it's allowed). After every reboot the
  Mac boots straight into the session → WindowServer + all LaunchAgents (the fleet) start → CRD works.
  Set remotely via Tailscale SSH (sshd is loaded, reachable at loginwindow with FileVault off):
  `sudo sysadminctl -autologin set -userName sheikhmusa -password <pw>` then `sudo reboot`.
- **(OP) Drop one remote-desktop stack.** Fleet is managed over Tailscale + SSH + tmux; neither CRD nor
  Screen Sharing needs to run 24/7 driving WindowServer. Keep one, on-demand.
- **(cc-orch, optional) UPS** for the -128 power blips — lower priority than the two above.

## 3. The cai gate (only blocker on the compute cutover)

Running Claude **Max** headless 24/7 on a cloud Linux VM via `CLAUDE_CODE_OAUTH_TOKEN`. Whole-fleet
compute depends on it. cai to rule: (a) ToS/flag/throttle risk vs live Anthropic terms; (b) fallback if
Max is restricted (cloud Mac EC2/MacStadium vs metered-API subset) + the tripwire; (c) provider/region
(VM near the DBs — orch substrate ap-southeast-2 Sydney, PII DB ap-southeast-1 Singapore → SG VM leans
right). Everything in §5 that is NOT the Claude lanes proceeds in parallel (consensus = build authority).

## 4. Service inventory → systemd blueprint

All currently `~/Library/LaunchAgents/*.plist`, RunAtLoad+KeepAlive unless noted. Convert clean ones to
`/etc/systemd/system/*.service` (`WantedBy=multi-user.target`, `Restart=always`) — starts at boot, no GUI.

| Service | Command | Session/Max coupled? | Linux target |
|---|---|---|---|
| tg-bridge | `.venv/bin/python3 -m nervous_system.tg_bridge` | no | systemd, clean |
| cai-bridge | `.venv/bin/python3 -m nervous_system.cai_bridge` | no | systemd, clean |
| fleet-console | `.venv/bin/python3 -m nervous_system.console` | no | systemd, clean |
| watchdog | `.venv/bin/python3 watchdog.py` | no | systemd, clean |
| wingmendev-bot | `.venv/bin/python3 -m bot.manage_bot` | no | systemd, clean |
| mizan-bot | `python3 mizan_bot.py` (RunAtLoad unset) | no | systemd, clean |
| nutri-study-bot | `bash ~/.wingmen/nutri_study_bot.sh` | no | systemd, clean |
| cc-orch | `scripts/boot_orch.sh` (Claude CLI) | **YES — Max/keychain/tmux** | §3 gate |
| tailscale-up | `open -a Tailscale; Tailscale up` (GUI app) | GUI app | native `tailscale up` unit |

## 5. Phased plan

- **P0 (now):** §2 interim hardening (dummy plug + auto-login) — restores unattended recovery on the Mini.
- **P1:** Provision Ubuntu LTS VM (SG). Migrate secrets securely (never commit `.env`).
- **P2:** Convert the 7 clean glue services to systemd; move first (no Max dependency — kills most SPOF).
  Re-register Tailscale node + update `CONSOLE_HOST`/`.env` (new node = new tailnet IP). Headless
  Chromium/Playwright for screenshots.
- **P3 (cai-gated):** Validate Max-on-Linux stability; cut over cc-orch + lanes. Parallel-run, Mini as
  warm standby.
- **P4:** Decommission Mini (or keep as backup node).

## 6. Watch items

- macOS-specifics needing Linux equivalents: keychain/OAuth token handling, `osascript`, `pmset`,
  Tailscale.app → CLI, any `~/Library` paths, screenshot rendering parity.
- Bots' OAuth tokens (`CLAUDE_CODE_OAUTH_TOKEN`) must move with secrets.
- iBridge/T2 bridgeOS panics also seen (03:42, prev day 16:26) — separate, hardware-adjacent; another
  reason to leave the aging 2018 hardware behind.

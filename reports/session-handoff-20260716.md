# Orchestrator handoff — 2026-07-16 ~02:00Z (overnight, Nazim-greenlit reset)

Written for a Nazim-greenlit checkpoint/reset. I self-flagged context length after two full build→review→deploy pipelines + a long multi-phase session; Nazim agreed, holds the /clear trigger. Everything below is also in the bus (`agent_messages`), `operator_messages`, and STATUS.md. Standing doctrine unchanged: money/PII/residency/audit steps stay cai/operator-gated; lanes build authored-unapplied, hub reviews+gates+applies/deploys; never `supabase db push` vs prod — direct-psycopg `--expect-ref`; **reconcile bus + operator_log every turn AND as the last step before idle** (I missed this once today, op-caught — now a hard rule); verify, don't assert (paid off repeatedly today).

## JOB ONE (fresh-me) — the parked roadmap is the next big thing
**op#4618 — Elly's roadmap, PARKED for the operator to prioritize (Nazim #9058, do NOT auto-build):**
1. **Bank statement / GIRO upload** — `rather urgent`, AUDIT-CRITICAL. Hard deadline: finalise end-Aug/early-Sept → enhancements through Sept → DONE by end-Sept, because Elly reconciles Jan–Sept 2026 in October. **= MONEY + AUDIT-CRITICAL → hard PARKED line.** Needs proper design (audit integrity, financial-data handling, RESIDENCY check on where bank statements live — irsyad silo goumlyne), cai input, + operator sign-off. Design-first only; do NOT build or commit to a design overnight/autonomously.
2. **Tabung Fajr** — a more complex tabung variant than Keluarga, with OUTSTANDING-TINS inventory/stock listing (issued-but-unreturned tin tracking). Non-money but a real build. Design-first.
- Client told: both buildable, concurrent OK, proper plan+timeline to follow (no dates over-promised). They said "update us once done." Fresh-me: wait for operator prioritization before scoping; then design-first spec each → Nazim → operator LOOK.

## DONE this session (all verified + deployed)
- **P0 migration 105 → ceayj APPLIED + PROVEN + TRACKED** (PR #163). Reverted the out-of-band `_deprecated_fund_raised/_target` → `fund_raised/fund_target`; ceayj donations were live-broken. Doubly-gated (cai CAI-RESP-458 + operator op#4528). 62 rows intact, donations path clean. schema_migrations(105), work_outputs job 205.
- **Purge 14a55c8f test stub → DONE** (59 rows, 11 tables, org gone). Gated cai-458 + operator FYI + op#4536. Full no-PII cert. Audited substrate work_outputs job 206 (NOT hash-chain). goumlyne untouched.
- **cc-support v1 BUILT + BOOTED (Phase A)** — agent_id `cc-support`, tmux `support` on Studio, Opus 4.8, heartbeating. Files committed (orch 53315f5): scripts/boot_support.sh, prompts/support_boot.md, docs/support/knowledge-base.md; home ~/wingmen/wingmen-support. Phase A = channel stays `log-and-route` (hub is the reliable receiver+sender; zero-regression), cc-support DRAFTS → hub/Nazim approve+send (op#4559 SLA: hub fast, Nazim 10-min backstop via irsyad_support_send.sh -5330147776). **nudge_support.sh** (9231627) wakes it per new client msg. Grad = 15 clean drafts → agent-session flip. **NOTE: the trigger isn't auto-wired — I've been handling client msgs HUB-DIRECT; fresh-me should either nudge cc-support to draft (nudge_support.sh) or keep hub-direct until graduation.** Watchdog excludes `support` (8786a80).
- **op#4584 tabung-report (S/No + Calculated Date) → DEPLOYED** (merged 1cb0630). Display-only, no figure/hash change. Nazim live-verified (real counted_at data). PR #164.
- **op#4613 tabung-report DENOMINATION GRID → DEPLOYED** (merged b39b801). Full per-denom piece-count grid (13 cols, landscape), matches client sample; totals stay from source-of-truth amount_* (NOT recomputed). I independently eyeballed the PDF render (vitest renderToFile) vs the sample. Nazim live-verified (300/892 kk tins have denom data). PR #165. $1K col renders 0 (app excludes $1000 per #6902) — noted; real $1K = a parked denom-set change.
- **TG-native onboarding spec** (op#4529) → posted for operator LOOK (reports/tg-native-onboarding-spec-20260716.md). ~80% already built (CAI-294 bridge); gap = name+handle capture + unify org-creation paths. Awaiting operator's 4 answers.
- **Drain-and-stop** done earlier (fleet → hub+cai+shipforge+support). cc-ihsanos re-spun for op#4584/4613, stood down after.

## PENDING (others / awaiting)
- **op#4618 roadmap** — parked for operator (above).
- **cc-support graduation** — awaiting first real supervised drafts + operator flip after 15 clean.
- **TG-onboarding spec** — awaiting operator's 4 LOOK answers.
- **Shipforge** — Nazim running step-2 synthetic copy (source = SUBSTRATE `public.*` schema, NOT a shipforge schema; target = ceayj `shipforge`); he verifies then I originate the SHIPFORGE_DB_URL flip. cai owes a ruling on business-names in the synthetic copy (Crumbs/Hadramawt/Hersing — keep vs anonymize; bus #8989).
- **Consolidation** — Mini-first push order agreed; both sides de-risked LOCAL (Mini 3 commits, Studio ae2e97c gauge). Shared cc_session_costs_auto_writer.py = Nazim's Mini version canonical (I left mine uncommitted). Push when operator work quiets.
- **Repo-hygiene** (#9052, deferred): cosem-tdu 47-commit deploy-gap; ihsanos-batch/broadcast stale WIP.
- Client feedback awaited: Elly on the two shipped report changes + the "customer can't see the keluarga report" support Q (I gave account/role/stale-cache guidance + offered to check the account — op#4624).

## GOTCHAS / INFRA
- **ihsanos `lint-and-typecheck` CI is RED on every PR** — PRE-EXISTING debt (place-order.ts schema-drift on organizations.slug + storefront.ts unbounded queries), NOT new work. Masks real failures; worth a cleanup PR. Don't let it block (verify your change's own checks: tabung-correctness/unit-tests/Vercel previews).
- **Lane-boot gotcha:** `launch_dangerous_cc.sh` resolves agent family STRICTLY from the git-toplevel BASENAME; `--repo`/CC_REPO does NOT override it. A worktree must be named exactly a registered family (e.g. `ihsanos`) or it fail-fasts UnknownRepoError. cc-ihsanos worktree = ~/wingmen/wt/ihsanos.
- ceayj admin conn = `IHSANOS_PROD_DATABASE_URL`; goumlyne = `GOUMLYNE_DATABASE_URL`; substrate/bus = `DATABASE_URL`. Guarded ceayj applier = scratchpad/apply_ceayj_guarded.py (--expect-ref, handles DO-$$).
- Fleet now: cai, orch (hub), shipforge, support. Studio hub, ORCH_BODY_ROLE=hub, lease held.

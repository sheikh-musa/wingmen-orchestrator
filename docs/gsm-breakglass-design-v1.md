# Google Secret Manager break-glass design (v1)

**Status: DESIGN ONLY. Nothing in this document is built.** No GCP project,
IAM binding, or secret has been created. Per orch-console (bus #84140,
op#36050): this touches substrate credentials and the operator bot tokens,
so it requires cai advisory review AND Musa's explicit go before any build
step (GCP project creation, IAM, secret writes) happens.

## Why this exists

`nervous_system/vault.py` is the fleet's live, envelope-encrypted secret
store, and it works well for routine operation. But it has a structural
gap: its own secrets live *inside* the Postgres substrate. If that
substrate is down, corrupted, or unreachable, vault.py cannot bootstrap
access to the very database it depends on — there is no rung below it.

This design is that one rung: a small, independently-hosted kit a human
can reach from a clean machine to get the fleet substrate itself back up,
nothing more. It is explicitly NOT a vault.py replacement or mirror.

## Which secrets (curated, not a vault mirror)

Only what is needed to re-establish a working connection to the substrate
and reach the operator while it's down — four items:

1. **`DATABASE_URL` / `SUPABASE_SERVICE_KEY`** for the orchestrator
   substrate (project `tscuymavysscrvoberrr`, per
   `docs/data-store-registry.md`) — the chicken-and-egg credential vault.py
   itself cannot serve while the DB is the thing that's down.
2. **`@wingmennorchbot` token** — so the fleet (or a human) can reach the
   operator over Telegram even with the DB dark.
3. **`@nazim_cto_bot` token** — the console's own sanctioned operator
   channel, same reasoning.
4. **One GitHub PAT**, scoped read-only to the `wingmen-orchestrator` repo
   — enough to pull a clean checkout from a fresh machine.

Plus a short pointer string (not a secret): where the recovery runbook
lives and the order to use these four in.

**Explicitly out of scope** — these stay in vault.py, not GSM:
- Every provider coding-plan key (Anthropic/DeepSeek/GLM) — normal
  operational secrets, not a bootstrap problem.
- Any client PII, client-silo DB credentials, or per-client vault keys.
- Anything that needs frequent rotation or routine lane access — GSM
  break-glass is for a human in an outage, never a lane's normal boot path.

## Which GCP project

A **new, dedicated, minimal** GCP project created for this purpose alone —
never reused from any client-facing or app-hosting project. This keeps the
break-glass kit's IAM surface from ever overlapping with anything a client
workload touches, so a client-project compromise can't reach it and vice
versa.

## Access control

- `roles/secretmanager.secretAccessor` on this project granted **only to
  Musa's own Google identity** (his normal 2FA'd human login).
- **No service account.** No fleet lane, no orchestrator process, no CI
  runner ever authenticates to this project as part of normal operation.
  The moment a lane could read it routinely, it stops being break-glass
  and becomes just another leak surface with extra steps.
- If a second human (e.g. Nazim, in a console-operator capacity) needs
  access, that is a separate explicit grant decision, not a default —
  raised to Musa before added.
- Audit logging (Cloud Audit Logs, enabled by default on GCP) is the
  record of every access; a break-glass read is rare enough that any
  access outside a drill or a real outage should itself be an alertable
  event (future work, not in this v1 design).

## Restore drill

An undrilled break-glass kit is a false sense of safety, not a real one.

- **Cadence:** quarterly. Musa (or whoever the operator designates) logs
  into the GCP console or `gcloud` CLI, pulls the four secrets, and from a
  clean/throwaway machine (not any fleet host) proves a minimal substrate
  reconnect actually works end to end — i.e. the pulled `DATABASE_URL`
  really connects, the bot tokens really send a message.
- **Record:** the drill date and outcome get a durable record (a
  `held_commitments` row recurring quarterly is the natural fit, same
  pattern already used elsewhere in the fleet for dated recurring checks)
  so staleness is visible rather than assumed-fine.
- **Rotation:** if any of the four secrets rotate in their primary
  location (vault.py for the PAT/bot tokens, the substrate provider for
  `DATABASE_URL`/`SUPABASE_SERVICE_KEY`), the GSM copy must be updated in
  the same change — a stale break-glass secret is worse than none, since
  it creates false confidence until the day it's actually needed.

## Review routing (per bus #84140)

1. cai — advisory review (governance/credential-exposure angle; this
   agent is ops-not-governance and does not self-authorize credential
   infrastructure).
2. Musa — explicit go, since this is new infrastructure holding
   substrate-level credentials.
3. Only after both: a separate, reviewed PR actually creates the GCP
   project, IAM bindings, and the four secrets — tracked independently of
   this design doc.

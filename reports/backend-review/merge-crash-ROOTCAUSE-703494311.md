# Merge-crash ROOT CAUSE — digest 703494311 (RESOLVED to cause 2026-08-22)

## How captured
`.env VERCEL_TOKEN` (vcp_7t…, named "cto-bot" in Musa's Vercel account) DOES reach the
prod team wingmen-aa9356e1 / ihsanos-irsyad (handoff's "stale dev-team, invalid for prod"
was WRONG — verified: whoami=musaaaaaaa, sees team_mYxOkemmlg8a3HnKFAE9di7N, project OK).
Runtime logs pulled self-serve (no Musa paste/token needed):

    vercel logs ihsanos-irsyad-6yctku7ms-wingmen-aa9356e1.vercel.app \
      --token "$VERCEL_TOKEN" --scope wingmen-aa9356e1 --project ihsanos-irsyad \
      --status-code 500 --since 4h --expand --no-follow

## Un-redacted runtime stack (digest 703494311, deterministic, both λ and ε)
    error  POST /dashboard/people/duplicates
    ReferenceError: MergeCandidate is not defined
        at module evaluation (.next/server/chunks/ssr/_0bkrbpj._.js:2:11078)
        at instantiateModule ([turbopack]_runtime.js:853:9)
        at Context.esmImport [as i] ([turbopack]_runtime.js:281:20)
        at module evaluation (.next/server/chunks/ssr/_0bkrbpj._.js:2:12088)
        ... { digest: '703494311' }

## Root cause (verified at source, deployed commit e750d4e3)
`src/actions/merge-candidates.ts` is a "use server" module. Line 41:
    export type { MergeCandidate, MergeCandidateFlag, MergeTier, DetectPersonRow };
A TYPE re-export inside a "use server" file. Next 16's server-action Turbopack transform
mis-compiles the type re-export into a RUNTIME binding reference to `MergeCandidate`
(which has no runtime value — type only) → ReferenceError at SSR module EVALUATION.
Thrown before any function body → uncatchable by try/catch or render_diagnostics (that is
why #425/#431/#432 all missed it). Every route importing this module (duplicates + merge)
500s on render. The file's OWN header comment states the invariant: "a 'use server' module
may only export async functions."

## Fix (pure app-code, MECHANICAL — no gate/mig/PII-logic/money change)
Remove the `export type { … }` line from the "use server" module; have consumers import
those types directly from `@shared/lib/merge-candidates-core` (the pure lib) instead of
from `@actions/merge-candidates`. Consumers to update: duplicates-client.tsx (L12-16),
merge-candidate-queue.tsx (L21-23 import the actions; types come from core). Minors-
exclusion logic MUST be byte-identical (CAI-1199/1222) — diff is export mechanics only.

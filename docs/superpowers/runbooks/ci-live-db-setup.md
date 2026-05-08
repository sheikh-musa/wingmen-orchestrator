# CI Live-DB Setup Runbook

Per CAI-RESP-153 P1 (BUG-PIPELINE-SYNTHETIC-FILTER-001 substrate-hygiene batch P1).

## What this gives you

Every PR runs integration tests against a real Supabase Postgres branch. Migration changes, trigger updates, and schema-dependent code all get verified before merge. CI blocks merge on failure.

## Prerequisites

- Supabase CLI installed (`supabase --version` should work)
- `gh` CLI installed and authenticated
- Owner / admin access on the GitHub repo

## Steps

### 1. Provision the Supabase branch

Branch type: `--no-data` (per CAI-RESP-153 P1 default ratification — empty branch, migrations re-applied on creation). Privacy-clean; integration tests insert their own fixtures.

```bash
supabase branches create ci-test --no-data
supabase branches list  # verify it appears
```

### 2. Capture the branch connection string

```bash
supabase branches get ci-test  # prints connection details
```

Look for the `postgresql://` URI. Note the password segment.

### 3. Set the GitHub Actions secret

```bash
gh secret set DATABASE_URL --body '<paste the postgresql:// URI here>'
gh secret list  # verify DATABASE_URL appears
```

### 4. Measure cold-start (REQUIRED per CAI-RESP-153 P1)

After the branch has been idle for at least 5 minutes, run:

```bash
./scripts/measure_supabase_branch_coldstart.py 'postgresql://...'
```

Expected: both queries report under 30s. If either is over budget, file a follow-up decision per CAI-RESP-153 P1 — branch may need a never-idle config or a CI-side warm-up hook.

### 5. Verify CI on a test PR

Push a trivial PR (e.g. add a comment to a file) and confirm the workflow runs both test steps:
- "Test (unit + integration-skipped-when-no-DSN)" — should report N integration tests RUN (not SKIPPED) now that DATABASE_URL is set
- "Integration tests gate" — should appear and pass

### 6. Confirm merge gating

Try a PR that intentionally breaks an integration test (e.g. a typo in a migration). Verify merge is blocked.

## Tear-down (if abandoning)

```bash
supabase branches delete ci-test
gh secret delete DATABASE_URL
```

The CI workflow handles missing secret gracefully — the integration-tests-gate step is conditional and the unit-tests step skips integration tests when the secret is absent.

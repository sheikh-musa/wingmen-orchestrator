"""Backfill 5 historical bugs that have status='deployed' but deploy_url=NULL.

Per CAI-RESP-093 R1 (Refinement): backfill ships as a separate idempotent
script, not embedded in the migration. Run AFTER migration applies + after
test suite green; reduces blast radius if any of the 5 SHAs/URLs are wrong.

Bugs backfilled:
  - 418af36c (cosem-adcda) — manual remediation via c5fb68b push 2026-04-23
  - 2386d2a4 (hifz)        — manual override per CAI-PIPELINE-BYPASS-001
  - 0f80ee00 (hifz)        — same override category
  - 19d0c5bb (cosem-tdu)   — NEW find (pre-flight Task 1 surfaced)
  - effb5a09 (cosem-tdu)   — NEW find (pre-flight Task 1 surfaced)

Idempotent: WHERE clause guards on status='deployed' AND
manual_override_reason IS NULL — re-running won't overwrite a previously-
backfilled value. deploy_url left NULL: rows pre-date Option B's verifier
and the override path explicitly skips deploy verification (CAI-PIPELINE-
BYPASS-001).
"""
import os
from dotenv import load_dotenv
load_dotenv()
import psycopg


BACKFILLS = [
    {
        "id": "418af36c-a0ae-4f55-b2a7-7fa0d993236b",
        "repo": "cosem-adcda",
        "manual_override_reason": (
            "ORCHESTRATOR-STATUS-001 incident remediation: c5fb68b pushed via "
            "cc-cosem manual flow 2026-04-23, pre-Option-B-ship; no mechanical "
            "verification chain available retroactively"
        ),
        "verification_diagnostic": "pre-option-b-ship: manually remediated",
    },
    {
        "id": "2386d2a4-6c7c-4768-b7ef-36dae38fe00b",
        "repo": "hifz",
        "manual_override_reason": (
            "CAI-PIPELINE-BYPASS-001 retroactive approval: hifz REPOS.json "
            "mapping gap unblocked by PR #4; original diagnosis in diagnosis_full"
        ),
        "verification_diagnostic": "bypass: pre-PR-4 hifz dispatcher gap",
    },
    {
        "id": "0f80ee00-3b94-4a33-ae83-c2463184bbbc",
        "repo": "hifz",
        "manual_override_reason": (
            "CAI-PIPELINE-BYPASS-001 retroactive approval: lam+alif rendering fix "
            "shipped via cc-scholar manual flow same gap as 2386d2a4"
        ),
        "verification_diagnostic": "bypass: pre-PR-4 hifz dispatcher gap",
    },
    {
        "id": "19d0c5bb-3643-4ce5-a14f-6aa7f34bc782",
        "repo": "cosem-tdu",
        "manual_override_reason": (
            "Pre-Option-B legacy: status set 'deployed' before verification "
            "chain existed; no diagnostic preserved at time of write"
        ),
        "verification_diagnostic": "pre-option-b-ship: legacy",
    },
    {
        "id": "effb5a09-e98d-40e3-98c8-51bf385a302f",
        "repo": "cosem-tdu",
        "manual_override_reason": (
            "Pre-Option-B legacy: status set 'deployed' before verification "
            "chain existed; no diagnostic preserved at time of write"
        ),
        "verification_diagnostic": "pre-option-b-ship: legacy",
    },
]


def main():
    dsn = os.environ.get("DATABASE_URL") or os.environ["SUPABASE_DB_URL"]
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            for row in BACKFILLS:
                cur.execute(
                    """
                    UPDATE bug_reports
                       SET manual_override_reason  = %s,
                           verification_diagnostic = %s,
                           verified_at             = COALESCE(verified_at, now()),
                           resolved_at             = COALESCE(resolved_at, now())
                     WHERE id = %s
                       AND status = 'deployed'
                       AND manual_override_reason IS NULL
                    RETURNING id
                    """,
                    (row["manual_override_reason"], row["verification_diagnostic"], row["id"]),
                )
                result = cur.fetchone()
                if result:
                    print(f"backfilled: {result[0]} ({row['repo']})")
                else:
                    print(f"skipped (already backfilled or not found): {row['id']}")
        conn.commit()


if __name__ == "__main__":
    main()

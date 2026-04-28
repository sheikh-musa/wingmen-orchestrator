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

Idempotent: each UPDATE is keyed on bug_id + an existing-state guard
(WHERE clause checks deploy_url IS NULL or manual_override_reason IS NULL
to prevent overwriting a previously-backfilled value).
"""
import os
from dotenv import load_dotenv
load_dotenv()
import psycopg

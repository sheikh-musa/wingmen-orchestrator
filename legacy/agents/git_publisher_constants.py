"""Status values written by the git publisher.

Kept in one place so that wingmen_orch, git_publisher, and
cc-ihsanos's verification worker (Option B, separate plan) agree
on the state-machine vocabulary.

The canonical enum definition lives in cc-ihsanos's Batch 1
migration per CAI-RESP-078 COORD 1. These constants mirror what
that migration will ADD to jobs.status / bug_reports.status. If
the migration ships with different spellings, update here first,
then refactor call sites.
"""

# jobs.status — extends the existing queued/running/completed/failed/paused
JOB_STATUS_PUSHED = "pushed"           # branch pushed, PR not yet opened
JOB_STATUS_PR_OPEN = "pr_open"          # PR opened, awaiting human merge
JOB_STATUS_PUSH_FAILED = "push_failed"
JOB_STATUS_PR_FAILED = "pr_failed"

# bug_reports.status — extends the existing new/diagnosing/proposed/…/deployed
BUG_STATUS_PR_OPEN = "pr_open"
BUG_STATUS_PUSH_FAILED = "push_failed"
BUG_STATUS_PR_FAILED = "pr_failed"
# BUG_STATUS_DEPLOYED is set by cc-ihsanos's verification worker — not here.

PUBLISHER_STATUS_VALUES = frozenset({
    JOB_STATUS_PUSHED,
    JOB_STATUS_PR_OPEN,
    JOB_STATUS_PUSH_FAILED,
    JOB_STATUS_PR_FAILED,
})

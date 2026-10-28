"""Reply evidence: has a bus row's RECIPIENT answered it, whatever its read_at says?

Lanes routinely act on a row and reply on the bus without stamping read_at or
responded_at (orch-console #80400: three false SLA stalls on 2026-10-28, plus a
wake-backstop page). This is the check-time version of the evidence the nightly
responded_at sweeper (scripts/reconcile_responded_at.py, PR#446) stamps, using the
same two reply tiers, so it adds no new fuzziness:

  * thread    -- a later message from the recipient in the row's OWN thread
  * backref   -- a later message from the recipient citing `#<id>`, word-bounded
                 so #42 never matches #420

"The recipient" is the row's to_agent itself, OR its base id sending with
sub_tag = that instance (a lane addressed as `cc-cosem-adcda-1` usually sends as
`cc-cosem-adcda` with sub_tag `cc-cosem-adcda-1`). Base = agent_status.base_agent_id,
else the to_agent with a trailing `-<digits>` stripped. A SIBLING instance -- by its
own id, or as the base with a sibling's sub_tag -- is not the addressee and never
counts; a base send with NULL sub_tag cannot be attributed and does not count either
(orch-console #80417).

Deliberately NOT evidence: an uncited message in a new thread (indistinguishable
from the lane talking about something else). Fail toward PAGING: any lookup error
returns an empty set, never "attended".
"""
from typing import Iterable, Set

_SQL = r"""
SELECT m.id
FROM agent_messages m
WHERE m.id = ANY(%s)
  AND EXISTS (
    SELECT 1 FROM agent_messages r
    WHERE r.created_at > m.created_at
      AND (r.from_agent = m.to_agent
           OR (r.from_agent = COALESCE((SELECT s.base_agent_id FROM agent_status s
                                        WHERE s.agent_id = m.to_agent
                                          AND s.base_agent_id IS NOT NULL
                                        LIMIT 1),
                                       regexp_replace(m.to_agent, '-[0-9]+$', ''))
               AND r.sub_tag = m.to_agent))
      AND ((m.thread_id IS NOT NULL AND r.thread_id = m.thread_id)
           OR (coalesce(r.subject, '') || ' ' || coalesce(r.body, ''))
              ~ ('(^|[^0-9])#' || m.id::text || '([^0-9]|$)')))
"""


def replied_row_ids(conn, row_ids: Iterable[int]) -> Set[int]:
    """The subset of `row_ids` whose recipient has replied (thread or #id cite)."""
    ids = [int(i) for i in row_ids]
    if not ids:
        return set()
    try:
        # Own savepoint: a failed lookup must not leave the CALLER's transaction aborted
        # (attended_for runs more checks on the same connection after this one).
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_SQL, (ids,))
            return {int(r[0]) for r in cur.fetchall()}
    except Exception:
        return set()

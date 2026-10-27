# work_item equivalence proof -- PASS
at: 2026-10-27T19:21:47.186369+00:00

| kind | open source | live work_item | open w/o live | live w/o open | wi rows | distinct sources |
|---|---|---|---|---|---|---|
| ask | 468 | 468 | 0 | 0 | 1163 | 1163 |
| bus | 1213 | 1213 | 0 | 0 | 2520 | 2520 |
| commitment | 145 | 145 | 0 | 0 | 226 | 226 |

- ask: legacy CLOSE-drift (closed_at NULL but triage terminal): 5 row(s); expected work_item state ['done', 'dropped']; wrong-state rows: []
- bus: legacy skipped (RR, unresponded, skipped_at set): 26 row(s); expected work_item state ['dropped']; wrong-state rows: []

## (P3) state/owner equality + structural divergence: 0 finding(s)

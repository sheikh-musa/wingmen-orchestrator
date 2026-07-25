# Recipe: parallel same-repo sibling lanes (distinctly bus-addressable, no #59 double-claim)

**Problem.** Boot N sibling lanes in ONE repo, each distinctly bus-addressable.
`agent_messages.from_agent` AND `to_agent` are FK → `agents(id)`, so each sibling
needs its own `agents` row. But a second `agents` row with
`repo_scope=['<repo>']` canonicalizes to the same repo as the parent →
`auto_agent_id.load_family_map` raises (the #59 double-claim) → **all** allocation
blocks.

**Model (verified 2026-07-12, op / Nazim #8120).** Register each sibling as an
`agents` row with **EMPTY `repo_scope`** — FK-addressable, but claims no repo, so
it adds no key to the family map and cannot collide. Each sibling boots via
`CC_BASE_OVERRIDE=cc-<base>` (the spawn_reviewer / spawn_uiux pattern), which makes
`auto_agent_id` skip pwd→family resolution entirely and allocate `cc-<base>-N`.
Each runs in its OWN git worktree + branch (parallel same-repo isolation).

## Why it's safe (verified against the live substrate)

- **(a) Empty-scope rows don't break `auto_agent_id`.** `load_family_map` iterates
  `repo_scope`; `[]` contributes nothing → family map unchanged (16 entries),
  `ihsanos-storefront → cc-storefront` still sole-owned, **no collision, no raise.**
- **(b) The override path works.** `CC_BASE_OVERRIDE=cc-storefront-b` →
  `validate_base_override` accepts it (registered `cc-*` family, not an authority id)
  → allocates `cc-storefront-b-1`, `override_used=true`. Confirmed for b, c, e.
- **(c) No new double-claims.** After allocating all three siblings,
  `load_family_map` still builds clean. Critically, sub-tag NAMING does not
  cross-contaminate: `pick_sub_tag('cc-storefront', [...,'cc-storefront-b-1',...])`
  returns `cc-storefront-3` — the `b-1/c-1/e-1` suffixes are non-numeric and are
  **ignored**, so a sibling can never steal or corrupt the parent's numeric slot
  (and vice-versa: the parent's `cc-storefront-2` never matches `cc-storefront-b-%`).

## Recipe (b / c / e — generalize the pattern to any repo)

Run on the fleet host (Studio). `$L` is the launcher WITH the non-login-PATH fix
(PR #69). `<BASE_REF>` is the ONE thing you own: the storefront branch you are
WS-splitting from.

### 1. Register the sibling `agents` rows (empty scope) — idempotent
> Already registered + verified during design; included for reproducibility.
```
cd /Users/Musa/wingmen/orchestrator && .venv/bin/python3 -c "
import os, psycopg
from dotenv import load_dotenv; load_dotenv()
rows=[('cc-storefront-b','CC Storefront WS-B'),('cc-storefront-c','CC Storefront WS-C'),('cc-storefront-e','CC Storefront WS-E')]
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as c:
    for r in rows:
        c.execute(\"INSERT INTO agents (id,display_name,repo_scope,status) VALUES (%s,%s,'{}','active') ON CONFLICT (id) DO NOTHING\", r)
print('agents rows ensured (empty repo_scope)')
"
```

### 2. One worktree + branch per sibling
```
SF=/Users/Musa/wingmen/projects/ihsanos-storefront
git -C "$SF" worktree add -b feat/storefront-ws-b /Users/Musa/wingmen/projects-worktrees/storefront-ws-b <BASE_REF>
git -C "$SF" worktree add -b feat/storefront-ws-c /Users/Musa/wingmen/projects-worktrees/storefront-ws-c <BASE_REF>
git -C "$SF" worktree add -b feat/storefront-ws-e /Users/Musa/wingmen/projects-worktrees/storefront-ws-e <BASE_REF>
```

### 3. Boot each sibling (CC_BASE_OVERRIDE inside the pane command — NOT a tmux prefix)
```
L=/Users/Musa/wingmen/orchestrator/scripts/launch_dangerous_cc.sh
tmux new-session -d -s storefront-ws-b -c /Users/Musa/wingmen/projects-worktrees/storefront-ws-b "CC_BASE_OVERRIDE=cc-storefront-b CC_REPO=ihsanos-storefront exec $L"
tmux new-session -d -s storefront-ws-c -c /Users/Musa/wingmen/projects-worktrees/storefront-ws-c "CC_BASE_OVERRIDE=cc-storefront-c CC_REPO=ihsanos-storefront exec $L"
tmux new-session -d -s storefront-ws-e -c /Users/Musa/wingmen/projects-worktrees/storefront-ws-e "CC_BASE_OVERRIDE=cc-storefront-e CC_REPO=ihsanos-storefront exec $L"
```

## Addressing them on the bus
Each sibling posts as `from_agent='cc-storefront-b|c|e'` (the base id; the sub-tag
`cc-storefront-b-1` is NOT in `agents`). Address one with
`to_agent='cc-storefront-b'`. Distinct base id per sibling = distinct FK target =
distinctly addressable.

## Notes / tradeoffs
- **Empty `repo_scope` means the siblings won't match `ihsanos-storefront`-scoped
  governance** in `agent_boot` (only `*` / unscoped decisions). This is inherent —
  a non-owner cannot hold the repo scope without re-triggering #59. The parent
  `cc-storefront` carries repo governance; siblings are ephemeral WS workers.
- `CC_REPO=ihsanos-storefront` makes `scope_repos` truthful, so the 3 siblings show
  as overlapping on the real repo (a benign, informative overlap warning at boot —
  they ARE deliberately parallel on one repo).
- **Teardown:** when a WS sibling is done, kill its tmux session + `git worktree
  remove` its dir. The `agents` row can stay (harmless, empty-scope) for future
  reuse, or be deleted once no `agent_messages` reference it.

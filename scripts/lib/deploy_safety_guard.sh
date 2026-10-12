#!/bin/bash
# scripts/lib/deploy_safety_guard.sh — FAIL-CLOSED pre-deploy safety for deploy_ihsanos_prod.sh
# (op#29023 / orch-console #63449).
#
# deploy_ihsanos_prod.sh runs `vercel deploy --prod` from the shared
# ~/wingmen/projects/ihsanos WORKING TREE. Without this guard a manual run from a
# dirty / behind / wrong-branch / wrong-linked-project tree ships WIP or the wrong
# app to a CLIENT production (today that tree was parked on a June test branch with
# ~1326 uncommitted files). The git-integration auto-deploy of the committed main
# SHA is the safe path; this manual script is the hazard.
#
# Sourceable. NO override flag (Nazim #63449). Prints drift LOUD to stderr; returns
# non-zero on ANY failure. bash-3.2 safe (no mapfile / associative arrays).
#
#   _deploy_safety_guard <dir> <expected_project_id> <expected_org_id>
#
# Checks ALL of:
#   (a) tree is CLEAN      — git status --porcelain empty (modified AND untracked)
#   (b) on branch main
#   (c) HEAD == origin/main after a FRESH `git fetch origin main`
#   (d) the linked Vercel project (.vercel/project.json) projectId+orgId == intended
#       target — so it can't cross-ship ihsanos <-> ihsanos-irsyad.
_deploy_safety_guard() {
  local dir="$1" exp_proj="$2" exp_org="$3"
  local rc=0

  if ! git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
    echo "::deploy-guard REFUSE:: '$dir' is not a git repo — refusing to deploy." >&2
    return 2
  fi

  # (a) clean tree — modified AND untracked both count
  local dirty; dirty="$(git -C "$dir" status --porcelain 2>/dev/null)"
  if [ -n "$dirty" ]; then
    local n; n="$(printf '%s\n' "$dirty" | wc -l | tr -d ' ')"
    echo "::deploy-guard REFUSE:: working tree is DIRTY — ${n} uncommitted/untracked path(s); commit or stash before a prod deploy:" >&2
    printf '%s\n' "$dirty" | head -20 >&2
    rc=1
  fi

  # (b) on branch main
  local branch; branch="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [ "$branch" != "main" ]; then
    echo "::deploy-guard REFUSE:: on branch '${branch}', not 'main' — a prod deploy must ship main." >&2
    rc=1
  fi

  # (c) HEAD == origin/main after a FRESH fetch
  if ! git -C "$dir" fetch origin main >/dev/null 2>&1; then
    echo "::deploy-guard REFUSE:: 'git fetch origin main' failed in '$dir' — cannot confirm HEAD is current." >&2
    rc=1
  else
    local head omain
    head="$(git -C "$dir" rev-parse HEAD 2>/dev/null)"
    omain="$(git -C "$dir" rev-parse origin/main 2>/dev/null)"
    if [ "$head" != "$omain" ]; then
      echo "::deploy-guard REFUSE:: HEAD (${head}) != origin/main (${omain}) — tree is behind/ahead of main." >&2
      rc=1
    fi
  fi

  # (d) linked Vercel project matches the intended target
  local vjson="$dir/.vercel/project.json"
  if [ ! -f "$vjson" ]; then
    echo "::deploy-guard REFUSE:: no linked Vercel project ($vjson missing) — cannot confirm the deploy target." >&2
    rc=1
  elif [ -z "$exp_proj" ] || [ -z "$exp_org" ]; then
    echo "::deploy-guard REFUSE:: no intended projectId/orgId supplied — cannot confirm the deploy target (set the expected ids; no blind deploy)." >&2
    rc=1
  else
    local got_proj got_org
    got_proj="$(sed -n 's/.*"projectId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$vjson" | head -1)"
    got_org="$(sed -n 's/.*"orgId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$vjson" | head -1)"
    if [ "$got_proj" != "$exp_proj" ] || [ "$got_org" != "$exp_org" ]; then
      echo "::deploy-guard REFUSE:: linked Vercel project (projectId=${got_proj}, orgId=${got_org}) != intended (projectId=${exp_proj}, orgId=${exp_org}) — refusing to cross-ship." >&2
      rc=1
    fi
  fi

  if [ "$rc" -ne 0 ]; then
    echo "::deploy-guard:: ABORT — NOT deploying '$dir' (intended projectId=${exp_proj:-<unset>}). Fix the drift above; there is NO override flag (op#29023/#63449)." >&2
  fi
  return "$rc"
}

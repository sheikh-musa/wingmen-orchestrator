#!/usr/bin/env bash
# VPS provision — P0 base setup for the stable-core migration (Hetzner CAX21, Ubuntu 24.04 ARM64).
# See reports/vps-migration-spec-20260730.md. Run as a sudo-capable user on the FRESH box.
# Idempotent-ish; safe to re-run. Does NOT transfer secrets or install services (that's P1).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sheikh-musa/wingmen-orchestrator.git}"   # confirm exact remote
REPO_DIR="${REPO_DIR:-$HOME/wingmen/orchestrator}"
REPO_BRANCH="${REPO_BRANCH:-main}"     # pick the convergence branch at cutover
PY=python3.9                            # match the orchestrator's Python 3.9 exactly

echo "== 1. base packages =="
sudo apt-get update -y
sudo apt-get install -y build-essential git curl tmux ca-certificates gnupg software-properties-common

echo "== 2. Python 3.9 (deadsnakes — Ubuntu 24.04 defaults to 3.12; orchestrator needs 3.9) =="
if ! command -v $PY >/dev/null 2>&1; then
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -y
  sudo apt-get install -y python3.9 python3.9-venv python3.9-dev
fi
$PY --version

echo "== 3. GitHub CLI =="
if ! command -v gh >/dev/null 2>&1; then
  (type -p wget >/dev/null || sudo apt-get install -y wget)
  sudo mkdir -p -m 755 /etc/apt/keyrings
  wget -nv -O- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update -y && sudo apt-get install -y gh
fi

echo "== 4. Tailscale (public IP -> DIRECT node, no relay — the whole point) =="
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo "   -> run: sudo tailscale up --hostname wingmen-core   (paste TS_AUTHKEY or do interactive)"

echo "== 5. clone repo + venv (repo is PRIVATE — needs auth FIRST) =="
mkdir -p "$(dirname "$REPO_DIR")"
if [ ! -d "$REPO_DIR/.git" ]; then
  if gh auth status >/dev/null 2>&1; then
    gh repo clone sheikh-musa/wingmen-orchestrator "$REPO_DIR" -- -b "$REPO_BRANCH"   # uses gh's auth for the private repo
  elif [ -n "${GH_TOKEN:-}" ]; then
    git clone -b "$REPO_BRANCH" "https://x-access-token:${GH_TOKEN}@github.com/sheikh-musa/wingmen-orchestrator.git" "$REPO_DIR"
  else
    echo "!! Private repo needs auth. Run 'gh auth login' (or export GH_TOKEN) THEN re-run this script." >&2
    exit 1
  fi
fi
cd "$REPO_DIR"
[ -d .venv ] || $PY -m venv .venv
./.venv/bin/pip install --upgrade pip
[ -f requirements.txt ] && ./.venv/bin/pip install -r requirements.txt || echo "   (no requirements.txt — install deps manually)"

cat <<'NEXT'

== P0 done. REMAINING (manual / P1) ==
  a) SECRETS: transfer .env OUT-OF-BAND (scp/age from the Mac) into the repo dir — NEVER via git.
     Preserve the DATABASE_URL leak guard (scripts force .env.local for subagents).
  b) AUTH: export CLAUDE_CODE_OAUTH_TOKEN (Max-backed headless) + `gh auth login`.
  c) TAILSCALE: `sudo tailscale up` (see step 4) — confirm a DIRECT (non-relay) node in `tailscale status`.
  d) SERVICES (P1): convert launchd plists -> systemd units, START DAEMONS FIRST
     (ingest, tg-out, health_check, fleet-health/SRE, watchdogs), verify each, THEN disable on the Macs.
     Health-check on this stable-network host = the ihsanOS false-alert fix.
  e) HUB (P2): boot tmux `orch` here + take orch_lease (loud CAS) — ONLY after the hub lands the irsyad upload.
NEXT
echo "P0 provision complete."

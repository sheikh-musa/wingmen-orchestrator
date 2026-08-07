#!/usr/bin/env bash
# sync_env_from_mini.sh — Pull .env from Mac Mini over Tailscale.
#
# Run on the Windows PC (WSL2) when the Mini is reachable.
# Safe to run repeatedly — backs up the existing .env before overwriting.
#
# Usage:
#   scripts/sync_env_from_mini.sh [--mini-ip 100.x.x.x] [--dry-run]

set -euo pipefail

ORCH_DIR="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
MINI_IP="${MINI_TAILSCALE_IP:-100.83.21.34}"
MINI_USER="${MINI_SSH_USER:-sheikhmusa}"
MINI_ORCH="$MINI_USER@$MINI_IP:~/wingmen/orchestrator"
DRY_RUN=false

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[sync]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mini-ip)   MINI_IP="$2"; shift 2 ;;
        --mini-user) MINI_USER="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=true; shift ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

MINI_ORCH="$MINI_USER@$MINI_IP:~/wingmen/orchestrator"

# ── check connectivity ───────────────────────────────────────────────────────
info "Checking Mini reachability (${MINI_USER}@${MINI_IP})…"
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${MINI_USER}@${MINI_IP}" "echo ok" >/dev/null 2>&1; then
    error "Cannot reach Mini at ${MINI_IP}"
    error "Is Tailscale connected? Run: tailscale status"
    exit 1
fi
info "Mini reachable"

if [[ "$DRY_RUN" == "true" ]]; then
    info "DRY RUN — would sync the following files:"
    ssh -o StrictHostKeyChecking=no "${MINI_USER}@${MINI_IP}" \
        "ls -lh ~/wingmen/orchestrator/.env 2>/dev/null && ls -lh ~/wingmen/orchestrator/.env.* 2>/dev/null || echo '(no .env files found)'"
    exit 0
fi

# ── backup existing .env ─────────────────────────────────────────────────────
if [[ -f "$ORCH_DIR/.env" ]]; then
    BACKUP="$ORCH_DIR/.env.bak.$(date '+%Y%m%d-%H%M%S')"
    cp "$ORCH_DIR/.env" "$BACKUP"
    warn "Backed up existing .env → $(basename "$BACKUP")"
fi

# ── pull .env ────────────────────────────────────────────────────────────────
info "Pulling .env from Mini…"
scp -o StrictHostKeyChecking=no "${MINI_ORCH}/.env" "$ORCH_DIR/.env"
chmod 600 "$ORCH_DIR/.env"
info ".env synced"

# ── re-apply .env.mirror overrides ───────────────────────────────────────────
# The mirror overrides file keeps IS_MIRROR=true + bridges OFF.
# It must exist (created by setup_mirror_wsl.sh); remind if missing.
if [[ ! -f "$ORCH_DIR/.env.mirror" ]]; then
    warn ".env.mirror not found — run setup_mirror_wsl.sh to create it, or create manually"
    warn "Without .env.mirror, boot_mirror.sh will start bridges (dangerous if Mini is alive)"
fi

info "Sync complete. boot_mirror.sh layers .env.mirror on top of .env — bridges stay OFF."

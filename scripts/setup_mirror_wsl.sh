#!/usr/bin/env bash
# setup_mirror_wsl.sh — Bootstrap the wingmen orchestrator mirror on WSL2 (Ubuntu).
#
# Run this ONCE on the Windows PC inside WSL2:
#   bash <(curl -fsSL https://raw.githubusercontent.com/....) [optional: --mini-ip 100.x.x.x]
# OR copy this file across and run it directly.
#
# What it does:
#   1. Installs system deps (Python 3, Node 20, tmux, git, curl, jq, openssh-client)
#   2. Clones the orchestrator repo
#   3. Sets up Python venv + pip install
#   4. Installs Claude Code CLI (npm -g)
#   5. Installs Tailscale for WSL2
#   6. Pulls .env from the Mac Mini over tailscale/scp (optional)
#   7. Writes mirror-specific env overrides (.env.mirror)
#   8. Installs boot_mirror.sh as a Windows Task Scheduler task (via schtasks.exe)
#   9. Prints next steps

set -euo pipefail

MINI_TAILSCALE_IP="${MINI_TAILSCALE_IP:-100.83.21.34}"
MINI_SSH_USER="${MINI_SSH_USER:-sheikhmusa}"
ORCH_DIR="$HOME/wingmen/orchestrator"
WINGMEN_DIR="$HOME/wingmen"
REPO_REMOTE="${REPO_REMOTE:-}"    # set if you have a GitHub remote, else we scp from Mini
CLAUDE_BIN="/usr/local/bin/claude"
WSL_DISTRO="${WSL_DISTRO:-Ubuntu}"

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

# ── parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mini-ip) MINI_TAILSCALE_IP="$2"; shift 2 ;;
        --mini-user) MINI_SSH_USER="$2"; shift 2 ;;
        --repo) REPO_REMOTE="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── guard: must be WSL2 ─────────────────────────────────────────────────────
if ! grep -qi "microsoft" /proc/version 2>/dev/null && ! grep -qi "WSL" /proc/version 2>/dev/null; then
    error "This script is for WSL2 only. Not running in WSL."
    exit 1
fi
info "WSL2 detected. Starting setup…"

# ── 1. system deps ──────────────────────────────────────────────────────────
info "[1/8] Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    tmux git curl jq wget openssh-client \
    build-essential libssl-dev libffi-dev python3-dev

# Node 20 (for Claude Code CLI)
if ! command -v node >/dev/null 2>&1 || [[ "$(node --version | cut -d. -f1 | tr -d v)" -lt 18 ]]; then
    info "Installing Node.js 20…"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
    sudo apt-get install -y nodejs
fi
info "Node $(node --version) / npm $(npm --version)"

# ── 2. create wingmen dir structure ─────────────────────────────────────────
info "[2/8] Setting up directory structure…"
mkdir -p "$WINGMEN_DIR"

# ── 3. get the orchestrator code ─────────────────────────────────────────────
info "[3/8] Getting orchestrator codebase…"
if [[ -d "$ORCH_DIR/.git" ]]; then
    warn "Repo already exists at $ORCH_DIR — pulling latest"
    git -C "$ORCH_DIR" pull --ff-only 2>/dev/null || warn "git pull failed, continuing with existing state"
elif [[ -n "$REPO_REMOTE" ]]; then
    info "Cloning from $REPO_REMOTE…"
    git clone "$REPO_REMOTE" "$ORCH_DIR"
else
    info "No git remote set — copying from Mini via scp (Mini must be reachable on tailscale)"
    info "Mini IP: $MINI_TAILSCALE_IP"
    if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${MINI_SSH_USER}@${MINI_TAILSCALE_IP}" "test -d ~/wingmen/orchestrator" 2>/dev/null; then
        error "Cannot reach Mini at ${MINI_TAILSCALE_IP}. Make sure Tailscale is connected first."
        error "Join tailnet first: see step below, then re-run with --mini-ip <ip>"
        exit 1
    fi
    rsync -az --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' \
        "${MINI_SSH_USER}@${MINI_TAILSCALE_IP}:~/wingmen/orchestrator/" \
        "$ORCH_DIR/"
    info "Synced orchestrator codebase from Mini"
fi

# ── 4. Python venv ───────────────────────────────────────────────────────────
info "[4/8] Setting up Python venv…"
python3 -m venv "$ORCH_DIR/.venv"
"$ORCH_DIR/.venv/bin/pip" install --quiet --upgrade pip
if [[ -f "$ORCH_DIR/requirements.txt" ]]; then
    "$ORCH_DIR/.venv/bin/pip" install --quiet -r "$ORCH_DIR/requirements.txt"
    info "requirements.txt installed"
fi

# ── 5. Claude Code CLI ───────────────────────────────────────────────────────
info "[5/8] Installing Claude Code CLI…"
if ! command -v claude >/dev/null 2>&1; then
    sudo npm install -g @anthropic-ai/claude-code 2>/dev/null || \
    npm install -g @anthropic-ai/claude-code
    info "Claude Code installed: $(claude --version 2>/dev/null || echo 'check manually')"
else
    info "Claude Code already installed: $(claude --version 2>/dev/null)"
fi

# ── 6. Tailscale for WSL2 ────────────────────────────────────────────────────
info "[6/8] Installing Tailscale for WSL2…"
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
    warn "Tailscale installed. After setup completes, run: sudo tailscale up"
    warn "Then authenticate and join the wingmen tailnet."
else
    info "Tailscale already installed"
fi

# ── 7. pull .env from Mini (if reachable) ───────────────────────────────────
info "[7/8] Fetching .env from Mini…"
if [[ -f "$ORCH_DIR/.env" ]]; then
    warn ".env already exists — skipping pull (delete it first to re-pull)"
else
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${MINI_SSH_USER}@${MINI_TAILSCALE_IP}" "test -f ~/wingmen/orchestrator/.env" 2>/dev/null; then
        scp -o StrictHostKeyChecking=no \
            "${MINI_SSH_USER}@${MINI_TAILSCALE_IP}:~/wingmen/orchestrator/.env" \
            "$ORCH_DIR/.env"
        chmod 600 "$ORCH_DIR/.env"
        info ".env pulled from Mini"
    else
        warn "Cannot reach Mini or .env missing — you must copy .env manually to $ORCH_DIR/.env"
        warn "From another terminal on the Mini, run: scp ~/wingmen/orchestrator/.env <wsl-ip>:$ORCH_DIR/.env"
    fi
fi

# Write mirror-specific overrides (layered over .env)
cat > "$ORCH_DIR/.env.mirror" << 'MIRRORENV'
# Mirror node overrides — sourced AFTER .env so these take precedence
IS_MIRROR=true

# Bridges are OFF on the mirror — only activate if Mini is confirmed down
TG_BRIDGE_ENABLED=false
CAI_BRIDGE_ENABLED=false
NUTRI_STUDY_BOT_ENABLED=false

# Fleet lanes start DOWN on the mirror (cold standby)
MIRROR_LANES_UP=false

# Mini's tailscale IP — used by sync_env_from_mini.sh
MINI_TAILSCALE_IP=100.83.21.34
MINI_SSH_USER=sheikhmusa
MIRRORENV
chmod 600 "$ORCH_DIR/.env.mirror"
info ".env.mirror written (bridges OFF by default)"

# ── 8. install boot script + Task Scheduler task ────────────────────────────
info "[8/8] Setting up auto-start…"

BOOT_SCRIPT="$ORCH_DIR/scripts/boot_mirror.sh"
# boot_mirror.sh is already written to the repo (created alongside this script)

# Register with Windows Task Scheduler via schtasks.exe (available in WSL2)
TASK_NAME="WingmenMirrorOrch"
WSL_BOOT_CMD="wsl.exe -d $WSL_DISTRO -- bash $BOOT_SCRIPT"

TASK_XML="$ORCH_DIR/ops/windows/wingmen-mirror-orch.xml"
mkdir -p "$(dirname "$TASK_XML")"
cat > "$TASK_XML" << TASKXML
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </LogonTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>wsl.exe</Command>
      <Arguments>-d $WSL_DISTRO -- bash $BOOT_SCRIPT</Arguments>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
</Task>
TASKXML

# Convert to Windows path for schtasks
WIN_XML_PATH=$(wslpath -w "$TASK_XML" 2>/dev/null || echo "")
if [[ -n "$WIN_XML_PATH" ]]; then
    if schtasks.exe /Create /TN "$TASK_NAME" /XML "$WIN_XML_PATH" /F 2>/dev/null; then
        info "Task Scheduler task '$TASK_NAME' registered — runs at login after 30s"
    else
        warn "schtasks registration failed — import manually:"
        warn "  Task Scheduler → Import Task → $WIN_XML_PATH"
    fi
else
    warn "Could not register Task Scheduler task automatically."
    warn "Import manually: Task Scheduler → Import Task → ops/windows/wingmen-mirror-orch.xml"
fi

# ── done ────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Mirror setup complete"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo ""
echo "  1. Join the tailnet (if not already connected):"
echo "       sudo tailscale up"
echo "       # authenticate in your browser"
echo ""
echo "  2. If .env wasn't pulled automatically, copy it now:"
echo "       scp sheikhmusa@100.83.21.34:~/wingmen/orchestrator/.env $ORCH_DIR/.env"
echo ""
echo "  3. Authenticate Claude Code (one-time):"
echo "       claude login"
echo "       # use the same Anthropic account as the Mini"
echo ""
echo "  4. Test a manual boot:"
echo "       $BOOT_SCRIPT"
echo "       # should create tmux 'orch-mirror' session"
echo ""
echo "  5. To ACTIVATE (Mini is down — take over as primary):"
echo "       $ORCH_DIR/scripts/mirror_activate.sh"
echo "       # enables bridge, flips IS_MIRROR=false"
echo ""
echo "  Cold standby is ON by default. Bridge + lanes stay OFF until"
echo "  you explicitly activate."
echo ""

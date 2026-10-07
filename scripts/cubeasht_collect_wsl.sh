# cubeasht_collect_wsl.sh — READ-ONLY probe inside the cubeasht WSL distro
# 'ci-orchestrator' (the self-hosted GitHub Actions runner). Sent on stdin:
#   ssh cubeasht 'wsl -d ci-orchestrator -u root -- bash -s' < cubeasht_collect_wsl.sh
# Prints key=value lines plus the raw `free -m` block. Changes nothing.
# No `set -e`: each probe reports its own failure as data.
echo "CUBEMON_WSL_BEGIN"
st=$(systemctl is-active actions.runner.sheikh-musa-wingmen-orchestrator.cubeasht-orchestrator.service 2>&1)
echo "runner_service=${st}"
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
echo "loadavg=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
echo "FREE_BEGIN"
free -m 2>&1
echo "FREE_END"
echo "CUBEMON_WSL_END"

# cubeasht_collect_wsl_generic.sh — READ-ONLY probe inside ANY cubeasht WSL
# distro that hosts a GitHub Actions runner. Sent on stdin:
#   ssh cubeasht 'wsl -d <distro> -- bash -s' < cubeasht_collect_wsl_generic.sh
# Unlike cubeasht_collect_wsl.sh (hardcoded to ci-orchestrator's single
# systemd unit), this one DISCOVERS whichever actions.runner.*.service units
# exist in the distro it runs inside and reports all of them — op#35970/bus
# #86104 found ci-cosem-adcda hosts TWO runner services and ci-cosem-platform
# hosts one; a single hardcoded unit name silently missed both. Prints
# key=value lines plus the raw `free -m` block. Changes nothing.
# No `set -e`: each probe reports its own failure as data.
echo "CUBEMON_WSL_BEGIN"
echo "RUNNER_SERVICES_BEGIN"
systemctl list-units --type=service --all --no-legend --plain 2>/dev/null \
  | awk '{print $1}' | grep -E '^actions\.runner\.' | while read -r unit; do
    st=$(systemctl is-active "$unit" 2>&1)
    echo "${unit}=${st}"
  done
echo "RUNNER_SERVICES_END"
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
echo "loadavg=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
echo "FREE_BEGIN"
free -m 2>&1
echo "FREE_END"
echo "CUBEMON_WSL_END"

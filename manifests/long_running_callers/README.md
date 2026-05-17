# Long-running claude-caller manifests

Per CAI-RESP-161 Phase A. Any long-running process that invokes `claude`
(plugins, daemons, scheduled tasks, cron, launchd, watch-loops) on a
substrate host MUST be registered.

Two ways to register:

## (a) Python helper (preferred for Python callers)

```python
from nervous_system.long_running_claude_callers import register

await register(
    supabase,
    caller_name="my-daemon",
    cmd="python3 scripts/my_daemon.py",
    expected_cadence_seconds=300,
    expected_tokens_per_day=14_000_000,
    ratified_by_decision_ref="CC-MY-DAEMON-001",
    registered_by_identity="operator",  # or "cc_family" or "substrate"
    purpose="Description for operator-facing review",
)
```

## (b) Manifest file (preferred for static configs)

Drop a YAML or JSON file in this directory. Schema:

```yaml
caller_name: my-daemon                # unique key
cmd: python3 scripts/my_daemon.py     # actual launch command
expected_cadence_seconds: 300         # how often it fires claude
expected_tokens_per_day: 14000000     # informational, for soft-alert threshold
max_tokens_per_day: 20000000          # optional, harder ceiling
ratified_by_decision_ref: CC-MY-DAEMON-001  # filed decision_ref ratifying this caller
registered_by_identity: operator      # operator | cc_family | substrate
purpose: Description for operator-facing review
auto_kill_policy: soft_alert          # optional; defaults derived from identity
```

Manifests are swept on orchestrator startup. Errors logged; valid manifests upserted to the registry table.

## Default auto_kill_policy (per CAI-RESP-161 Q6)

| registered_by_identity | default auto_kill_policy |
|---|---|
| operator | soft_alert (operator-authored — respect operator's intentional infrastructure) |
| cc_family | soft_alert (CC family-spawned — soft escalation) |
| substrate | no_kill (substrate-native carve-out — hard-coded; cannot be overridden) |

## Filing a ratification decision

New callers require a filed `decision_ref` in `strategic_decisions` BEFORE registration. Required fields per CAI-RESP-160:
- Source (path/repo/plugin name)
- Cadence (how often it fires claude)
- Expected tokens/day
- Failure mode if killed
- Why this caller cannot use substrate-native mechanisms

## Phase B enforcement (separate PR per CAI-RESP-161)

Once Phase B ships (paired with CAI-RESP-157 [B] watchdog-kill):
- Unregistered + cadence pattern (>50 sessions/24h per CAI-RESP-157 [A]) → watchdog SIGTERMs
- Registered + exceeds expected_tokens_per_day → soft alert (notification_log + agent_message to CAI)
- Registered + cadence drift (>2x expected over 30min) → soft alert
- Substrate-native carve-out → exempt

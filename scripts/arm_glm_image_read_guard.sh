#!/usr/bin/env bash
# arm_glm_image_read_guard.sh — POST-MERGE arming for the GLM image-Read residency
# guard (PR#397 / Nazim #65950). Registers the PreToolUse hook at USER level
# (~/.claude/settings.json) with ABSOLUTE paths.
#
# WHY USER-LEVEL + ABSOLUTE: provider-routed lanes (e.g. cosem-adcda) run in their
# OWN project cwd, where the orchestrator's PROJECT-level .claude/settings.json is
# INERT (fleet-wide-guard-needs-both-project-and-user-level-reg). $CLAUDE_PROJECT_DIR
# also won't resolve to the orchestrator for a foreign-cwd lane, so the user-level
# entry hard-codes the absolute venv-python + hook paths.
#
# SAFETY (fail-closed PRE-CHECK, runs BEFORE touching settings.json; ABORTS changing
# NOTHING unless ALL pass):
#   (1) the hook file exists + is readable on disk;
#   (2) the venv python exists + is executable;
#   (3) the hook EXITS 0 on a CLAUDE-lane env (ANTHROPIC_BASE_URL unset) for an
#       image-Read sample — proving user-level arming will NOT fail-closed-block
#       every Bash/Read on every Claude lane fleet-wide (the catastrophic mode);
#   (4) the hook EXITS 2 and prints its own marker on a ROUTED env for the same
#       sample — proving the guard actually fires when it should.
#   The probe path is a NON-EXISTENT /tmp/…png (extension-only check; reads no real
#   file — guard-proof-probe-must-fail-harmless).
#
# ROLLBACK (1 line): scripts/arm_glm_image_read_guard.sh --rollback
#   removes the user-level entry (restoring the pre-arm backup it took).
#
# Overridable for tests: GLM_GUARD_HOOK, GLM_GUARD_PY, GLM_GUARD_SETTINGS.
set -uo pipefail

HOOK="${GLM_GUARD_HOOK:-$HOME/wingmen/orchestrator/scripts/hooks/glm_image_read_guard.py}"
PY="${GLM_GUARD_PY:-$HOME/wingmen/orchestrator/.venv/bin/python3}"
SETTINGS="${GLM_GUARD_SETTINGS:-$HOME/.claude/settings.json}"
MARKER="glm_image_read_guard"
SAMPLE='{"tool_name":"Read","tool_input":{"file_path":"/tmp/glm_guard_probe_nonexistent.png"}}'

MODE="arm"
[ "${1:-}" = "--rollback" ] && MODE="rollback"

die() { echo "ARM-ABORT: $*" >&2; exit 1; }

# ── ROLLBACK ────────────────────────────────────────────────────────────────
if [ "$MODE" = "rollback" ]; then
    [ -f "$SETTINGS" ] || { echo "rollback: no settings file at $SETTINGS — nothing to do"; exit 0; }
    "$PY" - "$SETTINGS" "$HOOK" <<'PYEOF' || die "rollback: JSON edit failed — settings UNCHANGED"
import json, sys, os, time
settings, hook = sys.argv[1], sys.argv[2]
with open(settings) as f: d = json.load(f)
pre = (d.get("hooks") or {}).get("PreToolUse") or []
kept = [e for e in pre if hook not in json.dumps(e)]
if len(kept) == len(pre):
    print("rollback: no glm_image_read_guard entry present — nothing removed"); sys.exit(0)
bak = settings + ".bak-glmguard-" + time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
with open(bak, "w") as f: json.dump(d, f, indent=2)
d.setdefault("hooks", {})["PreToolUse"] = kept
tmp = settings + ".tmp"
with open(tmp, "w") as f: json.dump(d, f, indent=2); f.write("\n")
os.replace(tmp, settings)
print(f"rollback: removed glm_image_read_guard from {settings} (backup {bak})")
PYEOF
    exit 0
fi

# ── PRE-CHECK (fail-closed; change NOTHING unless all pass) ───────────────────
[ -r "$HOOK" ] || die "hook not found/readable: $HOOK"
[ -x "$PY" ]   || die "venv python not found/executable: $PY"

# (3) Claude-lane env MUST be a no-op (exit 0) even for an image Read.
set +e
printf '%s' "$SAMPLE" | env -u ANTHROPIC_BASE_URL "$PY" "$HOOK" >/dev/null 2>&1
rc_claude=$?
# (4) Routed env MUST block (exit 2) + print the marker.
routed_err="$(printf '%s' "$SAMPLE" | ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic" "$PY" "$HOOK" 2>&1 >/dev/null)"
rc_routed=$?
set -e

[ "$rc_claude" = "0" ] || die "PRE-CHECK FAILED: hook exited $rc_claude on a CLAUDE env (expected 0). Arming would fail-closed-block EVERY Bash/Read on EVERY Claude lane. Refusing."
[ "$rc_routed" = "2" ] || die "PRE-CHECK FAILED: hook exited $rc_routed on a ROUTED env (expected 2 = block). Refusing."
case "$routed_err" in
    *"$MARKER"*) : ;;
    *) die "PRE-CHECK FAILED: routed-env block did not print the '$MARKER' marker (a crash/other guard can also exit 2). Refusing." ;;
esac
echo "pre-check OK: hook no-ops on Claude (exit 0), blocks + marks on routed (exit 2)."

# ── ARM: merge the user-level PreToolUse entry (idempotent, atomic, backed up) ─
mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || printf '{}\n' > "$SETTINGS"
"$PY" - "$SETTINGS" "$PY" "$HOOK" <<'PYEOF' || die "ARM: JSON edit failed — settings UNCHANGED"
import json, sys, os, time
settings, py, hook = sys.argv[1], sys.argv[2], sys.argv[3]
with open(settings) as f:
    try: d = json.load(f)
    except Exception: d = {}
cmd = '"%s" "%s"' % (py, hook)
hooks = d.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
if any(hook in json.dumps(e) for e in pre):
    print("ARM: glm_image_read_guard already registered — idempotent no-op"); sys.exit(0)
bak = settings + ".bak-glmguard-" + time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
with open(bak, "w") as f: json.dump(d, f, indent=2)
pre.append({"matcher": "Bash|Read",
            "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
tmp = settings + ".tmp"
with open(tmp, "w") as f: json.dump(d, f, indent=2); f.write("\n")
os.replace(tmp, settings)
print(f"ARM: registered glm_image_read_guard at user level {settings} (backup {bak})")
PYEOF
echo "ARMED. Rollback: $0 --rollback"

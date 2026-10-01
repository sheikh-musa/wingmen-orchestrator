#!/usr/bin/env bash
# The sanctioned way to change ONE key in a .env-shaped file without ever putting its
# value in the transcript (Musa op#24408 delta, bus #48639/#48642 -- the Edit/Write
# tool path is now blocked outright on secret files; this is the escape hatch).
#
# The new value is read from STDIN (or a file via -f), never from argv -- argv would
# land in the tool_input of the invoking Bash call just as plainly as echoing it would.
# Only a short sha1 fingerprint of the new value is ever printed, never the value.
#
# Usage:
#   printf '%s' "$NEW_VALUE" | scripts/env_set.sh path/to/.env KEY_NAME
#   scripts/env_set.sh path/to/.env KEY_NAME -f /path/to/value-file
#
# Updates KEY_NAME in place if present, appends it if absent. Preserves every other
# line byte-for-byte.
set -euo pipefail

FILE="${1:?usage: env_set.sh <file> <KEY> [-f value-file]}"
KEY="${2:?usage: env_set.sh <file> <KEY> [-f value-file]}"

[ -f "$FILE" ] || { echo "env_set.sh: no such file: $FILE" >&2; exit 1; }
[[ "$KEY" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "env_set.sh: invalid key name: $KEY" >&2; exit 1; }

if [ "${3:-}" = "-f" ]; then
    VALUE_FILE="${4:?usage: env_set.sh <file> <KEY> -f <value-file>}"
    [ -f "$VALUE_FILE" ] || { echo "env_set.sh: no such value file: $VALUE_FILE" >&2; exit 1; }
    VALUE="$(cat "$VALUE_FILE")"
elif [ -t 0 ]; then
    echo "env_set.sh: refusing to read a secret value from an interactive terminal -- pipe it in or use -f <file>" >&2
    exit 1
else
    VALUE="$(cat -)"
fi

[ -n "$VALUE" ] || { echo "env_set.sh: empty value, refusing" >&2; exit 1; }

export ENV_SET_FILE="$FILE"
export ENV_SET_KEY="$KEY"
export ENV_SET_VALUE="$VALUE"
python3 - <<'PYEOF'
import os

file_path = os.environ["ENV_SET_FILE"]
key = os.environ["ENV_SET_KEY"]
value = os.environ["ENV_SET_VALUE"]

with open(file_path, "r") as f:
    lines = f.readlines()

prefix = key + "="
found = False
out = []
for line in lines:
    if line.startswith(prefix):
        out.append(f"{key}={value}\n")
        found = True
    else:
        out.append(line)
if not found:
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out.append(f"{key}={value}\n")

with open(file_path, "w") as f:
    f.writelines(out)
PYEOF

FINGERPRINT="$(printf '%s' "$VALUE" | shasum | cut -c1-10)"
unset ENV_SET_FILE ENV_SET_KEY ENV_SET_VALUE VALUE
echo "$KEY updated (sha1 $FINGERPRINT)"

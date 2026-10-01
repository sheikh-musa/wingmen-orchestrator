#!/usr/bin/env bash
# NOTE: canonical TRACKED copy. The LIVE copy installs to
# /usr/local/sbin/gzb-fetch-secrets-wrapper.sh on gzb, owned root:root, mode
# 0500 (root-exec-only; gazzai reaches it ONLY via the scoped sudoers grant in
# deploy/wingmen-fetch-secrets-wrapper.sudoers, never by reading/copying it).
#
# LOCK 1 (Musa op#24409, "never again"). This is the ENTIRE root-owned attack
# surface the sudoers grant exposes to gazzai: takes no arguments (rejected at
# TWO independent layers -- this script's own `$#` check, AND the sudoers
# fragment's own `""` no-args lockdown, per cc-quality bus #48664 #2), reads
# the gzb_to_mini private key from its root:root 0400 home, and does the
# WHOLE secrets-fetch job as root: fetch, unpack into tmpfs, strip/split, fix
# ownership, atomic swap into place -- then prints ONLY a one-line STATUS
# summary (file count + per-file sha1 PREFIXES, never contents).
#
# orch-console bus #48668 (structural change, after the PASS-WITH-FIXES review
# bus #48664/#48665): the ORIGINAL design had this wrapper stream the fetched
# tar to ITS OWN STDOUT for fetch-secrets.sh (running as gazzai) to unpack.
# That means `sudo -n <this wrapper>` run directly as gazzai -- by ANY agent
# shell, not just the systemd unit -- would print the ENTIRE secrets bundle
# into that Bash call's tool_result, i.e. incident (a) all over again, with
# only Lock 2's scanner standing in the way. Doing the full unpack HERE, as
# root, with only a status line ever reaching stdout, means a gazzai agent
# running this wrapper directly can at most REFRESH /dev/shm -- it can never
# print a secret value, because no secret value ever reaches this process's
# stdout at all.
#
# Rotation is explicitly OUT of scope (Musa op#24408): same keypair, new
# holder. If the key is ever rotated, only this KEY path matters -- nothing
# else in LOCK 1 needs to change.
set -euo pipefail

if [ "$#" -ne 0 ]; then
    echo "gzb-fetch-secrets-wrapper: takes no arguments" >&2
    exit 1
fi

KEY="/root/.ssh/gzb_to_mini"
CORE="sheikhmusa@100.83.21.34"
GAZZAI_HOME="/home/gazzai"
FINAL_DEST="/dev/shm/wingmen-secrets"

# Explicit root-owned temp stage under tmpfs -- never persistent disk. A fresh,
# uniquely-named dir each run (no reuse, no need to clear anything first).
STAGE_DIR="$(mktemp -d /dev/shm/wingmen-secrets.new.XXXXXX)"
cleanup() { rm -rf "$STAGE_DIR" 2>/dev/null || true; }
trap cleanup EXIT

chmod 700 "$STAGE_DIR"

# Forced command on core ignores the client command ("true"); it streams the
# tar. `tar -xf -` writes FILES to STAGE_DIR, never echoes the tar bytes to
# ITS OWN stdout -- this pipeline produces no wrapper-level stdout output.
ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
    "$CORE" true | tar -xf - -C "$STAGE_DIR"

[ -f "$STAGE_DIR/.env" ] || { echo "gzb-fetch-secrets-wrapper: FATAL no .env in fetched bundle, aborting before touching $FINAL_DEST" >&2; exit 1; }

# STRIP identity vars from the SHARED bundle (Nazim #46266/#46267, 2026-09-30).
# Identity belongs to each unit/launcher, NEVER a shared secrets file.
grep -vE '^(ORCH_BODY_ROLE|ORCH_AGENT_ID|AGENT_ID|CC_BASE_AGENT_ID|ORCH_TMUX_SESSION|FLEET_HOST_ID)=' \
    "$STAGE_DIR/.env" > "$STAGE_DIR/.env.stripped" && mv "$STAGE_DIR/.env.stripped" "$STAGE_DIR/.env"

# CAI-1225 (CAI-RESP-1412): split write creds out of the SHARED .env into a 0600
# RESTRICTED store, leaving RO + substrate in shared. GATED on the RO counterpart
# being present (no-op until the bundle ships it). FAIL-OPEN: a split failure must
# NOT abort the fetch; it logs loudly and leaves the shared .env intact. Explicit
# GAZZAI_HOME, not $HOME -- this process runs as root, $HOME would be /root.
if [ -f "$GAZZAI_HOME/cai1225_postprocess.sh" ]; then
    "$GAZZAI_HOME/cai1225_postprocess.sh" "$STAGE_DIR/.env" "$STAGE_DIR/private/write_dsn.env" \
        || echo "gzb-fetch-secrets-wrapper: WARN CAI-1225 post-process failed (fail-open; write creds remain in shared .env)" >&2
fi

# Lock down perms, THEN hand ownership to gazzai (the only OS user that needs to
# read this) -- exact same perms fetch-secrets.sh used to set for itself.
find "$STAGE_DIR" -type d -exec chmod 700 {} +
find "$STAGE_DIR" -type f -exec chmod 600 {} +
chown -R gazzai:gazzai "$STAGE_DIR"

# Atomic swap: FINAL_DEST is always either the full old bundle or the full new
# one, never a partial write. Both moves are same-filesystem (tmpfs) renames.
if [ -e "$FINAL_DEST" ]; then
    mv "$FINAL_DEST" "${FINAL_DEST}.old.$$"
fi
mv "$STAGE_DIR" "$FINAL_DEST"
trap - EXIT
rm -rf "${FINAL_DEST}.old.$$" 2>/dev/null || true

# STATUS LINE ONLY -- file count + per-file sha1 PREFIXES. Never contents, never
# full hashes (a full hash of a single-line file can occasionally be reversible
# against a tiny guess space; a 10-char prefix is identification-only, matching
# every other LOCK 1 script's convention).
FILE_COUNT=0
STATUS_PARTS=()
while IFS= read -r -d '' f; do
    FILE_COUNT=$((FILE_COUNT + 1))
    REL="${f#"$FINAL_DEST"/}"
    HASH="$(sha1sum "$f" | cut -c1-10)"
    STATUS_PARTS+=("$REL(sha1 $HASH)")
done < <(find "$FINAL_DEST" -type f -print0 | sort -z)

echo "gzb-fetch-secrets-wrapper: $FILE_COUNT files extracted: ${STATUS_PARTS[*]}"

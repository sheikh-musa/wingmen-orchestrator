#!/usr/bin/env bash
# NOTE: canonical TRACKED copy. The LIVE copy runs at /home/gazzai/fetch-secrets.sh
# (gzb-local, like coord_supervisor.sh / orch_supervisor.sh). Keep the two in sync;
# wingmen-fetch-secrets.service ExecStart points at the /home/gazzai/ live copy.
#
# Pull the fleet secrets bundle from wingmen-core into tmpfs (RAM) ONLY.
# Nothing is ever written to gzb persistent disk.
set -euo pipefail

DEST="/dev/shm/wingmen-secrets"

# tmpfs staging dir (0700). /dev/shm is tmpfs (RAM); clears on reboot.
mkdir -p "$DEST"
chmod 700 "$DEST"
find "$DEST" -mindepth 1 -delete   # clear any prior contents

# LOCK 1 (Musa op#24409, "never again"): the gzb_to_mini private key now lives
# root:root 0400 under /root/.ssh -- gazzai (the OS user every lane ALSO runs
# as) can no longer read it directly, closing incident (a)'s exact path. The
# ONE scoped sudoers grant (/etc/sudoers.d/wingmen-fetch-secrets-wrapper) lets
# gazzai run ONLY this exact root-owned wrapper, no args, no password; the
# wrapper (deploy/gzb-fetch-secrets-wrapper.sh) does the ssh pull and streams
# the tar to stdout exactly as the old direct-ssh-as-gazzai call did. Rotation
# is explicitly out of scope here (Musa op#24408) -- same keypair, new holder.
sudo -n /usr/local/sbin/gzb-fetch-secrets-wrapper.sh | tar -xf - -C "$DEST"

# Lock down perms in tmpfs.
find "$DEST" -type d -exec chmod 700 {} +
find "$DEST" -type f -exec chmod 600 {} +

# STRIP identity vars from the SHARED bundle (Nazim #46266/#46267, 2026-09-30).
# Identity belongs to each unit/launcher, NEVER a shared secrets file: the wingmen-core
# bundle carried ORCH_BODY_ROLE=console/ORCH_AGENT_ID=orch-console (the MINI console's
# identity), so every gzb process that sourced $DEST/.env took on the console identity
# (bus posts mis-attributed, tg_send fail-closed, self-recovery refusal loop). Filter
# them out so a gzb body's own launcher (orch_supervisor.sh etc.) is the sole identity source.
if [ -f "$DEST/.env" ]; then
    grep -vE '^(ORCH_BODY_ROLE|ORCH_AGENT_ID|AGENT_ID|CC_BASE_AGENT_ID|ORCH_TMUX_SESSION|FLEET_HOST_ID)=' \
        "$DEST/.env" > "$DEST/.env.stripped" && mv "$DEST/.env.stripped" "$DEST/.env"
    chmod 600 "$DEST/.env"
fi

# CAI-1225 (CAI-RESP-1412): split write creds out of the SHARED .env into a 0600
# RESTRICTED store (tmpfs — still RAM-only, no persistent disk), leaving RO + substrate
# in shared. GATED on the RO counterpart being present, so it is a NO-OP until the
# wingmen-core bundle ships GOUMLYNE_RO/IHSANOS_PROD_RO (won't break read paths early).
# FAIL-OPEN: a split failure must NOT abort the secrets fetch (secrets-available beats
# split-enforced); it logs loudly and leaves the shared .env intact.
if [ -f "$HOME/cai1225_postprocess.sh" ]; then
    "$HOME/cai1225_postprocess.sh" "$DEST/.env" "$DEST/private/write_dsn.env" \
        || echo "fetch-secrets: WARN CAI-1225 post-process failed (fail-open; write creds remain in shared .env)" >&2
fi

echo "fetch-secrets: bundle extracted into $DEST"

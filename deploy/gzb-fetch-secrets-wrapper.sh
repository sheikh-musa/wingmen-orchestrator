#!/usr/bin/env bash
# NOTE: canonical TRACKED copy. The LIVE copy installs to
# /usr/local/sbin/gzb-fetch-secrets-wrapper.sh on gzb, owned root:root, mode
# 0500 (root-exec-only; gazzai reaches it ONLY via the scoped sudoers grant in
# deploy/wingmen-fetch-secrets-wrapper.sudoers, never by reading/copying it).
#
# LOCK 1 (Musa op#24409, "never again"). This is the ENTIRE root-owned attack
# surface the sudoers grant exposes to gazzai: takes no arguments (any given
# are rejected, so the grant can never be leveraged into an arbitrary root
# command via argv), reads the gzb_to_mini private key from its new
# root:root 0400 home, and streams the secrets tar to stdout -- exactly what
# the old direct-ssh-as-gazzai call in fetch-secrets.sh did. Nothing here
# writes anything; nothing here is secret-value-printing (the tar bytes go
# straight to the pipe, never through a shell that could echo them).
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

# Forced command on core ignores the client command ("true"); it streams the tar.
exec ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
    "$CORE" true

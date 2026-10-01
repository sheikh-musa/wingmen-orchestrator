#!/usr/bin/env bash
# NOTE: canonical TRACKED copy. The LIVE copy runs at /home/gazzai/fetch-secrets.sh
# (gzb-local, like coord_supervisor.sh / orch_supervisor.sh). Keep the two in sync;
# wingmen-fetch-secrets.service ExecStart points at the /home/gazzai/ live copy.
#
# LOCK 1 (Musa op#24409, "never again") + the stdout-residual fix (orch-console bus
# #48668): the ENTIRE job -- ssh pull, unpack into tmpfs, identity-strip, CAI-1225
# split, ownership/perms, atomic swap -- now runs INSIDE the root-owned wrapper
# (deploy/gzb-fetch-secrets-wrapper.sh), reached via the one scoped sudoers NOPASSWD
# grant (/etc/sudoers.d/wingmen-fetch-secrets-wrapper). This script does nothing but
# invoke it and surface the wrapper's own status line (file count + per-file sha1
# PREFIXES only -- the wrapper never puts a secret VALUE on its own stdout, so there
# is nothing here to leak regardless of who/what captures this script's output).
set -euo pipefail

sudo -n /usr/local/sbin/gzb-fetch-secrets-wrapper.sh

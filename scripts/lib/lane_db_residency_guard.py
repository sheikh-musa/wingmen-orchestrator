"""Fail-loud, DETECT-ONLY lane-DB residency guard (CAI-RESP-1308).

WHY: launch_dangerous_cc.sh sources the orchestrator .env, which EXPORTS the bus/
coordination DATABASE_URL (tscuymavysscrvoberrr) into every lane's process env. That is
intentional (lanes write agent_messages to the bus), but it SHADOWS a client lane's own
`.env.local` DATABASE_URL: a naive `connect(os.environ['DATABASE_URL'])` in a client lane
would write to the BUS DB instead of the client's own = TENANT-RESIDENCY-001 commingling.

DOCTRINE (cai, CAI-RESP-1308): bare DATABASE_URL is the bus/coordination DB ONLY; ALL
client/tenant DB access MUST use an explicit named var (GOUMLYNE_DATABASE_URL /
IHSANOS_PROD_DATABASE_URL / a per-project var) — never the bare name.

THIS GUARD: at lane boot, if the lane's OWN repo declares a DATABASE_URL (in .env.local,
else .env) that DIFFERS from the inherited (bus) DATABASE_URL, print a LOUD warning so the
lane is migrated to an explicit var before it silently commingles. It is DETECT-ONLY: it
NEVER blocks the launch (always exit 0) and NEVER prints the secret URL VALUES (only the
file + var name + the doctrine). cai declined the alternative fleet-wide rename (real blast
radius on live bus plumbing for a risk not currently live anywhere).

Usage (from the launcher, after it has sourced .env so DATABASE_URL is the bus one):
  "$VENV_PY" -m scripts.lib.lane_db_residency_guard --repo-dir "$CALLER_DIR"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

# .env.local is the runtime override (Next.js/dotenv convention) so it wins over .env.
_ENV_FILES = (".env.local", ".env")
_DB_LINE = re.compile(r"^\s*(?:export\s+)?DATABASE_URL\s*=\s*(.*?)\s*$")


@dataclass
class ShadowResult:
    shadowed: bool
    env_file: str | None = None   # which lane env file declared the shadowed DATABASE_URL


def _strip_value(raw: str) -> str:
    """Strip one layer of matching surrounding quotes from an env value."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw


def _declared_db_url(repo_dir: str):
    """The lane's OWN declared DATABASE_URL (value, filename) from .env.local else .env,
    or (None, None) if the lane declares none. Best-effort text parse; any read error is
    treated as 'no declaration' so the guard can never crash a launch."""
    for fname in _ENV_FILES:
        path = os.path.join(repo_dir, fname)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = _DB_LINE.match(line)
                    if m:
                        return _strip_value(m.group(1)), fname
        except (OSError, UnicodeError):
            continue
    return None, None


def detect_shadow(repo_dir: str, inherited_url) -> ShadowResult:
    """A shadow exists iff the process inherited a DATABASE_URL (the bus one) AND the
    lane's own repo declares a DIFFERENT DATABASE_URL. If nothing is inherited, the lane's
    own value would be used — no shadow. If the lane declares nothing or the same value
    (an orchestrator lane legitimately on the bus), no shadow."""
    if not inherited_url:
        return ShadowResult(False)
    declared, fname = _declared_db_url(repo_dir)
    if declared is None:
        return ShadowResult(False)
    if declared == inherited_url:
        return ShadowResult(False)
    return ShadowResult(True, env_file=fname)


def warning_banner(res: ShadowResult) -> str:
    """The LOUD boot warning. NAMES the file + var + doctrine; NEVER the secret values.
    Left-gutter style (no right border) so variable-width interpolated content — e.g. the
    env filename — can never make the box read ragged (Nazim #32609 nit)."""
    rule = "═" * 76
    return (
        "\n"
        f"╔{rule}\n"
        "║ ⚠  RESIDENCY SHADOW — lane DATABASE_URL is being SHADOWED by the BUS DB\n"
        f"╟{'─' * 76}\n"
        f"║ This lane's {res.env_file} declares its OWN DATABASE_URL, but the launcher exported\n"
        "║ the orchestrator/BUS DATABASE_URL into the env, which SHADOWS it. A client/tenant\n"
        "║ connect(os.environ['DATABASE_URL']) here would write to the BUS DB — that is\n"
        "║ TENANT-RESIDENCY-001 commingling.\n"
        "║ DOCTRINE (CAI-RESP-1308): bare DATABASE_URL = the BUS/coordination DB ONLY.\n"
        "║ FIX: move this lane's client DB access to an EXPLICIT named var\n"
        "║ (<PROJECT>_DATABASE_URL) and read THAT — never bare DATABASE_URL.\n"
        f"╚{rule}\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Detect-only lane-DB residency guard (CAI-RESP-1308).")
    ap.add_argument("--repo-dir", required=True, help="the lane's repo/working dir (CALLER_DIR)")
    args = ap.parse_args(argv)
    res = detect_shadow(args.repo_dir, os.environ.get("DATABASE_URL"))
    if res.shadowed:
        # Fail LOUD to stderr; DETECT-ONLY -> never a non-zero exit (must not block launch).
        sys.stderr.write(warning_banner(res))
        sys.stderr.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

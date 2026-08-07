"""Every fleet boot launcher must strip ANTHROPIC_API_KEY.

.env carries a live ANTHROPIC_API_KEY. If it survives into a launched
`claude` session's environment, the session silently routes through the
metered Anthropic API instead of the Mac Mini's Claude Max subscription
(MEMORY: "Lane .env ANTHROPIC_API_KEY forces METERED API").

The fable substrate scan (critic #2) found boot_orch.sh — which starts the
single most continuously-running body in the fleet (the hub `orch` tmux
session) — was the lone launcher that sourced .env but never `unset`
ANTHROPIC_API_KEY, while all four siblings did. This regression test pins the
invariant across every launcher so the omission cannot silently return.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"

# Every script that sources .env and then launches a `claude` session.
LAUNCHERS = [
    "boot_orch.sh",
    "boot_nazim.sh",
    "boot_cai.sh",
    "boot_fleet_health.sh",
    "launch_dangerous_cc.sh",
]

_UNSET_RE = re.compile(r"^\s*unset\s+ANTHROPIC_API_KEY\s*$", re.MULTILINE)


def test_all_launchers_unset_anthropic_api_key():
    missing = []
    for name in LAUNCHERS:
        path = _SCRIPTS / name
        assert path.exists(), f"launcher {name} not found at {path}"
        if not _UNSET_RE.search(path.read_text()):
            missing.append(name)
    assert not missing, (
        "these launchers source .env but never `unset ANTHROPIC_API_KEY`, "
        "so their `claude` session bills to the metered API instead of Max: "
        + ", ".join(missing)
    )


def test_boot_orch_unsets_after_sourcing_env():
    """The unset must come AFTER the .env source, or it's a no-op.

    boot_orch.sh sources .env (which SETS the key) inside an `if` block, then
    must unset it afterwards. Assert the `unset` appears after the last
    `source "$ORCH_DIR/.env"`.
    """
    src = (_SCRIPTS / "boot_orch.sh").read_text()
    src_idx = src.rfind('source "$ORCH_DIR/.env"')
    assert src_idx != -1, "boot_orch.sh no longer sources .env — update this test"
    m = _UNSET_RE.search(src)
    assert m, "boot_orch.sh must `unset ANTHROPIC_API_KEY`"
    assert m.start() > src_idx, (
        "unset ANTHROPIC_API_KEY must come AFTER sourcing .env (else the "
        "source re-sets it and the strip is a no-op)"
    )

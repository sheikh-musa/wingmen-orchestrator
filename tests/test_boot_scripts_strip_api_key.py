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
import os
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"

# launchd plists that launch the orchestrator loop / CTO-council — the only
# entrypoints that (a) call load_dotenv() and (b) then instantiate
# anthropic.Anthropic(api_key=...). For these a shell `unset` is NOT enough
# (see test_shell_unset_is_defeated_by_load_dotenv below); the launch must
# EMPTY-EXPORT the key so load_dotenv(override=False) cannot re-add it.
_ORCH_LOOP_TOKENS = ("wingmen_orch", "council_agent")

# `export ANTHROPIC_API_KEY=` with an EMPTY value (next char ends the word:
# ; whitespace, quote, or end-of-string). `export ANTHROPIC_API_KEY=$X` fails.
_EMPTY_EXPORT_RE = re.compile(r"export\s+ANTHROPIC_API_KEY=(?=[;\s'\"]|$)")

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


def test_shell_unset_is_defeated_by_load_dotenv_but_empty_export_survives(tmp_path):
    """WHY the orch-loop plists must EMPTY-EXPORT, not `unset`, the key.

    The `unset`-then-`claude` launchers are safe: `claude` never re-reads .env.
    But `wingmen_orch.py` calls `load_dotenv()` at import. python-dotenv with the
    default `override=False` sets any var that is ABSENT from the environment —
    so a shell `unset ANTHROPIC_API_KEY` immediately before it is a NO-OP: the
    real key is re-added from .env and the loop bills the metered API. Exporting
    the key EMPTY keeps it PRESENT, so load_dotenv leaves it alone. This test
    pins that dotenv behaviour so the plist guard below cannot be "fixed" by
    swapping the empty-export back to a (defeated) bare unset.
    """
    from dotenv import load_dotenv

    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-SENTINEL\n")
    saved = os.environ.get("ANTHROPIC_API_KEY")
    try:
        # bare unset -> key absent -> load_dotenv REPOPULATES it (the trap)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        load_dotenv(env)
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-SENTINEL", (
            "regression: python-dotenv no longer repopulates an absent key — "
            "if this ever holds, the bare-unset trap is gone and the empty-export "
            "rationale must be revisited"
        )

        # empty export -> key PRESENT (but "") -> load_dotenv does NOT override
        os.environ["ANTHROPIC_API_KEY"] = ""
        load_dotenv(env)
        assert os.environ.get("ANTHROPIC_API_KEY") == "", (
            "empty-export was overridden by load_dotenv — the plist scrub is no "
            "longer sufficient; the key would go metered"
        )
    finally:
        if saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved


def _tracked_plists():
    for d in ("launchd", "ops/launchd", "legacy", "scripts"):
        base = _REPO_ROOT / d
        if base.is_dir():
            yield from base.rglob("*.plist")


def test_orch_loop_plists_neutralise_metered_key_via_empty_export():
    """Any tracked plist that launches the orch loop / council MUST empty-export
    ANTHROPIC_API_KEY in its ProgramArguments, and must not rely on a bare unset.

    Guards the stale `dev.wingmen.orchestrator` label (op#22407): restart_orch.sh
    can kickstart it, and its target calls load_dotenv — so without the empty
    export it would re-acquire the metered key.
    """
    # Raw-text scan (not plistlib): several plists are deploy TEMPLATES with
    # `{{...}}` tokens that a strict XML parser rejects; the metered-key
    # entrypoint tokens and the empty-export both live in ProgramArguments, so
    # a text scan is both sufficient and template-safe.
    offenders = []
    for plist in _tracked_plists():
        text = plist.read_text(errors="replace")
        if not any(tok in text for tok in _ORCH_LOOP_TOKENS):
            continue
        if not _EMPTY_EXPORT_RE.search(text):
            offenders.append(
                f"{plist.name}: launches the orch loop/council but does not "
                f"`export ANTHROPIC_API_KEY=` (empty) — it would bill metered via "
                f"load_dotenv"
            )
    assert not offenders, "orch-loop plists leak the metered key:\n" + "\n".join(offenders)

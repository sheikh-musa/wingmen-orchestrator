"""Canonical lane→token resolver (GAP-B per-GROUP pointers).

Pins the precedence contract the console display AND the lane boot both consult:
  session pointer > group pointer > fleet default > (None => .env)
plus fail-open on a missing/unreadable group file, forbidden-fp refusal, and the
load-bearing BACK-COMPAT guarantee: with NO group file present the resolution is
byte-identical to the pre-GAP-B behaviour.

Also exercises the __main__ CLI shim launch_dangerous_cc.sh calls, proving bash and
python get the SAME answer from the SAME code.
"""
import hashlib
import os
import subprocess
import sys

import pytest

from scripts.lib import lane_token_resolver as R

_FORBIDDEN_FP = "13589de86f29"  # gazzabyte consumer Max (CAI-729)


def _fp(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()[:12]


@pytest.fixture
def orch(tmp_path):
    """A fake $ORCH_DIR with a keys/ dir. Returns (orch_dir, make_key, ptr)."""
    keys = tmp_path / "keys"
    keys.mkdir()

    def make_key(name: str, token: str):
        p = keys / name
        p.write_text(token + "\n")
        return str(p)

    def ptr(name: str, target: str):
        (tmp_path / name).write_text(target + "\n")

    return str(tmp_path), make_key, ptr


# ── Tier precedence ───────────────────────────────────────────────────────────

def test_fleet_default_when_no_group(orch):
    """A worker lane with only the fleet default set -> the fleet key file."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    ptr(".lane_default_token", fleet)
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == fleet


def test_group_pointer_wins_over_fleet_default(orch):
    """A per-group pin (.group_default_token.irsyad) beats the fleet default for a
    lane in that family — while OTHER families still get the fleet default."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    grp = make_key("musa2-oauth-token", "MUSA2")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.irsyad", grp)
    # in-family lanes -> group pin
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) == grp
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == grp
    assert R.resolve_lane_token_path("irsyad-prog2", orch_dir=orch_dir) == grp
    assert R.resolve_lane_token_path("cc-irsyad-1", orch_dir=orch_dir) == grp
    # a DIFFERENT family is unaffected -> fleet default
    assert R.resolve_lane_token_path("cosem-tdu", orch_dir=orch_dir) == fleet


def test_session_pointer_wins_for_singleton_bodies(orch):
    """nazim/cc-orchestrator use their OWN per-session pointer, never group/fleet."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    naz = make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", fleet)
    ptr(".nazim_default_token", naz)
    # even a stray group file for a "nazim" family must NOT be consulted
    ptr(".group_default_token.nazim", make_key("x-oauth-token", "XTOK"))
    assert R.resolve_lane_token_path("nazim", orch_dir=orch_dir) == naz


def test_no_pointer_singletons_return_none(orch):
    """cai + fleet-health boot off .env -> None (caller uses the .env account)."""
    orch_dir, make_key, ptr = orch
    ptr(".lane_default_token", make_key("musa-oauth-token", "MUSA"))
    assert R.resolve_lane_token_path("cai", orch_dir=orch_dir) is None
    assert R.resolve_lane_token_path("fleet-health", orch_dir=orch_dir) is None


def test_nothing_configured_returns_none(orch):
    """No pointer files at all -> None (fall through to .env)."""
    orch_dir, _make_key, _ptr = orch
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) is None


# ── Fail-open ─────────────────────────────────────────────────────────────────

def test_missing_group_file_falls_through_to_fleet(orch):
    """No .group_default_token.<fam> -> fleet default (fail-open, never error)."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    ptr(".lane_default_token", fleet)
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) == fleet


def test_group_pointing_at_unreadable_token_falls_through(orch):
    """A group pin whose TARGET token file is missing -> fall through to fleet,
    not to .env (honors precedence; never raises)."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.irsyad", str(os.path.join(orch_dir, "keys", "gone-oauth-token")))
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) == fleet


def test_empty_group_file_falls_through(orch):
    """An empty .group_default_token.<fam> -> fleet default."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    ptr(".lane_default_token", fleet)
    (os.path.join(orch_dir, ".group_default_token.irsyad"))
    open(os.path.join(orch_dir, ".group_default_token.irsyad"), "w").close()
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) == fleet


# ── Forbidden-fp refusal ──────────────────────────────────────────────────────

def test_real_gazzabyte_fp_is_wired_into_the_forbidden_set():
    """The refusal guards the REAL gazzabyte fp (CAI-729), not just a test value."""
    assert _FORBIDDEN_FP in R._FORBIDDEN_TOKEN_FPS


def test_forbidden_fp_group_pointer_is_refused(orch, monkeypatch):
    """A group pin whose target token fingerprints to a FORBIDDEN account is NEVER
    honored -> falls through to the fleet default (the lane can never boot on a
    forbidden token via a group pin). We add the crafted token's own fp to the
    forbidden set (a 48-bit preimage of the literal gazzabyte fp is infeasible to
    forge; the refusal MECHANISM is what matters and is exercised identically)."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    bad_tok = "aliased-forbidden-secret"
    bad = make_key("gzb-oauth-token", bad_tok)   # aliased name, forbidden CONTENT
    monkeypatch.setattr(R, "_FORBIDDEN_TOKEN_FPS", {_fp(bad_tok)})
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.irsyad", bad)
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) == fleet
    # sanity: with the fp NOT forbidden, the SAME pin IS honored (proves the
    # fall-through above was the forbidden guard, not some other skip).
    monkeypatch.setattr(R, "_FORBIDDEN_TOKEN_FPS", set())
    assert R.resolve_lane_token_path("irsyad", orch_dir=orch_dir) == bad


# ── Back-compat: byte-identical to pre-GAP-B when NO group file ───────────────

def _legacy_resolve(session, orch_dir):
    """The pre-GAP-B resolution, transcribed from panes._expected_fp's pointer
    logic: session pointer for nazim/orch, .lane_default_token for a worker lane,
    None for the no-pointer singletons. Returns the token-file PATH (or None)."""
    body_pointer = {"nazim": ".nazim_default_token", "cc-orchestrator": ".orch_default_token"}
    no_pointer = {"cai", "fleet-health"}
    ptr = body_pointer.get(session)
    if ptr is None and session not in no_pointer:
        ptr = ".lane_default_token"
    if ptr:
        try:
            with open(os.path.join(orch_dir, ptr)) as f:
                target = f.read().strip()
            return target or None
        except Exception:
            return None
    return None


def test_backcompat_byte_identical_without_group_files(orch):
    """With NO group file present, the resolver returns EXACTLY the legacy path for
    every body class — worker lane, session-pointer body, singleton."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    naz = make_key("syed-oauth-token", "SYED")
    orchp = make_key("orch-oauth-token", "ORCH")
    ptr(".lane_default_token", fleet)
    ptr(".nazim_default_token", naz)
    ptr(".orch_default_token", orchp)
    for session in ("irsyad", "irsyad-coord", "cosem-tdu", "cc-ihsanos-1",
                    "nazim", "cc-orchestrator", "cai", "fleet-health", "unknownlane"):
        assert (R.resolve_lane_token_path(session, orch_dir=orch_dir)
                == _legacy_resolve(session, orch_dir)), session


# ── family_of helper ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("session,fam", [
    ("irsyad", "irsyad"),
    ("irsyad-coord", "irsyad"),
    ("irsyad-prog2", "irsyad"),
    ("cc-irsyad-1", "irsyad"),
    ("cosem-tdu", "cosem"),
    ("cc-ihsanos-1", "ihsanos"),
    ("", None),
    ("cc-", None),
])
def test_family_of(session, fam):
    assert R.family_of(session) == fam


# ── CLI shim (the rail launch_dangerous_cc.sh calls) ─────────────────────────

def test_cli_shim_matches_python_api(orch):
    """`python3 -m scripts.lib.lane_token_resolver --session S` prints EXACTLY the
    same path resolve_lane_token_path returns (bash gets the same answer)."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    grp = make_key("musa2-oauth-token", "MUSA2")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.irsyad", grp)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(R.__file__))))
    for session, expect in [("irsyad-coord", grp), ("cosem-tdu", fleet),
                            ("cai", None)]:
        out = subprocess.run(
            [sys.executable, "-m", "scripts.lib.lane_token_resolver",
             "--session", session, "--orch-dir", orch_dir],
            capture_output=True, text=True, cwd=repo_root,
        )
        assert out.returncode == 0
        printed = out.stdout.strip()
        assert printed == (expect or ""), (session, printed, expect)


def test_cli_shim_prints_nothing_for_none(orch):
    """A None resolution prints an EMPTY stdout (so `$(...)` is empty in bash)."""
    orch_dir, _make_key, _ptr = orch
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(R.__file__))))
    out = subprocess.run(
        [sys.executable, "-m", "scripts.lib.lane_token_resolver",
         "--session", "irsyad", "--orch-dir", orch_dir],
        capture_output=True, text=True, cwd=repo_root,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == ""

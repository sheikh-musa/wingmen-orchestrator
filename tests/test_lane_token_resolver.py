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


def test_no_pointer_singletons_return_none_without_their_own_pointer(orch):
    """cai + fleet-health have no GROUP/fleet tier; with no OWN pointer file
    either (the fleet default here must NOT leak to them) -> None (.env)."""
    orch_dir, make_key, ptr = orch
    ptr(".lane_default_token", make_key("musa-oauth-token", "MUSA"))
    assert R.resolve_lane_token_path("cai", orch_dir=orch_dir) is None
    assert R.resolve_lane_token_path("fleet-health", orch_dir=orch_dir) is None


def test_singleton_own_pointer_is_honored(orch):
    """cai/fleet-health each have their OWN bespoke pointer file (read directly by
    boot_cai.sh/boot_fleet_health.sh in bash) -- this module must resolve it too,
    so the console's "expected" display matches what the body actually boots on
    (bus #51982/#46625). A fleet default or the OTHER singleton's pointer must
    never leak across."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    cai_tok = make_key("syed-oauth-token", "SYED")
    fh_tok = make_key("musa2-oauth-token", "MUSA2")
    ptr(".lane_default_token", fleet)
    ptr(".cai_default_token", cai_tok)
    ptr(".fleet-health_default_token", fh_tok)
    assert R.resolve_lane_token_path("cai", orch_dir=orch_dir) == cai_tok
    assert R.resolve_lane_token_path("fleet-health", orch_dir=orch_dir) == fh_tok


def test_singleton_own_pointer_absent_falls_through_to_none(orch):
    """No .cai_default_token file at all -> None (.env) -- same shape as an absent
    tier-2 session pointer; the resolver returns the raw pointer TARGET (even an
    unreadable one) and leaves verifying it to the caller (panes._expected_fp /
    boot_cai.sh), exactly like the existing session-pointer tier already does."""
    orch_dir, _make_key, _ptr = orch
    assert R.resolve_lane_token_path("cai", orch_dir=orch_dir) is None


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
    # op#22426: cosem-tdu is a COMPOUND family (its own token pool, on Syed) --
    # it must NOT collapse to the broader 'cosem' family.
    ("cosem-tdu", "cosem-tdu"),
    ("cosem-tdu-coord", "cosem-tdu"),
    ("cosem-tdu-worker-1", "cosem-tdu"),
    ("cc-cosem-tdu-coord", "cosem-tdu"),
    # the broader cosem family (cosem-exams pinned to Musa, op#16101) is
    # unaffected -- only the exact 'cosem-tdu' compound is special-cased.
    ("cosem-exams", "cosem"),
    ("cosem-adcda", "cosem"),
    ("cc-ihsanos-1", "ihsanos"),
    ("", None),
    ("cc-", None),
])
def test_family_of(session, fam):
    assert R.family_of(session) == fam


# ── Compound family (op#22426: cosem-tdu is its own token pool, on Syed) ──────

def test_compound_family_group_pointer_wins_and_broader_cosem_unaffected(orch):
    """A `.group_default_token.cosem-tdu` pin resolves cosem-tdu (and its coord/
    worker siblings) to Syed, while the broader `.group_default_token.cosem`
    pointer (Musa, op#16101's cosem-exams pin) keeps resolving unrelated cosem
    lanes -- proving the compound-family carve-out does not leak into the
    generic 'cosem' family."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    cosem_musa = make_key("musa-oauth-token-cosem", "MUSA-COSEM")
    tdu_syed = make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.cosem", cosem_musa)
    ptr(".group_default_token.cosem-tdu", tdu_syed)

    assert R.resolve_lane_token_path("cosem-tdu", orch_dir=orch_dir) == tdu_syed
    assert R.resolve_lane_token_path("cosem-tdu-coord", orch_dir=orch_dir) == tdu_syed
    assert R.resolve_lane_token_path("cc-cosem-tdu-coord", orch_dir=orch_dir) == tdu_syed
    assert R.resolve_lane_token_path("cosem-tdu-worker-1", orch_dir=orch_dir) == tdu_syed

    # sibling cosem lanes stay on the broader/Musa pointer, untouched
    assert R.resolve_lane_token_path("cosem-exams", orch_dir=orch_dir) == cosem_musa
    assert R.resolve_lane_token_path("cosem-adcda", orch_dir=orch_dir) == cosem_musa


def test_compound_family_falls_through_to_fleet_default_without_its_own_pointer(orch):
    """Before a `.group_default_token.cosem-tdu` file exists, cosem-tdu lanes fall
    through to the fleet default (never silently inherit the broader 'cosem'
    pointer) -- this is the pre-pointer-file state PR #156 lands in."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("musa-oauth-token", "MUSA")
    cosem_musa = make_key("musa-oauth-token-cosem", "MUSA-COSEM")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.cosem", cosem_musa)
    assert R.resolve_lane_token_path("cosem-tdu-coord", orch_dir=orch_dir) == fleet


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


# ── Musa-personal-pool FREEZE guard (operator directive Musa op#28857) ────────
# While a freeze is active, NO worker lane may boot on the Musa-PERSONAL OAuth
# token: a lane that would resolve to Musa must resolve to Syed, loudly logged,
# and NEVER silently fall through to Musa. Liftable + auto-expiring.

def _write_fp_map(tmp_path, pairs):
    """Write a token_fps.map mapping each account -> fp of its token string, and
    return its path (point TOKEN_FPS_MAP at it so account_for_fp resolves the FAKE
    test tokens — a 48-bit preimage of the REAL musa/syed fps is infeasible to
    forge, exactly why the existing forbidden-fp test monkeypatches too)."""
    m = tmp_path / "token_fps.map"
    lines = ["# test fp map"] + ["%s %s" % (acct, _fp(tok)) for acct, tok in pairs.items()]
    m.write_text("\n".join(lines) + "\n")
    return str(m)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(R.__file__))))


def test_account_for_fp_reverse_lookup(tmp_path, monkeypatch):
    """account_for_fp is a map-driven fp->account reverse lookup; fail-open to None
    for an unmapped/empty/None fp (never raises)."""
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    assert R.account_for_fp(_fp("MUSA")) == "musa"
    assert R.account_for_fp(_fp("SYED")) == "syed"
    assert R.account_for_fp("deadbeef0000") is None
    assert R.account_for_fp(None) is None
    assert R.account_for_fp("") is None


def test_account_for_fp_resolves_real_musa_fp_from_checked_in_map():
    """The guard keys off the REAL musa/syed fps in the checked-in token_fps.map
    (map-driven so a rotation is handled), not just test values."""
    assert R.account_for_fp("68142948c003") == "musa"
    assert R.account_for_fp("582043088eae") == "syed"


def test_musa_freeze_active_states(orch):
    """Active iff the flag file exists AND (no parseable expiry OR now < expiry).
    Absent/unreadable => NOT active (fail-OPEN). Empty/unparseable => active.
    Past expiry => NOT active."""
    orch_dir, _mk, ptr = orch
    assert R._musa_freeze_active(orch_dir) is False          # absent
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    assert R._musa_freeze_active(orch_dir) is True           # far-future expiry
    ptr(".musa_personal_freeze", "2000-01-01T00:00:00Z")
    assert R._musa_freeze_active(orch_dir) is False          # expired
    ptr(".musa_personal_freeze", "")
    assert R._musa_freeze_active(orch_dir) is True           # empty => no expiry => active
    ptr(".musa_personal_freeze", "not-a-timestamp")
    assert R._musa_freeze_active(orch_dir) is True           # unparseable => active


def test_no_freeze_flag_is_byte_identical_musa_stays_musa(orch, tmp_path, monkeypatch):
    """[back-compat] With NO .musa_personal_freeze flag, a lane that resolves to
    Musa stays Musa and a Syed lane stays Syed (byte-identical to pre-op#28857)."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")
    make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == musa


def test_freeze_active_musa_substitutes_syed(orch, tmp_path, monkeypatch):
    """Freeze active + a lane resolving to Musa -> the Syed key path beside it."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")
    syed = make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == syed


def test_freeze_active_group_pin_to_musa_substitutes_syed(orch, tmp_path, monkeypatch):
    """The guard applies to the FINAL resolved path: a per-GROUP pin to Musa is
    also redirected to Syed under an active freeze."""
    orch_dir, make_key, ptr = orch
    fleet = make_key("syed-oauth-token", "SYED")
    grp_musa = make_key("musa-oauth-token", "MUSA")
    ptr(".lane_default_token", fleet)
    ptr(".group_default_token.irsyad", grp_musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == fleet  # == the syed key


def test_freeze_active_syed_lane_unchanged(orch, tmp_path, monkeypatch):
    """Freeze active + a lane already resolving to Syed -> UNCHANGED."""
    orch_dir, make_key, ptr = orch
    syed = make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", syed)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == syed


def test_freeze_expired_musa_stays_musa(orch, tmp_path, monkeypatch):
    """A present-but-EXPIRED freeze flag => NOT active => Musa stays Musa."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")
    make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2000-01-01T00:00:00Z")
    assert R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir) == musa


def test_freeze_active_musa_no_syed_substitute_fails_closed(orch, tmp_path, monkeypatch):
    """Freeze active + Musa + the Syed substitute MISSING => FAIL-CLOSED: must NOT
    return the Musa path and must NOT return None (None => .env == Musa)."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")   # NO syed-oauth-token created
    ptr(".lane_default_token", musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    result = R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir)
    assert result != musa
    assert result is not None
    assert result == R._MUSA_FREEZE_REFUSE


def test_freeze_active_syed_substitute_mislabelled_fails_closed(orch, tmp_path, monkeypatch):
    """Freeze active + Musa + a syed-oauth-token that does NOT hash to syed =>
    FAIL-CLOSED (we never trust a mislabelled substitute, never fall to Musa)."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")
    make_key("syed-oauth-token", "NOT-REALLY-SYED")   # content won't map to syed
    ptr(".lane_default_token", musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    result = R.resolve_lane_token_path("irsyad-coord", orch_dir=orch_dir)
    assert result != musa
    assert result == R._MUSA_FREEZE_REFUSE


def test_freeze_active_none_default_is_musa_fails_closed(orch, tmp_path, monkeypatch):
    """A worker lane with NO pointer files resolves to None (=> the .env default,
    which IS Musa-personal). Under an active freeze that is ALSO a Musa boot; with
    no pointer there is no keys dir to locate a Syed key, so FAIL-CLOSED (refuse)
    rather than fall through to the .env Musa account."""
    orch_dir, make_key, ptr = orch
    make_key("syed-oauth-token", "SYED")   # present, but unreachable without a keys-dir pointer
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "")       # empty => active indefinitely
    result = R.resolve_lane_token_path("irsyad", orch_dir=orch_dir)
    assert result is not None
    assert result == R._MUSA_FREEZE_REFUSE


def test_freeze_does_not_touch_session_pointer_bodies_or_singletons(orch, tmp_path, monkeypatch):
    """The freeze is scoped to WORKER LANES. nazim/cc-orchestrator (console, reserved
    ON Musa by design) and the no-pointer singletons boot via their OWN launchers
    that do not consult this resolver's CLI, so substituting here would only create a
    display-vs-boot divergence. They resolve UNCHANGED even under an active freeze."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")
    make_key("syed-oauth-token", "SYED")
    ptr(".nazim_default_token", musa)
    ptr(".lane_default_token", musa)
    monkeypatch.setenv("TOKEN_FPS_MAP", _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"}))
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    assert R.resolve_lane_token_path("nazim", orch_dir=orch_dir) == musa          # console stays Musa
    assert R.resolve_lane_token_path("cai", orch_dir=orch_dir) is None            # no own pointer => None


def test_cli_shim_substitutes_syed_under_freeze(orch, tmp_path):
    """The bash rail (CLI shim) prints the SYED path under an active freeze+Musa."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")
    syed = make_key("syed-oauth-token", "SYED")
    ptr(".lane_default_token", musa)
    mp = _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"})
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    env = dict(os.environ, TOKEN_FPS_MAP=mp)
    out = subprocess.run(
        [sys.executable, "-m", "scripts.lib.lane_token_resolver",
         "--session", "irsyad-coord", "--orch-dir", orch_dir],
        capture_output=True, text=True, cwd=_repo_root(), env=env,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == syed


def test_cli_shim_refuses_with_exit_42_and_no_stdout_when_no_syed(orch, tmp_path):
    """FAIL-CLOSED on the rail: no Syed substitute => exit 42 + EMPTY stdout, so the
    launcher never gets a path to boot Musa on (it can honor 42 to abort)."""
    orch_dir, make_key, ptr = orch
    musa = make_key("musa-oauth-token", "MUSA")   # no syed
    ptr(".lane_default_token", musa)
    mp = _write_fp_map(tmp_path, {"musa": "MUSA", "syed": "SYED"})
    ptr(".musa_personal_freeze", "2999-01-01T00:00:00Z")
    env = dict(os.environ, TOKEN_FPS_MAP=mp)
    out = subprocess.run(
        [sys.executable, "-m", "scripts.lib.lane_token_resolver",
         "--session", "irsyad-coord", "--orch-dir", orch_dir],
        capture_output=True, text=True, cwd=_repo_root(), env=env,
    )
    assert out.returncode == 42
    assert out.stdout.strip() == ""

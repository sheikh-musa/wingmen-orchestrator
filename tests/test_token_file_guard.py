"""token_file_guard (orch-console #49107): a key file NAMED <acct>-oauth-token must hash to
<acct>'s fingerprint, or it is refused. Field case 2026-10-02: gzb ~/.wingmen/keys/musa-oauth-token
held the SYED token, so anything launched "as Musa" from it silently ran on Syed.

Fabricated tokens only. A test map (TOKEN_FPS_MAP) stands in for scripts/lib/token_fps.map.
Covers BOTH the bash helper (what launchers/boot scripts source) and the python helper
(weekly_limit_monitor), which must agree because they read the SAME map file."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.lib import token_file_guard as g  # noqa: E402

LIB_SH = REPO / "scripts" / "lib" / "token_file_guard.sh"
MUSA, SYED = "fake-musa-token-" + "A" * 40, "fake-syed-token-" + "B" * 40


def fp(t):
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def _setup(tmp_path):
    m = tmp_path / "fps.map"
    m.write_text("# test map\nmusa  %s\nsyed  %s\n" % (fp(MUSA), fp(SYED)))
    keys = tmp_path / "keys"; keys.mkdir()
    return m, keys


def _sh(path, mapfile):
    env = dict(os.environ, TOKEN_FPS_MAP=str(mapfile))
    return subprocess.run(["/bin/bash", "-c", 'set -uo pipefail; . "$1"; token_file_guard "$2"', "_",
                           str(LIB_SH), str(path)], capture_output=True, text=True, env=env)


def test_correctly_named_file_passes_both(tmp_path):
    m, keys = _setup(tmp_path)
    f = keys / "musa-oauth-token"; f.write_text(MUSA + "\n")       # trailing newline like real files
    assert _sh(f, m).returncode == 0
    assert g.check(str(f), map_path=str(m)) == (True, "")


def test_mislabelled_file_is_refused_with_self_explaining_message(tmp_path):
    m, keys = _setup(tmp_path)
    f = keys / "musa-oauth-token"; f.write_text(SYED + "\n")       # the gzb field case
    r = _sh(f, m)
    assert r.returncode != 0
    assert "fp map out of date" in r.stderr and "mislabelled" in r.stderr
    assert "(= syed)" in r.stderr                                   # names what it actually is, once
    assert SYED not in r.stderr + r.stdout                          # never prints the token
    ok, msg = g.check(str(f), map_path=str(m))
    assert not ok and "fp map out of date" in msg and "mislabelled" in msg and SYED not in msg


def test_unknown_fp_says_not_in_map(tmp_path):
    m, keys = _setup(tmp_path)
    f = keys / "musa-oauth-token"; f.write_text("fake-rotated-token-" + "C" * 40)
    r = _sh(f, m)
    assert r.returncode != 0 and "not in the map" in r.stderr


def test_unmapped_name_is_not_judged(tmp_path):
    m, keys = _setup(tmp_path)
    f = keys / "qwen-api-key"; f.write_text("whatever-value-123456")
    assert _sh(f, m).returncode == 0
    assert g.check(str(f), map_path=str(m))[0] is True


def test_symlink_named_for_one_account_pointing_at_another_is_refused(tmp_path):
    m, keys = _setup(tmp_path)
    real = keys / "syed-oauth-token"; real.write_text(SYED)
    link = keys / "musa-oauth-token"; link.symlink_to(real)
    assert _sh(link, m).returncode != 0
    assert g.check(str(link), map_path=str(m))[0] is False


def test_missing_map_fails_closed(tmp_path):
    _, keys = _setup(tmp_path)
    f = keys / "musa-oauth-token"; f.write_text(MUSA)
    assert _sh(f, tmp_path / "nope.map").returncode != 0
    assert g.check(str(f), map_path=str(tmp_path / "nope.map"))[0] is False


def test_unreadable_or_empty_token_file_fails(tmp_path):
    m, keys = _setup(tmp_path)
    f = keys / "musa-oauth-token"; f.write_text("")
    assert _sh(f, m).returncode != 0
    assert g.check(str(f), map_path=str(m))[0] is False


def test_real_map_is_the_one_place_and_well_formed():
    real = REPO / "scripts" / "lib" / "token_fps.map"
    lines = [l.split() for l in real.read_text().splitlines() if l.strip() and not l.startswith("#")]
    accts = {a for a, _ in lines}
    assert {"musa", "musa2", "syed"} <= accts
    assert all(len(f) == 12 and all(c in "0123456789abcdef" for c in f) for _, f in lines)
    assert "one place" in real.read_text().lower()


def test_every_token_reader_calls_the_guard():
    # every script that reads a key file into a token var must run it through the guard first
    for rel in ["scripts/launch_lane_as.sh", "scripts/launch_dangerous_cc.sh", "scripts/switch_lane_token.sh",
                "scripts/switch_singleton_token.sh", "scripts/flip_fleet.sh", "scripts/boot_orch.sh",
                "scripts/boot_cai.sh", "scripts/boot_fleet_health.sh", "scripts/boot_nazim.sh",
                "scripts/boot_quality.sh"]:
        src = (REPO / rel).read_text()
        assert "token_file_guard" in src, rel
    assert "token_file_guard" in (REPO / "nervous_system" / "weekly_limit_monitor.py").read_text()

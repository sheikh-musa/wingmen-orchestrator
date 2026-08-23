"""CAI-RESP-1308: the fail-loud, detect-only lane-DB residency guard.

Exercises the SHIPPED guard (scripts.lib.lane_db_residency_guard, imported — not a copy).
The launcher exports the BUS DATABASE_URL into every lane; if a lane's OWN repo declares a
DIFFERENT client DATABASE_URL (in .env.local/.env) the bus DB SHADOWS it → TENANT-RESIDENCY-001
commingling. The guard WARNS LOUDLY at boot but NEVER blocks the launch (detect-only), and
must NEVER print the secret URL values.

Run (from ~/wingmen/orchestrator): .venv/bin/python -m pytest tests/test_lane_db_residency_guard.py -q
"""
import subprocess
import sys
import pathlib

from scripts.lib import lane_db_residency_guard as g

BUS = "postgresql://bus_user:BUSSECRET@bus-host:5432/postgres"
CLIENT = "postgresql://client_user:CLIENTSECRET@client-host:5432/postgres"


def _write_env(tmp_path, fname, url):
    (tmp_path / fname).write_text(f"SOME_OTHER=1\nDATABASE_URL={url}\n")
    return tmp_path


def test_shadow_detected_when_lane_declares_different_db(tmp_path):
    _write_env(tmp_path, ".env.local", CLIENT)
    res = g.detect_shadow(str(tmp_path), BUS)
    assert res.shadowed is True
    assert res.env_file.endswith(".env.local")


def test_no_shadow_when_lane_db_matches_inherited(tmp_path):
    # An orchestrator lane legitimately using the bus DB — its declared == inherited.
    _write_env(tmp_path, ".env.local", BUS)
    assert g.detect_shadow(str(tmp_path), BUS).shadowed is False


def test_no_shadow_when_lane_declares_no_db(tmp_path):
    (tmp_path / ".env.local").write_text("SOME_OTHER=1\n")  # no DATABASE_URL
    assert g.detect_shadow(str(tmp_path), BUS).shadowed is False


def test_no_shadow_when_nothing_inherited(tmp_path):
    # If the bus URL isn't in the env, the lane's own is used — no shadow possible.
    _write_env(tmp_path, ".env.local", CLIENT)
    assert g.detect_shadow(str(tmp_path), None).shadowed is False


def test_env_local_precedence_and_export_and_quotes(tmp_path):
    # .env.local wins over .env; `export ` prefix + surrounding quotes are handled.
    (tmp_path / ".env").write_text('DATABASE_URL="%s"\n' % BUS)          # matches -> would be no shadow
    (tmp_path / ".env.local").write_text('export DATABASE_URL="%s"\n' % CLIENT)  # differs -> shadow
    res = g.detect_shadow(str(tmp_path), BUS)
    assert res.shadowed is True and res.env_file.endswith(".env.local")


def test_warning_text_never_leaks_secret_values(tmp_path):
    _write_env(tmp_path, ".env.local", CLIENT)
    res = g.detect_shadow(str(tmp_path), BUS)
    msg = g.warning_banner(res)
    assert "CLIENTSECRET" not in msg and "BUSSECRET" not in msg
    assert CLIENT not in msg and BUS not in msg
    assert "DATABASE_URL" in msg and "residency" in msg.lower()


def test_cli_never_blocks_and_warns_on_shadow(tmp_path):
    _write_env(tmp_path, ".env.local", CLIENT)
    root = pathlib.Path(__file__).resolve().parent.parent
    p = subprocess.run(
        [sys.executable, "-m", "scripts.lib.lane_db_residency_guard", "--repo-dir", str(tmp_path)],
        cwd=str(root), env={"DATABASE_URL": BUS, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True,
    )
    assert p.returncode == 0, "detect-only guard must NEVER block the launch"
    assert "residency" in p.stderr.lower() and "DATABASE_URL" in p.stderr
    assert "CLIENTSECRET" not in p.stderr and BUS not in p.stderr


def test_cli_silent_and_zero_when_no_shadow(tmp_path):
    (tmp_path / ".env.local").write_text("NOTHING=1\n")
    root = pathlib.Path(__file__).resolve().parent.parent
    p = subprocess.run(
        [sys.executable, "-m", "scripts.lib.lane_db_residency_guard", "--repo-dir", str(tmp_path)],
        cwd=str(root), env={"DATABASE_URL": BUS, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True,
    )
    assert p.returncode == 0
    assert "residency" not in p.stderr.lower()

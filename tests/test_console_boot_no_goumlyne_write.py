"""console #81299 (C) / op#21944: a console (boot_nazim) boot must not receive the
irsyad goumlyne live write DSN. Runs the real write-dsn slice of boot_nazim.sh in
bash against a fake store in a temp HOME; no real secrets are read."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOT = ROOT / "scripts" / "boot_nazim.sh"


def _slice() -> str:
    text = BOOT.read_text()
    m = re.search(r"# >>> write-dsn.*?\n(.*?)# <<< write-dsn", text, re.S)
    assert m, "boot_nazim.sh lost its write-dsn markers"
    return m.group(1)


def _run(tmp_path) -> dict:
    store = tmp_path / ".wingmen" / "private"
    store.mkdir(parents=True)
    (store / "write_dsn.env").write_text(
        'if [ "${WRITE_DSN_ALLOWED:-}" != "1" ]; then echo refused >&2; return 0; fi\n'
        "GOUMLYNE_DATABASE_URL=postgresql://fake-goumlyne\n"
        "IHSANOS_PROD_DATABASE_URL=postgresql://fake-ceayj\n"
    )
    script = _slice() + '\nprintf "G=%s\\nC=%s\\nW=%s\\n" "${GOUMLYNE_DATABASE_URL-UNSET}" "${IHSANOS_PROD_DATABASE_URL-UNSET}" "${WRITE_DSN_ALLOWED-UNSET}"\n'
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    out = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.strip().splitlines())


def test_console_boot_does_not_carry_goumlyne_write_dsn(tmp_path):
    got = _run(tmp_path)
    assert got["G"] == "UNSET"


def test_console_boot_keeps_other_silo_and_clears_sentinel(tmp_path):
    got = _run(tmp_path)
    assert got["C"] == "postgresql://fake-ceayj"
    assert got["W"] == "UNSET"


def test_boot_cai_is_unchanged_by_this_fix():
    # scope guard: only the console boot drops goumlyne; boot_cai is a separate decision
    assert "unset GOUMLYNE_DATABASE_URL" not in (ROOT / "scripts" / "boot_cai.sh").read_text()

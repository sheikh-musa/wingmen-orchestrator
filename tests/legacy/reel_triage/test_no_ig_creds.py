import pathlib
import subprocess

_ROOT = str(pathlib.Path(__file__).resolve().parents[2])


def test_no_instagram_credentials_in_repo():
    # Binding constraint (CAI-RESP-216): zero IG credentials anywhere. grep the
    # reel surfaces for credential patterns; must find nothing.
    res = subprocess.run(
        ["grep", "-rniE",
         r"(IG_PASSWORD|INSTAGRAM_PASSWORD|ig_username|instaloader|cookies-from-browser)",
         "--include=*.py", "--include=*.sql", "--include=*.plist", "--include=.env",
         f"{_ROOT}/reel_triage", f"{_ROOT}/migrations", f"{_ROOT}/ops"],
        capture_output=True, text=True)
    assert res.returncode != 0, f"IG credential pattern found:\n{res.stdout}"

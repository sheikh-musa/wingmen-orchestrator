"""test_cant_open_file_guard.py — the client-send guard against telling a
client "I can't open your file" / asking them to describe it (orch-console
bus #51060, Musa op#25437-25440).

Shells out to bash to source scripts/lib/cant_open_file_guard.sh and call the
function directly — no existing bash-test harness in this repo, so this is a
new-but-reasonable pattern (mirrors the sibling scripts/lib/client_send_leak_
guard.sh conceptually, just exercised from pytest instead of inline bash).
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

_GUARD = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "lib" / "cant_open_file_guard.sh"


def _run_guard(text: str) -> subprocess.CompletedProcess:
    script = f'source "{_GUARD}"; _cant_open_file_guard "$1"'
    return subprocess.run(["bash", "-c", script, "bash", text],
                           capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("text", [
    "Sorry, I can't open your file",
    "I cannot open the attachment you sent",
    "we're unable to open the spreadsheet",
    "I can't read your document, can you describe what it shows?",
    "Can't access the file right now",
    "could you describe it for me instead",
    # cc-quality bus #51094 BLOCKING #1: "cannot"/"unable to" paired with
    # read/access/view, not just "open" -- the earlier form missed all 4.
    "cannot read your file",
    "cannot access the attachment",
    "unable to read this document",
    "unable to access your spreadsheet",
])
def test_blocked_phrases_refused(text):
    r = _run_guard(text)
    assert r.returncode == 5
    assert "never open a client" in r.stderr.lower() or "stage" in r.stderr.lower()


@pytest.mark.parametrize("text", [
    "thanks for the file, I'll take a look and get back to you",
    "your payment has been received",
    "please resend the file as a CSV, the current format is corrupted on our end",
    "we've staged your file and will follow up shortly",
])
def test_normal_text_passes(text):
    r = _run_guard(text)
    assert r.returncode == 0
    assert r.stderr == ""


def test_case_insensitive():
    r = _run_guard("I CAN'T OPEN YOUR FILE")
    assert r.returncode == 5

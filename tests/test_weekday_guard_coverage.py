"""test_weekday_guard_coverage.py — EVERY outbound sender goes through the weekday/date guard.

Follow-up to PR#331 (orch-console #58047). A guard wired into a hand-picked list of
senders certifies only what its author remembered (see check_send_paths_report_failure.py's
history), so coverage is DISCOVERED here, not enumerated: every scripts/*_send*.sh plus the
other free-text send entrypoints must either
  (a) source scripts/lib/date_weekday_guard.sh and call _date_weekday_guard BEFORE its first
      send line, or
  (b) send ONLY through a guarded shared path (_tg_chunked_send.py / lib/tg_group_send.py)
      and never hit the Telegram API directly.
The shared paths themselves are held to refusing (behavioural tests below, no DB, no send).
"""
import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))

# Send entrypoints outside the *_send*.sh glob that carry free (agent-written) text.
EXTRA_ENTRYPOINTS = ("lane_reply.sh", "nazim_say.sh", "dev_group_edit.sh")

# Senders that genuinely carry no free text — name -> one-line reason. Keep it EMPTY unless
# that is true; every entry must still exist (no stale allowlist rows).
ALLOWLIST: dict[str, str] = {}

GUARDED_SHARED = ("_tg_chunked_send.py", "tg_group_send.py")
_SEND_LINE = re.compile(r"curl\s|api\.telegram\.org|_tg_chunked_send\.py|tg_group_send\.py|urlopen")
_GUARD_CALL = re.compile(r"^\s*_date_weekday_guard\s+\"")


def _senders():
    found = sorted(p.name for p in SCRIPTS.glob("*_send*.sh"))
    return found + [n for n in EXTRA_ENTRYPOINTS if n not in found]


def _code_lines(src):
    return [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]


def test_discovery_finds_the_known_senders():
    names = _senders()
    for must in ("tg_send.sh", "nazim_send.sh", "angullia_send.sh", "hk_send.sh",
                 "tg_send_file.sh", "oeh_send_photo.sh", "lane_reply.sh"):
        assert must in names
    assert len(names) >= 25


def test_allowlist_entries_exist_and_have_reasons():
    for name, reason in ALLOWLIST.items():
        assert (SCRIPTS / name).exists(), f"stale allowlist entry {name}"
        assert reason.strip(), f"allowlist entry {name} needs a reason"


@pytest.mark.parametrize("name", _senders())
def test_every_sender_is_guarded(name):
    if name in ALLOWLIST:
        pytest.skip(ALLOWLIST[name])
    src = (SCRIPTS / name).read_text(errors="ignore")
    code = _code_lines(src)
    sends = [i for i, ln in enumerate(code) if _SEND_LINE.search(ln)]
    assert sends, f"{name}: no send line found — is it still a sender? (allowlist with a reason)"
    calls = [i for i, ln in enumerate(code) if _GUARD_CALL.search(ln)]
    sources = any("lib/date_weekday_guard.sh" in ln and ln.lstrip().startswith("source")
                  for ln in code)
    if sources and calls:
        assert calls[0] < sends[0], (
            f"{name}: _date_weekday_guard is called AFTER the first send line")
        return
    direct = [ln for ln in code if re.search(r"curl\s|api\.telegram\.org|urlopen", ln)]
    via_shared = any(s in ln for ln in code for s in GUARDED_SHARED)
    assert via_shared and not direct, (
        f"{name}: sends without the weekday/date guard. Add\n"
        '  source "$ORCH_DIR/scripts/lib/date_weekday_guard.sh"\n'
        '  _date_weekday_guard "$TEXT" || exit 6\n'
        "before the send (guard captions too), or route through _tg_chunked_send.py.")


# ---- the shared paths refuse (behavioural, no DB, nothing sent) ------------

BAD = "Kickoff is Thursday 9 October 2026, see you there."


def test_chunked_send_refuses_mismatch_and_never_posts(monkeypatch, tmp_path):
    m = importlib.import_module("scripts._tg_chunked_send")
    posted = []
    monkeypatch.setattr(m, "send_with_resilience", lambda *a, **k: posted.append(a) or {"ok": True})
    monkeypatch.setattr(m, "duplicate_of", lambda chat, text: None)
    fail = tmp_path / "fail.json"
    for k, v in {"TG_TOK": "tok", "TG_CHAT": "chat", "TG_TEXT": BAD, "TG_FAIL_OUT": str(fail)}.items():
        monkeypatch.setenv(k, v)
    assert m.main() == 1
    assert posted == []
    assert "weekday" in fail.read_text()


def test_chunked_send_passes_clean_text(monkeypatch):
    m = importlib.import_module("scripts._tg_chunked_send")
    posted = []
    monkeypatch.setattr(m, "send_with_resilience",
                        lambda *a, **k: posted.append(a) or {"ok": True, "message_id": 1})
    monkeypatch.setattr(m, "duplicate_of", lambda chat, text: None)
    for k, v in {"TG_TOK": "tok", "TG_CHAT": "chat", "TG_TEXT": "Friday 9 October 2026"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TG_MSGID_OUT", raising=False)
    assert m.main() == 0
    assert len(posted) == 1


def test_chunked_send_fails_closed_if_guard_cannot_load(monkeypatch, tmp_path):
    m = importlib.import_module("scripts._tg_chunked_send")
    posted = []
    monkeypatch.setattr(m, "send_with_resilience", lambda *a, **k: posted.append(a) or {"ok": True})
    monkeypatch.setattr(m, "duplicate_of", lambda chat, text: None)
    monkeypatch.setattr(m, "_GUARD_PATH", tmp_path / "missing.py")
    for k, v in {"TG_TOK": "tok", "TG_CHAT": "chat", "TG_TEXT": "Friday 9 October 2026"}.items():
        monkeypatch.setenv(k, v)
    assert m.main() == 1
    assert posted == []


def test_tg_group_send_refuses_before_db(monkeypatch):
    tg = importlib.import_module("scripts.lib.tg_group_send")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-connect.invalid/x")
    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("connected to DB"))
    assert tg.main(["some-channel", BAD]) == 6


def test_tg_out_enqueue_refuses_before_db(monkeypatch):
    tg_out = importlib.import_module("nervous_system.tg_out")
    monkeypatch.setattr(tg_out, "_dsn", lambda: pytest.fail("touched the DB"))
    with pytest.raises(tg_out.DateWeekdayRefusal):
        tg_out.enqueue("operator-orch", BAD)

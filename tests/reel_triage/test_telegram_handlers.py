import pytest

from reel_triage import telegram_handlers as th


class FakeMsg:
    def __init__(self, text=None, doc=None):
        self.text, self.document = text, doc
        self.replies = []

    async def reply_text(self, t, **k):
        self.replies.append(t)


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeUpdate:
    def __init__(self, uid, text=None):
        self.effective_user = FakeUser(uid)
        self.message = FakeMsg(text=text)


@pytest.mark.asyncio
async def test_ingest_rejects_non_musa_id(reel_db, monkeypatch):
    monkeypatch.setattr(th, "MUSA_TG_ID", 111)
    monkeypatch.setattr(th, "_conn", lambda: reel_db)
    monkeypatch.setattr(th.config, "reel_triage_enabled", lambda: True)
    upd = FakeUpdate(uid=999, text="https://instagram.com/reel/Z")
    handled = await th.handle_message(upd, ctx=None)
    assert handled is False             # falls through to normal handlers
    assert upd.message.replies == []   # silent ignore for non-verified id
    assert reel_db.cursor().execute(
        "select count(*) from reel_inbox").fetchone()["count"] == 0


@pytest.mark.asyncio
async def test_ingest_link_reports_counts(reel_db, monkeypatch):
    monkeypatch.setattr(th, "MUSA_TG_ID", 111)
    monkeypatch.setattr(th, "_conn", lambda: reel_db)
    monkeypatch.setattr(th.config, "reel_triage_enabled", lambda: True)
    upd = FakeUpdate(uid=111, text="save https://instagram.com/reel/Z")
    handled = await th.handle_message(upd, ctx=None)
    assert handled is True              # reel message -> stops propagation
    assert any("1" in r for r in upd.message.replies)   # applied=1 surfaced


@pytest.mark.asyncio
async def test_musa_non_reel_text_falls_through(reel_db, monkeypatch):
    monkeypatch.setattr(th, "MUSA_TG_ID", 111)
    monkeypatch.setattr(th, "_conn", lambda: reel_db)
    monkeypatch.setattr(th.config, "reel_triage_enabled", lambda: True)
    upd = FakeUpdate(uid=111, text="just a normal brainstorm message")
    handled = await th.handle_message(upd, ctx=None)
    assert handled is False             # no IG link -> normal chat handler runs
    assert upd.message.replies == []

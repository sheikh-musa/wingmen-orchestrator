"""'Always know who's speaking' (bus #47892, orch-console #47912 point 2):
identity no longer lands in the substrate's operator_messages row for the
mamadah tag (PR #240 HIGH fix), so scripts/mamadah_reconcile.py must resolve
the speaker from wingmen-personal's real from_user_id instead. One synthetic
case per known identity plus the unknown fallback, per orch-console's
explicit mapping (286619815->Musa, 6606903261->Zahidah, else->unknown)."""
import importlib

mr = importlib.import_module("scripts.mamadah_reconcile")


def test_musa_id_resolves_to_musa():
    assert mr._mamadah_sender_label("286619815") == "Musa"


def test_zahidah_id_resolves_to_zahidah():
    assert mr._mamadah_sender_label("6606903261") == "Zahidah"


def test_unknown_id_resolves_to_unknown():
    assert mr._mamadah_sender_label("999999") == "unknown"


def test_missing_id_resolves_to_unknown():
    assert mr._mamadah_sender_label(None) == "unknown"


def test_int_type_id_matches_same_as_str(monkeypatch):
    # wingmen-personal's PostgREST JSON can hand back from_user_id as either a
    # string or a number depending on how it was stored — the comparison must
    # not be type-sensitive.
    assert mr._mamadah_sender_label(286619815) == "Musa"
    assert mr._mamadah_sender_label(6606903261) == "Zahidah"


def test_unprocessed_resolves_identity_from_personal_row_not_substrate(monkeypatch):
    """The substrate row's from_user_id is sentinel-NULL by design for this
    tag — unprocessed() must pull identity from the wingmen-personal row
    (personal_routing.read_personal_content), never from the substrate
    columns it no longer even selects."""
    class _FakeCur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [(1, "mamadah", "2026-10-01T00:00:00+00:00", "286619815")]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mr.psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        mr.personal_routing, "read_personal_content",
        lambda ids: {1: {"text": "real text from Zahidah", "from_user_id": "6606903261"}},
    )

    rows = mr.unprocessed("mamadah")
    assert len(rows) == 1
    rid, tag, text, created_at, sender, source = rows[0]
    assert text == "real text from Zahidah"
    assert sender == "Zahidah"

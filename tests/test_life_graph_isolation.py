"""Isolation proof-test for the life_graph P1 draft (CAI-RESP-336 condition 1 + 2 + 5).

The gate: a partner/vendor/agent slice CANNOT retrieve a personal entity — proven,
not asserted. Runs against an EPHEMERAL dry-run DB (CAI-RESP-356: never prod first):

    LIFE_GRAPH_TEST_DSN=postgresql://... pytest tests/test_life_graph_isolation.py

Skips cleanly when LIFE_GRAPH_TEST_DSN is unset (normal CI runs). The harness
applies migrations/drafts/life_graph_p1.sql to the ephemeral DB, seeds one entity
per sensitivity tier, then attacks every read path as a non-operator slice.
"""
import os
import pathlib

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("LIFE_GRAPH_TEST_DSN")
DRAFT = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "drafts" / "life_graph_p1.sql"

pytestmark = pytest.mark.skipif(not DSN, reason="LIFE_GRAPH_TEST_DSN not set (ephemeral dry-run only)")

# 1024-dim zero-ish vector literal for seeding/querying (BGE-M3 dims)
VEC = "[" + ",".join(["0.01"] * 1024) + "]"


@pytest.fixture(scope="module")
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Ephemeral (non-Supabase) instances lack the platform roles the draft
            # REVOKEs from — create them so the draft applies byte-identically.
            for role in ("anon", "authenticated"):
                cur.execute(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
            cur.execute(DRAFT.read_text())
            # Seed: one entity per tier. fleet + partner are slice-scoped; personal
            # and intimate are not (guard enforces intimate can't be).
            cur.execute(
                """
                INSERT INTO life_entities (entity_type, name, attributes, sensitivity, scope_slices, embedding, embed_text)
                VALUES
                  ('project','fleet-thing','{"note":"fleet"}','fleet','{gazzabyte,shen}', %(v)s::vector,'fleet-thing'),
                  ('person','partner-thing','{"note":"partner"}','partner','{shen}', %(v)s::vector,'partner-thing'),
                  ('person','personal-thing','{"note":"personal"}','personal','{}', %(v)s::vector,'personal-thing')
                """,
                {"v": VEC},
            )
            # personal→fleet edge, to prove edges never leak hidden endpoints
            cur.execute(
                """
                INSERT INTO life_relationships (from_entity, to_entity, rel_type)
                SELECT p.id, f.id, 'is-on' FROM life_entities p, life_entities f
                WHERE p.name='personal-thing' AND f.name='fleet-thing'
                """
            )
        yield conn


def _recall_names(conn, slice_):
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM life_recall(%s::vector, %s, 50)", (VEC, slice_))
        return {r[0] for r in cur.fetchall()}


def test_partner_slice_cannot_see_personal(db):
    names = _recall_names(db, "shen")
    assert "personal-thing" not in names
    assert names == {"fleet-thing", "partner-thing"}


def test_vendor_slice_sees_only_explicit_grants(db):
    names = _recall_names(db, "gazzabyte")
    assert names == {"fleet-thing"}  # partner-thing not granted to gazzabyte


def test_unknown_and_empty_slice_see_nothing(db):
    assert _recall_names(db, "some-new-agent") == set()
    assert _recall_names(db, "") == set()
    assert _recall_names(db, None) == set()


def test_operator_sees_all_embedded(db):
    assert _recall_names(db, "operator") == {"fleet-thing", "partner-thing", "personal-thing"}


def test_edges_never_reveal_hidden_endpoints(db):
    with db.cursor() as cur:
        # From the visible fleet node, a vendor slice must NOT see the edge to the
        # personal node (edge visible only if BOTH endpoints visible).
        cur.execute(
            "SELECT entity_name FROM life_neighbours((SELECT id FROM life_entities WHERE name='fleet-thing'), 'gazzabyte')"
        )
        assert cur.fetchall() == []
        # Operator sees it.
        cur.execute(
            "SELECT entity_name FROM life_neighbours((SELECT id FROM life_entities WHERE name='fleet-thing'), 'operator')"
        )
        assert {r[0] for r in cur.fetchall()} == {"personal-thing"}


def test_intimate_guard_rejects_plaintext_and_scoping(db):
    with db.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "INSERT INTO life_entities (entity_type, name, attributes, sensitivity) "
                "VALUES ('person','x','{\"secret\":1}','intimate')"
            )
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "INSERT INTO life_entities (entity_type, name, sensitivity, scope_slices) "
                "VALUES ('person','x','intimate','{shen}')"
            )
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "INSERT INTO life_entities (entity_type, name, sensitivity, embedding, embed_text) "
                "VALUES ('person','x','intimate', %s::vector, 'leak')",
                (VEC,),
            )
        # ciphertext-only intimate row is accepted
        cur.execute(
            "INSERT INTO life_entities (entity_type, name, sensitivity, attributes_enc) "
            "VALUES ('person','intimate-ok','intimate', '\\xdeadbeef'::bytea)"
        )


def test_anon_role_denied_direct_table_access(db):
    # F4 (CAI-RESP-360): SET LOCAL ROLE on an autocommit connection is a warned
    # no-op (the SELECT would run as owner). Use a REAL transaction so the role
    # switch is in effect for the probe, for both platform roles.
    for role in ("anon", "authenticated"):
        with db.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == role  # prove the switch actually happened
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT * FROM life_entities")
            cur.execute("ROLLBACK")


def test_isolation_audit_clean_on_valid_seed(db):
    with db.cursor() as cur:
        cur.execute("SELECT violation FROM life_isolation_audit()")
        assert cur.fetchall() == []

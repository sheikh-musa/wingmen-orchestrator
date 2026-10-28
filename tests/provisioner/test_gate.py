"""Gate binding (plan §4/§9): a provision verb may only run against a real
orch-console/cai GO, and — STRENGTHENED beyond exec_broker's require_gate —
the gate row must mention the store ref, the client_key, AND the rendered
bundle's sha256. That last bind is the anti-drift control: a GO written
before a spec or template change can no longer authorize the re-rendered
artifact, because the bytes are different.

One-artifact-per-gate (a second application citing the same GO must refuse)
is enforced by the access ledger at execution time, not here — this module
only sees the bus row.
"""
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ORCH_DIR / "scripts" / "lib"))

from provisioner import gate as gate_mod  # noqa: E402
from provisioner.registry import Refuse, store_ref  # noqa: E402
from provisioner.render import Bundle  # noqa: E402
from provisioner.spec import ClientSpec  # noqa: E402


def _spec() -> ClientSpec:
    return ClientSpec(
        client_key="acme",
        alias="ihsanos multi-tenant DB",
        org_uuid="61636d65-7100-4000-8000-000000000001",
        org_name="ACME Services Pte. Ltd.",
        org_uen="202219999Z",
        org_slug="acme-services",
        seed_keys=("site", "hero"),
    )


_SHA = "a" * 64


def _bundle(sha=_SHA, silo_ref=None) -> Bundle:
    # a Bundle stub carrying just what the gate binds to; silo_ref comes from
    # the registry (never a literal — test_registry pins that rule for code)
    return Bundle(name="acme-ceayj", silo_ref=silo_ref or store_ref("ihsanos multi-tenant DB"),
                  forward_sql="-- forward", revert_sql="-- revert", sha256=sha)


class FakeRow(dict):
    pass


class FakeCursor:
    """dict_row-shaped cursor: one canned row, execute captured."""

    def __init__(self, row):
        self.row = row
        self.executed = None

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchone(self):
        return self.row


def _go_row(**overrides):
    row = FakeRow(
        from_agent="orch-console",
        message_type="decision",
        subject="GO: provision acme tenant",
        body=f"approved bundle {store_ref('ihsanos multi-tenant DB')} sha256 {_SHA}",
        is_past=True,
    )
    row.update(overrides)
    return row


def _call(row, gate_id="78463", bundle=None, verb="provision"):
    return gate_mod.require_provision_gate(gate_id, spec=_spec(),
                                           bundle=bundle or _bundle(), verb=verb,
                                           cur=FakeCursor(row))


def test_gate_happy_path_reads_the_bus_row():
    cur = FakeCursor(_go_row())
    gate_mod.require_provision_gate("78463", spec=_spec(), bundle=_bundle(),
                                    verb="provision", cur=cur)
    sql, params = cur.executed
    assert "FROM agent_messages WHERE id = %s" in sql
    assert params == (78463,)
    assert "is_past" in sql


def test_gate_accepts_the_alias_instead_of_the_raw_ref():
    # mirrors exec_broker _text_mentions_silo: ref OR its alias, whole-word
    row = _go_row(body=f"approved bundle ihsanos multi-tenant DB sha256 {_SHA}")
    _call(row)  # no raise


def test_gate_refuses_wrong_shape_row():
    for overrides in (
        dict(from_agent="cc-bariq"),                      # not console/cai
        dict(message_type="update"),                      # not decision/agreed
        dict(is_past=False),                              # created in the future
    ):
        with pytest.raises(Refuse):
            _call(_go_row(**overrides))
    with pytest.raises(Refuse):  # id not on the bus
        _call(None)
    with pytest.raises(Refuse):  # malformed gate id
        _call(_go_row(), gate_id="GO-78463")
    with pytest.raises(Refuse):  # missing gate id
        _call(_go_row(), gate_id="")


def test_gate_must_mention_the_client_key():
    # the GO names the store and the sha but never the client — a GO for a
    # DIFFERENT client on the same shared store must not authorize acme
    ref = store_ref("ihsanos multi-tenant DB")
    row = _go_row(subject="GO: provision another client",
                  body=f"approved bundle {ref} sha256 {_SHA}")
    with pytest.raises(Refuse, match="client_key"):
        _call(row)
    # and the mention must be a whole word, not buried inside another word
    row = _go_row(subject="GO",
                  body=f"approved bundle {ref} acmeCLIENT sha256 {_SHA}")
    with pytest.raises(Refuse, match="client_key"):
        _call(row)


def test_gate_must_mention_this_bundle_sha256():
    # the anti-drift bind: same client, same store, but the artifact bytes
    # changed (spec/template edit after the GO was written) — refuse
    ref = store_ref("ihsanos multi-tenant DB")
    with pytest.raises(Refuse, match="sha256"):
        _call(_go_row(body=f"approved bundle {ref} acme sha256 " + "b" * 64))
    # a stale-GO replay with the sha truncated to a prefix does not match
    with pytest.raises(Refuse, match="sha256"):
        _call(_go_row(body=f"approved bundle {ref} acme sha256 {_SHA[:16]}"))


def test_gate_refuses_when_the_store_is_not_mentioned():
    # names client + sha but never the store (ref or alias) — a GO that
    # doesn't name WHERE the bundle lands authorizes nothing
    row = _go_row(subject="GO", body=f"approved acme bundle sha256 {_SHA}")
    with pytest.raises(Refuse, match="silo|store|mention"):
        _call(row)


def test_gate_binds_the_verb():
    # cc-quality BLOCKER 1 (PR#566 review): a provision GO must not
    # authorize a REVERT — revert drops both tables, drops the role, and
    # deletes the org row. Same store, same client, same forward-sha, so
    # only a VERB mention separates them. One test per direction.
    ref = store_ref("ihsanos multi-tenant DB")
    bundle = _bundle()
    provision_go = _go_row(subject="GO: provision acme tenant",
                           body=f"approved bundle {ref} acme sha256 {_SHA}")
    revert_go = _go_row(subject="GO: revert acme tenant",
                        body=f"approved revert of bundle {ref} acme sha256 {_SHA}")
    # each verb passes under its own GO ...
    _call(provision_go, verb="provision", bundle=bundle)
    _call(revert_go, verb="revert", bundle=bundle)
    # ... and refuses under the OTHER verb's GO
    with pytest.raises(Refuse, match="verb"):
        _call(provision_go, verb="revert", bundle=bundle)
    with pytest.raises(Refuse, match="verb"):
        _call(revert_go, verb="provision", bundle=bundle)

"""Static guards for the irsyad_monitor_ro migration + its QA proof (console #81213 item 4, #82720).

No DB. Keeps the QA proof exercising the REAL migration text, and pins the shape the gated exec
path needs (no top-level transaction control, no backslashes, never a password literal).
"""
from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "migrations" / "infra"
MIG = INFRA / "irsyad_monitor_ro_goumlyne.sql"
PROOF = INFRA / "irsyad_monitor_ro_qa_proof.sql"
IRSYAD_ORG = "73339164-7c1f-40ba-a093-33f1f292dd4c"
TABLES = ["audit_log", "persons", "sch_students", "sch_student_parents",
          "tabung_coin_deposits", "ui_events", "org_members"]


def _code(sql: str) -> str:
    """SQL with -- comments stripped (good enough: no string in these files contains '--')."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_proof_includes_migration_verbatim():
    mig = MIG.read_text()
    proof = PROOF.read_text()
    begin = "-- ===== BEGIN-INCLUDE irsyad_monitor_ro_goumlyne.sql =====\n"
    end = "-- ===== END-INCLUDE irsyad_monitor_ro_goumlyne.sql ====="
    assert begin in proof and end in proof
    included = proof.split(begin, 1)[1].split(end, 1)[0]
    assert included == mig, "QA proof section (a) drifted from irsyad_monitor_ro_goumlyne.sql; re-sync it"


def test_no_backslash_no_txn_control_no_password():
    for f in (MIG, PROOF):
        sql = f.read_text()
        assert "\\" not in sql, f"{f.name}: backslash present"
        code = _code(sql)
        assert not re.search(r"^\s*(BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\s*;", code, re.I | re.M), \
            f"{f.name}: top-level transaction control"
        assert not re.search(r"\bPASSWORD\b", code, re.I), f"{f.name}: PASSWORD in executable SQL"
        assert not re.search(r"\bLOGIN\b", code.replace("NOLOGIN", ""), re.I), \
            f"{f.name}: role must be created NOLOGIN (LOGIN is set later by the secrets holder)"


def test_grants_and_policy_pairs():
    code = _code(MIG.read_text())
    granted = set(re.findall(r"GRANT SELECT ON TABLE public\.(\w+)\s+TO irsyad_monitor_ro", code))
    assert granted == set(TABLES)
    assert not re.search(r"GRANT\s+pg_\w+", code, re.I), "must not grant any predefined role"
    assert not re.search(r"GRANT\s+(INSERT|UPDATE|DELETE|ALL)", code, re.I)
    for t in TABLES:
        for kind in ("PERMISSIVE", "RESTRICTIVE"):
            pat = (rf"CREATE POLICY irsyad_monitor_ro_org_{kind.lower()}\s+ON public\.{t} AS {kind}\s+"
                   rf"FOR SELECT TO irsyad_monitor_ro\s+USING \(([^;]*)\);")
            m = re.search(pat, code)
            assert m, f"missing {kind} policy on {t}"
            assert IRSYAD_ORG in m.group(1)
    for fn in ("auth_nonadmin_staff_student_person_ids", "auth_subadmin_student_person_ids",
               "auth_user_subadmin_org_ids"):
        assert f"GRANT EXECUTE ON FUNCTION public.{fn}()" in code
    assert "GRANT USAGE ON SCHEMA auth TO irsyad_monitor_ro" in code


def test_proof_ends_in_rollback_raise():
    code = _code(PROOF.read_text())
    assert "RAISE EXCEPTION 'QA_PROOF_RESULT %'" in code
    # the QA-only guard runs before the included migration creates anything
    assert code.index("QA_PROOF_RESULT ABORT") < code.index("CREATE ROLE irsyad_monitor_ro")

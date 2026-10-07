"""test_client_artifact_scan.py — output-tree scanner for files bound for a client.

Real D1/D2 cases from reports/fable-audit-substrate-20261006/E-client-lanes-quality.md
§1: D1 is the "undefinedp" x4 placeholder leak into a client Overview workbook
(#54400/#54497); D2 is the `op#26543` fleet bus id leaked into 4 client docs by a C3
render whose vocab scan covered src/ only (#54497 self-catch). Both are reproduced
here against a REAL rendered .xlsx / .docx, not a text-only approximation, per the
bus #58159 item-3 instruction to test against the real cases. PURE-LOGIC + synthetic
fixtures only — no client data, no network, no DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.lib import client_artifact_scan as cas  # noqa: E402

# ── scan_text: placeholder (D1) ───────────────────────────────────────────────


def test_scan_text_finds_the_real_d1_undefinedp_leak():
    text = "Period-Accounting: HR block undefinedp, BSM block undefinedp"
    findings = cas.scan_text(text, source="Overview!C12")
    cats = [f.category for f in findings]
    assert "placeholder" in cats


def test_scan_text_finds_null_and_nan_and_object():
    assert cas.scan_text("value: null")[0].category == "placeholder"
    assert cas.scan_text("ratio: NaN%")[0].category == "placeholder"
    assert cas.scan_text("owner: [object Object]")[0].category == "placeholder"


def test_scan_text_clean_prose_has_no_placeholder_finding():
    findings = cas.scan_text("The plan is final and the totals reconcile.")
    assert findings == []


# ── scan_text: fleet vocabulary (D2) ──────────────────────────────────────────


def test_scan_text_finds_the_real_d2_op_ref_leak():
    text = "Rendered by op#26543 for the Overview tab"
    findings = cas.scan_text(text, source="document body")
    cats = [f.category for f in findings]
    assert "fleet-vocab:op-ref" in cats


def test_scan_text_finds_bare_bus_id_but_not_when_prefixed_by_op():
    bare = cas.scan_text("see #54659 for context")
    assert any(f.category == "fleet-vocab:bare-bus-id" for f in bare)
    opref = cas.scan_text("see op#54659 for context")
    assert not any(f.category == "fleet-vocab:bare-bus-id" for f in opref)
    assert any(f.category == "fleet-vocab:op-ref" for f in opref)


def test_scan_text_finds_cc_agent_cai_console_and_internal_terms():
    assert any(f.category == "fleet-vocab:cc-agent" for f in cas.scan_text("ask cc-cosem about this"))
    assert any(f.category == "fleet-vocab:cai-ref" for f in cas.scan_text("gated per CAI-898"))
    assert any(
        f.category == "fleet-vocab:orch-console-or-nazim" for f in cas.scan_text("reviewed by orch-console")
    )
    assert any(f.category == "fleet-vocab:orch-console-or-nazim" for f in cas.scan_text("ask Nazim"))
    assert any(f.category == "fleet-vocab:internal-term" for f in cas.scan_text("wet-proof this first"))
    assert any(f.category == "fleet-vocab:internal-term" for f in cas.scan_text("the silo holds this client"))
    assert any(f.category == "fleet-vocab:internal-term" for f in cas.scan_text("the lane shipped it"))
    assert any(f.category == "fleet-vocab:internal-term" for f in cas.scan_text("logged as a bus row"))


def test_scan_text_clean_client_prose_has_no_fleet_vocab_finding():
    findings = cas.scan_text("The finance team approved the plan for Q4.")
    assert findings == []


# ── scan_text: stale PROPOSED label (synthetic -- no D-case in E §1) ─────────


def test_scan_text_flags_proposed_marker_when_doc_claims_applied():
    text = "Status: Applied\n\nItem 3: PROPOSED change to the schedule"
    findings = cas.scan_text(text)
    assert any(f.category == "stale-proposed" for f in findings)


def test_scan_text_does_not_flag_proposed_alone_without_an_applied_label():
    findings = cas.scan_text("Item 3: PROPOSED change to the schedule")
    assert not any(f.category == "stale-proposed" for f in findings)


# ── scan_file: real rendered .xlsx (D1) and .docx (D2) ────────────────────────


def test_scan_file_catches_d1_in_a_real_rendered_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Overview"
    ws["A1"] = "Period-Accounting"
    ws["B1"] = "HR block undefinedp"
    ws["B2"] = "BSM block undefinedp"
    path = tmp_path / "C3_Overview.xlsx"
    wb.save(str(path))

    findings = cas.scan_file(path)
    assert any(f.category == "placeholder" for f in findings)
    assert any("Overview!B1" == f.source or "Overview!B2" == f.source for f in findings)


def test_scan_file_catches_d2_in_a_real_rendered_docx(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Client Overview")
    d.add_paragraph("Rendered by op#26543")
    path = tmp_path / "C3_FIF.docx"
    d.save(str(path))

    findings = cas.scan_file(path)
    assert any(f.category == "fleet-vocab:op-ref" for f in findings)


def test_scan_file_on_a_clean_rendered_xlsx_finds_nothing(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Period-Accounting"
    ws["B1"] = "HR block 42 periods"
    path = tmp_path / "clean.xlsx"
    wb.save(str(path))

    assert cas.scan_file(path) == []


def test_scan_file_on_a_clean_rendered_docx_finds_nothing(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Final report for the client.")
    path = tmp_path / "clean.docx"
    d.save(str(path))

    assert cas.scan_file(path) == []


def test_scan_file_plain_text_dirty_and_clean(tmp_path):
    dirty = tmp_path / "notes.md"
    dirty.write_text("value came back undefined for this row")
    assert any(f.category == "placeholder" for f in cas.scan_file(dirty))

    clean = tmp_path / "notes2.md"
    clean.write_text("All totals reconcile for this period.")
    assert cas.scan_file(clean) == []


def test_scan_file_unknown_binary_extension_is_not_scanned(tmp_path):
    png = tmp_path / "chart.png"
    png.write_bytes(b"not a real png but op#26543 undefined")
    assert cas.scan_file(png) == []


# ── assert_clean + CLI ────────────────────────────────────────────────────────


def test_assert_clean_raises_on_a_dirty_file(tmp_path):
    f = tmp_path / "dirty.md"
    f.write_text("rendered value: undefined")
    with pytest.raises(cas.ClientArtifactViolation):
        cas.assert_clean(f)


def test_assert_clean_passes_silently_on_a_clean_file(tmp_path):
    f = tmp_path / "clean.md"
    f.write_text("All good.")
    cas.assert_clean(f)  # must not raise


def test_main_cli_returns_1_and_prints_refused_on_a_dirty_file(tmp_path, capsys):
    f = tmp_path / "dirty.md"
    f.write_text("owner: [object Object]")
    rc = cas.main([str(f)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "REFUSED:" in captured.err


def test_main_cli_returns_0_on_a_clean_file(tmp_path, capsys):
    f = tmp_path / "clean.md"
    f.write_text("All good.")
    rc = cas.main([str(f)])
    assert rc == 0


def test_main_cli_returns_2_when_file_missing(tmp_path, capsys):
    rc = cas.main([str(tmp_path / "nope.md")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not found" in captured.err

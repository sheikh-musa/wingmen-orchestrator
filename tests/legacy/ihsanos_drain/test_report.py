from legacy.ihsanos_drain.report import build_report_row
import pytest

pytestmark = pytest.mark.skip(reason="op#19103 item 4: retired with wingmen_orch.py, see legacy/README.md")



def test_report_row_is_substrate_with_prefixed_subtag():
    row = build_report_row(summary="polled 3, 0 granted", report_only=True)
    assert row["from_agent"] == "substrate"
    assert row["sub_tag"] == "substrate-ihsanos-drain"
    assert row["to_agent"] == "cai"
    assert row["message_type"] == "update"
    assert row["requires_response"] is False
    assert row["subject"].startswith("[REPORT-ONLY]")


def test_live_report_has_no_report_only_prefix():
    row = build_report_row(summary="executed grant for #123", report_only=False)
    assert not row["subject"].startswith("[REPORT-ONLY]")

"""op#11774 G-b VPS-instance oracle publisher — publish_once contract.

DETECT-ONLY publisher: one cycle reads the hub's LOCAL pane (via the signed oracle)
and UPSERTs the verdict. It must publish WHATEVER the oracle returns — including
UNSURE on a capture failure — never skip/fabricate.
"""
import importlib

vps = importlib.import_module("nervous_system.body_activity_oracle_vps")
o = importlib.import_module("nervous_system.body_activity_oracle")


def test_publish_once_publishes_the_oracle_verdict():
    published = {}
    state = vps.publish_once(
        activity=lambda: o.Verdict(o.WORKING, "busy-marker"),
        publish=lambda v: published.update(state=v.state, reason=v.reason),
    )
    assert state == o.WORKING
    assert published == {"state": o.WORKING, "reason": "busy-marker"}


def test_publish_once_publishes_unsure_honestly_on_capture_failure():
    # a capture failure surfaces as UNSURE from the oracle; the publisher must
    # publish THAT, not skip it (a stale row would then age out to UNSURE on the
    # Mini too — but publishing UNSURE is the honest, immediate signal).
    published = {}
    state = vps.publish_once(
        activity=lambda: o.Verdict(o.UNSURE, "capture-failed"),
        publish=lambda v: published.update(state=v.state),
    )
    assert state == o.UNSURE
    assert published["state"] == o.UNSURE

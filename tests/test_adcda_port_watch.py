"""adcda_port_watch.py — watermark ordering (bus #66195).

The per-branch watermark in the state file must only advance AFTER the bus post
succeeds. A failed post must leave the OLD watermarks in place so the next run
re-emits the same commits (at-least-once) instead of silently skipping them.
Everything here is stubbed: no real git fetch, no real bus post, no live state
file.
"""
import importlib.util
import pathlib

import pytest

_MOD = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "adcda_port_watch.py"
_spec = importlib.util.spec_from_file_location("adcda_port_watch", _MOD)
watch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch)

OLD = "a" * 40
NEW = "b" * 40


class Rig:
    """Stub _env/_git/_post_bus + tmp STATE/ADCDA, then run watch.main()."""

    def __init__(self, monkeypatch, tmp_path, *, state=None, head=NEW, log="", post_error=None):
        self.posts = []
        self.head = head
        self.log = log
        self.post_error = post_error
        self.state = tmp_path / "adcda_port_watch.state"
        if state is not None:
            self.state.write_text(state)
        adcda = tmp_path / "cosem-adcda"
        adcda.mkdir()
        monkeypatch.setattr(watch, "STATE", self.state)
        monkeypatch.setattr(watch, "ADCDA", adcda)
        monkeypatch.setattr(watch, "_env", lambda: {})
        monkeypatch.setattr(watch, "_git", self._git)
        monkeypatch.setattr(watch, "_post_bus", self._post_bus)

    def _git(self, env, *args, check=True):
        if args[0] == "rev-parse":
            return self.head
        if args[0] == "log":
            return self.log
        return ""

    def _post_bus(self, env, to_agent, subject, body, priority="P2"):
        if self.post_error is not None:
            raise self.post_error
        self.posts.append((to_agent, subject, body, priority))
        return 4242


def test_baseline_posts_and_advances_watermark(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, state=None, head=NEW)
    watch.main()
    assert len(rig.posts) == 1
    assert rig.posts[0][0] == "cc-cosem-platform"
    assert "BASELINE" in rig.posts[0][2]
    assert rig.state.read_text() == f"main {NEW}\n"


def test_no_delta_no_post_state_unchanged(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, state=f"main {NEW}\n", head=NEW)
    watch.main()
    assert rig.posts == []
    assert rig.state.read_text() == f"main {NEW}\n"


def test_delta_post_success_advances_watermark(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, state=f"main {OLD}\n", head=NEW,
              log="abc1234 2026-10-13 12:00:00 +0000 tweak checkout flow")
    watch.main()
    assert len(rig.posts) == 1
    assert "abc1234" in rig.posts[0][2]
    assert rig.state.read_text() == f"main {NEW}\n"


def test_delta_post_failure_keeps_old_watermark(monkeypatch, tmp_path):
    # THE regression test (bus #66195): _post_bus raising must NOT have already
    # advanced the state — old code wrote state BEFORE posting, so a failed post
    # permanently skipped the commits. Pre-fix this fails: state == NEW.
    rig = Rig(monkeypatch, tmp_path, state=f"main {OLD}\n", head=NEW,
              log="abc1234 2026-10-13 12:00:00 +0000 tweak checkout flow",
              post_error=RuntimeError("simulated DB outage"))
    with pytest.raises(RuntimeError):
        watch.main()
    assert rig.state.read_text() == f"main {OLD}\n"


def test_empty_log_delta_advances_watermark_without_post(monkeypatch, tmp_path):
    # head moved but git log came back empty (e.g. history rewritten): nothing to
    # post, watermark still advances so it isn't re-detected forever.
    rig = Rig(monkeypatch, tmp_path, state=f"main {OLD}\n", head=NEW, log="")
    watch.main()
    assert rig.posts == []
    assert rig.state.read_text() == f"main {NEW}\n"

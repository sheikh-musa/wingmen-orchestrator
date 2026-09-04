"""Unit tests for ensure_cwd_trusted (fleet-boot trust-gate preflight).

A Claude Code auto-update reset the folder-TRUST first-run prompt: any lane
that reboots via launch_dangerous_cc.sh in a cwd not marked
hasTrustDialogAccepted:true in ~/.claude.json wedges at the trust gate ->
48s kill -> crash loop. This preflight pre-seeds trust for the launching
lane's OWN cwd, atomically, before claude starts. (Nazim bus 37420; my
fleet-boot domain.)

Pure JSON-transform core + the atomic/backup/idempotent IO behavior, all
against a temp config — never touches the real ~/.claude.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

import ensure_cwd_trusted as ect  # noqa: E402


# ── pure transform core ──────────────────────────────────────────────────────
def test_seed_adds_missing_cwd():
    cfg = {"projects": {}}
    new, changed = ect.seed_trust(cfg, "/w/quality")
    assert changed is True
    assert new["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True


def test_seed_idempotent_when_already_trusted():
    cfg = {"projects": {"/w/quality": {"hasTrustDialogAccepted": True}}}
    new, changed = ect.seed_trust(cfg, "/w/quality")
    assert changed is False
    assert new["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True


def test_seed_flips_explicit_false():
    cfg = {"projects": {"/w/quality": {"hasTrustDialogAccepted": False, "foo": 1}}}
    new, changed = ect.seed_trust(cfg, "/w/quality")
    assert changed is True
    assert new["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True
    assert new["projects"]["/w/quality"]["foo"] == 1  # preserves sibling keys


def test_seed_preserves_other_projects_and_toplevel():
    cfg = {"topKey": "keep", "projects": {"/other": {"hasTrustDialogAccepted": True, "x": 9}}}
    new, changed = ect.seed_trust(cfg, "/w/quality")
    assert changed is True
    assert new["topKey"] == "keep"
    assert new["projects"]["/other"] == {"hasTrustDialogAccepted": True, "x": 9}
    assert new["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True


def test_seed_creates_projects_key_when_absent():
    cfg = {"topKey": 1}
    new, changed = ect.seed_trust(cfg, "/w/quality")
    assert changed is True
    assert new["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True
    assert new["topKey"] == 1


# ── atomic file IO behavior ──────────────────────────────────────────────────
def _write(p: Path, obj) -> None:
    p.write_text(json.dumps(obj))


def test_ensure_trusted_seeds_and_backs_up(tmp_path):
    cfg = tmp_path / "claude.json"
    _write(cfg, {"projects": {"/other": {"hasTrustDialogAccepted": True}}})
    res = ect.ensure_trusted(str(cfg), "/w/quality")
    assert res["changed"] is True
    on_disk = json.loads(cfg.read_text())
    assert on_disk["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True
    assert on_disk["projects"]["/other"]["hasTrustDialogAccepted"] is True  # preserved
    assert res["backup"] and Path(res["backup"]).exists()  # backup made on change


def test_ensure_trusted_idempotent_no_backup_second_run(tmp_path):
    cfg = tmp_path / "claude.json"
    _write(cfg, {"projects": {}})
    first = ect.ensure_trusted(str(cfg), "/w/quality")
    assert first["changed"] is True
    second = ect.ensure_trusted(str(cfg), "/w/quality")
    assert second["changed"] is False
    assert second["backup"] is None  # no-op writes nothing, backs up nothing


def test_ensure_trusted_output_is_valid_json(tmp_path):
    cfg = tmp_path / "claude.json"
    _write(cfg, {"projects": {"/other": {"hasTrustDialogAccepted": True}}})
    ect.ensure_trusted(str(cfg), "/w/quality")
    json.loads(cfg.read_text())  # raises if corrupt


def test_ensure_trusted_missing_config_creates_minimal(tmp_path):
    cfg = tmp_path / "claude.json"  # does not exist
    res = ect.ensure_trusted(str(cfg), "/w/quality")
    assert res["changed"] is True
    on_disk = json.loads(cfg.read_text())
    assert on_disk["projects"]["/w/quality"]["hasTrustDialogAccepted"] is True


def test_ensure_trusted_corrupt_config_fails_loud_no_write(tmp_path):
    cfg = tmp_path / "claude.json"
    cfg.write_text("{not valid json")
    with pytest.raises(Exception):
        ect.ensure_trusted(str(cfg), "/w/quality")
    # original left intact — we NEVER clobber an unparseable real config blindly
    assert cfg.read_text() == "{not valid json"


def test_concurrent_lane_boots_do_not_lose_entries(tmp_path):
    """THE race Nazim named (37425): N lanes launching at once each seed their own
    cwd. A naive whole-file read-modify-rename drops all but the last. The flock
    must make every distinct entry survive."""
    import threading

    cfg = tmp_path / "claude.json"
    _write(cfg, {"projects": {"/pre-existing": {"hasTrustDialogAccepted": True}}})
    cwds = [f"/w/lane-{i}" for i in range(24)]
    barrier = threading.Barrier(len(cwds))
    errors = []

    def worker(cwd):
        try:
            barrier.wait()  # maximise contention — all fire together
            ect.ensure_trusted(str(cfg), cwd)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(c,)) for c in cwds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    on_disk = json.loads(cfg.read_text())  # still valid JSON
    projects = on_disk["projects"]
    assert projects["/pre-existing"]["hasTrustDialogAccepted"] is True  # never dropped
    for c in cwds:
        assert projects[c]["hasTrustDialogAccepted"] is True  # every lane survived

"""Tests for launcher heartbeat error log and LockTimeout diagnostic flush.

CAI-RESP-053:
- A2: agents.last_heartbeat fails loud (no `|| true`) and appends stderr to
  a size-capped /tmp/cc_heartbeat_err.log.
- A4: allocate_sub_tag_and_register flushes a full diagnostic file with
  fsync before raising LockTimeoutError, with newest-20 retention.

CAI-RESP-054 SA1:
- Heartbeat log rotates to .1 before truncate (evidence survives error-storm).
- Lock-timeout retention raised to newest-40 (overflow generation).
"""
import re
from pathlib import Path


# Resolve relative to repo root so tests work regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = _REPO_ROOT / "scripts" / "launch_dangerous_cc.sh"
HELPER = _REPO_ROOT / "scripts" / "lib" / "auto_agent_id.py"


def test_launcher_heartbeat_agents_path_does_not_swallow_stderr():
    """The supabase-py heartbeat (agents table) must fail loud, not `|| true`.

    Locates the `sb.table('agents').update(...).execute()` heartbeat block and
    asserts the trailing shell redirect appends stderr to the heartbeat err
    log (NOT `2>/dev/null || true`).
    """
    src = LAUNCHER.read_text()
    # Find the closing `"` of the heredoc after the agents heartbeat, plus
    # the immediate tail (redirect). The python script ends with `.execute()`
    # then a newline, then the closing `"` on its own, then a shell tail.
    m = re.search(
        r"sb\.table\('agents'\)\.update\(\{'last_heartbeat'[\s\S]*?execute\(\)\n\"\s*([^\n]+)",
        src,
    )
    assert m, "could not locate agents heartbeat block"
    tail = m.group(1)
    assert "/tmp/cc_heartbeat_err.log" in tail or "$HB_ERR_LOG" in tail, (
        f"expected redirect to heartbeat err log, got {tail!r}"
    )
    assert "|| true" not in tail, (
        f"agents heartbeat must fail loud (no `|| true`), got {tail!r}"
    )


def test_launcher_heartbeat_cap_check_present():
    """Heartbeat log must be size-capped (10MB) to prevent /tmp sprawl."""
    src = LAUNCHER.read_text()
    assert (
        "10485760" in src
        or "10*1024*1024" in src
        or "10 * 1024 * 1024" in src
    ), "expected 10MB cap constant in launcher"
    assert "cc_heartbeat_err.log" in src


def test_lock_timeout_flush_path_present():
    """allocate_sub_tag_and_register writes a diagnostic file before raising.

    Verifies:
      - the `/tmp/cc_lock_timeout_<...>.log` pattern is present,
      - fsync is called on the file handle,
      - glob-based retention pruning is present.
    """
    src = HELPER.read_text()
    assert "cc_lock_timeout_" in src, (
        "expected LockTimeout diagnostic filename pattern"
    )
    assert "fsync" in src, "expected fsync on the diagnostic file handle"
    assert "/tmp/cc_lock_timeout_" in src
    assert re.search(r"glob.*cc_lock_timeout", src), (
        "expected glob for retention pruning"
    )


def test_heartbeat_log_rotates_before_truncate():
    """SA1 (CAI-RESP-054): evidence at error-storm time survives the cap.

    Old behavior was `: > "$HB_ERR_LOG"` — an error-storm faster than the
    5-min cadence loses its own evidence. New behavior renames the full log
    to .1 before opening a fresh one, keeping one generation.
    """
    src = LAUNCHER.read_text()
    # The old truncate form must be gone — if it reappears, the rotation is lost.
    assert ': > "$HB_ERR_LOG"' not in src, (
        "heartbeat log must rotate (mv to .1), not truncate in place"
    )
    # The rotation target must be .1 suffix.
    assert '${HB_ERR_LOG}.1' in src or '"$HB_ERR_LOG".1' in src, (
        "expected rotation target ${HB_ERR_LOG}.1"
    )
    # Rotation must use mv (not cp — cp+truncate loses the atomicity window).
    assert re.search(r'mv\s+-f\s+"\$HB_ERR_LOG"', src), (
        "expected mv -f rotation of heartbeat err log"
    )


def test_lock_timeout_retention_is_forty():
    """SA1 (CAI-RESP-054): keep newest-40 (up from 20) so the oldest-20
    function as an overflow generation — evidence survives bursty storms."""
    src = HELPER.read_text()
    assert re.search(r"_existing\[:\-40\]", src), (
        "expected retention slice _existing[:-40] (newest-40 kept)"
    )
    assert "_existing[:-20]" not in src, (
        "old newest-20 retention must be gone — bump to newest-40"
    )

"""Tests for scripts/agent_messages_writer_gate.py (backlog#124, orch-console
ruling bus #74885/#74906 part 4): the enforce-in-code gate that makes the
migration-124 revoke safe to release. Fails on any raw `INSERT INTO
agent_messages` found in production code outside the sanctioned helper
(bus_send.py's send(), which now posts through post_agent_message -- see PR
#490). Mirrors scripts/check_additive_migration.py's shape (comment-aware
regex scan, clear exit codes) but scans Python source, not SQL.

This is the completion proof for the ~50-script migration, not a one-time
audit: it must stay green as new scripts are added, which is why it is a gate
(CI + pre-push), not a report.
"""
from __future__ import annotations

import pathlib
import textwrap
from pathlib import Path

import pytest

from scripts import agent_messages_writer_gate as gate


def _write(tmp_path: pathlib.Path, relpath: str, content: str) -> pathlib.Path:
    p = tmp_path / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))
    return p


def test_detects_raw_insert_in_a_script(tmp_path):
    _write(tmp_path, "scripts/some_watchdog.py", """
        def page():
            cur.execute(
                "INSERT INTO agent_messages (from_agent, to_agent, message_type) "
                "VALUES ('x','y','update')"
            )
    """)
    findings = gate.scan(tmp_path)
    assert len(findings) == 1
    assert findings[0].path.name == "some_watchdog.py"


def test_detects_insert_split_across_adjacent_string_literals(tmp_path):
    """The real shape found in scripts/lane_drill_seed.py: the statement is
    split across two adjacent Python string literals on separate lines."""
    _write(tmp_path, "scripts/lane_drill_seed.py", """
        cur.execute(
            "INSERT INTO agent_messages (from_agent, to_agent, message_type, subject, "
            "body, priority, requires_response, is_test) "
            "VALUES ('orch-console',%s,'question',%s,%s,'P2',true,true) RETURNING id",
            (agent, body))
    """)
    findings = gate.scan(tmp_path)
    assert len(findings) == 1


def test_ignores_a_comment_mentioning_insert(tmp_path):
    _write(tmp_path, "scripts/clean_script.py", """
        # Historically this did INSERT INTO agent_messages directly; migrated
        # to bus_send.send() in backlog#124.
        def page():
            from scripts.bus_send import send
            send(from_agent="x", to="y", mtype="update", subject="s", body="b")
    """)
    findings = gate.scan(tmp_path)
    assert findings == []


def test_ignores_files_under_tests_directory(tmp_path):
    """Test fixtures legitimately build their own throwaway agent_messages
    tables and insert into them against an isolated ephemeral PG -- that is
    not a production write path and must not be flagged."""
    _write(tmp_path, "tests/test_something.py", """
        def test_x(cur):
            cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    findings = gate.scan(tmp_path)
    assert findings == []


def test_bus_send_py_itself_is_clean_after_the_rpc_cutover(tmp_path):
    """bus_send.py posts through post_agent_message, not a raw INSERT, since
    PR #490 -- this is what makes it the sanctioned helper in the first place.
    No special-case allowlisting needed: it is clean on its own merits."""
    _write(tmp_path, "scripts/bus_send.py", """
        def send(from_agent, to, mtype, subject, body, priority="P2", req=False, thread=None, sub_tag=None):
            cur.execute(
                "SELECT * FROM post_agent_message(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (from_agent, to, mtype, subject, body, priority, req, thread, sub_tag),
            )
    """)
    findings = gate.scan(tmp_path)
    assert findings == []


def test_reports_file_and_line_number(tmp_path):
    _write(tmp_path, "scripts/some_watchdog.py", """
        x = 1
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    findings = gate.scan(tmp_path)
    assert len(findings) == 1
    assert findings[0].line == 3


def test_cli_exit_code_zero_on_clean_tree(tmp_path, capsys):
    _write(tmp_path, "scripts/clean_script.py", "x = 1\n")
    code = gate.main([str(tmp_path)])
    assert code == 0


def test_cli_exit_code_one_on_findings(tmp_path, capsys):
    _write(tmp_path, "scripts/dirty_script.py", """
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    code = gate.main([str(tmp_path)])
    assert code == 1


def test_cli_output_names_the_offending_file(tmp_path, capsys):
    _write(tmp_path, "scripts/dirty_script.py", """
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    gate.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "dirty_script.py" in out


def test_nervous_system_directory_is_scanned_too(tmp_path):
    _write(tmp_path, "nervous_system/some_daemon.py", """
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    findings = gate.scan(tmp_path)
    assert len(findings) == 1


def test_ignores_mention_inside_a_docstring(tmp_path):
    """Real false positive hit against the live repo: scripts/bus_send.py and
    scripts/monthly_burn_snapshot.py both explain IN PROSE, inside a
    docstring, that they never hand-write the statement -- the phrase itself
    appears but as documentation, not code. '#' comment-stripping alone
    doesn't catch this; triple-quoted strings need their own handling."""
    _write(tmp_path, "scripts/documented_script.py", '''
        def send():
            """Posts via scripts/bus_send.py -- NEVER a hand-written INSERT INTO
            agent_messages (CLAUDE.md, bus #43651)."""
            pass
    ''')
    findings = gate.scan(tmp_path)
    assert findings == []


def test_ignores_mention_inside_a_module_docstring_preceded_by_a_shebang(tmp_path):
    """Real false positive hit against the live repo: scripts/bus_send.py's
    MODULE docstring (which legitimately explains the history of this exact
    phrase) is preceded by a shebang line, not a ':' -- the plain
    start-of-file check alone doesn't recognize that as docstring position."""
    _write(tmp_path, "scripts/shebang_script.py", '''
        #!/usr/bin/env python3
        """bus_send.py -- the ONE sanctioned way to post an agent_messages row.

        there is exactly one INSERT INTO agent_messages in the whole fleet's
        ad-hoc-send path.
        """
        def send():
            pass
    ''')
    findings = gate.scan(tmp_path)
    assert findings == []


def test_the_gate_script_itself_is_excluded_from_scanning(tmp_path):
    """The gate's own source necessarily contains the literal phrase (in its
    regex pattern and docstrings) -- self-matching is a known false positive
    for this class of linter, so the gate excludes its own file by name."""
    gate_src = Path(gate.__file__).read_text()
    _write(tmp_path, "scripts/agent_messages_writer_gate.py", gate_src)
    findings = gate.scan(tmp_path)
    assert findings == []


def test_detects_triple_quoted_sql_literal_passed_as_call_argument(tmp_path):
    """Real shape found in nervous_system/lane_watchdog.py (a genuine gap in
    the original ~54-script audit, caught only by building this gate): the
    raw INSERT lives in a triple-quoted string passed straight to
    cur.execute(...), NOT in docstring position -- must not be stripped by
    the docstring heuristic that protects bus_send.py's prose."""
    _write(tmp_path, "scripts/lane_watchdog_shaped.py", '''
        with conn.cursor() as cur:
            cur.execute(
                """insert into agent_messages (from_agent,to_agent,message_type)
                   values ('x','y','update')""",
            )
    ''')
    findings = gate.scan(tmp_path)
    assert len(findings) == 1


def test_baseline_entry_suppresses_ci_failure_but_is_still_reported(tmp_path, capsys):
    """Mirrors tests/ci_known_failing.txt's shape: wiring this gate into CI
    today must not red-line every PR over the ~55 scripts not yet migrated
    (backlog#124 is a multi-PR effort). A baseline-listed path doesn't fail
    the build, but still prints so it stays visible, not silently hidden."""
    _write(tmp_path, "scripts/not_yet_migrated.py", """
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    baseline = _write(tmp_path, "baseline.txt", "scripts/not_yet_migrated.py\n")
    code = gate.main([str(tmp_path), "--baseline", str(baseline)])
    assert code == 0
    assert "not_yet_migrated.py" in capsys.readouterr().out


def test_finding_not_on_the_baseline_still_fails_even_with_a_baseline_given(tmp_path):
    """The whole point: a baseline tracks PRE-EXISTING debt, it does not
    disable the gate -- a brand NEW bypass must still fail CI immediately."""
    _write(tmp_path, "scripts/brand_new_bypass.py", """
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    baseline = _write(tmp_path, "baseline.txt", "scripts/some_other_file.py\n")
    code = gate.main([str(tmp_path), "--baseline", str(baseline)])
    assert code == 1


def test_require_complete_flag_fails_even_on_baseline_listed_findings(tmp_path):
    """The RELEASE check migration 124 actually needs (console's ruling,
    bus #74885/#74906): not 'nothing new', but 'truly zero, fleet-wide'."""
    _write(tmp_path, "scripts/not_yet_migrated.py", """
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    baseline = _write(tmp_path, "baseline.txt", "scripts/not_yet_migrated.py\n")
    code = gate.main([str(tmp_path), "--baseline", str(baseline), "--require-complete"])
    assert code == 1


def test_inline_allowlist_comment_suppresses_a_finding(tmp_path):
    """Mirrors check_additive_migration.py's `-- linter-allow:` escape hatch,
    for the rare legitimate case (e.g. a migration helper script that applies
    raw SQL on purpose) -- `# writer-gate-allow: <reason>` on the line before."""
    _write(tmp_path, "scripts/special_case.py", """
        # writer-gate-allow: this IS the migration-apply tool, not a daemon
        cur.execute("INSERT INTO agent_messages (from_agent) VALUES ('x')")
    """)
    findings = gate.scan(tmp_path)
    assert findings == []

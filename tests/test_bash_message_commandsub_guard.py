import importlib.util
import io
import json
import os
import sys

_SPEC = importlib.util.spec_from_file_location(
    "bash_message_commandsub_guard",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "hooks", "bash_message_commandsub_guard.py"),
)
guard = importlib.util.module_from_spec(_SPEC)
sys.modules["bash_message_commandsub_guard"] = guard
_SPEC.loader.exec_module(guard)


def _run(cmd: str, monkeypatch) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    return guard.main()


def test_refuses_the_exact_83620_backtick_shape(monkeypatch):
    cmd = (
        'scripts/bus_send.py --to orch-console --type update --priority P2 '
        '--body "ran `install_ceayj_provisioner.py --apply --step 3` just now" --from cc-orchestrator'
    )
    assert _run(cmd, monkeypatch) == 2


def test_refuses_dollar_paren_inside_double_quoted_body(monkeypatch):
    cmd = (
        'scripts/bus_send.py --to orch-console --type update --priority P2 '
        '--body "ran $(install_ceayj_provisioner.py --apply --step 3) just now"'
    )
    assert _run(cmd, monkeypatch) == 2


def test_allows_single_quoted_backticks(monkeypatch):
    cmd = "scripts/bus_send.py --to orch-console --type update --priority P2 --body 'see `this` literal text'"
    assert _run(cmd, monkeypatch) == 0


def test_allows_single_quoted_dollar_paren(monkeypatch):
    cmd = "scripts/bus_send.py --to orch-console --type update --priority P2 --body 'see $(this) literal text'"
    assert _run(cmd, monkeypatch) == 0


def test_allows_dollar_paren_elsewhere_in_the_command_not_in_a_guarded_flag(monkeypatch):
    cmd = 'echo "today is $(date)"'
    assert _run(cmd, monkeypatch) == 0


def test_allows_a_normal_body_with_no_substitution(monkeypatch):
    cmd = 'scripts/bus_send.py --to orch-console --type update --priority P2 --body "all clear, nothing to report"'
    assert _run(cmd, monkeypatch) == 0


def test_refuses_gh_title_with_backtick(monkeypatch):
    cmd = 'gh pr create --title "fix `rm -rf /tmp/x`" --body "see PR"'
    assert _run(cmd, monkeypatch) == 2


def test_refuses_gh_body_with_dollar_paren(monkeypatch):
    cmd = 'gh pr create --title "ok" --body "ran $(whoami) as a check"'
    assert _run(cmd, monkeypatch) == 2


def test_refuses_git_commit_dash_m_with_backtick(monkeypatch):
    cmd = 'git commit -m "fixes `cat /etc/passwd`"'
    assert _run(cmd, monkeypatch) == 2


def test_allows_git_commit_dash_m_plain(monkeypatch):
    cmd = 'git commit -m "docs: add runbook"'
    assert _run(cmd, monkeypatch) == 0


def test_refuses_inline_equals_form(monkeypatch):
    cmd = 'gh pr create --title="ok" --body="ran `whoami` here"'
    assert _run(cmd, monkeypatch) == 2


def test_refuses_unquoted_dangerous_value(monkeypatch):
    cmd = "scripts/bus_send.py --to orch-console --type update --priority P2 --subject done_`whoami`_now"
    assert _run(cmd, monkeypatch) == 2


def test_allows_unrelated_bash_commands(monkeypatch):
    cmd = "ls -la ~/wingmen/orchestrator/scripts"
    assert _run(cmd, monkeypatch) == 0


def test_non_bash_tool_is_a_noop(monkeypatch):
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert guard.main() == 0


def test_fails_closed_on_unparseable_input(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert guard.main() == 2


def test_fails_closed_on_unbalanced_quotes_in_command(monkeypatch):
    cmd = 'scripts/bus_send.py --to orch-console --body "unterminated'
    assert _run(cmd, monkeypatch) == 2

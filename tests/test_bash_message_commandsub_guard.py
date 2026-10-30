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


# --- cc-quality PR#657 review, Finding A: adjacent-concatenation bypass ---
# A shell WORD can be a concatenation of adjacent quoted/unquoted segments
# with no whitespace between them -- bash runs a command substitution in
# ANY of those segments. The single-segment scan this guard shipped with
# missed this; these are the exact bypasses cc-quality grounded against bash.

def test_refuses_double_quote_then_adjacent_unquoted_backtick(monkeypatch):
    cmd = 'scripts/bus_send.py --to orch-console --body "x"`echo SUBRAN`'
    assert _run(cmd, monkeypatch) == 2


def test_refuses_single_quote_then_adjacent_unquoted_backtick(monkeypatch):
    cmd = "scripts/bus_send.py --to orch-console --body ''`echo SUBRAN`"
    assert _run(cmd, monkeypatch) == 2


def test_refuses_single_quote_then_adjacent_unquoted_dollar_paren(monkeypatch):
    cmd = "scripts/bus_send.py --to orch-console --body ''$(echo SUBRAN)"
    assert _run(cmd, monkeypatch) == 2


def test_allows_single_quoted_segment_concatenated_with_safe_unquoted_text(monkeypatch):
    cmd = "scripts/bus_send.py --to orch-console --body 'hello'world"
    assert _run(cmd, monkeypatch) == 0


# --- orch-console bus #83846/#83852: narrow allow for the standard
# quoted-heredoc commit/message pattern every lane's own commit
# instructions use. Safe because a single-quoted heredoc delimiter
# suppresses ALL expansion in the body (same as single quotes), and
# `cat` with no other args only ever echoes stdin back.

def test_allows_exact_quoted_heredoc_cat_substitution(monkeypatch):
    cmd = "git commit " + "-m \"$(cat <<'EOF'\ndocs: fix stale comment\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 0


def test_refuses_same_shape_with_unquoted_delimiter(monkeypatch):
    cmd = "git commit " + "-m \"$(cat <<EOF\ndocs: fix stale comment\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


def test_refuses_backtick_elsewhere_alongside_the_safe_heredoc(monkeypatch):
    cmd = "git commit " + "-m \"$(cat <<'EOF'\ndocs: fix\nEOF\n)" + "`whoami`\""
    assert _run(cmd, monkeypatch) == 2


def test_refuses_dollar_paren_elsewhere_alongside_the_safe_heredoc(monkeypatch):
    cmd = "git commit " + "-m \"$(whoami)$(cat <<'EOF'\ndocs: fix\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


def test_refuses_cat_with_extra_arguments_inside_the_parens(monkeypatch):
    cmd = "git commit " + "-m \"$(cat -v <<'EOF'\ndocs: fix\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


def test_refuses_trailing_text_after_the_closing_paren(monkeypatch):
    cmd = "git commit " + "-m \"$(cat <<'EOF'\ndocs: fix\nEOF\n) extra\""
    assert _run(cmd, monkeypatch) == 2


def test_allows_the_heredoc_dash_variant_with_single_quoted_delimiter(monkeypatch):
    cmd = "git commit " + "-m \"$(cat <<-'EOF'\ndocs: fix\n\tEOF\n)\""
    assert _run(cmd, monkeypatch) == 0


def test_heredoc_body_containing_its_own_backticks_stays_safe(monkeypatch):
    # The whole point: a single-quoted delimiter means bash never expands the
    # body, so a literal backtick/`$(` INSIDE it is inert, not a hazard.
    cmd = "git commit " + "-m \"$(cat <<'EOF'\nsee " + "`rm -rf /tmp/x`" + " for details\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 0


# --- cc-quality PR#657 review, Finding B: no outer catch-all = fail-open risk ---

def test_unexpected_exception_in_main_fails_closed(monkeypatch):
    def _boom():
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(guard, "_main_inner", _boom)
    assert guard.main() == 2

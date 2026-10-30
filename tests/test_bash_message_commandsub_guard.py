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


# --- cc-quality bus #83877/#83878 (2026-10-30): confirmed delimiter-injection
# bypass of the allow above, grounded against real bash (the embedded
# substitution EXECUTED). A body line equal to the delimiter ends the
# heredoc at that FIRST occurrence in real bash, but the old non-greedy
# `.*?` + backref regex could match a LATER occurrence instead, treating a
# live trailing substitution between the two as inert body text. Fixed by
# parsing the heredoc the way bash does (line by line, first-match-wins)
# instead of a single backtracking regex.

def test_refuses_cc_quality_83877_delimiter_injection_bypass(monkeypatch):
    # $(cat <<'EOF'\nEOF\n)$(id)\nEOF\n) -- bash ends the heredoc at the
    # FIRST "EOF" (empty body), so the trailing )$(id) is a LIVE, separate
    # substitution, not heredoc body. Must refuse.
    cmd = "git commit " + "-m \"$(cat <<'EOF'\nEOF\n)" + "$(id)" + "\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


def test_allows_terminator_line_with_trailing_space_and_clean_tail(monkeypatch):
    # Corrected premise (cc-quality bus #83914, grounded against real
    # /bin/bash 3.2.57): a delimiter line with trailing whitespace DOES
    # terminate the heredoc there -- bash is lenient on trailing whitespace,
    # not strict. Since nothing but ")" follows that line, this is genuinely
    # safe and must be allowed, not refused.
    cmd = "git commit " + "-m \"$(cat <<'EOF'\ndocs: fix\nEOF \n)\""
    assert _run(cmd, monkeypatch) == 0


def test_allows_dash_heredoc_with_tab_indented_terminator(monkeypatch):
    # <<- strips only LEADING TABS from the terminator line before comparing
    # -- a tab-indented "\tEOF" is a valid terminator for the dash form.
    cmd = "git commit " + "-m \"$(cat <<-'EOF'\ndocs: fix\n\tEOF\n)\""
    assert _run(cmd, monkeypatch) == 0


def test_refuses_dash_heredoc_delimiter_injection_bypass(monkeypatch):
    # Same bypass shape as 83877, but via the tab-stripping dash form: a
    # tab-indented "\tEOF" line still terminates the heredoc at its FIRST
    # occurrence, so trailing )$(id) + a second EOF + ) must still refuse.
    cmd = "git commit " + "-m \"$(cat <<-'EOF'\n\tEOF\n)" + "$(id)" + "\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


# --- cc-quality bus #83914 (2026-10-30): a SECOND round of the same
# delimiter-injection class, reopened by one trailing space. Bash 3.2.57
# (grounded, with an execution marker) terminates a heredoc at a delimiter
# line even with trailing whitespace, but the first fix's exact `==` match
# skipped that line and found a LATER clean delimiter instead, treating a
# live trailing substitution as inert body. Fixed by stripping trailing
# whitespace (and leading tabs for <<-) before comparing to the delimiter,
# plus a defense-in-depth check (console bus #83924) refusing any value
# where some OTHER line is delimiter-shaped under a looser full-strip.

def test_refuses_trailing_space_delimiter_injection_bypass(monkeypatch):
    # $(cat <<'EOF'\nEOF \n)$(echo PWNED)\nEOF\n) -- bash terminates at the
    # FIRST line "EOF " (trailing space), so the trailing )$(echo PWNED) is
    # live, separate substitution, not heredoc body. Must refuse.
    cmd = "git commit " + "-m \"$(cat <<'EOF'\nEOF \n)" + "$(echo PWNED)" + "\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


def test_refuses_ambiguous_leading_whitespace_delimiter_elsewhere(monkeypatch):
    # Defense in depth: a line " EOF" (leading space) does not match under
    # the strict non-dash rule (which strips trailing only), so the real
    # terminator is the later clean "EOF" -- but a DIFFERENT shell could
    # plausibly treat " EOF" as a terminator too. That ambiguity refuses.
    cmd = "git commit " + "-m \"$(cat <<'EOF'\n docs\n EOF\nEOF\n)\""
    assert _run(cmd, monkeypatch) == 2


# --- orch-console bus #84048: unquoted newline/;/&/| must end a shell word ---

def test_allows_clean_body_then_separate_safe_heredoc_cat_on_next_line(monkeypatch):
    # Exactly console's reported false positive: a clean double-quoted
    # --body (plain prose), closing quote, newline, then an UNRELATED next
    # command that happens to be the already-allowlisted safe heredoc-cat
    # shape. Before the fix, the newline never ended the first word, so the
    # scanner glued the second command's entire text onto the --body value
    # and flagged its dollar-paren as if it belonged to --body.
    cmd = (
        'scripts/bus_send.py --to orch-console --type update --priority P2 '
        '--body "ran the nightly sweep, no issues" --from cc-orchestrator\n'
        'MSG="$(cat <<\'EOF\'\nhello\nEOF\n)"'
    )
    assert _run(cmd, monkeypatch) == 0


def test_refuses_dollar_paren_second_command_not_the_safe_shape(monkeypatch):
    # Defense in depth: the newline fix must only fix the WORD boundary, not
    # weaken detection -- a genuinely dangerous unquoted $(...) in a second,
    # separate command (not the safe heredoc-cat exemption) must still be
    # caught, scanned on its own terms.
    cmd = (
        'scripts/bus_send.py --to orch-console --type update --priority P2 '
        '--body "ran the nightly sweep, no issues" --from cc-orchestrator\n'
        'git commit -m "$(whoami)"'
    )
    assert _run(cmd, monkeypatch) == 2


def test_semicolon_ends_a_word_same_as_newline(monkeypatch):
    cmd = (
        'scripts/bus_send.py --to orch-console --type update --priority P2 '
        '--body "ran the nightly sweep, no issues"; git commit -m "$(whoami)"'
    )
    assert _run(cmd, monkeypatch) == 2


# --- cc-quality PR#657 review, Finding B: no outer catch-all = fail-open risk ---

def test_unexpected_exception_in_main_fails_closed(monkeypatch):
    def _boom():
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(guard, "_main_inner", _boom)
    assert guard.main() == 2

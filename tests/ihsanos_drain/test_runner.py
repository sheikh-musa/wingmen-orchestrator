from ihsanos_drain.runner import (
    build_prompt,
    execute_ruling,
    parse_changed_files,
    sanitize_ref,
    summarize_ci,
    unauthorized_migrations,
    worktree_paths,
)


# ---- sanitize_ref (pure) ----

def test_sanitize_ref_keeps_safe_chars():
    assert sanitize_ref("CADENCE-008") == "CADENCE-008"
    assert sanitize_ref("BUG-024.1") == "BUG-024.1"


def test_sanitize_ref_replaces_unsafe_runs_with_single_dash():
    # path/shell metacharacters must never reach a branch name or /tmp path
    assert sanitize_ref("a/b c;rm -rf") == "a-b-c-rm-rf"


def test_sanitize_ref_empty_or_all_unsafe_falls_back():
    assert sanitize_ref("") == "unknown"
    assert sanitize_ref("///") == "unknown"


# ---- worktree_paths (pure) ----

def test_worktree_paths_derive_branch_and_tmp_path():
    wt, branch = worktree_paths("CADENCE-008")
    assert branch == "ihsanos-drain-CADENCE-008"
    assert wt == "/tmp/wingmen-wt-ihsanos-drain-CADENCE-008"


def test_worktree_paths_sanitizes_ref():
    wt, branch = worktree_paths("a/b")
    assert branch == "ihsanos-drain-a-b"
    assert wt == "/tmp/wingmen-wt-ihsanos-drain-a-b"


# ---- summarize_ci (pure) ----

def test_summarize_ci_all_green():
    r = summarize_ci([("lint", 0, ""), ("unit-tests", 0, "12 passed")])
    assert r["green"] is True
    assert "2" in r["detail"]  # mentions how many steps passed


def test_summarize_ci_first_failure_wins_and_names_step():
    r = summarize_ci(
        [("lint", 0, ""), ("type-check", 1, "TS2345 boom"), ("unit-tests", 0, "")]
    )
    assert r["green"] is False
    assert "type-check" in r["detail"]
    assert "TS2345" in r["detail"]


def test_summarize_ci_empty_is_not_green():
    # no steps ran -> cannot assert green; fail closed
    r = summarize_ci([])
    assert r["green"] is False


# ---- parse_changed_files (pure) ----

def test_parse_changed_files_splits_and_drops_blanks():
    out = "src/app.tsx\nsupabase/migrations/x.sql\n\n"
    assert parse_changed_files(out) == [
        "src/app.tsx",
        "supabase/migrations/x.sql",
    ]


def test_parse_changed_files_empty():
    assert parse_changed_files("") == []
    assert parse_changed_files("\n  \n") == []


# ---- unauthorized_migrations (pure) ----

def test_unauthorized_migrations_flags_unnamed():
    changed = ["supabase/migrations/20260612_add_x.sql", "src/app.py"]
    bad = unauthorized_migrations(changed, decision_text="apply the schema change")
    assert bad == ["supabase/migrations/20260612_add_x.sql"]


def test_unauthorized_migrations_empty_when_named():
    changed = ["supabase/migrations/20260612_add_x.sql"]
    bad = unauthorized_migrations(
        changed, decision_text="run 20260612_add_x.sql exactly"
    )
    assert bad == []


def test_unauthorized_migrations_ignores_nonmigration_files():
    changed = ["src/app.py", "tests/test_app.py", "README.md"]
    assert unauthorized_migrations(changed, decision_text="no migrations here") == []


# ---- build_prompt (pure) ----

def test_build_prompt_contains_ref_and_hard_migration_rule():
    p = build_prompt({"decision_ref": "IRSYAD-DEMO-001", "decision": "do the demo"})
    assert "IRSYAD-DEMO-001" in p
    assert "do the demo" in p
    assert "migration" in p.lower()
    # the hard rule must be present verbatim-ish
    assert "not named in this ruling" in p.lower() or "named in the ruling" in p.lower()


# ---- execute_ruling (orchestration via DI) ----

def _ruling():
    return {"decision_ref": "R1", "decision": "do the thing"}


def test_execute_ci_green_merges_and_records():
    merged = []
    out = execute_ruling(
        _ruling(),
        run_claude=lambda prompt: {"ok": True, "summary": "did it", "tokens": 1234},
        changed_files_fn=lambda: ["src/app.py"],
        run_ci=lambda: {"green": True, "detail": "12 passed"},
        merge_fn=lambda: merged.append(True),
    )
    assert out.status == "merged"
    assert out.tokens_spent == 1234
    assert merged == [True]


def test_execute_ci_red_escalates_no_merge():
    merged = []
    out = execute_ruling(
        _ruling(),
        run_claude=lambda prompt: {"ok": True, "summary": "did it", "tokens": 50},
        changed_files_fn=lambda: ["src/app.py"],
        run_ci=lambda: {"green": False, "detail": "2 failed"},
        merge_fn=lambda: merged.append(True),
    )
    assert out.status == "escalated_ci_red"
    assert merged == []


def test_execute_unauthorized_migration_refuses_no_merge():
    merged = []
    ci_calls = []
    out = execute_ruling(
        {"decision_ref": "R1", "decision": "do the thing"},
        run_claude=lambda prompt: {"ok": True, "summary": "added a migration", "tokens": 99},
        changed_files_fn=lambda: ["supabase/migrations/20260612_sneaky.sql"],
        run_ci=lambda: ci_calls.append(True) or {"green": True, "detail": ""},
        merge_fn=lambda: merged.append(True),
    )
    assert out.status == "refused_migration"
    assert merged == []
    assert ci_calls == []  # refuse before even running CI


def test_execute_claude_failure_escalates():
    merged = []
    out = execute_ruling(
        _ruling(),
        run_claude=lambda prompt: {"ok": False, "summary": "timeout", "tokens": 0},
        changed_files_fn=lambda: [],
        run_ci=lambda: {"green": True, "detail": ""},
        merge_fn=lambda: merged.append(True),
    )
    assert out.status == "escalated_ambiguous"
    assert merged == []

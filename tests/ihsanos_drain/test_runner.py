from ihsanos_drain.runner import (
    build_prompt,
    execute_ruling,
    unauthorized_migrations,
)


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

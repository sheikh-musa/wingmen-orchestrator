"""Fixer Agent — surgical single-file code fixer."""

from __future__ import annotations


def build_fixer_prompt(*, issue: dict, repo_path: str) -> str:
    """Build the Fixer Agent prompt for a single issue."""
    file_path = issue.get("file_path", "unknown")
    description = issue.get("description", "")
    suggested_fix = issue.get("suggested_fix", "")

    file_hint = f"\nFile: {repo_path}/{file_path}" if file_path and file_path != "unknown" else ""
    fix_hint = f"\nSuggested fix: {suggested_fix}" if suggested_fix else ""

    return f"""You are a surgical code fixer. Fix exactly ONE issue in ONE file.

The user's request (pay close attention to their exact words):
{description}
{file_hint}{fix_hint}

Steps:
1. Understand what the USER is asking for — their words describe the problem, not your interpretation
2. Find the relevant file in {repo_path} (use Grep/Glob to search if no file path given)
3. Read the file and identify the exact code causing the user's issue
4. Make the minimal edit to fix what the user described
5. Run: cd {repo_path} && git add -A && git commit -m "fix: {description[:60]}"
6. Report what you changed and why it fixes the user's issue
7. On the LAST line of your response, write ONLY the page route that was fixed, like: PAGE:/donor/donate/1 or PAGE:/admin/donors — this is used for verification screenshots

Rules:
- Fix what the USER asked for, not what you think is wrong. If they say "header spilling into next section", that means add spacing — not change z-index.
- Change ONLY what's needed. No refactoring, no style changes, no "while I'm here" improvements.
- If you modified a file that has tests (check for *.test.* or test_* files), run the tests and fix any failures before committing. If you change a return type (e.g., dict to dataclass), update ALL callers and tests.
- If the fix is unclear or would require changing multiple files, respond with "SKIP: <reason>" instead of editing.
- Do NOT run git push — that's handled separately.
- Do NOT run any deploy commands.
"""

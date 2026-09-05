"""Auditor Agent — crawls live pages and reports issues as structured JSON."""

from __future__ import annotations

import json
import re


def build_auditor_prompt(
    *,
    deploy_url: str,
    repo_path: str,
    file_tree: str,
    detail: str,
) -> str:
    """Build the Auditor Agent prompt. Read-only — no edit instructions."""
    return f"""You are a QA auditor for a web application. Your job:
1. Use WebFetch to crawl every route on the live site
2. Check each page loads without errors (look for error text, blank pages, broken layouts)
3. Use Read/Grep to inspect source code for issues (broken imports, placeholder text, inconsistent styling)
4. Report ALL findings as a JSON array

Live site: {deploy_url}
Repo path: {repo_path}
User request: {detail}

Known routes (from file tree):
{file_tree}

For each issue, output this JSON format inside a ```json code block:
[
  {{
    "page": "/admin/donors",
    "severity": "high" | "medium" | "low",
    "description": "Duplicate navigation cards both linking to /my",
    "fix_confidence": "high" | "medium" | "low",
    "file_path": "app/page.tsx",
    "suggested_fix": "Remove the duplicate My Portal card, keep Donor Portal"
  }}
]

Confidence levels:
- "high" = obvious fix, single file, no ambiguity (will be auto-fixed)
- "medium" = likely fix but needs verification
- "low" = needs human decision (infrastructure, design choice, etc.)

Rules:
- Do NOT edit any files. Only report.
- Crawl ALL routes, not just a sample.
- If a page returns an error or blank content, that's a "high" severity issue.
- After the JSON block, write a brief human-readable summary.
"""


def parse_auditor_response(raw: str) -> tuple[list[dict], str]:
    """Parse auditor output into (issues_list, human_summary).

    Returns ([], raw_text) if no valid JSON found.
    """
    json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', raw)
    if not json_match:
        json_match = re.search(r'(\[\s*\{[\s\S]*?\}\s*\])', raw)

    issues = []
    if json_match:
        try:
            issues = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    if json_match:
        summary = raw[:json_match.start()].strip() + "\n" + raw[json_match.end():].strip()
        summary = summary.strip()
    else:
        summary = raw.strip()

    if not summary:
        summary = f"Audit complete. {len(issues)} issue(s) found."

    return issues, summary

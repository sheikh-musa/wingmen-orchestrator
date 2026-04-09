"""Diagnostic Agent — analyzes bug reports and proposes fixes."""

from __future__ import annotations


def build_diagnostic_prompt(
    *,
    description: str,
    page_url: str | None,
    screenshot_description: str | None,
    repo_path: str,
    repo_context: str,  # CLAUDE.md + relevant file contents
    recent_commits: str,  # git log --oneline -10
) -> str:
    """Build the diagnostic prompt for a bug report."""

    page_hint = f"\nPage: {page_url}" if page_url else ""
    screenshot_hint = f"\nScreenshot shows: {screenshot_description}" if screenshot_description else ""

    return f"""You are a senior developer diagnosing a bug report.

Bug Report:
{description}{page_hint}{screenshot_hint}

Repository: {repo_path}
Recent commits:
{recent_commits}

Codebase context:
{repo_context}

Analyze this bug and respond with ONLY valid JSON:
{{
  "root_cause": "One sentence explaining the root cause",
  "confidence": "high" | "medium" | "low",
  "affected_files": ["exact/file/paths.ext"],
  "proposed_diff": "unified diff format showing the fix",
  "diagnosis_full": "Detailed diagnosis: error analysis, what triggers it, impact assessment, which users are affected, test plan to verify the fix"
}}

Confidence guide:
- high: Single file, clear error pattern (null check, typo, missing import, CSS fix)
- medium: 2-3 files affected, root cause identified but fix touches logic
- low: Multi-file, unclear cause, or you're not sure the proposed fix is correct

If you cannot determine the issue, set confidence to "low" and explain what's unclear in diagnosis_full.
"""


def parse_diagnostic_response(response: str) -> dict:
    """Parse the diagnostic agent's JSON response.

    Returns dict with: root_cause, confidence, affected_files, proposed_diff, diagnosis_full
    Falls back to sensible defaults if parsing fails.
    """
    import json
    import re

    # Try direct JSON parse
    try:
        data = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        # Try extracting from code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                data = None
        else:
            # Try finding JSON object
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except (json.JSONDecodeError, TypeError):
                    data = None
            else:
                data = None

    if not data or not isinstance(data, dict):
        return {
            "root_cause": "Unable to determine root cause automatically",
            "confidence": "low",
            "affected_files": [],
            "proposed_diff": "",
            "diagnosis_full": f"Automatic diagnosis failed. Raw response:\n{response[:2000]}",
        }

    return {
        "root_cause": data.get("root_cause", "Unknown"),
        "confidence": data.get("confidence", "low") if data.get("confidence") in ("high", "medium", "low") else "low",
        "affected_files": data.get("affected_files", []),
        "proposed_diff": data.get("proposed_diff", ""),
        "diagnosis_full": data.get("diagnosis_full", ""),
    }

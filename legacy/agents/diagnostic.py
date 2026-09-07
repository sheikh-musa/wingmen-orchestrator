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
    prev_diagnosis: str | None = None,
) -> str:
    """Build the diagnostic prompt for a bug report."""

    page_hint = f"\nPage: {page_url}" if page_url else ""
    screenshot_hint = f"\nScreenshot shows: {screenshot_description}" if screenshot_description else ""

    prev_attempt = ""
    if prev_diagnosis:
        prev_attempt = (
            f"\n\n## Previous Fix Attempt (FAILED)\n"
            f"The following diagnosis and fix was attempted but the reporter confirmed it did NOT resolve the issue:\n"
            f"{prev_diagnosis}\n\n"
            f"You MUST try a different approach."
        )

    return f"""You are a senior developer diagnosing a bug report.

Bug Report:
{description}{page_hint}{screenshot_hint}

Repository: {repo_path}
Recent commits:
{recent_commits}

Codebase context:
{repo_context}{prev_attempt}

Analyze this bug and respond with ONLY valid JSON:
{{
  "root_cause": "One sentence explaining the root cause",
  "confidence": "high" | "medium" | "low",
  "severity": "critical" | "high" | "medium" | "low",
  "affected_files": ["exact/file/paths.ext"],
  "proposed_diff": "unified diff format showing the fix",
  "diagnosis_full": "Detailed diagnosis: error analysis, what triggers it, impact assessment, which users are affected, test plan to verify the fix"
}}

Confidence guide:
- high: Single file, clear error pattern (null check, typo, missing import, CSS fix)
- medium: 2-3 files affected, root cause identified but fix touches logic
- low: Multi-file, unclear cause, or you're not sure the proposed fix is correct

Severity guide:
- critical: App crash, data loss, auth bypass
- high: Feature broken, page blank
- medium: UI glitch, wrong data shown
- low: Cosmetic, minor UX

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
            "severity": "medium",
            "affected_files": [],
            "proposed_diff": "",
            "diagnosis_full": f"Automatic diagnosis failed. Raw response:\n{response[:2000]}",
        }

    severity = data.get("severity", "medium")
    if severity not in ("critical", "high", "medium", "low"):
        severity = "medium"

    return {
        "root_cause": data.get("root_cause", "Unknown"),
        "confidence": data.get("confidence", "low") if data.get("confidence") in ("high", "medium", "low") else "low",
        "severity": severity,
        "affected_files": data.get("affected_files", []),
        "proposed_diff": data.get("proposed_diff", ""),
        "diagnosis_full": data.get("diagnosis_full", ""),
    }

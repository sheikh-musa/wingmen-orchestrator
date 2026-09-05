from __future__ import annotations

import json
import subprocess

from reel_triage import config

_EVIDENCE = {"cited", "anecdote", "vibes"}
_EFFORT = {"5min", "habit", "project"}

_PROMPT = """You are triaging one Instagram reel into a single concrete action.
Transcript:
{transcript}

Return ONLY strict JSON, no prose, with exactly these keys:
{{"topic": str, "claim": str, "evidence_grade": "cited"|"anecdote"|"vibes",
  "action": "one concrete first step", "effort": "5min"|"habit"|"project",
  "impact": int 1-5, "confidence": float 0-1}}"""


def priority(impact: int, confidence: float, effort: str) -> float:
    return round(impact * confidence / config.effort_weight(effort), 4)


def _run_claude(prompt: str) -> str:
    # Max-first (CAI-PROCESS-MAX-FIRST-001): the CLI routes through the Max plan.
    res = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True,
                         timeout=180)
    res.check_returncode()
    return res.stdout.strip()


def structure(transcript: str, keyframes: list[str]) -> dict:
    raw = _run_claude(_PROMPT.format(transcript=transcript))
    data = json.loads(raw)  # raises json.JSONDecodeError on non-JSON
    if data.get("evidence_grade") not in _EVIDENCE:
        raise ValueError(f"bad evidence_grade: {data.get('evidence_grade')}")
    if data.get("effort") not in _EFFORT:
        raise ValueError(f"bad effort: {data.get('effort')}")
    if not (1 <= int(data["impact"]) <= 5):
        raise ValueError(f"impact out of range: {data.get('impact')}")
    if not (0 <= float(data["confidence"]) <= 1):
        raise ValueError(f"confidence out of range: {data.get('confidence')}")
    data["priority"] = priority(int(data["impact"]), float(data["confidence"]),
                                data["effort"])
    return data

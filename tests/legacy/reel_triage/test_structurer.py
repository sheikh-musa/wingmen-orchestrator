import json

import pytest

from reel_triage import structurer


def test_priority_formula():
    # impact*confidence / effort_weight ; habit weight = 2 -> 4*0.5/2 = 1.0
    assert structurer.priority(impact=4, confidence=0.5, effort="habit") == 1.0
    assert structurer.priority(impact=5, confidence=1.0, effort="5min") == 5.0
    assert structurer.priority(impact=4, confidence=1.0, effort="project") == 1.0


def test_structure_parses_strict_json(monkeypatch):
    payload = {"topic": "sleep", "claim": "x", "evidence_grade": "cited",
               "action": "go to bed by 11", "effort": "habit", "impact": 4, "confidence": 0.5}
    monkeypatch.setattr(structurer, "_run_claude", lambda prompt: json.dumps(payload))
    out = structurer.structure("transcript text", ["frame1.jpg"])
    assert out["action"] == "go to bed by 11"
    assert out["priority"] == 1.0


def test_structure_rejects_bad_evidence_grade(monkeypatch):
    bad = {"topic": "t", "claim": "c", "evidence_grade": "BOGUS",
           "action": "a", "effort": "5min", "impact": 3, "confidence": 0.9}
    monkeypatch.setattr(structurer, "_run_claude", lambda prompt: json.dumps(bad))
    with pytest.raises(ValueError):
        structurer.structure("t", [])


def test_structure_rejects_impact_out_of_range(monkeypatch):
    bad = {"topic": "t", "claim": "c", "evidence_grade": "cited",
           "action": "a", "effort": "5min", "impact": 9, "confidence": 0.9}
    monkeypatch.setattr(structurer, "_run_claude", lambda prompt: json.dumps(bad))
    with pytest.raises(ValueError):
        structurer.structure("t", [])


def test_structure_raises_on_non_json(monkeypatch):
    monkeypatch.setattr(structurer, "_run_claude", lambda prompt: "not json at all")
    with pytest.raises(json.JSONDecodeError):
        structurer.structure("t", [])

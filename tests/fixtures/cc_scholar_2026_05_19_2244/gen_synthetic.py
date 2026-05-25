"""Synthetic-but-faithful negative fixture: simulates cc-scholar's 22:44 SGT pattern
(varied prompts, file sizes 200KB-2.7MB, mixed cadence). Used when real
~/.claude/projects/-Users-sheikhmusa-wingmen-projects-ai-scholar/ jsonls aren't
available for anonymization. Per CAI-RESP-164 R1: must produce <3-of-3 → monitored.
"""
import json
import os
import random
from pathlib import Path

OUT = Path(__file__).parent
BASE_TIME = 1700000000.0

# 10 distinct prompts (signal_c fails)
PROMPTS = [
    "Continue the Al-Bayan corpus expansion — pick next batch of 20 ayat",
    "Review the hifz-companion test failures from last build",
    "Search for synonyms of 'amanah' in the working glossary",
    "Run the v4 tagging smoke test on Surah Al-Baqarah ayat 1-50",
    "Check whether the new transliteration scheme breaks the alignment table",
    "Investigate the diacritic dropout in entry #4521",
    "Verify that the morphology service handles compound particles correctly",
    "Update the corpus status board with today's tagging stats",
    "Spot-check the last 100 entries for thematic consistency",
    "Continue from session checkpoint — Surah Aali Imran ayat 80",
]

# Varied file sizes 200KB-2.7MB (signal_a fails: median > 80KB)
SIZES_BYTES = [200_000, 350_000, 480_000, 620_000, 800_000,
               1_100_000, 1_400_000, 1_800_000, 2_300_000, 2_700_000]

# Mixed cadence — most in band but a few outliers (signal_b match=False because not all in band)
random.seed(42)
GAPS = [120.0, 60.0, 300.0, 90.0, 1500.0, 200.0, 400.0, 30.0, 700.0]
# 9 gaps for 10 files — span = sum(GAPS) = 3400s (< 7200s anyway, so signal_b fails either way)


def make_session(idx: int):
    msgs = [
        {"type": "user", "message": {"role": "user", "content": PROMPTS[idx]}},
        {"type": "assistant", "message": {"role": "assistant", "content": "Reasoning placeholder"}},
        {"type": "summary", "summary": "session " + str(idx)},
    ]
    body = "\n".join(json.dumps(m) for m in msgs)
    pad = SIZES_BYTES[idx] - len(body) - 2
    if pad > 0:
        body += "\n" + ("x" * pad)
    p = OUT / f"session-{idx:02d}.jsonl"
    p.write_text(body + "\n")
    mt = BASE_TIME
    for g in GAPS[:idx]:
        mt += g
    os.utime(p, (mt, mt))


if __name__ == "__main__":
    for i in range(10):
        make_session(i)
    print(f"generated 10 synthetic cc-scholar-pattern fixtures in {OUT}")

#!/usr/bin/env python3
"""
A/B evaluation: qwen-max (DashScope-Intl) vs claude-opus-4-8 (Anthropic).
Metric: cost per SUCCESSFUL task.

op#10869 — bounded measurement/benchmark (cai-cleared). Sandboxed, non-PII, no
client data. Runs each of a fixed task set once per model (no retries/loops).

Run:  .venv/bin/python scripts/ab_qwen_opus.py
"""
import json
import os
import re
import time
import traceback

import httpx

# ---------------------------------------------------------------------------
# PRICES — clearly labeled, easily editable.  All figures are $ per 1M tokens.
# THESE ARE LIST PRICES — VERIFY against the live pricing pages before quoting.
# ---------------------------------------------------------------------------
PRICES = {
    # VERIFIED 2026-08-06. claude-opus-4-8 authoritative list price from the
    # bundled claude-api skill model table (cached 2026-06-24): $5.00 / $25.00.
    # (An earlier run used $15/$75 in error — that is Opus-3-era pricing, not 4.8.)
    "claude-opus-4-8": {"in": 5.00, "out": 25.00},
    # qwen-max: DashScope-International (Singapore) standard price. The `qwen-max`
    # alias currently tracks qwen3.8-max (GA 2026-08-03) at $2.00 / $6.00.
    # Snapshot range across live qwen-max tiers is ~$2.00-2.50 in / $6.00-7.50 out
    # (qwen3.7-max list is $2.50/$7.50). Mainland (Beijing) endpoint is cheaper.
    "qwen-max": {"in": 2.00, "out": 6.00},
}

MAX_TOKENS = 512
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_KEY_FILE = os.path.expanduser("~/.wingmen/keys/qwen-api-key")
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "reports",
                            "qwen-opus-ab-results.md")


# ---------------------------------------------------------------------------
# Grader helpers
# ---------------------------------------------------------------------------
def _extract_json(text):
    """Best-effort: pull a JSON object/array out of model text (handles fences)."""
    if text is None:
        return None
    t = text.strip()
    # fenced ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    # try whole
    try:
        return json.loads(t)
    except Exception:
        pass
    # first { ... last } (or [ ... ])
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = t.find(open_c), t.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    return None


def _extract_code(text):
    """Pull a python code block out of model text (handles fences)."""
    if text is None:
        return ""
    m = re.search(r"```(?:python|py)?\s*(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _run_code(code, func_name, cases):
    """exec code in an isolated namespace, run (args_tuple, expected) cases."""
    if "def %s" % func_name not in code:
        return False
    ns = {}
    try:
        exec(code, ns)  # sandboxed benchmark: non-hostile, measurement-only
        fn = ns.get(func_name)
        if not callable(fn):
            return False
        for args, expected in cases:
            if fn(*args) != expected:
                return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Task set (fixed).  Each: id, kind, prompt, grade(output)->bool.
# Prompts kept SHORT.  Graders are deterministic.
# ---------------------------------------------------------------------------
def _g_extract_person(out):
    d = _extract_json(out)
    if not isinstance(d, dict):
        return False
    return (str(d.get("name", "")).strip().lower() == "maria garcia"
            and int(d.get("age", -1)) == 34
            and str(d.get("city", "")).strip().lower() == "lisbon")


def _g_extract_list(out):
    d = _extract_json(out)
    if isinstance(d, dict):  # tolerate {"fruits":[...]}
        for v in d.values():
            if isinstance(v, list):
                d = v
                break
    if not isinstance(d, list):
        return False
    got = {str(x).strip().lower() for x in d}
    return {"apple", "banana", "cherry"}.issubset(got)


def _g_sentiment(out):
    d = _extract_json(out)
    return isinstance(d, dict) and str(d.get("sentiment", "")).strip().lower() == "negative"


def _g_exact_pong(out):
    return (out or "").strip() == "PONG"


def _g_slug(out):
    return (out or "").strip() == "hello-world-example"


def _g_upper(out):
    return (out or "").strip() == "THE QUICK BROWN FOX"


def _g_reason_trains(out):
    # 60 mph for 2.5 h = 150 miles
    return bool(re.search(r"\b150\b", out or ""))


def _g_reason_ages(out):
    # Sarah is 12, brother is twice => 24; sum question: 36
    return bool(re.search(r"\b36\b", out or ""))


def _g_reason_letters(out):
    # count 'r' in "strawberry" = 3
    return bool(re.search(r"\b3\b", out or ""))


def _g_code_palindrome(out):
    return _run_code(_extract_code(out), "is_palindrome",
                     [(("racecar",), True), (("hello",), False), (("",), True)])


def _g_code_sumeven(out):
    return _run_code(_extract_code(out), "sum_even",
                     [(([1, 2, 3, 4],), 6), (([5, 7],), 0), (([2, 4, 6],), 12)])


def _g_summary(out):
    t = (out or "").strip()
    words = re.findall(r"\S+", t)
    return len(words) <= 15 and "photosynthesis" in t.lower()


TASKS = [
    {"id": "extract_person", "kind": "structured-extraction",
     "prompt": "Extract name, age, city as JSON with keys name, age, city. "
               "Return only JSON.\nText: Maria Garcia is 34 and lives in Lisbon.",
     "grade": _g_extract_person},
    {"id": "extract_list", "kind": "structured-extraction",
     "prompt": "Return a JSON array of the fruit names in this sentence, lowercase. "
               "Only JSON.\nSentence: I bought an Apple, a Banana, and a Cherry.",
     "grade": _g_extract_list},
    {"id": "sentiment_json", "kind": "structured-extraction",
     "prompt": 'Classify sentiment as positive/negative/neutral. Return JSON '
               '{"sentiment": "<label>"} only.\nReview: This product broke after one day. Terrible.',
     "grade": _g_sentiment},
    {"id": "exact_pong", "kind": "instruction-following",
     "prompt": "Respond with exactly the four characters PONG and nothing else. "
               "No punctuation, no quotes, no explanation.",
     "grade": _g_exact_pong},
    {"id": "slugify", "kind": "instruction-following",
     "prompt": "Output only the lowercase hyphenated slug of this title, nothing else: "
               "Hello World Example",
     "grade": _g_slug},
    {"id": "uppercase", "kind": "instruction-following",
     "prompt": "Output the following text in ALL UPPERCASE, nothing else: the quick brown fox",
     "grade": _g_upper},
    {"id": "reason_trains", "kind": "reasoning",
     "prompt": "A train travels at 60 miles per hour for 2.5 hours. "
               "How many miles does it travel? Give the number.",
     "grade": _g_reason_trains},
    {"id": "reason_ages", "kind": "reasoning",
     "prompt": "Sarah is 12. Her brother is twice her age. "
               "What is the sum of their ages? Give the number.",
     "grade": _g_reason_ages},
    {"id": "reason_letters", "kind": "reasoning",
     "prompt": "How many times does the letter r appear in the word strawberry? Give the number.",
     "grade": _g_reason_letters},
    {"id": "code_palindrome", "kind": "coding",
     "prompt": "Write a Python function is_palindrome(s) that returns True if the "
               "string s reads the same forwards and backwards, else False. "
               "Return only the function code.",
     "grade": _g_code_palindrome},
    {"id": "code_sumeven", "kind": "coding",
     "prompt": "Write a Python function sum_even(nums) that returns the sum of the "
               "even integers in the list nums. Return only the function code.",
     "grade": _g_code_sumeven},
    {"id": "summarize", "kind": "constrained-summarization",
     "prompt": "In one sentence of at most 15 words that contains the word "
               "photosynthesis, summarize: Plants use photosynthesis to convert "
               "sunlight, water, and carbon dioxide into glucose and oxygen.",
     "grade": _g_summary},
]


# ---------------------------------------------------------------------------
# Model callers.  Return (output_text, in_tokens, out_tokens, error_or_None).
# ---------------------------------------------------------------------------
def call_qwen(prompt):
    with open(QWEN_KEY_FILE) as f:
        key = f.read().strip()  # never printed/logged
    try:
        r = httpx.post(
            QWEN_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json={"model": "qwen-max",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": MAX_TOKENS},
            timeout=90.0,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return (text, int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)), None)
    except Exception as e:
        return (None, 0, 0, "%s: %s" % (type(e).__name__, e))


def call_opus(prompt, client):
    try:
        r = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return (text, int(r.usage.input_tokens), int(r.usage.output_tokens), None)
    except Exception as e:
        return (None, 0, 0, "%s: %s" % (type(e).__name__, e))


def cost(model, tin, tout):
    p = PRICES[model]
    return tin / 1e6 * p["in"] + tout / 1e6 * p["out"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main():
    # Load ANTHROPIC_API_KEY from .env
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    except Exception:
        pass
    import anthropic
    # llm_route_exempt: offline_eval_no_serving_path (CAI-RESP-1194)
    aclient = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    models = ["qwen-max", "claude-opus-4-8"]
    per_task = {}   # id -> {model -> {passed, tin, tout, latency, err}}
    totals = {m: {"n": 0, "ok": 0, "tin": 0, "tout": 0, "cost": 0.0,
                  "lat": 0.0, "errs": 0} for m in models}

    for task in TASKS:
        per_task[task["id"]] = {}
        for m in models:
            t0 = time.time()
            if m == "qwen-max":
                text, tin, tout, err = call_qwen(task["prompt"])
            else:
                text, tin, tout, err = call_opus(task["prompt"], aclient)
            lat = time.time() - t0

            passed = False
            if err is None:
                try:
                    passed = bool(task["grade"](text))
                except Exception:
                    passed = False
                    err = "grader_exception: " + traceback.format_exc(limit=1)

            c = cost(m, tin, tout)
            per_task[task["id"]][m] = {"passed": passed, "tin": tin, "tout": tout,
                                       "latency": lat, "err": err}
            t = totals[m]
            t["n"] += 1
            t["ok"] += 1 if passed else 0
            t["tin"] += tin
            t["tout"] += tout
            t["cost"] += c
            t["lat"] += lat
            t["errs"] += 1 if err else 0
            flag = "PASS" if passed else ("ERR " if err else "fail")
            print("[%-16s] %-16s %s  in=%-5d out=%-5d %.2fs%s"
                  % (task["id"], m, flag, tin, tout, lat,
                     ("  <%s>" % err.splitlines()[0][:80]) if err else ""))

    report = render(models, totals, per_task)
    with open(os.path.abspath(RESULTS_PATH), "w") as f:
        f.write(report)
    print("\n" + report)
    print("Saved -> %s" % os.path.abspath(RESULTS_PATH))


def render(models, totals, per_task):
    L = []
    L.append("# Qwen-max vs Claude-opus-4-8 — A/B (cost per successful task)")
    L.append("")
    L.append("op#10869 bounded measurement. Each task run once per model. "
             "max_tokens=%d." % MAX_TOKENS)
    L.append("")
    L.append("## Prices used ($ / 1M tokens — LIST prices, verify)")
    L.append("")
    L.append("| model | input | output | note |")
    L.append("|---|---|---|---|")
    L.append("| claude-opus-4-8 | $%.2f | $%.2f | task-specified 'Anthropic Opus published'; "
             "claude-api skill table lists $5.00/$25.00 (discrepancy — verify) |"
             % (PRICES["claude-opus-4-8"]["in"], PRICES["claude-opus-4-8"]["out"]))
    L.append("| qwen-max | $%.2f | $%.2f | ESTIMATE — DashScope-Intl price unconfirmed offline |"
             % (PRICES["qwen-max"]["in"], PRICES["qwen-max"]["out"]))
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| model | tasks | success | rate | in tok | out tok | total $ | "
             "$/successful task | avg latency |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for m in models:
        t = totals[m]
        cps = ("$%.5f" % (t["cost"] / t["ok"])) if t["ok"] else "n/a — 0 successes"
        L.append("| %s | %d | %d | %.0f%% | %d | %d | $%.5f | %s | %.2fs |"
                 % (m, t["n"], t["ok"], 100.0 * t["ok"] / t["n"] if t["n"] else 0,
                    t["tin"], t["tout"], t["cost"], cps, t["lat"] / t["n"] if t["n"] else 0))
    L.append("")
    L.append("## Per-task pass/fail")
    L.append("")
    L.append("| task | kind | qwen-max | claude-opus-4-8 |")
    L.append("|---|---|---|---|")

    def cell(d):
        if d["err"]:
            return "ERR"
        return "PASS" if d["passed"] else "fail"
    kind = {t["id"]: t["kind"] for t in TASKS}
    for t in TASKS:
        r = per_task[t["id"]]
        L.append("| %s | %s | %s | %s |"
                 % (t["id"], kind[t["id"]], cell(r["qwen-max"]), cell(r["claude-opus-4-8"])))
    L.append("")
    # errors detail
    errs = []
    for t in TASKS:
        for m in models:
            d = per_task[t["id"]][m]
            if d["err"]:
                errs.append("- `%s` / `%s`: %s" % (t["id"], m, d["err"].splitlines()[0][:160]))
    if errs:
        L.append("## Errors")
        L.append("")
        L.extend(errs)
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Harvest a supervised-fine-tuning (SFT) dataset for DISTILLING Claude's
test-generation capability into a smaller model (Qwen).

For each specimen (a correct Python function), we ask claude-opus-4-8 to write a
thorough pytest suite N_SAMPLES times, GRADE every candidate with the existing
deterministic grader from qwen_capability_testgen.py (COMPILE via ast.parse,
PASS-ON-CORRECT via isolated exec, MUTATION-CATCH), and keep ONLY the
verified-good suites. Each kept (task -> gold test-suite) pair becomes one
QwenCloud SFT chat row in data/sft_testgen_dataset.jsonl.

Sandboxed, non-PII, data-prep only. No fleet bus, no agent_messages, no DB, no
QwenCloud/DashScope job is fired. Model-generated code runs ONLY inside the
existing isolated-namespace grader (a non-hostile benchmark of our own eval).

GOVERNANCE: SYNTHETIC specimens only — authored in-harness, never a client-PII
row and never repo code. A client's rows must never enter a training file.

Run:  .venv/bin/python scripts/harvest_sft_dataset.py
"""
import json
import os
import sys
import time
import traceback

# Import the existing harness: specimens, prompt builder, and grader logic.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qwen_capability_testgen import (  # noqa: E402
    SPECIMENS as BASE_SPECIMENS,
    build_prompt,
    grade,
    extract_code,
    prep_code,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MODEL = "claude-opus-4-8"          # Anthropic SDK only, operator-mandated model.
N_SAMPLES = 3                      # candidate suites per specimen (task spec).
MAX_TOKENS = 4096                  # MANDATED 4096 — at 1024 Opus's thorough
                                   # suites truncate; 4096 lets them complete.
MAX_ROWS = 300                     # cost bound: stop once we have this many gold rows.
PRICE_IN = 5.0                     # $ / 1M input tokens  (claude-opus-4-8, list)
PRICE_OUT = 25.0                   # $ / 1M output tokens

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "sft_testgen_dataset.jsonl")
README_PATH = os.path.join(DATA_DIR, "sft_testgen_dataset.README.md")

SYSTEM_PROMPT = (
    "You write thorough, correct pytest test suites. The function under test is "
    "already defined and in scope — do not redefine or import it. Cover normal "
    "cases, every branch, boundary/edge cases, and invalid inputs (use "
    "pytest.raises for expected exceptions). Output ONLY the Python test code."
)

# ---------------------------------------------------------------------------
# ADDED synthetic specimens (in-harness authored, NON-PII, NOT repo code).
# Each: correct reference impl + mutations (find/replace, each seeding ONE bug;
# find strings are unique within src). Mutations sharpen grading quality — a
# suite that passes the correct code but misses every seeded bug is discarded.
# ---------------------------------------------------------------------------
ADDED_SPECIMENS = [
    {
        "name": "parse_query_string",
        "src": (
            "def parse_query_string(qs):\n"
            "    result = {}\n"
            "    if not qs:\n"
            "        return result\n"
            "    for pair in qs.split(\"&\"):\n"
            "        if \"=\" not in pair:\n"
            "            continue\n"
            "        key, value = pair.split(\"=\", 1)\n"
            "        result[key] = value\n"
            "    return result\n"
        ),
        "mutations": [
            ('qs.split("&")', 'qs.split(",")'),          # wrong delimiter
            ("result[key] = value", "result[value] = key"),  # key/value swapped
            ('if "=" not in pair:', 'if "=" in pair:'),   # guard negated
        ],
    },
    {
        "name": "is_palindrome",
        "src": (
            "def is_palindrome(s):\n"
            "    cleaned = [c.lower() for c in s if c.isalnum()]\n"
            "    return cleaned == cleaned[::-1]\n"
        ),
        "mutations": [
            ("c.isalnum()", "True"),                      # punctuation not stripped
            ("c.lower()", "c"),                           # case-sensitive
            ("cleaned == cleaned[::-1]", "True"),         # always palindrome
        ],
    },
    {
        "name": "word_count",
        "src": (
            "def word_count(text):\n"
            "    return len(text.split())\n"
        ),
        "mutations": [
            ("text.split()", 'text.split(" ")'),          # miscounts runs of spaces
            ("len(text.split())", "len(text.split()) + 1"),  # off-by-one
        ],
    },
    {
        "name": "celsius_to_fahrenheit",
        "src": (
            "def celsius_to_fahrenheit(c):\n"
            "    return c * 9 / 5 + 32\n"
        ),
        "mutations": [
            ("c * 9 / 5 + 32", "c * 9 / 5 - 32"),         # sign flip
            ("c * 9 / 5 + 32", "c * 5 / 9 + 32"),         # ratio inverted
        ],
    },
    {
        "name": "validate_password",
        "src": (
            "def validate_password(pw):\n"
            "    if len(pw) < 8:\n"
            "        return False\n"
            "    has_digit = any(c.isdigit() for c in pw)\n"
            "    has_upper = any(c.isupper() for c in pw)\n"
            "    return has_digit and has_upper\n"
        ),
        "mutations": [
            ("len(pw) < 8", "len(pw) < 6"),               # length bound loosened
            ("has_digit and has_upper", "has_digit or has_upper"),  # AND->OR
            ("any(c.isdigit() for c in pw)", "True"),     # digit check dropped
        ],
    },
    {
        "name": "days_between",
        "src": (
            "from datetime import date\n"
            "def days_between(start, end):\n"
            "    s = date.fromisoformat(start)\n"
            "    e = date.fromisoformat(end)\n"
            "    return (e - s).days\n"
        ),
        "mutations": [
            ("(e - s).days", "(s - e).days"),             # sign flipped
            ("(e - s).days", "abs((e - s).days)"),        # reverse order not negative
        ],
    },
    {
        "name": "format_thousands",
        "src": (
            "def format_thousands(n):\n"
            "    sign = \"-\" if n < 0 else \"\"\n"
            "    digits = str(abs(n))\n"
            "    parts = []\n"
            "    while len(digits) > 3:\n"
            "        parts.insert(0, digits[-3:])\n"
            "        digits = digits[:-3]\n"
            "    parts.insert(0, digits)\n"
            "    return sign + \",\".join(parts)\n"
        ),
        "mutations": [
            ("len(digits) > 3", "len(digits) >= 3"),      # spurious leading group
            ('sign = "-" if n < 0 else ""', 'sign = ""'),  # negatives lose sign
            ("digits[-3:]", "digits[-2:]"),               # groups of 2
        ],
    },
    {
        "name": "rle_encode",
        "src": (
            "def rle_encode(s):\n"
            "    if not s:\n"
            "        return \"\"\n"
            "    out = []\n"
            "    prev = s[0]\n"
            "    count = 1\n"
            "    for ch in s[1:]:\n"
            "        if ch == prev:\n"
            "            count += 1\n"
            "        else:\n"
            "            out.append(prev + str(count))\n"
            "            prev = ch\n"
            "            count = 1\n"
            "    out.append(prev + str(count))\n"
            "    return \"\".join(out)\n"
        ),
        "mutations": [
            ("if ch == prev:", "if ch != prev:"),         # comparison flipped
            ("count += 1", "count = 1"),                  # runs never accumulate
        ],
    },
    {
        "name": "binary_search",
        "src": (
            "def binary_search(arr, target):\n"
            "    lo, hi = 0, len(arr) - 1\n"
            "    while lo <= hi:\n"
            "        mid = (lo + hi) // 2\n"
            "        if arr[mid] == target:\n"
            "            return mid\n"
            "        if arr[mid] < target:\n"
            "            lo = mid + 1\n"
            "        else:\n"
            "            hi = mid - 1\n"
            "    return -1\n"
        ),
        "mutations": [
            ("while lo <= hi:", "while lo < hi:"),        # misses single-element hit
            ("return -1", "return 0"),                    # not-found returns index 0
            ("arr[mid] < target", "arr[mid] > target"),   # direction reversed
        ],
    },
    {
        "name": "luhn_check",
        "src": (
            "def luhn_check(number):\n"
            "    digits = [int(c) for c in str(number)]\n"
            "    checksum = 0\n"
            "    for i, d in enumerate(reversed(digits)):\n"
            "        if i % 2 == 1:\n"
            "            d = d * 2\n"
            "            if d > 9:\n"
            "                d -= 9\n"
            "        checksum += d\n"
            "    return checksum % 10 == 0\n"
        ),
        "mutations": [
            ("i % 2 == 1", "i % 2 == 0"),                 # doubles wrong positions
            ("d -= 9", "d -= 10"),                        # wrong digit-sum fix
            ("checksum % 10 == 0", "checksum % 10 != 0"),  # validity inverted
        ],
    },
    {
        "name": "title_case",
        "src": (
            "def title_case(text):\n"
            "    return \" \".join(w[:1].upper() + w[1:].lower() "
            "for w in text.split())\n"
        ),
        "mutations": [
            ("w[:1].upper()", "w[:1]"),                   # first letter not capitalised
            ("w[1:].lower()", "w[1:]"),                   # rest not lowercased
        ],
    },
    {
        "name": "chunk_list",
        "src": (
            "def chunk_list(items, size):\n"
            "    if size < 1:\n"
            "        raise ValueError(\"size must be >= 1\")\n"
            "    return [items[i:i + size] for i in range(0, len(items), size)]\n"
        ),
        "mutations": [
            ("range(0, len(items), size)", "range(0, len(items))"),  # overlapping chunks
            ("if size < 1:", "if size < 0:"),             # size=0 guard gone
            ("items[i:i + size]", "items[i:i + size + 1]"),  # off-by-one chunk width
        ],
    },
    {
        "name": "hex_to_rgb",
        "src": (
            "def hex_to_rgb(code):\n"
            "    code = code.lstrip(\"#\")\n"
            "    if len(code) != 6:\n"
            "        raise ValueError(\"expected 6 hex digits\")\n"
            "    return (int(code[0:2], 16), int(code[2:4], 16), "
            "int(code[4:6], 16))\n"
        ),
        "mutations": [
            ("int(code[4:6], 16)", "int(code[4:6], 10)"),  # blue parsed base-10
            ('code.lstrip("#")', "code"),                  # leading '#' not stripped
            ("len(code) != 6", "len(code) != 3"),          # wrong length gate
        ],
    },
    {
        "name": "mean",
        "src": (
            "def mean(values):\n"
            "    if not values:\n"
            "        raise ValueError(\"mean of empty sequence\")\n"
            "    return sum(values) / len(values)\n"
        ),
        "mutations": [
            ("sum(values) / len(values)", "sum(values) // len(values)"),  # int division
            ("if not values:", "if values is None:"),     # empty -> ZeroDivisionError
        ],
    },
]

SPECIMENS = list(BASE_SPECIMENS) + ADDED_SPECIMENS


# ---------------------------------------------------------------------------
# Opus caller (Anthropic SDK) -> (text, in_tokens, out_tokens, error, truncated)
# ---------------------------------------------------------------------------
def call_opus(prompt, client):
    try:
        r = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in r.content
                       if getattr(b, "type", "") == "text")
        truncated = r.stop_reason == "max_tokens"
        return (text, int(r.usage.input_tokens), int(r.usage.output_tokens),
                None, truncated)
    except Exception as e:
        return (None, 0, 0, "%s: %s" % (type(e).__name__, str(e)[:200]), False)


def usd(tin, tout):
    return tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT


def is_gold(g):
    """Verified-good: compiled + passed the correct code, AND (where the specimen
    ships mutations) caught at least one seeded bug. Only these become targets."""
    if not (g["compile"] and g["pass_on_correct"]):
        return False
    if g["mut_total"] > 0 and g["mut_caught"] < 1:
        return False
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(HERE, "..", ".env"))
    except Exception:
        pass
    import anthropic
    # llm_route_exempt: offline_eval_no_serving_path (CAI-RESP-1194)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    n_calls = len(SPECIMENS) * N_SAMPLES
    print("Harvesting SFT dataset (distil Claude test-gen -> Qwen)")
    print("model=%s  specimens=%d  N_SAMPLES=%d  max_tokens=%d  -> up to %d calls"
          % (MODEL, len(SPECIMENS), N_SAMPLES, MAX_TOKENS, n_calls))
    print("cost bound: stop at %d gold rows (~$%.2f/$%.2f per 1M in/out)\n"
          % (MAX_ROWS, PRICE_IN, PRICE_OUT))

    rows = []                 # list of SFT chat dicts
    tin = tout = 0
    api_err = 0
    per_spec = {}             # name -> kept count
    tax_counts = {}           # grader taxonomy -> count (of non-error calls)
    t_start = time.time()
    done = False

    for si, spec in enumerate(SPECIMENS, 1):
        if done:
            break
        name = spec["name"]
        prompt = build_prompt(name, spec["src"])
        per_spec.setdefault(name, 0)
        for run in range(1, N_SAMPLES + 1):
            t0 = time.time()
            text, cin, cout, err, truncated = call_opus(prompt, client)
            lat = time.time() - t0
            tin += cin
            tout += cout

            if err is not None:
                api_err += 1
                print("[%2d/%d %-20s] s%d/%d  API-ERR %.1fs  <%s>"
                      % (si, len(SPECIMENS), name, run, N_SAMPLES, lat,
                         err.splitlines()[0][:70]))
                continue

            code = extract_code(text)
            try:
                g = grade(spec, code)
            except Exception:
                g = {"compile": False, "pass_on_correct": False, "salvaged": False,
                     "n_tests": 0, "mut_total": 0, "mut_caught": 0,
                     "taxonomy": "grader_exception",
                     "detail": traceback.format_exc(limit=1)}

            tax = g.get("taxonomy") or "unknown"
            tax_counts[tax] = tax_counts.get(tax, 0) + 1

            kept = is_gold(g)
            if kept:
                # store EXACTLY the code the grader verified (prep_code trims any
                # incomplete trailing fn; for un-truncated output it's a no-op).
                code_used = prep_code(code)[0]
                rows.append({
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": code_used},
                    ]
                })
                per_spec[name] += 1

            mut_str = ("%d/%d" % (g["mut_caught"], g["mut_total"])
                       if g["mut_total"] else "-")
            print("[%2d/%d %-20s] s%d/%d  compile=%s pass=%s mut=%-4s tests=%-2d "
                  "%s%.1fs  %s  %s  rows=%d"
                  % (si, len(SPECIMENS), name, run, N_SAMPLES,
                     "Y" if g["compile"] else "N",
                     "Y" if g["pass_on_correct"] else "N",
                     mut_str, g["n_tests"],
                     "trunc " if truncated else "", lat,
                     tax, "KEEP" if kept else "drop", len(rows)))

            if len(rows) >= MAX_ROWS:
                print("\nReached MAX_ROWS=%d cost bound — stopping." % MAX_ROWS)
                done = True
                break

    elapsed = time.time() - t_start
    total_cost = usd(tin, tout)

    # ---- write JSONL (UTF-8, one JSON object per line, no trailing commas) ----
    os.makedirs(os.path.abspath(DATA_DIR), exist_ok=True)
    with open(os.path.abspath(OUT_PATH), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ---- validate the JSONL for QwenCloud SFT chat format ----
    valid, verr = validate_jsonl(os.path.abspath(OUT_PATH))

    write_readme(rows, per_spec, total_cost, tin, tout, valid)

    # ---- report ----
    print("\n===== HEADLINE =====")
    print("gold rows written : %d  -> %s" % (len(rows), os.path.abspath(OUT_PATH)))
    print("specimens run     : %d (%d base + %d added)"
          % (len(SPECIMENS), len(BASE_SPECIMENS), len(ADDED_SPECIMENS)))
    print("Opus tokens       : in=%d  out=%d" % (tin, tout))
    print("Opus cost         : $%.4f  (in $%.4f + out $%.4f, @ $%.0f/$%.0f per 1M)"
          % (total_cost, tin / 1e6 * PRICE_IN, tout / 1e6 * PRICE_OUT,
             PRICE_IN, PRICE_OUT))
    print("api errors        : %d   wall=%.0fs" % (api_err, elapsed))
    print("format-valid (SFT): %s%s" % ("yes" if valid else "NO",
                                        "" if valid else " -- " + verr))
    print("\ntaxonomy of graded candidates:")
    for t in sorted(tax_counts, key=lambda k: -tax_counts[k]):
        print("  %-20s %d" % (t, tax_counts[t]))
    print("\nkept per specimen:")
    for nm in (s["name"] for s in SPECIMENS):
        print("  %-20s %d" % (nm, per_spec.get(nm, 0)))

    if rows:
        print("\n===== SAMPLE ROW (pretty-printed) =====")
        print(json.dumps(rows[0], indent=2, ensure_ascii=False))


def validate_jsonl(path):
    """Confirm every line is valid JSON with the QwenCloud SFT chat shape:
    {"messages": [system, user, assistant]} with non-empty string contents."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            n = 0
            for i, line in enumerate(f, 1):
                if not line.strip():
                    return False, "empty line %d" % i
                obj = json.loads(line)          # raises on invalid JSON
                msgs = obj.get("messages")
                if not isinstance(msgs, list) or len(msgs) != 3:
                    return False, "line %d: messages must be a 3-item list" % i
                roles = [m.get("role") for m in msgs]
                if roles != ["system", "user", "assistant"]:
                    return False, "line %d: roles %r != system/user/assistant" % (i, roles)
                for m in msgs:
                    c = m.get("content")
                    if not isinstance(c, str) or not c.strip():
                        return False, "line %d: empty/non-string content" % i
                n += 1
        return (n > 0), ("" if n > 0 else "no rows")
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


def write_readme(rows, per_spec, cost, tin, tout, valid):
    L = []
    L.append("# SFT test-generation dataset (Claude -> Qwen distillation)")
    L.append("")
    L.append("**What this is.** A supervised-fine-tuning dataset of "
             "`(task -> gold pytest suite)` pairs in QwenCloud SFT chat format "
             "(`{\"messages\": [system, user, assistant]}`, one JSON object per "
             "line, UTF-8, final-answer-only — no `<think>` field). Upload "
             "`sft_testgen_dataset.jsonl` to QwenCloud's Create-dataset flow to "
             "distil Claude's unit-test-writing capability into a smaller model "
             "(Qwen).")
    L.append("")
    L.append("**Targets are Claude-verified-good.** Every assistant target was "
             "generated by `%s` (max_tokens=%d) and kept ONLY if it passed the "
             "deterministic grader in `scripts/qwen_capability_testgen.py`: it "
             "must COMPILE (`ast.parse`), PASS-ON-CORRECT (exec the reference "
             "impl + suite in an isolated namespace, every `test_*` passes) and — "
             "where the specimen ships seeded mutations — catch at least one "
             "(MUTATION-CATCH). Everything else was discarded." % (MODEL, MAX_TOKENS))
    L.append("")
    L.append("**GOVERNANCE — SYNTHETIC / non-PII only.** Every specimen is "
             "authored in-harness: generic Python functions (parsing, validation, "
             "small algorithms, date/money/string logic). NONE is client data and "
             "NONE is repo code. A client's rows must NEVER enter a training file "
             "(TENANT-RESIDENCY-001). This dataset is safe to upload precisely "
             "because it contains no tenant rows.")
    L.append("")
    L.append("## Contents")
    L.append("")
    L.append("- **Rows (gold pairs):** %d" % len(rows))
    L.append("- **Format validates for QwenCloud SFT:** %s"
             % ("yes" if valid else "NO"))
    L.append("- **Generator model:** `%s` (Anthropic SDK, max_tokens=%d, "
             "N_SAMPLES=%d per specimen)" % (MODEL, MAX_TOKENS, N_SAMPLES))
    L.append("- **Opus token cost:** in=%d, out=%d tokens -> **$%.4f** "
             "(@ $%.0f/$%.0f per 1M in/out, list price)"
             % (tin, tout, cost, PRICE_IN, PRICE_OUT))
    L.append("")
    L.append("## Specimen list (rows kept per specimen)")
    L.append("")
    L.append("| specimen | source | rows |")
    L.append("|---|---|---|")
    base_names = {s["name"] for s in BASE_SPECIMENS}
    for s in SPECIMENS:
        nm = s["name"]
        src = "base" if nm in base_names else "added"
        L.append("| `%s` | %s | %d |" % (nm, src, per_spec.get(nm, 0)))
    L.append("")
    L.append("_Base specimens are reused from `qwen_capability_testgen.py`; "
             "added specimens are new in `harvest_sft_dataset.py`._")
    L.append("")
    with open(os.path.abspath(README_PATH), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()

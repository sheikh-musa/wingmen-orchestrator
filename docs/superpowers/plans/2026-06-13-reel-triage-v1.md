# Reel Triage v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn saved Instagram reels into a weekly 5-item action digest: Musa shares reel links (or one Meta DYI export ZIP) to the Telegram bot; a Mac Studio worker fetches/transcribes/LLM-structures each reel into a `reel_inbox` table; every Friday 09:00 SGT the bot pushes the top-5 actions with Apply/Discard buttons under a hard WIP cap of 3.

**Architecture:** Feature-flagged (`reel_triage`) module inside wingmen-orchestrator, no fork. Three runtime surfaces sharing one cross-project Supabase table (`reel_inbox`, project `tscuymavysscrvoberrr` — NOT orch prod `ceayjeamtmcyzzvqflus`): (1) INGEST + DIGEST run in the Telegram bot on the Mac Mini; (2) a headless WORKER runs on the Mac Studio via launchd. The product is the forcing function (5-item digest, WIP cap 3, discard-by-default); the pipeline is plumbing. Ban-risk hygiene is binding: yt-dlp public fetch only, no IG credentials anywhere, serial fetches with 30–60s sleeps.

**Tech Stack:** Python 3.9 (`.venv`), psycopg3, python-telegram-bot (existing bot), yt-dlp, ffmpeg, faster-whisper, `claude -p` CLI (Max-first per CAI-PROCESS-MAX-FIRST-001), launchd. Tests: pytest against an ephemeral local PG17 cluster (reuse `tests/migrations/conftest.py` substrate pattern).

---

## Binding Constraints (from CAI-RESP-216)

- **Identity doctrine:** ingest accepts links/ZIP ONLY from Musa's verified Telegram ID. Reuse the existing verified-TG-ID gate (BUG-024 button-handler fix).
- **Zero IG credentials** anywhere in repo or env. yt-dlp public fetch only, NO login cookies in v1. Acceptance test: `grep` shows no IG creds.
- **Media never leaves the Mac Studio.** Delete media after extraction; retain transcript only.
- **Serial fetches:** 30–60s sleep between IG fetches, zero parallel fetches.
- **One repo, feature-flag `reel_triage`, no fork.**
- **Migration apply:** direct psycopg-apply to project `tscuymavysscrvoberrr`, never `supabase db push` (decision-962).
- **STATUS.md protocol mandatory** every state change.

## Open Questions for CTO (resolve before / during build)

1. **Schema gap — auto-discard tracking.** The "verbatim" schema lists no field to count digest appearances, but "untouched after 2 digests → auto-discard" requires one. This plan adds `digests_shown int not null default 0`. **Needs cai confirmation** that augmenting the verbatim schema with this single counter is acceptable (it is additive, no behavior change to listed columns).
2. **Hard dependency — DB creds.** Need a DSN / service-role creds for project `tscuymavysscrvoberrr` (env `REEL_INBOX_DB_URL`) to apply the migration and run ingest/worker/digest. (Flagged to cai in #2109.)
3. **Hard dependency — Mac Studio host.** Worker needs a launchd slot + toolchain (yt-dlp, ffmpeg, faster-whisper, `claude` CLI Max-first) on the Mac Studio. Confirm orch's deploy/access path to that host, or operator provisions it. (Flagged in #2109.)

`REEL_INBOX_DB_URL` keeps reel data on its own project; do NOT route it through orch prod's `DATABASE_URL`.

## File Structure

- Create: `migrations/001_reel_inbox.sql` — exact-named DDL (project `tscuymavysscrvoberrr`).
- Create: `scripts/apply_reel_inbox_migration.py` — psycopg dry-run/`--apply` to `REEL_INBOX_DB_URL`.
- Create: `reel_triage/__init__.py`
- Create: `reel_triage/config.py` — feature flag, DSN accessor, constants (effort weights, WIP cap, sleep bounds, keyframe cap).
- Create: `reel_triage/db.py` — `REEL_INBOX_DB_URL` connection + row CRUD helpers.
- Create: `reel_triage/links.py` — pure functions: extract shortcode from IG URL, classify link type.
- Create: `reel_triage/dyi.py` — parse Meta DYI export ZIP (saved_posts JSON + self-DM threads) → IG links.
- Create: `reel_triage/ingest.py` — orchestrate link/ZIP ingest, dedupe by shortcode, insert rows, return applied/skipped/failed counts.
- Create: `reel_triage/fetcher.py` — yt-dlp public fetch (no cookies) + ffmpeg keyframes (scene-detect, max 6).
- Create: `reel_triage/transcribe.py` — faster-whisper local transcription wrapper.
- Create: `reel_triage/structurer.py` — `claude -p` strict-JSON structuring + `priority` calc.
- Create: `reel_triage/worker.py` — Mac Studio poll loop: claim row → fetch → keyframes → transcribe → structure → `triaged`; full stderr to `error` on failure.
- Create: `reel_triage/digest.py` — Friday top-5 builder, button payloads, WIP-cap enforcement, auto-discard.
- Create: `reel_triage/telegram_handlers.py` — bot wiring: ingest message handler (identity-gated) + Apply/Discard/Done callbacks.
- Create: `ops/launchd/dev.wingmen.reel-worker.plist` — Mac Studio launchd (fail-closed `WINGMEN_REEL_WORKER_DISABLED=1` default).
- Create: `tests/reel_triage/conftest.py` — ephemeral PG17 + `reel_inbox` schema fixture.
- Create: `tests/reel_triage/test_*.py` — per-module tests.

---

## Task 1: Migration DDL + apply script

**Files:**
- Create: `migrations/001_reel_inbox.sql`
- Create: `scripts/apply_reel_inbox_migration.py`
- Test: `tests/reel_triage/test_migration.py`, `tests/reel_triage/conftest.py`

- [ ] **Step 1: Write the failing test (schema applies + constraints bite)**

`tests/reel_triage/conftest.py` (mirror `tests/migrations/conftest.py` — ephemeral PG17, `fresh_db`, but apply `reel_inbox`):

```python
import os, pathlib
import psycopg, pytest

# Reuse the migrations substrate; copy the pg_dsn/fresh_db fixtures verbatim
# from tests/migrations/conftest.py (PG17 keg, port 54329, trust auth).
# Only the schema differs.

MIGRATION_SQL = pathlib.Path(
    "/Users/sheikhmusa/wingmen/orchestrator/migrations/001_reel_inbox.sql"
).read_text()

@pytest.fixture
def reel_db(fresh_db):
    conn = psycopg.connect(fresh_db, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
    yield conn
    conn.close()
```

`tests/reel_triage/test_migration.py`:

```python
import psycopg, pytest

def test_status_defaults_to_inbox(reel_db):
    cur = reel_db.cursor()
    cur.execute("insert into reel_inbox (shortcode, url, source) "
                "values ('abc', 'https://instagram.com/reel/abc', 'share_link') returning status")
    assert cur.fetchone()[0] == "inbox"

def test_source_check_rejects_bad_value(reel_db):
    cur = reel_db.cursor()
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("insert into reel_inbox (shortcode, url, source) "
                    "values ('x', 'u', 'bogus')")

def test_shortcode_is_unique(reel_db):
    cur = reel_db.cursor()
    cur.execute("insert into reel_inbox (shortcode, url, source) values ('dup','u','share_link')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute("insert into reel_inbox (shortcode, url, source) values ('dup','u2','share_link')")
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_migration.py -v`
Expected: FAIL — `001_reel_inbox.sql` missing / table absent.

- [ ] **Step 3: Write `migrations/001_reel_inbox.sql`**

```sql
create extension if not exists pgcrypto;

create table if not exists reel_inbox (
  id             uuid primary key default gen_random_uuid(),
  shortcode      text not null unique,
  url            text not null,
  source         text not null check (source in ('share_link','dyi_saved','dyi_dm')),
  saved_at       timestamptz,
  ingested_at    timestamptz not null default now(),
  caption        text,
  transcript     text,
  ocr_text       text,
  topic          text,
  claim          text,
  evidence_grade text check (evidence_grade in ('cited','anecdote','vibes')),
  action         text,
  effort         text check (effort in ('5min','habit','project')),
  impact         int  check (impact between 1 and 5),
  confidence     numeric check (confidence >= 0 and confidence <= 1),
  priority       numeric,
  status         text not null default 'inbox'
                   check (status in ('inbox','triaged','applying','done','discarded')),
  error          text,
  raw_json       jsonb,
  digests_shown  int not null default 0   -- augmentation: tracks auto-discard (CTO Q1)
);

create index if not exists idx_reel_inbox_status on reel_inbox (status);
-- worker claim predicate: untriaged, not yet errored
create index if not exists idx_reel_inbox_pending
  on reel_inbox (ingested_at) where transcript is null and error is null;
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_migration.py -v`
Expected: PASS (3/3).

- [ ] **Step 5: Write `scripts/apply_reel_inbox_migration.py`** (dry-run default, `--apply` commits, targets `REEL_INBOX_DB_URL`)

```python
"""Apply migrations/001_reel_inbox.sql to project tscuymavysscrvoberrr.
Direct psycopg-apply (decision-962: never `supabase db push`). Dry-run default.
  python scripts/apply_reel_inbox_migration.py          # dry-run
  python scripts/apply_reel_inbox_migration.py --apply   # commit
"""
from __future__ import annotations
import os, sys, pathlib
import psycopg
from dotenv import load_dotenv

SQL = pathlib.Path(__file__).parent.parent.joinpath("migrations/001_reel_inbox.sql").read_text()

def main() -> int:
    apply = "--apply" in sys.argv
    load_dotenv("/Users/sheikhmusa/wingmen/orchestrator/.env")
    dsn = os.environ.get("REEL_INBOX_DB_URL")
    if not dsn:
        raise SystemExit("REEL_INBOX_DB_URL not set (project tscuymavysscrvoberrr)")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(SQL)
        cur.execute("select to_regclass('public.reel_inbox')")
        print("reel_inbox present after DDL:", cur.fetchone()[0])
        if apply:
            conn.commit(); print("APPLIED + committed.")
        else:
            conn.rollback(); print("DRY-RUN (rolled back). Re-run with --apply.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add migrations/001_reel_inbox.sql scripts/apply_reel_inbox_migration.py tests/reel_triage/
git commit -m "feat(reel-triage): 001_reel_inbox migration + apply script + schema tests"
```

---

## Task 2: Config + DB helpers

**Files:**
- Create: `reel_triage/__init__.py` (empty), `reel_triage/config.py`, `reel_triage/db.py`
- Test: `tests/reel_triage/test_config.py`

- [ ] **Step 1: Write failing test**

```python
from reel_triage import config

def test_effort_weight_mapping():
    assert config.effort_weight("5min") == 1
    assert config.effort_weight("habit") == 2
    assert config.effort_weight("project") == 4

def test_feature_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("WINGMEN_REEL_TRIAGE_ENABLED", raising=False)
    assert config.reel_triage_enabled() is False

def test_constants():
    assert config.WIP_CAP == 3
    assert config.MAX_KEYFRAMES == 6
    assert config.FETCH_SLEEP_RANGE == (30, 60)
    assert config.AUTO_DISCARD_AFTER_DIGESTS == 2
    assert config.DIGEST_TOP_N == 5
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_config.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `reel_triage/config.py`**

```python
import os

WIP_CAP = 3
MAX_KEYFRAMES = 6
FETCH_SLEEP_RANGE = (30, 60)
AUTO_DISCARD_AFTER_DIGESTS = 2
DIGEST_TOP_N = 5
_EFFORT_WEIGHTS = {"5min": 1, "habit": 2, "project": 4}

def effort_weight(effort: str) -> int:
    return _EFFORT_WEIGHTS[effort]

def reel_triage_enabled() -> bool:
    return os.environ.get("WINGMEN_REEL_TRIAGE_ENABLED", "").lower() in ("1", "true", "yes")

def reel_inbox_dsn() -> str:
    dsn = os.environ.get("REEL_INBOX_DB_URL")
    if not dsn:
        raise RuntimeError("REEL_INBOX_DB_URL not set (project tscuymavysscrvoberrr)")
    return dsn
```

- [ ] **Step 4: Implement `reel_triage/db.py`** (thin connection + row helpers; `dict_row`)

```python
import contextlib
import psycopg
from psycopg.rows import dict_row
from reel_triage import config

@contextlib.contextmanager
def connect():
    with psycopg.connect(config.reel_inbox_dsn(), row_factory=dict_row, autocommit=True) as conn:
        yield conn
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add reel_triage/__init__.py reel_triage/config.py reel_triage/db.py tests/reel_triage/test_config.py
git commit -m "feat(reel-triage): config (flag, constants, DSN) + db connection helper"
```

---

## Task 3: IG link parsing (pure functions)

**Files:**
- Create: `reel_triage/links.py`
- Test: `tests/reel_triage/test_links.py`

- [ ] **Step 1: Write failing test**

```python
from reel_triage import links

def test_extracts_shortcode_from_reel_url():
    assert links.shortcode("https://www.instagram.com/reel/CxYz123/") == "CxYz123"

def test_extracts_shortcode_from_p_url():
    assert links.shortcode("https://instagram.com/p/AbC_9/?igshid=1") == "AbC_9"

def test_non_instagram_url_returns_none():
    assert links.shortcode("https://youtube.com/watch?v=x") is None

def test_find_all_ig_links_in_text():
    text = "check https://instagram.com/reel/AAA and https://www.instagram.com/p/BBB/"
    assert set(links.find_links(text)) == {
        "https://instagram.com/reel/AAA", "https://www.instagram.com/p/BBB/"}
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_links.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/links.py`**

```python
import re

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)")
_LINK_RE = re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[A-Za-z0-9_-]+/?[^\s]*")

def shortcode(url: str) -> str | None:
    m = _SHORTCODE_RE.search(url or "")
    return m.group(1) if m else None

def find_links(text: str) -> list[str]:
    return [m.rstrip("/").split("?")[0] if False else m for m in _LINK_RE.findall(text or "")]
```

Note: keep `find_links` returning the matched URL as-is (callers dedupe by `shortcode()`). The `[... if False ...]` placeholder above is illustrative — implement as `return _LINK_RE.findall(text or "")`.

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_links.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reel_triage/links.py tests/reel_triage/test_links.py
git commit -m "feat(reel-triage): IG link parsing (shortcode extract + find_links)"
```

---

## Task 4: DYI export ZIP parsing

**Files:**
- Create: `reel_triage/dyi.py`
- Test: `tests/reel_triage/test_dyi.py` (+ a small fixture ZIP built in-test)

- [ ] **Step 1: Write failing test**

Meta DYI exports put saved posts under `saved_posts.json` and DMs under `messages/inbox/<thread>/message_1.json`. Build a fixture ZIP in-test with both shapes.

```python
import io, json, zipfile
from reel_triage import dyi

def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()

def test_parses_saved_posts_and_dm_links_with_source():
    saved = json.dumps({"saved_saved_media": [
        {"title": "user", "string_map_data": {"Saved on": {
            "href": "https://www.instagram.com/reel/SAVED1/", "timestamp": 1700000000}}}]})
    dm = json.dumps({"messages": [
        {"content": "look https://instagram.com/p/DM1/", "timestamp_ms": 1700000001000}]})
    data = _zip({"saved_posts.json": saved,
                 "messages/inbox/x/message_1.json": dm})
    found = dyi.parse(data)
    by_code = {f["shortcode"]: f for f in found}
    assert by_code["SAVED1"]["source"] == "dyi_saved"
    assert by_code["DM1"]["source"] == "dyi_dm"

def test_dedupes_by_shortcode_within_zip():
    saved = json.dumps({"saved_saved_media": [
        {"string_map_data": {"Saved on": {"href": "https://instagram.com/reel/DUP/"}}}]})
    dm = json.dumps({"messages": [{"content": "https://instagram.com/reel/DUP/"}]})
    found = dyi.parse(_zip({"saved_posts.json": saved,
                            "messages/inbox/x/message_1.json": dm}))
    assert len([f for f in found if f["shortcode"] == "DUP"]) == 1
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_dyi.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/dyi.py`**

```python
import io, json, zipfile
from reel_triage import links

def _walk_strings(obj):
    """Yield every string value anywhere in a nested JSON structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)

def parse(zip_bytes: bytes) -> list[dict]:
    """Return deduped [{shortcode, url, source}] from a Meta DYI export ZIP.
    saved_posts*.json -> dyi_saved; messages/inbox/**/message_*.json -> dyi_dm.
    First-seen shortcode wins (saved files iterate before DM files)."""
    seen: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        ordered = ([n for n in names if "saved" in n.lower() and n.endswith(".json")]
                   + [n for n in names if "/inbox/" in n and n.endswith(".json")])
        for name in ordered:
            source = "dyi_saved" if "saved" in name.lower() else "dyi_dm"
            try:
                doc = json.loads(z.read(name).decode("utf-8", "replace"))
            except (json.JSONDecodeError, KeyError):
                continue
            for s in _walk_strings(doc):
                for url in links.find_links(s):
                    code = links.shortcode(url)
                    if code and code not in seen:
                        seen[code] = {"shortcode": code, "url": url, "source": source}
    return list(seen.values())
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_dyi.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reel_triage/dyi.py tests/reel_triage/test_dyi.py
git commit -m "feat(reel-triage): Meta DYI ZIP parse (saved_posts + DMs), dedupe by shortcode"
```

---

## Task 5: Ingest orchestration (dedupe + insert + counts)

**Files:**
- Create: `reel_triage/ingest.py`
- Test: `tests/reel_triage/test_ingest.py`

- [ ] **Step 1: Write failing test** (uses `reel_db` fixture)

```python
from reel_triage import ingest

def test_ingest_links_inserts_new_and_skips_dupes(reel_db):
    items = [{"shortcode": "A", "url": "https://instagram.com/reel/A", "source": "share_link"},
             {"shortcode": "A", "url": "https://instagram.com/reel/A", "source": "share_link"}]
    counts = ingest.ingest_items(reel_db, items)
    assert counts == {"applied": 1, "skipped": 1, "failed": 0}
    cur = reel_db.cursor()
    cur.execute("select count(*) from reel_inbox where shortcode = 'A'")
    assert cur.fetchone()["count"] == 1

def test_ingest_skips_shortcode_already_in_db(reel_db):
    one = [{"shortcode": "B", "url": "u", "source": "dyi_saved"}]
    ingest.ingest_items(reel_db, one)
    counts = ingest.ingest_items(reel_db, one)   # second pass
    assert counts == {"applied": 0, "skipped": 1, "failed": 0}
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_ingest.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/ingest.py`**

```python
def ingest_items(conn, items: list[dict]) -> dict:
    """Insert new reels; skip shortcodes already present (in-batch or in-DB).
    Returns {applied, skipped, failed}. ON CONFLICT (shortcode) DO NOTHING makes
    insert idempotent against the unique constraint."""
    applied = skipped = failed = 0
    seen_in_batch: set[str] = set()
    cur = conn.cursor()
    for it in items:
        code = it["shortcode"]
        if code in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(code)
        try:
            cur.execute(
                "insert into reel_inbox (shortcode, url, source) values (%s, %s, %s) "
                "on conflict (shortcode) do nothing",
                (code, it["url"], it["source"]))
            if cur.rowcount == 1:
                applied += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {"applied": applied, "skipped": skipped, "failed": failed}
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_ingest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reel_triage/ingest.py tests/reel_triage/test_ingest.py
git commit -m "feat(reel-triage): ingest orchestration — dedupe + idempotent insert + counts"
```

---

## Task 6: priority calc + structurer (claude -p strict JSON)

**Files:**
- Create: `reel_triage/structurer.py`
- Test: `tests/reel_triage/test_structurer.py`

- [ ] **Step 1: Write failing test** (mock the `claude -p` subprocess; assert JSON parse + priority)

```python
import json
from reel_triage import structurer

def test_priority_formula():
    # impact*confidence / effort_weight ; habit weight = 2
    assert structurer.priority(impact=4, confidence=0.5, effort="habit") == 1.0

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
    import pytest
    with pytest.raises(ValueError):
        structurer.structure("t", [])
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_structurer.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/structurer.py`** (Max-first CLI; strict validation)

```python
import json, subprocess
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
    res = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=180)
    res.check_returncode()
    return res.stdout.strip()

def structure(transcript: str, keyframes: list[str]) -> dict:
    raw = _run_claude(_PROMPT.format(transcript=transcript))
    data = json.loads(raw)            # raises on non-JSON
    if data.get("evidence_grade") not in _EVIDENCE:
        raise ValueError(f"bad evidence_grade: {data.get('evidence_grade')}")
    if data.get("effort") not in _EFFORT:
        raise ValueError(f"bad effort: {data.get('effort')}")
    if not (1 <= int(data["impact"]) <= 5):
        raise ValueError("impact out of range")
    if not (0 <= float(data["confidence"]) <= 1):
        raise ValueError("confidence out of range")
    data["priority"] = priority(int(data["impact"]), float(data["confidence"]), data["effort"])
    return data
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_structurer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reel_triage/structurer.py tests/reel_triage/test_structurer.py
git commit -m "feat(reel-triage): claude -p strict-JSON structurer + priority formula"
```

---

## Task 7: Fetcher + transcriber wrappers (mocked unit tests)

**Files:**
- Create: `reel_triage/fetcher.py`, `reel_triage/transcribe.py`
- Test: `tests/reel_triage/test_fetcher.py`

Real yt-dlp/ffmpeg/faster-whisper runs are integration-only (Mac Studio acceptance). Unit-test the orchestration: command construction, no-cookies guarantee, keyframe cap, media cleanup.

- [ ] **Step 1: Write failing test**

```python
from reel_triage import fetcher

def test_ytdlp_command_has_no_cookies():
    cmd = fetcher.build_ytdlp_cmd("https://instagram.com/reel/A", "/tmp/A.mp4")
    joined = " ".join(cmd)
    assert "--cookies" not in joined and "--cookies-from-browser" not in joined
    assert "https://instagram.com/reel/A" in cmd

def test_keyframe_cmd_caps_at_max(monkeypatch):
    cmd = fetcher.build_keyframe_cmd("/tmp/A.mp4", "/tmp/frames", max_frames=6)
    assert any("6" in part for part in cmd)  # cap passed to ffmpeg select/scene filter
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_fetcher.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/fetcher.py`**

```python
import os, glob
import subprocess
from reel_triage import config

def build_ytdlp_cmd(url: str, out_path: str) -> list[str]:
    # Public fetch ONLY — no cookies, ever (v1 ban-risk hygiene + zero IG creds).
    return ["yt-dlp", "--no-playlist", "-f", "mp4", "-o", out_path, url]

def build_keyframe_cmd(media: str, frames_dir: str, max_frames: int = config.MAX_KEYFRAMES) -> list[str]:
    # scene-detect, cap frames
    return ["ffmpeg", "-i", media,
            "-vf", f"select='gt(scene,0.3)',showinfo",
            "-frames:v", str(max_frames), "-vsync", "vfr",
            os.path.join(frames_dir, "frame_%02d.jpg")]

def fetch(url: str, work_dir: str) -> str:
    media = os.path.join(work_dir, "reel.mp4")
    subprocess.run(build_ytdlp_cmd(url, media), capture_output=True, text=True, check=True)
    return media

def keyframes(media: str, frames_dir: str) -> list[str]:
    os.makedirs(frames_dir, exist_ok=True)
    subprocess.run(build_keyframe_cmd(media, frames_dir), capture_output=True, text=True, check=True)
    return sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))

def cleanup_media(media: str) -> None:
    # Media never persists (Amanah/Satr). Transcript is the only retained artifact.
    if os.path.exists(media):
        os.remove(media)
```

- [ ] **Step 4: Implement `reel_triage/transcribe.py`**

```python
def transcribe(media_or_audio: str) -> str:
    """faster-whisper local transcription. Imported lazily so unit tests and the
    Mac Mini bot host don't require the model/runtime — only the Mac Studio worker does."""
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(media_or_audio)
    return " ".join(seg.text.strip() for seg in segments).strip()
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_fetcher.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add reel_triage/fetcher.py reel_triage/transcribe.py tests/reel_triage/test_fetcher.py
git commit -m "feat(reel-triage): yt-dlp(no-cookies)+ffmpeg fetcher + faster-whisper transcriber"
```

---

## Task 8: Worker loop (claim → process → triaged / error)

**Files:**
- Create: `reel_triage/worker.py`
- Test: `tests/reel_triage/test_worker.py`

- [ ] **Step 1: Write failing test** (inject fake fetch/transcribe/structure; assert row transitions + full-stderr capture)

```python
from reel_triage import worker

def _insert(reel_db, code):
    reel_db.cursor().execute(
        "insert into reel_inbox (shortcode, url, source) values (%s, %s, 'share_link')",
        (code, f"https://instagram.com/reel/{code}"))

def test_process_one_marks_triaged(reel_db, monkeypatch):
    _insert(reel_db, "OK1")
    monkeypatch.setattr(worker.fetcher, "fetch", lambda url, d: "/tmp/x.mp4")
    monkeypatch.setattr(worker.fetcher, "keyframes", lambda m, d: [])
    monkeypatch.setattr(worker.fetcher, "cleanup_media", lambda m: None)
    monkeypatch.setattr(worker, "transcribe", lambda m: "hello transcript")
    monkeypatch.setattr(worker, "structure", lambda t, f: {
        "topic": "t", "claim": "c", "evidence_grade": "cited", "action": "a",
        "effort": "5min", "impact": 5, "confidence": 1.0, "priority": 5.0})
    worker.process_one(reel_db)
    row = reel_db.cursor().execute(
        "select status, transcript, priority from reel_inbox where shortcode='OK1'").fetchone()
    assert row["status"] == "triaged" and row["priority"] == 5.0

def test_process_one_writes_full_stderr_on_fetch_failure(reel_db, monkeypatch):
    import subprocess
    _insert(reel_db, "FAIL1")
    def boom(url, d):
        raise subprocess.CalledProcessError(1, "yt-dlp", stderr="LONG STDERR ERROR DETAIL")
    monkeypatch.setattr(worker.fetcher, "fetch", boom)
    worker.process_one(reel_db)
    row = reel_db.cursor().execute(
        "select status, error from reel_inbox where shortcode='FAIL1'").fetchone()
    assert "LONG STDERR ERROR DETAIL" in row["error"]
    assert row["status"] == "inbox"   # stays inbox (not triaged); surfaced as needs-manual
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_worker.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/worker.py`**

```python
import os, time, random, tempfile, subprocess
from reel_triage import fetcher, config
from reel_triage.transcribe import transcribe
from reel_triage.structurer import structure

def _claim(conn):
    # one row at a time, oldest first; serial by design (no parallel fetch)
    cur = conn.cursor()
    return cur.execute(
        "select id, shortcode, url from reel_inbox "
        "where transcript is null and error is null and status = 'inbox' "
        "order by ingested_at limit 1").fetchone()

def process_one(conn) -> bool:
    row = _claim(conn)
    if not row:
        return False
    cur = conn.cursor()
    work = tempfile.mkdtemp(prefix="reel-")
    try:
        media = fetcher.fetch(row["url"], work)
        frames = fetcher.keyframes(media, os.path.join(work, "frames"))
        text = transcribe(media)
        fetcher.cleanup_media(media)            # media never persists
        data = structure(text, frames)
        cur.execute(
            "update reel_inbox set transcript=%s, topic=%s, claim=%s, evidence_grade=%s, "
            "action=%s, effort=%s, impact=%s, confidence=%s, priority=%s, raw_json=%s, "
            "status='triaged' where id=%s",
            (text, data["topic"], data["claim"], data["evidence_grade"], data["action"],
             data["effort"], data["impact"], data["confidence"], data["priority"],
             __import__("json").dumps(data), row["id"]))
        return True
    except subprocess.CalledProcessError as e:
        # FULL stderr — truncated evidence is not evidence (CAI-RESP-216)
        cur.execute("update reel_inbox set error=%s where id=%s",
                    (str(e.stderr or e), row["id"]))
        return True
    except Exception as e:
        cur.execute("update reel_inbox set error=%s where id=%s", (repr(e), row["id"]))
        return True

def run_forever(conn_factory):
    while True:
        with conn_factory() as conn:
            did = process_one(conn)
        lo, hi = config.FETCH_SLEEP_RANGE
        time.sleep(random.uniform(lo, hi) if did else hi)
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_worker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reel_triage/worker.py tests/reel_triage/test_worker.py
git commit -m "feat(reel-triage): worker loop — serial claim, full-stderr capture, media cleanup"
```

---

## Task 9: Digest builder (top-5, WIP cap, auto-discard)

**Files:**
- Create: `reel_triage/digest.py`
- Test: `tests/reel_triage/test_digest.py`

- [ ] **Step 1: Write failing test**

```python
from reel_triage import digest

def _triaged(reel_db, code, prio):
    reel_db.cursor().execute(
        "insert into reel_inbox (shortcode, url, source, status, action, priority) "
        "values (%s,%s,'share_link','triaged', 'do '||%s, %s)",
        (code, code, code, prio))

def test_top5_orders_by_priority_desc(reel_db):
    for i in range(7):
        _triaged(reel_db, f"R{i}", float(i))
    top = digest.top_actions(reel_db)
    assert len(top) == 5
    assert [r["shortcode"] for r in top] == ["R6", "R5", "R4", "R3", "R2"]

def test_apply_respects_wip_cap(reel_db):
    for i in range(3):
        reel_db.cursor().execute(
            "insert into reel_inbox (shortcode,url,source,status) values (%s,%s,'share_link','applying')",
            (f"W{i}", f"W{i}"))
    _triaged(reel_db, "NEW", 9.0)
    ok, in_progress = digest.apply(reel_db, "NEW")
    assert ok is False                 # at cap -> rejected
    assert len(in_progress) == 3       # returns current 3 for Done/Discard

def test_apply_under_cap_moves_to_applying(reel_db):
    _triaged(reel_db, "GO", 9.0)
    ok, _ = digest.apply(reel_db, "GO")
    assert ok is True
    row = reel_db.cursor().execute("select status from reel_inbox where shortcode='GO'").fetchone()
    assert row["status"] == "applying"

def test_auto_discard_after_two_digests(reel_db):
    _triaged(reel_db, "STALE", 1.0)
    digest.mark_shown(reel_db, ["STALE"])      # digest 1
    digest.mark_shown(reel_db, ["STALE"])      # digest 2 -> auto-discard
    row = reel_db.cursor().execute("select status from reel_inbox where shortcode='STALE'").fetchone()
    assert row["status"] == "discarded"
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_digest.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/digest.py`**

```python
from reel_triage import config

def top_actions(conn, n: int = config.DIGEST_TOP_N) -> list[dict]:
    return conn.cursor().execute(
        "select id, shortcode, action, priority from reel_inbox "
        "where status = 'triaged' order by priority desc nulls last, ingested_at limit %s",
        (n,)).fetchall()

def _applying_rows(conn) -> list[dict]:
    return conn.cursor().execute(
        "select id, shortcode, action from reel_inbox where status = 'applying' "
        "order by ingested_at").fetchall()

def apply(conn, shortcode: str) -> tuple[bool, list[dict]]:
    """Move a triaged reel to 'applying' iff under WIP cap. At cap: no change,
    return the current applying rows so the bot can offer Done/Discard."""
    current = _applying_rows(conn)
    if len(current) >= config.WIP_CAP:
        return False, current
    conn.cursor().execute(
        "update reel_inbox set status='applying' where shortcode=%s and status='triaged'",
        (shortcode,))
    return True, _applying_rows(conn)

def discard(conn, shortcode: str) -> None:
    conn.cursor().execute(
        "update reel_inbox set status='discarded' where shortcode=%s "
        "and status in ('triaged','applying')", (shortcode,))

def mark_done(conn, shortcode: str) -> None:
    conn.cursor().execute(
        "update reel_inbox set status='done' where shortcode=%s and status='applying'",
        (shortcode,))

def mark_shown(conn, shortcodes: list[str]) -> None:
    """Increment digest counter; auto-discard once a still-triaged row has been
    shown AUTO_DISCARD_AFTER_DIGESTS times without being applied/discarded."""
    cur = conn.cursor()
    cur.execute("update reel_inbox set digests_shown = digests_shown + 1 "
                "where shortcode = any(%s) and status = 'triaged'", (shortcodes,))
    cur.execute("update reel_inbox set status='discarded' "
                "where status='triaged' and digests_shown >= %s",
                (config.AUTO_DISCARD_AFTER_DIGESTS,))
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_digest.py -v`
Expected: PASS (5/5).

- [ ] **Step 5: Commit**

```bash
git add reel_triage/digest.py tests/reel_triage/test_digest.py
git commit -m "feat(reel-triage): digest — top5, WIP cap 3, done/discard, auto-discard after 2"
```

---

## Task 10: Telegram handlers (identity-gated ingest + callbacks)

**Files:**
- Create: `reel_triage/telegram_handlers.py`
- Test: `tests/reel_triage/test_telegram_handlers.py`
- Modify: bot registration in the main bot module (wire handlers behind the `reel_triage` flag) — locate the existing handler-registration site and add a guarded `register(app)` call.

- [ ] **Step 1: Write failing test** (fake Update/Context; assert identity gate + ingest counts message + callback routing). Mock DB via the `reel_db` fixture and a stub bot.

```python
import pytest
from reel_triage import telegram_handlers as th

class FakeMsg:
    def __init__(self, text=None, doc=None):
        self.text, self.document = text, doc
        self.replies = []
    async def reply_text(self, t, **k): self.replies.append(t)

class FakeUser:
    def __init__(self, uid): self.id = uid

class FakeUpdate:
    def __init__(self, uid, text=None):
        self.effective_user = FakeUser(uid)
        self.message = FakeMsg(text=text)

@pytest.mark.asyncio
async def test_ingest_rejects_non_musa_id(reel_db, monkeypatch):
    monkeypatch.setattr(th, "MUSA_TG_ID", 111)
    monkeypatch.setattr(th, "_conn", lambda: reel_db)
    upd = FakeUpdate(uid=999, text="https://instagram.com/reel/Z")
    await th.handle_message(upd, ctx=None)
    assert upd.message.replies == []   # silent ignore for non-verified id
    assert reel_db.cursor().execute("select count(*) from reel_inbox").fetchone()["count"] == 0

@pytest.mark.asyncio
async def test_ingest_link_reports_counts(reel_db, monkeypatch):
    monkeypatch.setattr(th, "MUSA_TG_ID", 111)
    monkeypatch.setattr(th, "_conn", lambda: reel_db)
    upd = FakeUpdate(uid=111, text="save https://instagram.com/reel/Z")
    await th.handle_message(upd, ctx=None)
    assert any("1" in r for r in upd.message.replies)   # applied=1 surfaced
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_telegram_handlers.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/telegram_handlers.py`** (identity gate reuses the verified-TG-ID env; link + ZIP ingest; Apply/Discard/Done callbacks)

```python
import os
from reel_triage import links, dyi, ingest, digest, config

MUSA_TG_ID = int(os.environ.get("MUSA_TELEGRAM_ID", "0"))

def _conn():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(config.reel_inbox_dsn(), row_factory=dict_row, autocommit=True)

def _is_musa(update) -> bool:
    u = getattr(update, "effective_user", None)
    return bool(u) and u.id == MUSA_TG_ID

async def handle_message(update, ctx):
    if not config.reel_triage_enabled() or not _is_musa(update):
        return                                   # silent ignore (identity doctrine)
    msg = update.message
    conn = _conn()
    # ZIP path (DYI export)
    if getattr(msg, "document", None) and str(msg.document.file_name).endswith(".zip"):
        file = await ctx.bot.get_file(msg.document.file_id)
        data = bytes(await file.download_as_bytearray())
        counts = ingest.ingest_items(conn, dyi.parse(data))
    else:                                        # link path
        found = [{"shortcode": links.shortcode(u), "url": u, "source": "share_link"}
                 for u in links.find_links(msg.text or "")]
        found = [f for f in found if f["shortcode"]]
        if not found:
            return
        counts = ingest.ingest_items(conn, found)
    await msg.reply_text(
        f"Reel triage: applied {counts['applied']}, skipped {counts['skipped']}, "
        f"failed {counts['failed']}.")

async def handle_callback(update, ctx):
    """Routes apply:<code> / discard:<code> / done:<code> from digest buttons."""
    if not _is_musa(update):
        return
    q = update.callback_query
    action, _, code = q.data.partition(":")
    conn = _conn()
    if action == "apply":
        ok, current = digest.apply(conn, code)
        if ok:
            await q.answer("Applied — added to your 3 in-progress.")
        else:
            lines = "\n".join(f"- {r['action']}" for r in current)
            await q.answer("At WIP cap (3).", show_alert=True)
            await q.message.reply_text("You already have 3 in progress:\n" + lines)
    elif action == "discard":
        digest.discard(conn, code); await q.answer("Discarded.")
    elif action == "done":
        digest.mark_done(conn, code); await q.answer("Marked done.")
```

- [ ] **Step 4: Run, verify pass** (add `pytest-asyncio` if not present — check `requirements*.txt` first)

Run: `.venv/bin/python -m pytest tests/reel_triage/test_telegram_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into the bot** behind the flag. Find the existing handler-registration site (grep for `add_handler` in the main bot module) and add:

```python
# reel_triage feature (CAI-RESP-216) — registered only when flag is on
from reel_triage import config as _rt_config
if _rt_config.reel_triage_enabled():
    from reel_triage import telegram_handlers as _rt
    from telegram.ext import MessageHandler, CallbackQueryHandler, filters
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ZIP, _rt.handle_message))
    app.add_handler(CallbackQueryHandler(_rt.handle_callback, pattern=r"^(apply|discard|done):"))
```

- [ ] **Step 6: Commit**

```bash
git add reel_triage/telegram_handlers.py tests/reel_triage/test_telegram_handlers.py <bot_module>
git commit -m "feat(reel-triage): identity-gated TG ingest + Apply/Discard/Done callbacks"
```

---

## Task 11: Friday digest sender + launchd worker plist

**Files:**
- Create: `reel_triage/digest_send.py` (compose + send the Friday digest message with buttons)
- Create: `ops/launchd/dev.wingmen.reel-worker.plist`
- Create: `scripts/run_reel_digest.py` (entrypoint the existing scheduler / a launchd calendar job invokes)
- Test: `tests/reel_triage/test_digest_send.py`

- [ ] **Step 1: Write failing test** (assert message composes top-5 with one action line + buttons; assert `mark_shown` called for shown codes)

```python
from reel_triage import digest_send

def test_compose_digest_has_one_line_per_action_and_buttons(reel_db):
    cur = reel_db.cursor()
    for i in range(5):
        cur.execute("insert into reel_inbox (shortcode,url,source,status,action,priority) "
                    "values (%s,%s,'share_link','triaged',%s,%s)",
                    (f"D{i}", f"D{i}", f"action {i}", float(i)))
    text, keyboard, shown = digest_send.compose(reel_db)
    assert text.count("\n") >= 5
    assert len(keyboard) == 5            # one [Apply][Discard] row per action
    assert len(shown) == 5
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_digest_send.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `reel_triage/digest_send.py`**

```python
from reel_triage import digest

def compose(conn):
    """Returns (text, inline_keyboard, shown_shortcodes). Keyboard is a list of
    button-rows: [[Apply(code), Discard(code)], ...]. Caller turns rows into the
    telegram InlineKeyboardMarkup and calls digest.mark_shown(shown) after send."""
    rows = digest.top_actions(conn)
    if not rows:
        return "No triaged reels this week.", [], []
    lines, keyboard, shown = ["This week's top actions:"], [], []
    for r in rows:
        lines.append(f"- {r['action']}")
        keyboard.append([("Apply", f"apply:{r['shortcode']}"),
                         ("Discard", f"discard:{r['shortcode']}")])
        shown.append(r["shortcode"])
    # needs-manual footer (rows that errored)
    errs = conn.cursor().execute(
        "select count(*) c from reel_inbox where error is not null and status='inbox'").fetchone()
    if errs and errs["c"]:
        lines.append(f"\n({errs['c']} reel(s) need manual fetch — see error column.)")
    return "\n".join(lines), keyboard, shown
```

- [ ] **Step 4: Implement `scripts/run_reel_digest.py`** (compose → send via bot token → `digest.mark_shown`). Friday 09:00 SGT scheduling: prefer the orchestrator's existing scheduler if it has a cron facility; else a launchd `StartCalendarInterval` (Weekday=5, Hour=9) in SGT — document the chosen mechanism in STATUS.

- [ ] **Step 5: Implement `ops/launchd/dev.wingmen.reel-worker.plist`** (Mac Studio; fail-closed default mirroring the cc-cai daemon pattern)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.wingmen.reel-worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/sheikhmusa/wingmen/orchestrator/.venv-reel/bin/python</string>
    <string>-m</string><string>reel_triage.worker</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/sheikhmusa/wingmen/orchestrator</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>Crashed</key><true/></dict>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>/Users/sheikhmusa/wingmen/orchestrator/logs/reel_worker.log</string>
  <key>StandardErrorPath</key><string>/Users/sheikhmusa/wingmen/orchestrator/logs/reel_worker.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <!-- Fail-closed: worker boots disabled. Set to 0 + reload to enable. -->
    <key>WINGMEN_REEL_WORKER_DISABLED</key><string>1</string>
  </dict>
</dict>
</plist>
```

Add a disabled-guard at the top of `worker.run_forever` honoring `WINGMEN_REEL_WORKER_DISABLED`.

- [ ] **Step 6: Run, verify pass**

Run: `.venv/bin/python -m pytest tests/reel_triage/test_digest_send.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add reel_triage/digest_send.py scripts/run_reel_digest.py ops/launchd/dev.wingmen.reel-worker.plist tests/reel_triage/test_digest_send.py
git commit -m "feat(reel-triage): Friday digest sender + fail-closed Mac Studio worker plist"
```

---

## Task 12: Acceptance harness + STATUS + creds-grep guard

**Files:**
- Create: `tests/reel_triage/test_no_ig_creds.py`
- Modify: `STATUS.md`

- [ ] **Step 1: Write the no-creds acceptance test** (binding constraint: zero IG credentials in repo)

```python
import subprocess

def test_no_instagram_credentials_in_repo():
    # grep the tree for IG credential patterns; must find nothing.
    res = subprocess.run(
        ["grep", "-rniE",
         r"(IG_PASSWORD|INSTAGRAM_PASSWORD|ig_username|instaloader|cookies-from-browser)",
         "--include=*.py", "--include=*.sql", "--include=*.plist", "--include=.env",
         "/Users/sheikhmusa/wingmen/orchestrator/reel_triage",
         "/Users/sheikhmusa/wingmen/orchestrator/migrations",
         "/Users/sheikhmusa/wingmen/orchestrator/ops"],
        capture_output=True, text=True)
    assert res.returncode != 0, f"IG credential pattern found:\n{res.stdout}"
```

- [ ] **Step 2: Run, verify it passes** (should pass — no creds were ever added).

Run: `.venv/bin/python -m pytest tests/reel_triage/test_no_ig_creds.py -v`
Expected: PASS.

- [ ] **Step 3: Run the full reel_triage suite**

Run: `.venv/bin/python -m pytest tests/reel_triage/ -v`
Expected: all green.

- [ ] **Step 4: Update `STATUS.md`** per protocol (Last Updated, Phase, Build status, Deploy URL=n/a, Completed, Failed/Blocked = [worker not yet on Mac Studio; migration not applied — both operator-gated], Files Changed, Next Up, Questions for CTO = the 3 open questions above).

- [ ] **Step 5: Commit**

```bash
git add tests/reel_triage/test_no_ig_creds.py STATUS.md
git commit -m "test(reel-triage): zero-IG-creds acceptance guard + STATUS protocol update"
```

---

## Operator-Gated Go-Live (NOT part of the build commits)

These run only after the build is merged AND the two hard deps are provisioned:

1. Operator sets `REEL_INBOX_DB_URL` (project `tscuymavysscrvoberrr`) in `.env`.
2. `python scripts/apply_reel_inbox_migration.py` (dry-run) → review → `--apply`.
3. Operator stands up `.venv-reel` + toolchain (yt-dlp, ffmpeg, faster-whisper, `claude` CLI) on the Mac Studio; copies the worker plist; sets `WINGMEN_REEL_WORKER_DISABLED=0`; `launchctl load`.
4. Operator sets `WINGMEN_REEL_TRIAGE_ENABLED=1` + `MUSA_TELEGRAM_ID` on the Mac Mini bot host; restart via `scripts/restart_orch.sh`.
5. Acceptance run: paste a link → row fully populated <10 min; forward a DYI ZIP → counts; force a Friday digest → buttons work; verify WIP cap; `grep` clean.

## Self-Review Notes

- **Spec coverage:** INGEST (T3–5,10), MIGRATION (T1), WORKER (T6–8), DIGEST (T9,11), CONSTRAINTS (T7 no-cookies, T12 no-creds, T8 serial+cleanup), ACCEPTANCE (T12 + operator harness). All five scope items mapped.
- **Type consistency:** `priority(impact, confidence, effort)` signature consistent T6↔T8; `apply()` returns `(bool, list)` consistent T9↔T10; status enum `inbox/triaged/applying/done/discarded` consistent across T1/T8/T9.
- **Flagged deviation:** `digests_shown` column augments the verbatim schema — CTO Q1, must confirm with cai before applying the migration.

#!/usr/bin/env python3
"""qwen_lane.py — SANDBOXED foreign-brain ("non-Claude") fleet-lane POC.

op#10706 f/u / LLM-agnostic-fleet-lanes — cai-approved as a SANDBOXED POC under
CAI-RESP-751. This is a proof that a non-Claude agent runtime can plug into the
fleet's message bus (`agent_messages`) as a drop-in PEER to a Claude lane: it
drains its own inbox, does one LOW-STAKES task via a Qwen model over an
OpenAI-COMPATIBLE endpoint, writes an ATTRIBUTABLE reply back through the bus,
and marks the inbound handled — the exact inbox-drain -> work -> attributable
write-back -> mark-handled contract every Claude lane obeys.

=====================================================================
HARD GUARDRAILS (CAI-RESP-751 — non-negotiable, enforced in CODE not by promise)
=====================================================================
G1  SANDBOXED / low-stakes only. The runtime refuses to touch any row that is
    not is_test=true (REQUIRE_TEST), and every row it writes is stamped
    is_test=true. The live pollers (agent_messages_poll / _realtime) filter
    is_test=False, so sandbox traffic is STRUCTURALLY INVISIBLE to Telegram and
    the real fleet. Non-money, non-client, non-PII, non-governance by construction.
G2  HARNESS-STAMPED ATTRIBUTION. `from_agent` is a hard-coded constant
    (SANDBOX_AGENT_ID) set by THIS code. The model only ever supplies `body`
    text; it can never set from_agent, to_agent, thread_id, is_test, priority or
    any attribution/routing field. `_attributable_reply()` is the only writer and
    it ignores everything the model returns except the completion string.
G3  NO money / gov pens. The runtime's ENTIRE toolset is two functions:
    read its inbox + write one text reply. There is no tool that moves money,
    writes grants / strategic_decisions, or touches governance. Those capabilities
    are STRUCTURALLY ABSENT — not gated, simply not present.
G4  PII-MINIMISATION at the tool layer. Only the inbound subject+body of a
    sandbox row is ever sent to the model, and it passes through secret_redact
    first. No env, no secrets, no DSN, no operator/client data reaches the model.
G5  EXTERNAL-HOSTED = UNTRUSTED. Phase-1 Qwen runs on Alibaba/DashScope (China
    jurisdiction). We treat the endpoint as an untrusted external service: we
    never route client/PII/secret content to it (G4), and we never trust its
    output for anything but body text (G2). Self-hosted sovereignty (vLLM/Ollama)
    is a LATER phase — reachable here by changing QWEN_BASE_URL only, no code change.
G6  NOT A LIVE LANE. No launchd/tmux wiring, no autonomous persistence. Designed
    to be proven by a MOCK run and a self-cleaning sandbox test. Nothing here
    joins the live fleet without review. `main()` runs a single bounded drain and
    exits; it never daemonises.

=====================================================================
PROTOCOL CONTRACT (studied from the live bus, not guessed)
=====================================================================
Bus = `agent_messages` on the substrate DB (env DATABASE_URL, psycopg v3).
 - INBOX read:  SELECT ... WHERE to_agent=<my id> AND read_at IS NULL
 - REPLY write: INSERT (from_agent,to_agent,thread_id,message_type,subject,body,
                requires_response,priority,is_test) — same column set the hub's
                emitters use (see scripts/fleet_stall_watch.py). Reuses the
                inbound thread_id so the reply threads to the request.
 - message_type is CHECK-constrained; valid set includes 'update'/'question'/
   'blocker' (also review_request/decision/agreed/challenge/counter). We reply
   with 'update'.
 - from_agent AND to_agent are FKs to agents(id): the sandbox id must be
   registered (the sandbox test harness registers + cleans it; the runtime does
   NOT self-register — registration is a setup act, kept out of the runtime).
 - Attribution: a BEFORE-INSERT trigger (populate_agent_messages_provenance)
   stamps posted_by_identity=current_user and from_agent_verified from
   identity_allowlist. We also set_config('app.current_agent_id', <id>) to match
   the documented emitter contract.
 - MARK HANDLED: stamp read_at (the addressed agent's own processing stamp — see
   BUG-021 in agent_messages_poll) and responded_at once the reply is written.

Endpoint adapter: OpenAI-compatible POST {base_url}/chat/completions. The SAME
code targets DashScope today and a self-hosted vLLM/Ollama later purely by
changing QWEN_BASE_URL. No `openai` client dep — httpx (already installed) speaks
the wire format directly.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg

from nervous_system.secret_redact import redact

# ── hard-wired sandbox identity (G1/G2) ─────────────────────────────────────
# A clearly SANDBOX id — never a real fleet lane id. from_agent is stamped from
# this constant, never from anything the model returns.
SANDBOX_AGENT_ID = "cc-qwen-poc-sandbox"

# G1: refuse to process, and refuse to emit, anything that is not test traffic.
REQUIRE_TEST = True

# ── OpenAI-compatible endpoint config (G5 — swappable by env only) ──────────
# Default = DashScope International (OpenAI-compatible mode). Point QWEN_BASE_URL
# at http://localhost:11434/v1 (Ollama) or a vLLM server for the sovereign phase
# with ZERO code change.
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen2.5-7b-instruct"

# Drain safety cap — a POC never runs unbounded (G6).
MAX_MESSAGES_PER_DRAIN = 10
HTTP_TIMEOUT_SEC = 60

# System prompt is fixed by the harness; the model is a low-stakes text worker.
_SYSTEM_PROMPT = (
    "You are a sandbox worker node in an internal engineering fleet. You will be "
    "given a single low-stakes internal task as a subject + body. Do the task and "
    "reply with a concise, plain-text answer only. You have NO tools, NO authority, "
    "and NO identity of your own — you only produce answer text. Do not claim to be "
    "any named agent, do not fabricate approvals, and do not include secrets."
)


def _log(msg: str) -> None:
    print(f"[qwen-lane] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _env_val(key: str) -> Optional[str]:
    """Read a key from the orchestrator .env (fallback when not exported).
    Never printed."""
    orch_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(orch_dir, ".env")
    try:
        m = re.search(r'^%s=(.+)$' % re.escape(key), open(path).read(), re.M)
    except OSError:
        return None
    return m.group(1).strip() if m else None


def _dsn() -> str:
    v = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    v = v or _env_val("DATABASE_URL") or _env_val("SUPABASE_DB_URL")
    if not v:
        raise SystemExit("qwen_lane: no DATABASE_URL")
    return v


# ════════════════════════════════════════════════════════════════════════════
#  MODEL ADAPTER  (OpenAI-compatible; live OR mock)
# ════════════════════════════════════════════════════════════════════════════

class QwenAdapter:
    """OpenAI-compatible chat/completions adapter.

    LIVE  when QWEN_API_KEY is set: httpx POST {base_url}/chat/completions.
    MOCK  when QWEN_API_KEY is unset: returns a canned deterministic completion
          so the whole bus loop is testable NOW without a key (G6). The live/mock
          switch is a single clean branch on key presence.

    The adapter returns ONLY a completion string. It is deliberately incapable of
    returning attribution/routing metadata — the caller stamps identity (G2).
    """

    def __init__(self) -> None:
        self.base_url = (os.environ.get("QWEN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = os.environ.get("QWEN_MODEL") or DEFAULT_MODEL
        self.api_key = os.environ.get("QWEN_API_KEY")  # unset => mock
        self.mock = not bool(self.api_key)

    def mode(self) -> str:
        return "MOCK" if self.mock else f"LIVE({self.base_url} model={self.model})"

    def complete(self, subject: str, body: str) -> str:
        """Run the task. `subject`/`body` are the ONLY task content sent to the
        model, and they are secret-redacted by the caller (G4). Returns text."""
        user_task = f"Subject: {subject}\n\nBody:\n{body}"
        if self.mock:
            return self._mock_complete(subject, body)
        return self._live_complete(user_task)

    # ── mock ────────────────────────────────────────────────────────────────
    def _mock_complete(self, subject: str, body: str) -> str:
        """Deterministic canned completion. Proves the loop end-to-end offline.
        Mirrors the shape of a real low-stakes answer without any network call."""
        return (
            f"[qwen-mock] Task received: {subject!r}. "
            f"This is a canned sandbox completion (no API key set, mock mode). "
            f"The runtime, not the model, stamps identity as {SANDBOX_AGENT_ID}."
        )

    # ── live (OpenAI-compatible wire format) ─────────────────────────────────
    def _live_complete(self, user_task: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_task},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        # OpenAI-compatible response shape (identical for DashScope/vLLM/Ollama).
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected completion shape: {json.dumps(data)[:400]}") from e


# ════════════════════════════════════════════════════════════════════════════
#  BUS  (the runtime's ENTIRE toolset: read inbox + write reply — G3)
# ════════════════════════════════════════════════════════════════════════════

def _read_inbox(conn) -> list[dict]:
    """Drain tool #1: unread rows addressed to the sandbox id.

    G1: hard-filters is_test=true — the runtime is structurally unable to read
    real (non-test) fleet traffic even if a row were mis-addressed to it.
    """
    sql = (
        "SELECT id, thread_id, from_agent, to_agent, message_type, subject, body, "
        "       requires_response, priority "
        "FROM agent_messages "
        "WHERE to_agent = %s AND read_at IS NULL "
        + ("AND is_test = true " if REQUIRE_TEST else "")
        + "ORDER BY created_at ASC LIMIT %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (SANDBOX_AGENT_ID, MAX_MESSAGES_PER_DRAIN))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _attributable_reply(conn, inbound: dict, answer_text: str) -> int:
    """Drain tool #2 — the ONLY writer. Harness-stamped attribution (G2):

      * from_agent is the hard-coded SANDBOX_AGENT_ID, NOT anything the model said.
      * is_test is forced true (G1); the model cannot flip it.
      * priority/message_type/to_agent/thread_id are all set by THIS code.
      * `answer_text` (the model's output) only ever fills `body`, after redaction.

    Threads the reply to the inbound request via its thread_id.
    """
    if REQUIRE_TEST and not inbound.get("is_test", True):
        # Belt-and-suspenders: _read_inbox already excludes non-test rows.
        raise RuntimeError("refusing to reply to a non-test inbound (G1)")

    reply_body = redact(answer_text or "")           # G4: never echo a secret back
    reply_subject = f"re: {inbound.get('subject', '(no subject)')}"[:180]
    to_agent = inbound["from_agent"]                 # reply to whoever asked

    with conn.cursor() as cur:
        # Documented emitter contract: name the speaker for the provenance trigger.
        cur.execute("SELECT set_config('app.current_agent_id', %s, true)", (SANDBOX_AGENT_ID,))
        cur.execute(
            "INSERT INTO agent_messages "
            "  (from_agent, to_agent, thread_id, message_type, subject, body, "
            "   requires_response, priority, is_test) "
            "VALUES (%s, %s, %s, 'update', %s, %s, false, 'P3', true) "
            "RETURNING id",
            (
                SANDBOX_AGENT_ID,          # G2: harness-stamped, model-independent
                to_agent,
                inbound["thread_id"],      # thread the reply to the request
                reply_subject,
                reply_body,
                # requires_response=false, priority=P3, is_test=true all fixed here
            ),
        )
        return cur.fetchone()[0]


def _mark_handled(conn, inbound_id: int) -> None:
    """Stamp read_at (the addressed agent's own processing stamp, per BUG-021)
    and responded_at. This is what makes the runtime a well-behaved peer: it
    closes its own inbox rows rather than leaving them unhandled."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE agent_messages SET read_at = %s, responded_at = %s WHERE id = %s",
            (datetime.now(timezone.utc), datetime.now(timezone.utc), inbound_id),
        )


# ════════════════════════════════════════════════════════════════════════════
#  DRAIN LOOP  (inbox-drain -> work -> attributable write-back -> mark-handled)
# ════════════════════════════════════════════════════════════════════════════

def drain_once(conn, adapter: QwenAdapter) -> list[dict]:
    """One bounded drain of the sandbox inbox. Returns a per-message result list
    (proof/observability). Never daemonises (G6)."""
    results: list[dict] = []
    inbox = _read_inbox(conn)
    _log(f"inbox: {len(inbox)} unread for {SANDBOX_AGENT_ID} (mode={adapter.mode()})")

    for msg in inbox:
        mid = msg["id"]
        # G4: only subject+body reach the model, and they are redacted first.
        safe_subject = redact(msg.get("subject") or "")
        safe_body = redact(msg.get("body") or "")
        try:
            answer = adapter.complete(safe_subject, safe_body)
            reply_id = _attributable_reply(conn, msg, answer)
            _mark_handled(conn, mid)
            conn.commit()
            _log(f"  msg #{mid} -> reply #{reply_id} (handled)")
            results.append({"inbound_id": mid, "reply_id": reply_id, "ok": True})
        except Exception as e:  # noqa: BLE001 — POC: record + continue, never crash the drain
            conn.rollback()
            _log(f"  msg #{mid} FAILED: {type(e).__name__}: {e}")
            results.append({"inbound_id": mid, "ok": False, "error": str(e)})
    return results


def main() -> int:
    adapter = QwenAdapter()
    _log(f"boot — sandbox id={SANDBOX_AGENT_ID} adapter={adapter.mode()} require_test={REQUIRE_TEST}")
    with psycopg.connect(_dsn()) as conn:
        results = drain_once(conn, adapter)
    ok = sum(1 for r in results if r.get("ok"))
    _log(f"drain complete: {ok}/{len(results)} handled")
    return 0


if __name__ == "__main__":
    sys.exit(main())

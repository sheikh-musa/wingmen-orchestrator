"""cc-cai daemon main loop entry point.

Orchestrates: poller → classifier → kill_switch.observe →
  (silent-lane handler) OR (escalator) per the kill_switch gate.

Per CAI-RESP-185 + CADENCE-002/003/004/005/006:
  - 5-min poll cadence (Realtime is amendment 1 deferred upgrade)
  - INV-5 audit row before every side effect
  - INV-6 default HOLD: kill_switch reverts to pure-escalation on confidence drop
  - INV-3 MAX-first: no ANTHROPIC_API_KEY; SDK uses Max OAuth (Phase 2 only)

Phase 1 silent-lane is narrow: mark_read_fyi + ack_fyi only (Q4).
Telegram bot inbound runs in a separate task alongside the poller.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# Cross-venv import: when running under .venv-cc-cai (py3.13), the orch's
# nervous_system modules need to be on the path. Insert repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from cc_cai_daemon.audit import AuditLogger
from cc_cai_daemon.classifier import classify, Classification
from cc_cai_daemon.escalator import escalate_to_operator
from cc_cai_daemon.kill_switch import (
    KillSwitch, STATE_PANIC_DISABLED, STATE_PURE_ESCALATION,
)
from cc_cai_daemon.poller import fetch_unread_for_cai, POLL_INTERVAL_SECONDS
from cc_cai_daemon.silent_lane import handle_mark_read_fyi, handle_ack_fyi

logger = logging.getLogger("cc_cai.main")

DSN = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or ""


class _SyncSupabaseShim:
    """Minimal sync supabase-style chain for the silent-lane handlers.

    The handlers expect a .table(name).update(d).eq(col,v).execute() builder
    interface (supabase-py style). For Phase 1 we use psycopg directly via
    this shim — avoids pulling supabase-py into the cc-cai venv.
    """

    def __init__(self, dsn: str):
        self._dsn = dsn

    def table(self, name: str) -> "_TableBuilder":
        return _TableBuilder(self._dsn, name)


class _TableBuilder:
    def __init__(self, dsn: str, name: str):
        self._dsn = dsn
        self._name = name
        self._action: str | None = None
        self._payload: dict | None = None
        self._filters: list[tuple[str, str, object]] = []

    def update(self, payload: dict) -> "_TableBuilder":
        self._action = "update"
        self._payload = payload
        return self

    def insert(self, payload: dict) -> "_TableBuilder":
        self._action = "insert"
        self._payload = payload
        return self

    def eq(self, col: str, val: object) -> "_TableBuilder":
        self._filters.append((col, "=", val))
        return self

    def execute(self) -> None:
        if self._action == "update":
            assert self._payload is not None
            sets = []
            vals = []
            for k, v in self._payload.items():
                if v == "now()":
                    sets.append(f"{k} = now()")
                else:
                    sets.append(f"{k} = %s")
                    vals.append(v)
            where = " AND ".join(f"{c} {op} %s" for c, op, _ in self._filters)
            where_vals = [v for _, _, v in self._filters]
            sql = f"UPDATE {self._name} SET {', '.join(sets)}"
            if where:
                sql += f" WHERE {where}"
            with psycopg.connect(self._dsn, autocommit=True) as c, c.cursor() as cur:
                cur.execute(sql, vals + where_vals)
        elif self._action == "insert":
            assert self._payload is not None
            cols = list(self._payload.keys())
            placeholders = []
            vals = []
            for k in cols:
                v = self._payload[k]
                if v == "now()":
                    placeholders.append("now()")
                else:
                    placeholders.append("%s")
                    vals.append(v)
            sql = (
                f"INSERT INTO {self._name} ({', '.join(cols)}) "
                f"VALUES ({', '.join(placeholders)})"
            )
            with psycopg.connect(self._dsn, autocommit=True) as c, c.cursor() as cur:
                cur.execute(sql, vals)


async def _run_one_cycle(
    *, audit: AuditLogger, kill_switch: KillSwitch,
    supabase, bot, chat_id: str, dry_run: bool,
) -> int:
    """One poll → classify → dispatch cycle. Returns number of messages processed."""
    if kill_switch.state == STATE_PANIC_DISABLED:
        logger.info("kill_switch=panic_disabled — skipping cycle")
        return 0

    rows = fetch_unread_for_cai(DSN)
    if not rows:
        return 0
    logger.info(f"cycle: {len(rows)} unread for cai")

    for msg in rows:
        try:
            classification = classify(msg)
        except Exception as e:
            logger.error(f"classify raised on msg #{msg['id']}: {e}")
            continue

        # Always audit the classification
        audit.log_classification(
            agent_message_id=msg["id"],
            classification=classification.label,
            reason=classification.reason,
            confidence=classification.confidence,
        )

        # Feed kill_switch (may trip → pure_escalation)
        kill_switch.observe(confidence=classification.confidence)

        if dry_run:
            logger.info(
                f"DRY_RUN msg #{msg['id']} → {classification.label} "
                f"(confidence={classification.confidence:.2f}, "
                f"reason={classification.reason})"
            )
            continue

        # Force escalation if kill_switch is in pure_escalation_mode
        effective_label = classification.label
        if kill_switch.state == STATE_PURE_ESCALATION and effective_label != "escalate":
            effective_label = "escalate"
            logger.info(f"msg #{msg['id']} forced to escalate (kill_switch=pure_escalation)")

        if effective_label == "mark_read_fyi" and kill_switch.should_act_silently():
            handle_mark_read_fyi(supabase, audit, msg, classification.reason)
        elif effective_label == "ack_fyi" and kill_switch.should_act_silently():
            handle_ack_fyi(supabase, audit, msg, classification.reason)
        elif kill_switch.should_escalate():
            # novel_low_confidence = the classifier's "not sure how to file this"
            # safety net (INV-6 default-HOLD). It is NOT a human decision —
            # pushing it to the operator's phone is noise ("what am I supposed to
            # do with this"). cai already polls every bus message, so we AUDIT it
            # and hold for cai instead of buzzing the operator; nothing slips
            # (still bus-visible + audited). Genuine operator-decision categories
            # still push to the phone.
            if classification.escalation_category == "novel_low_confidence":
                audit.log_escalation(
                    agent_message_id=msg["id"],
                    reason=f"held-for-cai (novel_low_confidence, not operator-pushed): {classification.reason}",
                )
                logger.info(
                    f"msg #{msg['id']}: novel_low_confidence held for cai (not pushed to operator)"
                )
            else:
                await escalate_to_operator(
                    bot, chat_id, msg, audit,
                    reason=classification.reason,
                    category=classification.escalation_category,
                )
        else:
            logger.info(f"msg #{msg['id']}: kill_switch blocks all action")

    return len(rows)


async def run_forever(*, dry_run: bool = False, max_cycles: int | None = None) -> None:
    if not DSN:
        raise RuntimeError("DATABASE_URL not set")

    session_id = f"cc-cai-{uuid.uuid4().hex[:12]}"
    audit = AuditLogger(dsn=DSN, session_id=session_id)
    kill_switch = KillSwitch(audit=audit)
    supabase = _SyncSupabaseShim(DSN)

    # Telegram bot — optional (dry-run can run without)
    bot = None
    chat_id = os.environ.get("MUSA_TELEGRAM_ID", "")
    if not dry_run:
        try:
            from telegram import Bot
            # ihsanosbot retired -> cai escalations now ride @wingmennorchbot
            token = os.environ.get("WINGMEN_BOT_TOKEN", "")
            if token and chat_id:
                bot = Bot(token=token)
                logger.info("Telegram bot initialized")
            else:
                logger.warning("WINGMEN_BOT_TOKEN or MUSA_TELEGRAM_ID missing — "
                               "escalations cannot push")
        except ImportError:
            logger.warning("python-telegram-bot not installed — no escalation push")

    logger.info(
        f"cc-cai daemon starting: session={session_id} state={kill_switch.state} "
        f"dry_run={dry_run}"
    )

    cycle_count = 0
    while True:
        try:
            await _run_one_cycle(
                audit=audit, kill_switch=kill_switch,
                supabase=supabase, bot=bot, chat_id=chat_id, dry_run=dry_run,
            )
        except Exception as e:
            logger.error(f"cycle failed: {e}", exc_info=True)

        cycle_count += 1
        if max_cycles is not None and cycle_count >= max_cycles:
            logger.info(f"max_cycles={max_cycles} reached — exiting")
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="classify + audit-log only, no side effects")
    p.add_argument("--max-cycles", type=int, default=None,
                   help="stop after N cycles (else run forever)")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(run_forever(dry_run=args.dry_run, max_cycles=args.max_cycles))
    except KeyboardInterrupt:
        logger.info("interrupted; exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())

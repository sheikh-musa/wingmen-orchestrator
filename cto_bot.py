"""Wingmen CTO Bot — multi-client Telegram bot for brainstorming and build jobs.

Supports:
- Musa (admin): full access to all repos, admin commands, client management
- Clients: scoped to their repos, can brainstorm and queue builds
- Free text: context-aware brainstorm using Claude with real repo knowledge
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from supabase import acreate_client
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import context_loader

# ── Setup ────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "cto_bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("wingmen.cto_bot")

MUSA_TELEGRAM_ID = os.environ.get("MUSA_TELEGRAM_ID", "")

_supabase = None


async def get_supabase():
    global _supabase
    if _supabase is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _supabase = await acreate_client(url, key)
    return _supabase


# ── User Resolution ──────────────────────────────────────────────

# ── User Cache (with eviction) ───────────────────────────────────

_user_cache: dict[str, dict] = {}
CACHE_TTL = 300  # 5 min
CACHE_MAX_SIZE = 200


def _evict_cache():
    """Remove expired entries from user cache."""
    if len(_user_cache) <= CACHE_MAX_SIZE:
        return
    now = time.monotonic()
    expired = [k for k, v in _user_cache.items() if now - v["ts"] > CACHE_TTL]
    for k in expired:
        del _user_cache[k]
    # If still over limit, drop oldest
    if len(_user_cache) > CACHE_MAX_SIZE:
        oldest = sorted(_user_cache, key=lambda k: _user_cache[k]["ts"])
        for k in oldest[:len(_user_cache) - CACHE_MAX_SIZE]:
            del _user_cache[k]


async def resolve_user(chat_id: str) -> dict | None:
    """Resolve a Telegram chat_id to a user object. Returns None if unregistered."""
    now = time.monotonic()
    cached = _user_cache.get(chat_id)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached

    _evict_cache()

    if chat_id == MUSA_TELEGRAM_ID:
        user = {
            "role": "admin",
            "client": None,
            "client_id": None,
            "repos": context_loader.get_all_repo_names(),
            "name": "Musa",
            "ts": now,
        }
        _user_cache[chat_id] = user
        return user

    supabase = await get_supabase()
    result = await supabase.table("clients").select("*").eq(
        "telegram_chat_id", chat_id
    ).eq("active", True).limit(1).execute()

    if not result.data:
        return None

    client = result.data[0]
    repos = await context_loader.get_client_repos(client["id"], supabase)
    repo_names = [r["name"] for r in repos]

    user = {
        "role": "client",
        "client": client,
        "client_id": client["id"],
        "repos": repo_names,
        "name": client["name"],
        "ts": now,
    }
    _user_cache[chat_id] = user
    return user


def is_admin(user: dict) -> bool:
    return user["role"] == "admin"


# ── Per-user state ───────────────────────────────────────────────

_active_repo: dict[str, str] = {}            # chat_id -> repo_name
_rate_limits: dict[str, list[float]] = {}    # chat_id -> list of timestamps

MAX_BUILDS_PER_HOUR = 10
MAX_MSG_LENGTH = 2000


def check_rate_limit(chat_id: str) -> bool:
    """Returns True if within rate limit, False if exceeded."""
    now = time.monotonic()
    if chat_id not in _rate_limits:
        _rate_limits[chat_id] = []
    _rate_limits[chat_id] = [t for t in _rate_limits[chat_id] if now - t < 3600]
    if len(_rate_limits[chat_id]) >= MAX_BUILDS_PER_HOUR:
        return False
    _rate_limits[chat_id].append(now)
    return True


async def get_history(chat_id: str) -> list[dict]:
    """Load chat history from Supabase, falling back to empty."""
    try:
        supabase = await get_supabase()
        result = await (
            supabase.table("chat_history")
            .select("role, content")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        if result.data:
            # Reverse to chronological order
            return list(reversed(result.data))
    except Exception as e:
        logger.warning(f"Failed to load chat history: {e}")
    return []


async def save_message(chat_id: str, role: str, content: str) -> None:
    """Persist a chat message to Supabase."""
    try:
        supabase = await get_supabase()
        await supabase.table("chat_history").insert({
            "chat_id": chat_id,
            "role": role,
            "content": content[:4000],
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to save chat message: {e}")


async def log_audit(chat_id: str, user_name: str, action: str, target: str = "", detail: str = "") -> None:
    """Write to audit log for all significant actions."""
    try:
        supabase = await get_supabase()
        await supabase.table("audit_log").insert({
            "user_chat_id": chat_id,
            "user_name": user_name,
            "action": action,
            "target": target,
            "detail": detail[:1000],
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")


async def log_usage(client_id: int | None, action_type: str, repo_name: str = "", tokens: int = 0, duration: float = 0) -> None:
    """Track usage for metering/billing."""
    try:
        supabase = await get_supabase()
        await supabase.table("usage_log").insert({
            "client_id": client_id,
            "action_type": action_type,
            "repo_name": repo_name,
            "tokens_used": tokens,
            "duration_seconds": duration,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to log usage: {e}")


def get_active_repo(chat_id: str, user: dict) -> str | None:
    if chat_id in _active_repo:
        return _active_repo[chat_id]
    if len(user["repos"]) == 1:
        _active_repo[chat_id] = user["repos"][0]
        return user["repos"][0]
    return None


# ── Commands ─────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)

    if not user:
        await update.message.reply_text(
            "Assalamu alaikum! You're not registered yet.\n"
            "Please contact the admin to get set up."
        )
        return

    if is_admin(user):
        await update.message.reply_text(
            "Assalamu alaikum, CTO.\n\n"
            "Commands:\n"
            "/build <repo> <task> — Queue a build job\n"
            "/status [repo] — Show status\n"
            "/jobs — Show queued/running jobs\n"
            "/repos — List all repos\n"
            "/repo <name> — Switch active repo\n"
            "/pause <job_id> — Pause a job\n"
            "/cancel <job_id> — Cancel a job\n"
            "/priority <job_id> <1-5> — Set job priority\n"
            "/addclient <name> <chat_id> <plan> — Register client\n"
            "/linkrepo <client_name> <repo_name> — Link repo to client\n"
            "/clients — List all clients\n"
            "/id — Show your Telegram chat ID\n\n"
            "Or just type normally to brainstorm."
        )
    else:
        repo = get_active_repo(chat_id, user)
        if len(user["repos"]) == 1:
            _active_repo[chat_id] = user["repos"][0]
            repo = user["repos"][0]

        if repo:
            await update.message.reply_text(
                f"Assalamu alaikum, {user['name']}! \U0001f44b\n\n"
                f"I'm your project assistant for {repo}.\n\n"
                "Just tell me what you need — whether it's:\n"
                "\u2022 Updating content or prices\n"
                "\u2022 Fixing something that's not working\n"
                "\u2022 Adding a new feature\n"
                "\u2022 Checking on progress\n\n"
                "No technical knowledge needed — just describe what you want in your own words!"
            )
        else:
            repo_list = "\n".join(f"\u2022 {r}" for r in user["repos"])
            await update.message.reply_text(
                f"Assalamu alaikum, {user['name']}! \U0001f44b\n\n"
                "I'm your project assistant from Wingmen.\n\n"
                f"You have these projects:\n{repo_list}\n\n"
                "Which one would you like to work on? Just mention its name or type /repo to switch."
            )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your chat ID: {update.effective_user.id}")


async def cmd_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        await update.message.reply_text("Not registered.")
        return

    if not context.args:
        current = get_active_repo(chat_id, user)
        lines = [f"Active: {current or 'none'}\n", "Your repos:"]
        for r in user["repos"]:
            marker = " (active)" if r == current else ""
            lines.append(f"  - {r}{marker}")
        await update.message.reply_text("\n".join(lines))
        return

    name = context.args[0]
    if name not in user["repos"]:
        await update.message.reply_text(
            f"Unknown repo: {name}\nYour repos: {', '.join(user['repos'])}"
        )
        return

    _active_repo[chat_id] = name
    await update.message.reply_text(f"Switched to {name}")


async def cmd_build(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        await update.message.reply_text("Not registered.")
        return

    args = context.args or []

    if is_admin(user):
        # Admin: /build <repo> <task>
        if len(args) < 2:
            await update.message.reply_text("Usage: /build <repo> <task description>")
            return
        repo_name = args[0]
        description = " ".join(args[1:])
        if repo_name not in user["repos"]:
            await update.message.reply_text(
                f"Unknown repo: {repo_name}\nAvailable: {', '.join(user['repos'])}"
            )
            return
    else:
        # Client: /build <task> (uses active repo)
        if not args:
            await update.message.reply_text("Usage: /build <describe what you need>")
            return
        repo_name = get_active_repo(chat_id, user)
        if not repo_name:
            await update.message.reply_text("Select a repo first with /repo <name>")
            return
        description = " ".join(args)

    # Validate
    if len(description) > MAX_MSG_LENGTH:
        await update.message.reply_text(f"Description too long (max {MAX_MSG_LENGTH} chars).")
        return

    if not check_rate_limit(chat_id):
        await update.message.reply_text("Too many requests. Please wait a bit before submitting more.")
        return

    try:
        config = context_loader.get_repo_config(repo_name)
    except ValueError:
        await update.message.reply_text(f"Repo {repo_name} not configured.")
        return

    supabase = await get_supabase()
    result = await supabase.table("jobs").insert({
        "repo_name": repo_name,
        "description": description,
        "status": "queued",
        "priority": config["priority"],
        "triggered_by": "telegram",
        "client_id": user.get("client_id"),
    }).execute()

    job = result.data[0]
    await update.message.reply_text(
        f"\u2705 Job #{job['id']} queued\n"
        f"\U0001f4e6 Repo: {repo_name}\n"
        f"\U0001f4dd Task: {description}\n"
        f"\u23f3 Priority: {config['priority']}"
    )
    await log_audit(chat_id, user["name"], "queue_build", repo_name, description[:200])
    await log_usage(user.get("client_id"), "build_queued", repo_name)
    logger.info(f"Job #{job['id']} queued by {user['name']}: {repo_name} — {description}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        return

    # Determine which repo to show
    if context.args and is_admin(user):
        repo_name = context.args[0]
    else:
        repo_name = get_active_repo(chat_id, user)

    if repo_name:
        try:
            config = context_loader.get_repo_config(repo_name)
            repo_path = Path(os.path.expanduser(config["local_path"]))
            status_file = repo_path / "STATUS.md"
            if status_file.exists():
                content = status_file.read_text()
                if len(content) > 4000:
                    content = content[:4000] + "\n...(truncated)"
                await update.message.reply_text(content)
                return
        except ValueError:
            pass

    # Fallback: orchestrator status
    orch_status = Path(__file__).parent / "STATUS.md"
    if orch_status.exists():
        content = orch_status.read_text()
        if len(content) > 4000:
            content = content[:4000] + "\n...(truncated)"
        await update.message.reply_text(content)
    else:
        await update.message.reply_text("No status available.")


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        return

    supabase = await get_supabase()
    query = (
        supabase.table("jobs")
        .select("id, repo_name, description, status, priority, fail_count, created_at")
        .in_("status", ["queued", "running", "paused"])
        .order("priority", desc=False)
    )

    # Clients only see their own jobs
    if not is_admin(user) and user["client_id"]:
        query = query.eq("client_id", user["client_id"])

    result = await query.execute()

    if not result.data:
        await update.message.reply_text("No active jobs.")
        return

    lines = ["Active Jobs:\n"]
    for j in result.data:
        icon = {"queued": "\u23f3", "running": "\u25b6\ufe0f", "paused": "\u23f8"}.get(j["status"], "\u2753")
        lines.append(f"{icon} #{j['id']} [{j['status']}] P{j['priority']} {j['repo_name']}\n   {j['description']}")
        if j["fail_count"] > 0:
            lines.append(f"   \u26a0\ufe0f Failures: {j['fail_count']}")

    await update.message.reply_text("\n".join(lines))


async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        return

    if is_admin(user):
        repos = context_loader._load_repos()
    else:
        supabase = await get_supabase()
        repos = await context_loader.get_client_repos(user["client_id"], supabase)

    if not repos:
        await update.message.reply_text("No repos linked to your account.")
        return

    lines = ["Your repos:\n"]
    for r in repos:
        icon = "\U0001f7e2" if r["status"] == "active" else "\U0001f7e1"
        lines.append(f"{icon} P{r['priority']} {r['name']} — {r['status']}")
        if r.get("deploy_url") and r["deploy_url"] != "FILL_IN":
            lines.append(f"   {r['deploy_url']}")

    await update.message.reply_text("\n".join(lines))


# ── Admin-only commands ──────────────────────────────────────────

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return
    if not context.args:
        await update.message.reply_text("Usage: /pause <job_id>")
        return
    job_id = int(context.args[0])
    supabase = await get_supabase()
    await supabase.table("jobs").update({"status": "paused"}).eq("id", job_id).execute()
    await log_audit(str(update.effective_user.id), user["name"], "pause_job", f"job_{job_id}")
    await update.message.reply_text(f"\u23f8 Job #{job_id} paused.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return
    if not context.args:
        await update.message.reply_text("Usage: /cancel <job_id>")
        return
    job_id = int(context.args[0])
    supabase = await get_supabase()
    await supabase.table("jobs").update({"status": "failed", "result_summary": "Cancelled by admin"}).eq("id", job_id).execute()
    await log_audit(str(update.effective_user.id), user["name"], "cancel_job", f"job_{job_id}")
    await update.message.reply_text(f"\u274c Job #{job_id} cancelled.")


async def cmd_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /priority <job_id> <1-5>")
        return
    job_id = int(context.args[0])
    priority = max(1, min(5, int(context.args[1])))
    supabase = await get_supabase()
    await supabase.table("jobs").update({"priority": priority}).eq("id", job_id).execute()
    await update.message.reply_text(f"\U0001f4ca Job #{job_id} priority set to {priority}.")


async def cmd_addclient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return
    if not context.args or len(context.args) < 3:
        await update.message.reply_text("Usage: /addclient <name> <telegram_chat_id> <plan>")
        return

    name = context.args[0]
    chat_id = context.args[1]
    plan = context.args[2]

    supabase = await get_supabase()
    result = await supabase.table("clients").insert({
        "name": name,
        "telegram_chat_id": chat_id,
        "plan": plan,
    }).execute()

    client = result.data[0]
    # Clear cache so new client is recognized
    _user_cache.pop(chat_id, None)
    await log_audit(str(update.effective_user.id), user["name"], "add_client", name, f"plan={plan}, chat_id={chat_id}")
    await update.message.reply_text(f"\u2705 Client '{name}' added (id: {client['id']}, plan: {plan})")
    logger.info(f"Client added: {name} (chat_id: {chat_id}, plan: {plan})")


async def cmd_linkrepo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /linkrepo <client_name> <repo_name>")
        return

    client_name = context.args[0]
    repo_name = context.args[1]

    # Validate repo exists
    try:
        context_loader.get_repo_config(repo_name)
    except ValueError:
        await update.message.reply_text(f"Repo '{repo_name}' not in REPOS.json")
        return

    supabase = await get_supabase()
    # Find client by name
    result = await supabase.table("clients").select("id, telegram_chat_id").eq("name", client_name).limit(1).execute()
    if not result.data:
        await update.message.reply_text(f"Client '{client_name}' not found")
        return

    client = result.data[0]
    await supabase.table("client_repos").insert({
        "client_id": client["id"],
        "repo_name": repo_name,
    }).execute()

    # Clear cache
    _user_cache.pop(client.get("telegram_chat_id", ""), None)
    await log_audit(str(update.effective_user.id), user["name"], "link_repo", f"{client_name}/{repo_name}")
    await update.message.reply_text(f"\u2705 Linked {repo_name} to {client_name}")
    logger.info(f"Repo {repo_name} linked to client {client_name}")


async def cmd_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return

    supabase = await get_supabase()
    result = await supabase.table("clients").select("id, name, telegram_chat_id, plan, active").execute()

    if not result.data:
        await update.message.reply_text("No clients registered.")
        return

    lines = ["Clients:\n"]
    for c in result.data:
        status = "\U0001f7e2" if c["active"] else "\u26d4"
        # Get their repos
        repos_result = await supabase.table("client_repos").select("repo_name").eq("client_id", c["id"]).execute()
        repo_names = [r["repo_name"] for r in (repos_result.data or [])]
        repos_str = ", ".join(repo_names) if repo_names else "none"
        lines.append(f"{status} {c['name']} [{c['plan']}] — repos: {repos_str}")

    await update.message.reply_text("\n".join(lines))


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show usage stats per client (admin only)."""
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return

    supabase = await get_supabase()
    # Get usage summary for last 7 days
    from datetime import datetime, timezone, timedelta
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    result = await (
        supabase.table("usage_log")
        .select("client_id, action_type, tokens_used, duration_seconds")
        .gte("created_at", week_ago)
        .execute()
    )

    if not result.data:
        await update.message.reply_text("No usage data in the last 7 days.")
        return

    # Aggregate by client
    from collections import defaultdict
    stats: dict[int | None, dict] = defaultdict(lambda: {"chats": 0, "builds": 0, "tokens": 0, "duration": 0})
    for r in result.data:
        cid = r.get("client_id")
        stats[cid]["tokens"] += r.get("tokens_used", 0)
        stats[cid]["duration"] += r.get("duration_seconds", 0)
        if r["action_type"] == "chat":
            stats[cid]["chats"] += 1
        elif r["action_type"] in ("build_queued", "build_completed"):
            stats[cid]["builds"] += 1

    # Resolve client names
    clients_result = await supabase.table("clients").select("id, name").execute()
    name_map = {c["id"]: c["name"] for c in (clients_result.data or [])}
    name_map[None] = "Admin"

    lines = ["Usage (last 7 days):\n"]
    for cid, s in sorted(stats.items(), key=lambda x: x[1]["tokens"], reverse=True):
        name = name_map.get(cid, f"client_{cid}")
        lines.append(
            f"{name}: {s['chats']} chats, {s['builds']} builds, "
            f"{s['tokens']:,} tokens, {s['duration']:.0f}s"
        )

    await update.message.reply_text("\n".join(lines))


# ── Chat (free text → Claude with repo context) ─────────────────

async def _load_repo_context_block(repo_name: str) -> str:
    """Load repo context and format as a prompt block."""
    block = ""
    try:
        supabase = await get_supabase()
        ctx = await context_loader.load_brainstorm_context(repo_name, supabase)

        if ctx["claude_md"]:
            block += f"\n--- PROJECT RULES ({repo_name}) ---\n{ctx['claude_md'][:2000]}\n"
        if ctx["status_md"]:
            block += f"\n--- CURRENT STATUS ({repo_name}) ---\n{ctx['status_md'][:1500]}\n"
        if ctx["recent_commits"]:
            block += f"\n--- RECENT CHANGES ({repo_name}) ---\n{ctx['recent_commits']}\n"
        if ctx["file_tree"]:
            block += f"\n--- FILES ({repo_name}) ---\n{ctx['file_tree'][:2000]}\n"
        if ctx["memory"]:
            block += f"\n--- MEMORY ({repo_name}) ---\n"
            for m in ctx["memory"][:10]:
                block += f"- {m['key']}: {m['value']}\n"
        if ctx["repo_config"].get("deploy_url"):
            block += f"\nLive at: {ctx['repo_config']['deploy_url']}\n"
    except Exception as e:
        logger.warning(f"Failed to load context for {repo_name}: {e}")
    return block


async def _build_system_prompt(user: dict, chat_id: str) -> str:
    """Build a context-aware system prompt — technical for admin, conversational for clients."""
    repo_name = get_active_repo(chat_id, user)

    if is_admin(user):
        # ── ADMIN PROMPT: technical, direct ──
        base = f"""You are Musa's CTO partner at Wingmen. Be direct, technical, and opinionated.
Keep responses concise (Telegram). When something should be built, suggest /build <repo> <detailed task>.

Example good suggestion:
/build ihsandms Add public /donate page with PayNow QR for non-onboarded donors - no login required, mobile-first layout, amount input with preset buttons

Example bad suggestion:
/build ihsandms fix donations

Always give detailed, actionable descriptions — an AI agent executes them.

Projects: {', '.join(user['repos'])}
"""
    else:
        # ── CLIENT PROMPT: conversational, non-technical ──
        base = f"""You are {user['name']}'s project assistant from Wingmen. You help them manage and improve their app/website through natural conversation.

PERSONALITY: Friendly, clear, proactive. Never use technical jargon. Speak like a helpful business partner, not a developer. Use simple language.

YOU CAN HELP WITH:
- Updating content (menu items, prices, text, images)
- Fixing things that aren't working
- Adding new features or pages
- Checking on progress of ongoing work
- Answering questions about their project
- Business advice related to their app

HOW ACTIONS WORK — ALWAYS CONFIRM BEFORE ACTING:
When the user wants something changed, built, or fixed, follow this flow strictly:

STEP 1 — UNDERSTAND: Ask clarifying questions if anything is ambiguous. What exactly? Where? Any preferences?
STEP 2 — SUMMARIZE: Restate what you'll do in plain, non-technical language. End with "Should I go ahead?"
STEP 3 — WAIT: Do NOT include an action block until the user explicitly confirms (e.g., "yes", "go ahead", "do it", "yep", "sure", "ok").
STEP 4 — EXECUTE: Only after confirmation, choose the RIGHT action tier and include the block.

THREE ACTION TIERS — use the lightest one that works:

TIER 1 — DATA (instant, for content/inventory changes):
Use for: updating prices, adding products, editing text, changing phone numbers, toggling settings.
[ACTION:DATA]
TABLE: products
OP: insert
DATA: {{"name": "Nasi Lemak", "price": 5.50, "category": "mains", "available": true}}
[/ACTION]

[ACTION:DATA]
TABLE: products
OP: update
WHERE: id=42
DATA: {{"price": 6.00}}
[/ACTION]

TIER 2 — CONFIG (provisioning, setup tasks):
Use for: new storefront, domain setup, account configuration.
[ACTION:CONFIG] Set up new storefront for merchant "Warung Pak Ali" with slug "warung-pak-ali", PayNow UEN 12345678A, phone +6581234567 [/ACTION]

TIER 3 — BUILD (code changes, takes minutes):
Use for: new features, bug fixes, design changes, new pages.
[ACTION:BUILD] detailed technical description for a developer. Include specific components, pages, behaviors, and acceptance criteria. [/ACTION]

CRITICAL RULES:
- NEVER include an action block on the first message about a topic — always clarify and confirm first
- The text BEFORE the action block is what the user sees — keep it friendly and non-technical
- The text INSIDE the action block is for the system — user won't see it
- Prefer DATA over BUILD — most client requests are data changes, not code changes
- For questions, status checks, or brainstorming — just respond normally, no action block
- If the user asks about progress, check the CURRENT STATUS section below
- If something is a quick question or chat, just respond — not everything needs an action

{user['name']}'s project{'s' if len(user['repos']) > 1 else ''}: {', '.join(user['repos'])}
"""

    if len(user["repos"]) > 1 and repo_name:
        base += f"Currently discussing: {repo_name}\n"

    if repo_name:
        base += await _load_repo_context_block(repo_name)

    return base


async def _parse_and_execute_actions(reply: str, user: dict, chat_id: str) -> str:
    """Parse action blocks from Claude's response and execute them.

    Supports 3 tiers:
    - [ACTION:DATA] — direct Supabase query (instant, e.g. update prices)
    - [ACTION:CONFIG] — provisioning (new storefront, DNS, etc.)
    - [ACTION:BUILD] — full code build pipeline
    """
    import re

    repo_name = get_active_repo(chat_id, user)

    # ── DATA actions (instant Supabase operations) ──
    data_match = re.search(r'\[ACTION:DATA\]\s*(.+?)\s*\[/ACTION\]', reply, re.DOTALL)
    if data_match:
        sql_or_op = data_match.group(1).strip()
        clean_reply = reply[:data_match.start()].strip()
        try:
            supabase = await get_supabase()
            # Parse structured operations (table, operation, data)
            result = await _execute_data_action(supabase, sql_or_op, repo_name)
            clean_reply += f"\n\n{result}"
            logger.info(f"Data action for {user['name']}: {sql_or_op[:100]}")
        except Exception as e:
            logger.error(f"Data action failed: {e}")
            clean_reply += "\n\nI tried to make that change but hit an issue. Let me flag this to the team."
        return clean_reply

    # ── CONFIG actions (provisioning) ──
    config_match = re.search(r'\[ACTION:CONFIG\]\s*(.+?)\s*\[/ACTION\]', reply, re.DOTALL)
    if config_match:
        config_op = config_match.group(1).strip()
        clean_reply = reply[:config_match.start()].strip()
        try:
            result = await _execute_config_action(config_op, user)
            clean_reply += f"\n\n{result}"
            logger.info(f"Config action for {user['name']}: {config_op[:100]}")
        except Exception as e:
            logger.error(f"Config action failed: {e}")
            clean_reply += "\n\nI'll need to get the team to handle this setup. I've flagged it!"
        return clean_reply

    # ── BUILD actions (code changes via Claude CLI) ──
    build_match = re.search(r'\[ACTION:BUILD\]\s*(.+?)\s*\[/ACTION\]', reply, re.DOTALL)
    if build_match:
        technical_desc = build_match.group(1).strip()
        clean_reply = reply[:build_match.start()].strip()

        if not repo_name:
            return clean_reply + "\n\nWhich project is this for? Just let me know!"

        if len(technical_desc) > 2000:
            technical_desc = technical_desc[:2000]

        try:
            config = context_loader.get_repo_config(repo_name)
            supabase = await get_supabase()
            result = await supabase.table("jobs").insert({
                "repo_name": repo_name,
                "description": technical_desc,
                "status": "queued",
                "priority": config["priority"],
                "triggered_by": "telegram",
                "client_id": user.get("client_id"),
            }).execute()

            if result.data:
                job = result.data[0]
                clean_reply += f"\n\nI've submitted this as a task (#{job['id']}). I'll update you on progress!"
                logger.info(f"Build queued #{job['id']} for {user['name']}: {repo_name} — {technical_desc[:100]}")
            else:
                clean_reply += "\n\nI tried to submit that but something went wrong. The team has been notified."
        except Exception as e:
            logger.error(f"Failed to queue build: {e}")
            clean_reply += "\n\nI tried to submit that but hit an issue. Let me flag this to the team."

        return clean_reply

    return reply


async def _execute_data_action(supabase, operation: str, repo_name: str | None) -> str:
    """Execute a structured data operation on Supabase.

    Expected format (from Claude):
    TABLE: <table_name>
    OP: insert|update|delete
    WHERE: <column>=<value>  (for update/delete)
    DATA: {"key": "value", ...}
    """
    import json as json_mod

    lines = operation.strip().splitlines()
    parsed = {}
    for line in lines:
        if ":" in line:
            key, val = line.split(":", 1)
            parsed[key.strip().upper()] = val.strip()

    table = parsed.get("TABLE", "")
    op = parsed.get("OP", "").lower()
    data_str = parsed.get("DATA", "{}")
    where_str = parsed.get("WHERE", "")

    if not table or not op:
        return "I couldn't process that change — the format was unclear. Let me try again."

    try:
        data = json_mod.loads(data_str)
    except json_mod.JSONDecodeError:
        return "I had trouble understanding the data format. Could you describe what you want changed?"

    if op == "insert":
        result = await supabase.table(table).insert(data).execute()
        if result.data:
            return "Done! The change is live now."
        return "I tried but the update didn't go through. Let me check with the team."

    elif op == "update" and where_str:
        col, val = where_str.split("=", 1)
        result = await supabase.table(table).update(data).eq(col.strip(), val.strip()).execute()
        if result.data:
            return "Done! The change is live now."
        return "I couldn't find that record to update. Can you double-check the details?"

    elif op == "delete" and where_str:
        col, val = where_str.split("=", 1)
        result = await supabase.table(table).delete().eq(col.strip(), val.strip()).execute()
        return "Done! That's been removed."

    return "I wasn't sure how to process that. Let me flag it to the team."


async def _execute_config_action(operation: str, user: dict) -> str:
    """Execute a configuration/provisioning action.

    Handles: new storefront setup, domain configuration, etc.
    """
    # For now, queue as a manual task for Musa
    supabase = await get_supabase()
    await supabase.table("jobs").insert({
        "repo_name": "orchestrator",
        "description": f"CONFIG: {operation}",
        "status": "queued",
        "priority": 2,
        "triggered_by": "telegram",
        "client_id": user.get("client_id"),
    }).execute()

    return "I've flagged this for setup. You'll be notified once it's ready!"


_chat_semaphore = asyncio.Semaphore(10)  # Max 10 concurrent Claude calls


async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages — technical brainstorm for admin, conversational for clients."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)

    if not user:
        await update.message.reply_text(
            "Assalamu alaikum! I don't have you in the system yet.\n"
            "Please ask your project manager to set you up."
        )
        return

    user_msg = update.message.text
    if not user_msg:
        return

    if len(user_msg) > MAX_MSG_LENGTH:
        await update.message.reply_text(f"Message too long (max {MAX_MSG_LENGTH} chars). Please shorten it.")
        return

    # Auto-detect repo from message if no active repo
    if not get_active_repo(chat_id, user):
        for repo in user["repos"]:
            if repo.lower() in user_msg.lower():
                _active_repo[chat_id] = repo
                break
        if not get_active_repo(chat_id, user) and len(user["repos"]) == 1:
            _active_repo[chat_id] = user["repos"][0]

    # Load persisted history
    history = await get_history(chat_id)
    history.append({"role": "user", "content": user_msg})

    # Trim to last 20 messages
    if len(history) > 20:
        history = history[-20:]

    # Persist user message
    await save_message(chat_id, "user", user_msg)

    async with _chat_semaphore:
        start = time.monotonic()
        try:
            system_prompt = await _build_system_prompt(user, chat_id)

            client = anthropic.AsyncAnthropic()
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=history,
            )
            reply = response.content[0].text
            tokens = response.usage.input_tokens + response.usage.output_tokens
            duration = time.monotonic() - start

            # For clients: parse action blocks and execute them
            if not is_admin(user):
                reply = await _parse_and_execute_actions(reply, user, chat_id)

            # Persist assistant reply
            await save_message(chat_id, "assistant", reply)

            # Log usage
            repo = get_active_repo(chat_id, user) or ""
            await log_usage(user.get("client_id"), "chat", repo, tokens, duration)

            if len(reply) > 4000:
                reply = reply[:4000] + "\n...(truncated)"

            await update.message.reply_text(reply)

        except anthropic.APIError as e:
            logger.error(f"Claude API error for {user['name']}: {e}")
            await update.message.reply_text("I'm having trouble connecting right now. Please try again in a moment.")

        except asyncio.TimeoutError:
            logger.error(f"Chat timeout for {user['name']}")
            await update.message.reply_text("That took too long. Please try a shorter message.")

        except Exception as e:
            logger.error(f"Chat error for {user['name']}: {e}")
            await update.message.reply_text("Sorry, something went wrong. Please try again.")


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        await update.message.reply_text("Not registered. Contact the admin.")
        return
    await update.message.reply_text("Unknown command. Send /start for help.")


# ── Main ─────────────────────────────────────────────────────────

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    app = Application.builder().token(token).build()

    # Universal commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("repo", cmd_repo))
    app.add_handler(CommandHandler("build", cmd_build))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("repos", cmd_repos))

    # Admin-only commands
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("priority", cmd_priority))
    app.add_handler(CommandHandler("addclient", cmd_addclient))
    app.add_handler(CommandHandler("linkrepo", cmd_linkrepo))
    app.add_handler(CommandHandler("clients", cmd_clients))
    app.add_handler(CommandHandler("usage", cmd_usage))

    # Free text → brainstorm
    app.add_handler(MessageHandler(filters.COMMAND, handle_unknown))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat))

    logger.info("Wingmen CTO Bot starting (long-polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

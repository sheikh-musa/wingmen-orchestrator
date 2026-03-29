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

# ── Whisper (local transcription) ────────────────────────────────
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def _whisper_transcribe(audio_path: str) -> str:
    """Transcribe audio file using local Whisper model. Runs in thread."""
    model = _get_whisper()
    result = model.transcribe(audio_path, language=None)  # auto-detect language
    return result.get("text", "").strip()


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

    # Load plan limits
    plan_id = client.get("plan", "free")
    plan_result = await supabase.table("plan_tiers").select("*").eq("id", plan_id).limit(1).execute()
    plan = plan_result.data[0] if plan_result.data else {
        "chats_per_day": 10, "builds_per_month": 2, "priority_queue": False
    }

    user = {
        "role": "client",
        "client": client,
        "client_id": client["id"],
        "repos": repo_names,
        "name": client["name"],
        "plan": plan,
        "ts": now,
    }
    _user_cache[chat_id] = user
    return user


def is_admin(user: dict) -> bool:
    return user["role"] == "admin"


# ── Onboarding ───────────────────────────────────────────────────

_onboarding_state: dict[str, dict] = {}  # chat_id -> {"step": ..., "data": {...}}


async def handle_onboarding(update: Update, chat_id: str) -> bool:
    """Handle new user onboarding flow. Returns True if handled (caller should return)."""
    supabase = await get_supabase()
    text = (update.message.text or "").strip()

    # Check if already has a pending signup
    existing = await supabase.table("pending_signups").select("*").eq(
        "telegram_chat_id", chat_id
    ).limit(1).execute()

    if existing.data and existing.data[0]["status"] == "pending":
        await update.message.reply_text(
            "Your account is pending approval. You'll be notified once you're set up! \U0001f44d"
        )
        return True

    state = _onboarding_state.get(chat_id, {"step": "start", "data": {}})

    if state["step"] == "start":
        tg_user = update.effective_user
        _onboarding_state[chat_id] = {
            "step": "name",
            "data": {"telegram_username": tg_user.username or ""},
        }
        await update.message.reply_text(
            "Assalamu alaikum! \U0001f44b Welcome to Wingmen.\n\n"
            "I'm your AI project assistant. Before we get started, "
            "I just need a couple of details.\n\n"
            "What's your name?"
        )
        return True

    elif state["step"] == "name":
        state["data"]["name"] = text
        state["step"] = "company"
        _onboarding_state[chat_id] = state
        await update.message.reply_text(
            f"Nice to meet you, {text}! \U0001f91d\n\n"
            "What's your company or project name?"
        )
        return True

    elif state["step"] == "company":
        state["data"]["company"] = text
        state["step"] = "done"

        # Save to pending_signups
        await supabase.table("pending_signups").insert({
            "telegram_chat_id": chat_id,
            "telegram_username": state["data"].get("telegram_username", ""),
            "name": state["data"]["name"],
            "company": text,
            "status": "pending",
        }).execute()

        # Notify Musa
        admin_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        admin_id = os.environ.get("MUSA_TELEGRAM_ID")
        if admin_token and admin_id:
            import httpx
            msg = (
                f"\U0001f195 New signup request:\n"
                f"Name: {state['data']['name']}\n"
                f"Company: {text}\n"
                f"Telegram: @{state['data'].get('telegram_username', 'N/A')}\n"
                f"Chat ID: {chat_id}\n\n"
                f"To approve:\n"
                f"/addclient {state['data']['name']} {chat_id} free\n"
                f"/linkrepo {state['data']['name']} <repo_name>"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{admin_token}/sendMessage",
                    json={"chat_id": admin_id, "text": msg},
                )

        _onboarding_state.pop(chat_id, None)

        await update.message.reply_text(
            f"Thanks, {state['data']['name']}! \u2705\n\n"
            "I've sent your details to the team. "
            "You'll be notified as soon as your account is ready.\n\n"
            "This usually takes just a few minutes!"
        )
        return True

    return False


async def check_usage_limit(user: dict, action: str) -> str | None:
    """Check if user is within their plan limits. Returns error message or None if OK."""
    if is_admin(user):
        return None

    plan = user.get("plan", {})
    client_id = user.get("client_id")
    if not client_id:
        return None

    supabase = await get_supabase()

    if action == "chat":
        # Check daily chat limit
        from datetime import datetime, timezone, timedelta
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
        result = await (
            supabase.table("usage_log")
            .select("id", count="exact")
            .eq("client_id", client_id)
            .eq("action_type", "chat")
            .gte("created_at", today_start)
            .execute()
        )
        count = result.count if hasattr(result, 'count') and result.count else len(result.data or [])
        limit = plan.get("chats_per_day", 10)
        if count >= limit:
            return (
                f"You've reached your daily chat limit ({limit} messages). "
                "Upgrade your plan for more! Send /upgrade to see options."
            )

    elif action == "build":
        # Check monthly build limit
        client_data = user.get("client", {})
        monthly_builds = client_data.get("monthly_build_count", 0)
        limit = plan.get("builds_per_month", 2)
        if monthly_builds >= limit:
            return (
                f"You've used all {limit} builds this month. "
                "Upgrade your plan for more! Send /upgrade to see options."
            )

    return None


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


async def _load_active_repo(chat_id: str) -> str | None:
    """Try to infer active repo from recent chat history."""
    try:
        supabase = await get_supabase()
        result = await (
            supabase.table("chat_history")
            .select("content")
            .eq("chat_id", chat_id)
            .eq("role", "user")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        if result.data:
            all_repos = context_loader.get_all_repo_names()
            for msg in result.data:
                for repo in all_repos:
                    if repo.lower() in msg["content"].lower():
                        return repo
    except Exception:
        pass
    return None


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
        await handle_onboarding(update, chat_id)
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


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show plan options."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)

    current_plan = "free"
    if user and user.get("client"):
        current_plan = user["client"].get("plan", "free")

    plans = (
        "Your plan: " + current_plan.upper() + "\n\n"
        "\U0001f7e2 FREE — $0/mo\n"
        "  10 chats/day, 2 builds/month\n\n"
        "\U0001f535 STARTER — $49/mo\n"
        "  50 chats/day, 10 builds/month\n\n"
        "\U0001f7e1 GROWTH — $149/mo\n"
        "  Unlimited chats, 30 builds/month, priority\n\n"
        "\U0001f7e0 SCALE — $399/mo\n"
        "  Unlimited everything, dedicated support\n\n"
        "To upgrade, contact the team or send /pay <plan>"
    )
    await update.message.reply_text(plans)


async def cmd_plan_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show realtime plan usage for admin — percentage of Max subscription used."""
    user = await resolve_user(str(update.effective_user.id))
    if not user or not is_admin(user):
        return

    supabase = await get_supabase()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Today's CLI calls (builds + chats + specs)
    day_result = await supabase.table("usage_log").select("id, action_type, duration_seconds").gte(
        "created_at", today_start
    ).execute()

    day_chats = sum(1 for r in (day_result.data or []) if r["action_type"] == "chat")
    day_builds = sum(1 for r in (day_result.data or []) if r["action_type"] in ("build_queued", "build_completed"))
    day_duration = sum(r.get("duration_seconds", 0) for r in (day_result.data or []))

    # This month's builds
    month_result = await supabase.table("usage_log").select("id, action_type, duration_seconds").gte(
        "created_at", month_start
    ).execute()

    month_chats = sum(1 for r in (month_result.data or []) if r["action_type"] == "chat")
    month_builds = sum(1 for r in (month_result.data or []) if r["action_type"] in ("build_queued", "build_completed"))
    month_duration = sum(r.get("duration_seconds", 0) for r in (month_result.data or []))

    # Active clients
    clients_result = await supabase.table("clients").select("id").eq("active", True).execute()
    active_clients = len(clients_result.data or [])

    # Active jobs
    jobs_result = await supabase.table("jobs").select("id, status").in_(
        "status", ["queued", "running"]
    ).execute()
    queued = sum(1 for j in (jobs_result.data or []) if j["status"] == "queued")
    running = sum(1 for j in (jobs_result.data or []) if j["status"] == "running")

    # Estimate plan usage (Pro 5x ~= 500 messages/day equivalent)
    # Each CLI call ≈ 1 message. Builds use ~10-50 messages each.
    estimated_daily_messages = day_chats + (day_builds * 30)
    daily_limit = 500  # rough Pro 5x estimate
    pct = min(100, int((estimated_daily_messages / daily_limit) * 100))

    bar_filled = pct // 5
    bar_empty = 20 - bar_filled
    bar = "\u2588" * bar_filled + "\u2591" * bar_empty

    days_left = (now.replace(month=now.month % 12 + 1, day=1) - now).days

    msg = (
        f"\U0001f4ca Plan Usage\n\n"
        f"Daily: [{bar}] {pct}%\n"
        f"  {day_chats} chats, {day_builds} builds, {day_duration:.0f}s CLI time\n\n"
        f"Monthly ({days_left} days left):\n"
        f"  {month_chats} chats, {month_builds} builds\n"
        f"  {month_duration / 3600:.1f}h total CLI time\n\n"
        f"System:\n"
        f"  {active_clients} active clients\n"
        f"  {running} running / {queued} queued jobs"
    )
    await update.message.reply_text(msg)


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Undo the last build — git revert + redeploy."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        return

    repo_name = get_active_repo(chat_id, user)
    if not repo_name:
        await update.message.reply_text("Select a repo first with /repo <name>")
        return

    try:
        config = context_loader.get_repo_config(repo_name)
        repo_path = os.path.expanduser(config["local_path"])

        # Check last commit
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-1",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        last_commit = stdout.decode(errors="replace").strip()

        if not last_commit:
            await update.message.reply_text("No commits to undo.")
            return

        # Confirm with user
        if not context.args or context.args[0] != "--confirm":
            await update.message.reply_text(
                f"Last commit on {repo_name}:\n{last_commit}\n\n"
                "This will revert it and redeploy. Send /undo --confirm to proceed."
            )
            return

        # Revert
        proc = await asyncio.create_subprocess_exec(
            "git", "revert", "HEAD", "--no-edit",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            await update.message.reply_text(f"Revert failed: {stderr.decode(errors='replace')[:500]}")
            return

        # Push
        proc = await asyncio.create_subprocess_exec(
            "git", "push",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=60)

        await log_audit(chat_id, user["name"], "undo", repo_name, last_commit)
        await update.message.reply_text(f"\u21a9\ufe0f Reverted and pushed: {last_commit}\n\nVercel will redeploy automatically.")

    except Exception as e:
        logger.error(f"Undo failed: {e}")
        await update.message.reply_text("Undo failed. Check with the team.")


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Take a screenshot of the live site and send it."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        return

    repo_name = get_active_repo(chat_id, user)
    if not repo_name:
        await update.message.reply_text("Select a repo first with /repo <name>")
        return

    try:
        config = context_loader.get_repo_config(repo_name)
        deploy_url = config.get("deploy_url")
        if not deploy_url or deploy_url == "FILL_IN":
            await update.message.reply_text(f"No deploy URL configured for {repo_name}.")
            return

        await update.message.reply_text(f"\U0001f4f8 Taking screenshot of {deploy_url}...")

        # Use a headless browser screenshot via CLI
        import tempfile
        screenshot_path = os.path.join(tempfile.gettempdir(), f"preview_{repo_name}.png")

        # Try playwright first, fall back to message
        proc = await asyncio.create_subprocess_exec(
            "npx", "playwright", "screenshot", deploy_url, screenshot_path,
            "--viewport-size", "375,812",  # iPhone viewport
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 0 and os.path.exists(screenshot_path):
            from telegram import InputFile
            with open(screenshot_path, "rb") as f:
                await update.message.reply_photo(photo=InputFile(f), caption=f"\U0001f4f1 {repo_name} — {deploy_url}")
            os.unlink(screenshot_path)
        else:
            await update.message.reply_text(f"Your site is live at: {deploy_url}")

    except asyncio.TimeoutError:
        await update.message.reply_text(f"Screenshot timed out. Your site is at: {deploy_url}")
    except Exception as e:
        logger.error(f"Preview failed: {e}")
        config = context_loader.get_repo_config(repo_name)
        await update.message.reply_text(f"Your site is live at: {config.get('deploy_url', 'N/A')}")


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekly digest of changes for active repo."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        return

    repo_name = get_active_repo(chat_id, user)
    if not repo_name:
        await update.message.reply_text("Select a repo first with /repo <name>")
        return

    try:
        config = context_loader.get_repo_config(repo_name)
        repo_path = os.path.expanduser(config["local_path"])

        # Get last 7 days of commits
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "--since=7 days ago",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        commits = stdout.decode(errors="replace").strip()

        # Get completed jobs this week
        supabase = await get_supabase()
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        jobs_result = await (
            supabase.table("jobs")
            .select("id, description, status, created_at")
            .eq("repo_name", repo_name)
            .eq("status", "completed")
            .gte("created_at", week_ago)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

        lines = [f"\U0001f4cb Weekly Digest — {repo_name}\n"]

        if jobs_result.data:
            lines.append("Completed tasks:")
            for j in jobs_result.data:
                desc = j["description"][:100].split("\n")[0]
                lines.append(f"  \u2705 #{j['id']}: {desc}")
        else:
            lines.append("No completed tasks this week.")

        if commits:
            lines.append(f"\nCommits ({commits.count(chr(10)) + 1}):")
            for line in commits.splitlines()[:10]:
                lines.append(f"  {line}")
        else:
            lines.append("\nNo commits this week.")

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        logger.error(f"Digest failed: {e}")
        await update.message.reply_text("Couldn't generate digest. Try again later.")


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
Keep responses concise (Telegram).

When Musa wants something built, you can either:
1. Suggest a /build command: /build <repo> <detailed task>
2. Or use an action block to auto-queue it after he confirms.

To auto-queue (preferred for conversational flow):
- First discuss and confirm with Musa
- After he says "yes"/"go ahead"/"do it", include:
[ACTION:BUILD] detailed technical description for the AI dev agent. Include specific files, components, behaviors, acceptance criteria. [/ACTION]

NEVER include action blocks without Musa confirming first.
For data changes, use [ACTION:DATA] with TABLE/OP/DATA/WHERE format.

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

    # ── BUILD actions (code changes via Claude CLI) — supports multiple ──
    build_matches = list(re.finditer(r'\[ACTION:BUILD\]\s*(.+?)\s*\[/ACTION\]', reply, re.DOTALL))
    if build_matches:
        # Clean reply = everything before the first action block
        clean_reply = reply[:build_matches[0].start()].strip()

        if not repo_name:
            return clean_reply + "\n\nWhich project is this for? Just let me know!"

        queued_ids = []
        for match in build_matches:
            technical_desc = match.group(1).strip()
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
                    queued_ids.append(str(job["id"]))
                    logger.info(f"Build queued #{job['id']} for {user['name']}: {repo_name} — {technical_desc[:100]}")
            except Exception as e:
                logger.error(f"Failed to queue build: {e}")

        if queued_ids:
            ids = ", #".join(queued_ids)
            clean_reply += f"\n\nI've submitted {len(queued_ids)} task{'s' if len(queued_ids) > 1 else ''} (#{ids}). I'll update you on progress!"
        else:
            clean_reply += "\n\nI tried to submit those but hit an issue. Let me flag this to the team."

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
        # Start onboarding flow for new users
        await handle_onboarding(update, chat_id)
        return

    user_msg = update.message.text
    if not user_msg:
        return

    if len(user_msg) > MAX_MSG_LENGTH:
        await update.message.reply_text(f"Message too long (max {MAX_MSG_LENGTH} chars). Please shorten it.")
        return

    # Check usage limits
    limit_msg = await check_usage_limit(user, "chat")
    if limit_msg:
        await update.message.reply_text(limit_msg)
        return

    # Auto-detect repo from message or recent history
    if not get_active_repo(chat_id, user):
        for repo in user["repos"]:
            if repo.lower() in user_msg.lower():
                _active_repo[chat_id] = repo
                break
        if not get_active_repo(chat_id, user) and len(user["repos"]) == 1:
            _active_repo[chat_id] = user["repos"][0]
        if not get_active_repo(chat_id, user):
            inferred = await _load_active_repo(chat_id)
            if inferred and inferred in user["repos"]:
                _active_repo[chat_id] = inferred

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

            # Build conversation as a single prompt for Claude CLI
            conv_parts = [f"SYSTEM:\n{system_prompt}\n"]
            for msg in history:
                role_label = "USER" if msg["role"] == "user" else "ASSISTANT"
                conv_parts.append(f"{role_label}:\n{msg['content']}\n")
            full_prompt = "\n---\n".join(conv_parts)
            full_prompt += "\n---\nRespond as ASSISTANT. Keep it concise (Telegram message)."

            # Use Claude CLI (Max subscription — no API cost)
            claude_bin = os.path.expanduser("~/.local/bin/claude")
            safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "USER", "SHELL", "LANG"}}
            safe_env["HOME"] = os.path.expanduser("~")
            safe_env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

            proc = await asyncio.create_subprocess_exec(
                claude_bin, "-p", full_prompt, "--output-format", "text",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=safe_env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            reply = stdout.decode(errors="replace").strip()
            duration = time.monotonic() - start

            if not reply:
                err = stderr.decode(errors="replace")
                logger.error(f"Claude CLI returned empty: {err}")
                await update.message.reply_text("I had trouble processing that. Please try again.")
                return

            # Parse action blocks for all users (admin + clients)
            reply = await _parse_and_execute_actions(reply, user, chat_id)

            # Persist assistant reply
            await save_message(chat_id, "assistant", reply)

            # Log usage (no token count from CLI, track duration)
            repo = get_active_repo(chat_id, user) or ""
            await log_usage(user.get("client_id"), "chat", repo, 0, duration)

            if len(reply) > 4000:
                reply = reply[:4000] + "\n...(truncated)"

            await update.message.reply_text(reply)

        except asyncio.TimeoutError:
            logger.error(f"Chat timeout for {user['name']}")
            await update.message.reply_text("That took too long. Please try a shorter message.")

        except Exception as e:
            logger.error(f"Chat error for {user['name']}: {e}")
            await update.message.reply_text("Sorry, something went wrong. Please try again.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages — save image and describe it in context for Claude."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)
    if not user:
        await handle_onboarding(update, chat_id)
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        return

    caption = update.message.caption or ""

    try:
        file = await context.bot.get_file(photo.file_id)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

        # Use Claude CLI to describe the image
        claude_bin = os.path.expanduser("~/.local/bin/claude")
        safe_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "USER", "SHELL", "LANG"}}
        safe_env["HOME"] = os.path.expanduser("~")
        safe_env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

        describe_prompt = (
            "Describe this image concisely. If it's a screenshot of a website or app, "
            "describe what you see including any bugs, layout issues, or content. "
            "If there's text, read it. Keep it under 200 words."
        )

        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", describe_prompt,
            "--files", tmp_path,
            "--output-format", "text",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        description = stdout.decode(errors="replace").strip()
        os.unlink(tmp_path)

        if not description:
            description = "(image received but couldn't be analyzed)"

        # Combine with caption and process as chat
        combined = f"[User sent a photo: {description}]"
        if caption:
            combined += f"\nUser's message: {caption}"

        update.message.text = combined
        await handle_chat(update, context)

    except Exception as e:
        logger.error(f"Photo processing error: {e}")
        if caption:
            update.message.text = f"[User sent a photo they want to discuss] {caption}"
            await handle_chat(update, context)
        else:
            await update.message.reply_text("I received your photo. Could you describe what you'd like me to do with it?")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages — download, transcribe via Claude, then process as text."""
    chat_id = str(update.effective_user.id)
    user = await resolve_user(chat_id)

    if not user:
        await update.message.reply_text("Not registered. Contact the admin.")
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    # Step 1: Download and transcribe
    try:
        file = await context.bot.get_file(voice.file_id)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

        transcription = await asyncio.to_thread(_whisper_transcribe, tmp_path)
        os.unlink(tmp_path)
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        await update.message.reply_text("I had trouble with the audio. Could you try again or type it instead?")
        return

    if not transcription:
        await update.message.reply_text("I couldn't make out what you said. Could you try again?")
        return

    # Step 2: Show transcription and process as chat
    await update.message.reply_text(f"\U0001f3a4 I heard: \"{transcription}\"")
    update.message.text = transcription
    await handle_chat(update, context)


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
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("mu", cmd_plan_usage))  # admin: plan usage
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("digest", cmd_digest))

    # Free text + voice → brainstorm
    app.add_handler(MessageHandler(filters.COMMAND, handle_unknown))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat))

    logger.info("Wingmen CTO Bot starting (long-polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

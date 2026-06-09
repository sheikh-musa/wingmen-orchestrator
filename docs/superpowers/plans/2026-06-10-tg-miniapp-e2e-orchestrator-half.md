# TG Mini App e2e — Orchestrator Half Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the orchestrator's half of the shared-bot storefront: register a merchant org, emit its `t.me/dookanabot?start=<slug>` deep-link, and on that deep-link launch the ihsanos Mini App at `/shop/<slug>` — all behind a SINGLE platform bot token.

**Architecture:** One shared Telegram bot (@dookanabot, `STOREFRONT_TG_BOT_TOKEN`) serves every merchant. A customer reaches a specific shop via `?start=<slug>`, which the dispatcher resolves statelessly against `clients.storefront_slug` and answers with a `web_app` inline button opening `<storefront_web_base>/shop/<slug>` inside Telegram. Merchant onboarding writes a `clients` row (no BotFather token paste) and returns the deep-link. The legacy per-merchant token path (`bot_onboarding.py`) stays DORMANT, not deleted.

**Tech Stack:** Python 3.9, `python-telegram-bot` (`telegram.Bot`, `InlineKeyboardMarkup`/`InlineKeyboardButton`/`WebAppInfo`), aiohttp webhook server, Supabase via psycopg (schema apply) and the async supabase client (runtime), httpx (Telegram getMe).

---

## Cross-Lane Contract (read before starting)

- **Slug source of truth:** the deep-link slug MUST equal a valid ihsanos `organizations.slug` (cc-ihsanos confirmed, msg #1913; resolved their side via `getStorefrontBySlug(slug)`). The orchestrator stores its own `clients.storefront_slug` copy and treats the operator-supplied slug as authoritative. Cross-system existence (does the slug exist in `organizations`?) is validated by the ihsanos Mini App, NOT here — an unknown-to-ihsanos slug renders an ihsanos-side "shop not found", which is acceptable.
- **cai ratification (CAI-RESP-201):** merchant-slug column on `clients` approved — unique + URL-safe; validate `?start=<slug>` against `clients`; unknown → graceful fallback. Build single-bot regardless; keep `bot_onboarding.py` DORMANT.
- **O5 (`IHSANOS_API_BASE`) is DEFERRED** to the 2026-06-15 model-sweep pass per cai. Task 7 documents it but the executor must NOT run it as part of this build.

## Scope Decision (flag for operator/cai before merge)

**IN scope (MVP critical path):** O1 org self-onboard + deep-link emit · O2 single platform-token load · O3 `?start=<slug>` stateless resolution + `storefront_slug` column + graceful fallback · O4 `web_app` Mini App launch button.

**OUT of MVP scope (documented follow-up, not built here):** the conversational chat handlers (`order_handler`, `qurban_handler`, `site_edit_handler`) under the shared bot. They assume one org per bot (`client_bot.ihsanos_org_id`) and use `client_bot.bot_username` as the shop slug — both false under @dookanabot. Making them work requires a persistent chat→org binding table (a customer's 2nd message carries no `start_param`). The Mini App e2e does NOT need this: ordering happens inside the Mini App (cc-ihsanos's `/shop/{slug}` WebApp + place-order), so the chat's only job is to launch it. Persistent binding + handler rework is tracked as **CC-ORCH-STOREFRONT-CHAT-BINDING-001** (file after this lands).

## File Structure

- **Create** `storefront/__init__.py` — package marker.
- **Create** `storefront/slug.py` — `validate_slug` / `normalize_slug` pure helpers (O3).
- **Create** `storefront/onboarding.py` — `register_storefront_org` + `build_deep_link` (O1).
- **Create** `storefront/miniapp.py` — `build_miniapp_keyboard` / `build_shop_url` (O4).
- **Create** `storefront/platform_bot.py` — `load_platform_bot` (O2).
- **Modify** `bot_manager.py` — add `is_platform: bool = False` and `slug: str | None = None` fields to `ClientBot` (O2/O3).
- **Modify** `message_dispatcher.py` — shared-bot `/start <slug>` resolution shim near the existing `/start` block (O3/O4).
- **Modify** `schema.sql` — `storefront_slug` column + unique index (O3).
- **Create** `scripts/apply_storefront_slug.py` — psycopg-apply migration (CLAUDE.md forbids `supabase db push`).
- **Create** `tests/storefront/__init__.py`, `tests/storefront/test_slug.py`, `tests/storefront/test_onboarding.py`, `tests/storefront/test_miniapp.py`.

Test runner: `.venv/bin/python -m pytest <path> -v`.

---

### Task 1: `storefront_slug` column + unique index

**Files:**
- Modify: `schema.sql` (append after line 145, the bot-support `clients` extensions)
- Create: `scripts/apply_storefront_slug.py`

- [ ] **Step 1: Add the column + index to `schema.sql`**

Append after the existing `alter table clients add column if not exists welcome_message text;` block:

```sql
-- Shared-bot storefront: merchant slug carried by t.me/dookanabot?start=<slug>
-- Must equal a valid ihsanos organizations.slug (cross-lane contract, msg #1913).
alter table clients add column if not exists storefront_slug text;
create unique index if not exists clients_storefront_slug_key
  on clients (storefront_slug)
  where storefront_slug is not null;
```

- [ ] **Step 2: Write the psycopg-apply migration script**

Create `scripts/apply_storefront_slug.py`:

```python
"""Apply the clients.storefront_slug column + unique index via psycopg.

CLAUDE.md forbids `supabase db push` against production (shadow-diff strips
view arms). Use the orch's direct psycopg-apply pattern instead.
"""
from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

DDL = """
alter table clients add column if not exists storefront_slug text;
create unique index if not exists clients_storefront_slug_key
  on clients (storefront_slug)
  where storefront_slug is not null;
"""


def main() -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL / SUPABASE_DB_URL not set")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    print("applied: clients.storefront_slug + clients_storefront_slug_key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Commit (do NOT run against prod yet — operator runs at deploy time)**

```bash
git add schema.sql scripts/apply_storefront_slug.py
git commit -m "feat(tg-miniapp): clients.storefront_slug column + psycopg apply script"
```

> The executor must NOT run `apply_storefront_slug.py` against production. The operator applies it during the live-e2e deploy step. Local correctness is covered by the pure-function tests below.

---

### Task 2: Slug validation + normalization

**Files:**
- Create: `storefront/__init__.py` (empty)
- Create: `storefront/slug.py`
- Create: `tests/storefront/__init__.py` (empty)
- Create: `tests/storefront/test_slug.py`

- [ ] **Step 1: Create package markers**

Create `storefront/__init__.py` and `tests/storefront/__init__.py`, both empty.

- [ ] **Step 2: Write the failing test**

Create `tests/storefront/test_slug.py`:

```python
"""URL-safe slug rules for t.me/dookanabot?start=<slug>.

Telegram start_param allows A-Z a-z 0-9 _ and - (max 64 chars). We further
lowercase and require 1-32 chars to keep shop URLs clean and stable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from storefront.slug import normalize_slug, validate_slug


def test_validate_accepts_simple_slug():
    assert validate_slug("aunty-mariam") is True


def test_validate_accepts_underscores_and_digits():
    assert validate_slug("shop_42") is True


def test_validate_rejects_empty():
    assert validate_slug("") is False


def test_validate_rejects_spaces():
    assert validate_slug("aunty mariam") is False


def test_validate_rejects_uppercase():
    assert validate_slug("AuntyMariam") is False


def test_validate_rejects_slash():
    assert validate_slug("a/b") is False


def test_validate_rejects_too_long():
    assert validate_slug("x" * 33) is False


def test_normalize_lowercases_and_trims():
    assert normalize_slug("  Aunty-Mariam  ") == "aunty-mariam"


def test_normalize_spaces_to_hyphens():
    assert normalize_slug("Aunty Mariam Kitchen") == "aunty-mariam-kitchen"


def test_normalize_strips_invalid_chars():
    assert normalize_slug("café!!bistro") == "cafbistro"
```

- [ ] **Step 3: Run test, verify it fails**

Run: `.venv/bin/python -m pytest tests/storefront/test_slug.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storefront.slug'`

- [ ] **Step 4: Write minimal implementation**

Create `storefront/slug.py`:

```python
"""URL-safe slug rules for the shared-bot deep-link `?start=<slug>`."""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def validate_slug(slug: str) -> bool:
    """True if slug is a clean, URL-safe storefront slug (1-32 chars,
    lowercase a-z, digits, hyphen, underscore)."""
    return bool(_SLUG_RE.match(slug or ""))


def normalize_slug(raw: str) -> str:
    """Best-effort coercion of free text into a candidate slug.

    Lowercases, trims, collapses whitespace to hyphens, drops any char
    outside [a-z0-9_-]. Caller must still validate_slug() the result.
    """
    s = (raw or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9_-]", "", s)
    return s
```

- [ ] **Step 5: Run test, verify it passes**

Run: `.venv/bin/python -m pytest tests/storefront/test_slug.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: Commit**

```bash
git add storefront/__init__.py storefront/slug.py tests/storefront/__init__.py tests/storefront/test_slug.py
git commit -m "feat(tg-miniapp): URL-safe storefront slug validate/normalize"
```

---

### Task 3: Org self-onboard + deep-link emit

**Files:**
- Create: `storefront/onboarding.py`
- Create: `tests/storefront/test_onboarding.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storefront/test_onboarding.py`:

```python
"""Frictionless org onboarding for the shared platform bot.

No BotFather token paste (that's the dormant per-merchant path). Onboarding
writes a clients row carrying ihsanos_org_id + storefront_slug, then emits
the customer deep-link t.me/<bot_username>?start=<slug>.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from storefront.onboarding import build_deep_link, register_storefront_org


def test_build_deep_link():
    assert (
        build_deep_link("dookanabot", "aunty-mariam")
        == "https://t.me/dookanabot?start=aunty-mariam"
    )


def test_build_deep_link_strips_at_sign():
    assert (
        build_deep_link("@dookanabot", "shop_42")
        == "https://t.me/dookanabot?start=shop_42"
    )


def test_register_rejects_invalid_slug():
    with pytest.raises(ValueError, match="invalid slug"):
        register_storefront_org(
            _FakeSupabase(), bot_username="dookanabot",
            name="X", ihsanos_org_id="org_1", slug="Bad Slug",
        )


def test_register_writes_clients_row_and_returns_link():
    sb = _FakeSupabase()
    link = register_storefront_org(
        sb, bot_username="dookanabot",
        name="Aunty Mariam Kitchen", ihsanos_org_id="org_1",
        slug="aunty-mariam", capabilities=["storefront"],
    )
    assert link == "https://t.me/dookanabot?start=aunty-mariam"
    assert sb.upserted["storefront_slug"] == "aunty-mariam"
    assert sb.upserted["ihsanos_org_id"] == "org_1"
    assert sb.upserted["name"] == "Aunty Mariam Kitchen"
    assert sb.upserted["capabilities"] == ["storefront"]


class _FakeSupabase:
    """Minimal stand-in for the sync upsert path used by register_storefront_org."""

    def __init__(self):
        self.upserted: dict = {}

    def table(self, _name):
        return self

    def upsert(self, row, **_kw):
        self.upserted = row
        return self

    def execute(self):
        return self
```

- [ ] **Step 2: Run test, verify it fails**

Run: `.venv/bin/python -m pytest tests/storefront/test_onboarding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storefront.onboarding'`

- [ ] **Step 3: Write minimal implementation**

Create `storefront/onboarding.py`:

```python
"""Frictionless storefront org onboarding under the shared platform bot.

register_storefront_org writes/updates a clients row (NO telegram_bot_token —
the shared bot owns the token) and returns the customer deep-link. The legacy
per-merchant flow in bot_onboarding.py stays DORMANT.
"""
from __future__ import annotations

from storefront.slug import validate_slug


def build_deep_link(bot_username: str, slug: str) -> str:
    """t.me deep-link that lands the customer in `slug`'s shop."""
    handle = bot_username.lstrip("@")
    return f"https://t.me/{handle}?start={slug}"


def register_storefront_org(
    supabase,
    *,
    bot_username: str,
    name: str,
    ihsanos_org_id: str,
    slug: str,
    capabilities: list[str] | None = None,
) -> str:
    """Register a merchant org behind the shared bot. Returns the deep-link.

    Idempotent on storefront_slug (upsert). Raises ValueError on a bad slug.
    """
    if not validate_slug(slug):
        raise ValueError(f"invalid slug: {slug!r}")

    supabase.table("clients").upsert(
        {
            "name": name,
            "ihsanos_org_id": ihsanos_org_id,
            "storefront_slug": slug,
            "platform": "ihsanos",
            "capabilities": capabilities or ["storefront"],
            "active": True,
        },
        on_conflict="storefront_slug",
    ).execute()

    return build_deep_link(bot_username, slug)
```

- [ ] **Step 4: Run test, verify it passes**

Run: `.venv/bin/python -m pytest tests/storefront/test_onboarding.py -v`
Expected: PASS (4 passed)

> Note: the runtime supabase client is async; this MVP function uses the sync
> upsert shape for testability and operator-CLI use. The dispatcher (Task 6)
> only READS slugs, so no async onboarding path is needed for the e2e. Wiring
> onboarding to a `/onboard` CTO command is a follow-up, not in this plan.

- [ ] **Step 5: Commit**

```bash
git add storefront/onboarding.py tests/storefront/test_onboarding.py
git commit -m "feat(tg-miniapp): storefront org onboarding + deep-link emit"
```

---

### Task 4: Mini App launch button (`web_app` inline keyboard)

**Files:**
- Create: `storefront/miniapp.py`
- Create: `tests/storefront/test_miniapp.py`

- [ ] **Step 1: Write the failing test**

Create `tests/storefront/test_miniapp.py`:

```python
"""web_app launch button that opens the ihsanos Mini App inside Telegram.

Telegram requires the web_app URL to be HTTPS. The shop lives at
<storefront_web_base>/shop/<slug> (cc-ihsanos owns the /shop/{slug} route).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from storefront.miniapp import build_miniapp_keyboard, build_shop_url


def test_build_shop_url_default_base():
    assert (
        build_shop_url("aunty-mariam", "https://ihsanos.com")
        == "https://ihsanos.com/shop/aunty-mariam"
    )


def test_build_shop_url_strips_trailing_slash():
    assert (
        build_shop_url("shop_42", "https://preview.ihsanos.com/")
        == "https://preview.ihsanos.com/shop/shop_42"
    )


def test_keyboard_has_single_web_app_button():
    kb = build_miniapp_keyboard("aunty-mariam", "https://ihsanos.com")
    assert len(kb.inline_keyboard) == 1
    row = kb.inline_keyboard[0]
    assert len(row) == 1
    button = row[0]
    assert button.web_app is not None
    assert button.web_app.url == "https://ihsanos.com/shop/aunty-mariam"
    assert "Shop" in button.text
```

- [ ] **Step 2: Run test, verify it fails**

Run: `.venv/bin/python -m pytest tests/storefront/test_miniapp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storefront.miniapp'`

- [ ] **Step 3: Write minimal implementation**

Create `storefront/miniapp.py`:

```python
"""Telegram Mini App launch surface for the shared storefront bot."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def build_shop_url(slug: str, web_base: str) -> str:
    """HTTPS URL of the merchant's Mini App storefront."""
    return f"{web_base.rstrip('/')}/shop/{slug}"


def build_miniapp_keyboard(slug: str, web_base: str) -> InlineKeyboardMarkup:
    """A one-button keyboard that opens the merchant's Mini App in-app."""
    url = build_shop_url(slug, web_base)
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="🛍️ Open Shop", web_app=WebAppInfo(url=url))]]
    )
```

- [ ] **Step 4: Run test, verify it passes**

Run: `.venv/bin/python -m pytest tests/storefront/test_miniapp.py -v`
Expected: PASS (3 passed)

> If `telegram` import differs in this repo's version, confirm `WebAppInfo`
> exists (python-telegram-bot >= 20). The dispatcher already imports
> `from telegram import Bot`, so the package is present.

- [ ] **Step 5: Commit**

```bash
git add storefront/miniapp.py tests/storefront/test_miniapp.py
git commit -m "feat(tg-miniapp): web_app Mini App launch keyboard"
```

---

### Task 5: Single platform-bot loader (`STOREFRONT_TG_BOT_TOKEN`)

**Files:**
- Modify: `bot_manager.py` (add fields to `ClientBot`)
- Create: `storefront/platform_bot.py`

- [ ] **Step 1: Add `is_platform` and `slug` fields to `ClientBot`**

In `bot_manager.py`, the `ClientBot` dataclass (lines 22-34) ends with `ihsanos_org_id: str | None`. Append two fields with defaults so existing constructions stay valid:

```python
@dataclass
class ClientBot:
    client_id: int
    token: str
    token_hash: str
    bot_username: str
    bot_display_name: str
    personality: str | None
    welcome_message: str | None
    capabilities: list[str]
    repo_name: str | None
    platform: str | None
    ihsanos_org_id: str | None
    is_platform: bool = False
    slug: str | None = None
```

- [ ] **Step 2: Write the platform-bot loader**

Create `storefront/platform_bot.py`:

```python
"""Load the single shared platform bot (@dookanabot) into the BotManager.

Unlike per-merchant bots, the platform bot has NO fixed ihsanos_org_id — the
merchant is resolved per-conversation from the ?start=<slug> deep-link. It is
registered with is_platform=True so the dispatcher takes the shared-bot path.
"""
from __future__ import annotations

import logging
import os

from bot_manager import BotManager, ClientBot, compute_token_hash
from bot_onboarding import validate_token

logger = logging.getLogger("wingmen.storefront.platform_bot")

_PLATFORM_CLIENT_ID = -1  # sentinel: the platform bot is not a merchant client


async def load_platform_bot(bot_manager: BotManager) -> ClientBot | None:
    """Read STOREFRONT_TG_BOT_TOKEN, validate via getMe, register the bot.

    Returns the registered ClientBot, or None if the token is unset/invalid.
    """
    token = os.environ.get("STOREFRONT_TG_BOT_TOKEN")
    if not token:
        logger.warning("STOREFRONT_TG_BOT_TOKEN not set; platform bot not loaded")
        return None

    info = await validate_token(token)
    if not info:
        logger.error("STOREFRONT_TG_BOT_TOKEN failed getMe validation")
        return None

    bot = ClientBot(
        client_id=_PLATFORM_CLIENT_ID,
        token=token,
        token_hash=compute_token_hash(token),
        bot_username=info.get("username", ""),
        bot_display_name=info.get("first_name", "Storefront"),
        personality=None,
        welcome_message=None,
        capabilities=["storefront"],
        repo_name=None,
        platform="ihsanos",
        ihsanos_org_id=None,
        is_platform=True,
        slug=None,
    )

    bot_manager._bots[bot.token_hash] = bot
    bot_manager._by_client_id[bot.client_id] = bot
    await bot_manager._set_webhook(bot.token, bot.token_hash)
    logger.info(f"Loaded platform bot @{bot.bot_username}")
    return bot
```

- [ ] **Step 3: Verify imports resolve (smoke check, no network)**

Run: `.venv/bin/python -c "import storefront.platform_bot; import bot_manager; b=bot_manager.ClientBot(1,'t','h','u','d',None,None,[],None,None,None); print(b.is_platform, b.slug)"`
Expected: `False None`

- [ ] **Step 4: Commit**

```bash
git add bot_manager.py storefront/platform_bot.py
git commit -m "feat(tg-miniapp): load single shared platform bot (is_platform)"
```

> Wiring `load_platform_bot` into the orchestrator boot sequence (alongside
> `BotManager.load_all`) is done in Task 6's integration step, where the
> dispatcher path that consumes `is_platform` also lands — keeps the
> boot-time behavior change and its handler together.

---

### Task 6: Dispatcher shared-bot resolution shim (`/start <slug>` → welcome + button)

**Files:**
- Modify: `message_dispatcher.py` (new branch inside `dispatch`, near the existing `/start` handling at lines 94-110)
- Modify: orchestrator boot (wherever `BotManager.load_all` is awaited) to also `await load_platform_bot(bot_manager)`

- [ ] **Step 1: Add a stateless slug-resolution helper to `message_dispatcher.py`**

Add near the top-level helpers (after imports, alongside `FLOW_HANDLERS`). This reads `clients` by slug and builds the launch message. It NEVER mutates `clients`.

```python
import os
from storefront.miniapp import build_miniapp_keyboard

_STOREFRONT_WEB_BASE_DEFAULT = "https://ihsanos.com"


async def _resolve_storefront_slug(supabase, slug: str) -> dict | None:
    """Look up a merchant clients row by storefront_slug. Read-only."""
    result = (
        await supabase.table("clients")
        .select("id, name, storefront_slug, ihsanos_org_id, welcome_message")
        .eq("storefront_slug", slug)
        .eq("active", True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


async def _handle_storefront_start(bot, supabase, chat_id: str, slug: str) -> None:
    """Shared-bot /start <slug>: resolve slug, send welcome + Mini App button.

    Unknown slug → graceful fallback (cai contract CAI-RESP-201). No state is
    persisted; the slug travels in the web_app button URL.
    """
    row = await _resolve_storefront_slug(supabase, slug)
    if not row:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "That shop link doesn't look right. Please use the link the "
                "shop shared with you, or ask them for a new one."
            ),
        )
        return

    web_base = os.environ.get("STOREFRONT_WEB_BASE", _STOREFRONT_WEB_BASE_DEFAULT)
    keyboard = build_miniapp_keyboard(row["storefront_slug"], web_base)
    welcome = row.get("welcome_message") or f"Welcome to {row['name']}!"
    await bot.send_message(
        chat_id=chat_id,
        text=f"{welcome}\n\nTap below to start shopping 👇",
        reply_markup=keyboard,
    )
```

- [ ] **Step 2: Branch the existing `/start` handling for the platform bot**

In `dispatch`, the current order is: invite check (line 95), then bare `/start` (line 101). Insert the platform-bot storefront branch BEFORE the invite check so a storefront slug isn't mistaken for an invite. Replace the block starting at line 94 (`# --- Handle /start with invite code ---`) with:

```python
        # --- Shared platform bot: /start <slug> opens the merchant Mini App ---
        if client_bot.is_platform and text.startswith("/start "):
            slug = text.split(" ", 1)[1].strip()
            if slug.startswith("invite_"):
                await _handle_invite(
                    supabase, bot, client_bot, chat_id, from_user,
                    slug.split("invite_", 1)[1].strip(),
                )
            else:
                await _handle_storefront_start(bot, supabase, chat_id, slug)
            return

        # --- Handle /start with invite code ---
        if text.startswith("/start invite_"):
            invite_code = text.split("invite_", 1)[1].strip()
            await _handle_invite(supabase, bot, client_bot, chat_id, from_user, invite_code)
            return

        # --- Handle /start (welcome message) ---
        if text == "/start":
            if client_bot.is_platform:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "Welcome! Open a shop using the link your merchant "
                        "shared with you."
                    ),
                    reply_to_message_id=reply_to,
                )
                return
            welcome = client_bot.welcome_message or f"Welcome to {client_bot.bot_display_name}! How can I help you?"
            await bot.send_message(chat_id=chat_id, text=welcome, reply_to_message_id=reply_to)
            # Auto-register as customer
            await resolve_user(
                supabase, client_bot.client_id, user_chat_id,
                telegram_username=from_user.get("username"),
                display_name=from_user.get("first_name", "Customer"),
            )
            return
```

- [ ] **Step 3: Short-circuit non-start messages on the platform bot (MVP)**

The conversational handlers are OUT of MVP scope under the shared bot. Immediately after the `/start` blocks above and before `# --- Resolve user ---` (line 112), add:

```python
        # MVP: the shared platform bot only launches the Mini App. Conversational
        # ordering happens inside the Mini App, not in chat. Anything that isn't a
        # recognized /start lands here. Chat→org binding is a follow-up
        # (CC-ORCH-STOREFRONT-CHAT-BINDING-001).
        if client_bot.is_platform:
            await bot.send_message(
                chat_id=chat_id,
                text="Open your shop with its link to browse and order 🛍️",
                reply_to_message_id=reply_to,
            )
            return
```

- [ ] **Step 4: Wire `load_platform_bot` into boot**

The boot site is `wingmen_orch.py:1600-1605`:

```python
    bot_manager = BotManager()
    count = await bot_manager.load_all(supabase)

    if count > 0:
        await bot_manager.register_all_webhooks()
        logger.info(f"Registered webhooks for {count} client bots")
```

Insert the platform-bot load immediately after the `if count > 0:` block (the loader self-registers its own webhook, so it works regardless of `count`):

```python
    from storefront.platform_bot import load_platform_bot
    platform_bot = await load_platform_bot(bot_manager)
    if platform_bot:
        logger.info(f"Registered shared platform bot @{platform_bot.bot_username}")
```

- [ ] **Step 5: Import + smoke-compile the dispatcher**

Run: `.venv/bin/python -c "import message_dispatcher; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Manual e2e checklist (requires live token + deployed Mini App — operator-gated)**

This step is NOT run by the implementer subagent; it's the live-run checklist for the operator/orchestrator after merge:

1. Operator sets on the Vercel preview AND the Mac Mini `.env`: `STOREFRONT_TG_BOT_TOKEN` (already set locally), `STOREFRONT_WEB_BASE=<ihsanos preview URL>`.
2. Operator runs `.venv/bin/python scripts/apply_storefront_slug.py` against the DB.
3. Register a test org: call `register_storefront_org(... slug="testshop" ...)` (or the future `/onboard` command) → get `t.me/dookanabot?start=testshop`.
4. Open the deep-link in Telegram → expect merchant welcome + "🛍️ Open Shop" button → tap → ihsanos Mini App `/shop/testshop` opens in-app.
5. Unknown slug `t.me/dookanabot?start=nope` → expect graceful fallback text, no button.
6. Ping cc-ihsanos: "orchestrator half landed" so they wire the live place-order run (their standing ask, msg #1913).

- [ ] **Step 7: Commit**

```bash
git add message_dispatcher.py wingmen_orch.py
git commit -m "feat(tg-miniapp): shared-bot /start <slug> resolves to Mini App launch"
```

---

### Task 7: `IHSANOS_API_BASE` parameterization — DEFERRED to 2026-06-15

> **DO NOT EXECUTE in this build.** cai (CAI-RESP-201) folded O5 into the
> 2026-06-15 model-sweep pass. Documented here only so the sweep executor has
> the exact change set.

**Files (for the 06-15 sweep):**
- Modify: `handlers/order_handler.py:15`, `handlers/qurban_handler.py:15`, `handlers/site_edit_handler.py:15`

Replace each module's `IHSANOS_API = "https://ihsanos.com/api"` with:

```python
import os
IHSANOS_API = os.environ.get("IHSANOS_API_BASE", "https://ihsanos.com/api")
```

Also fold in (same sweep, part of CC-ORCH-STOREFRONT-CHAT-BINDING-001): replace the shop-URL `client_bot.bot_username` usages (`order_handler.py:94`, `order_handler.py:251`, `qurban_handler.py:138`) with the merchant slug, since `bot_username` is `@dookanabot` (not unique per merchant) under the shared bot. This requires the chat→org binding that carries `slug` onto the per-conversation `ClientBot` — out of scope until that follow-up lands.

---

## Post-Build

- [ ] Run the full storefront suite: `.venv/bin/python -m pytest tests/storefront/ -v` (expect 17 passed: 10 slug + 4 onboarding + 3 miniapp).
- [ ] Dispatch final code reviewer over the whole branch.
- [ ] File **CC-ORCH-STOREFRONT-CHAT-BINDING-001** (persistent chat→org binding + conversational-handler rework + the bot_username→slug shop-URL fix).
- [ ] Ping cc-ihsanos that the orchestrator half landed (msg #1913 standing ask); ping cai with the scope decision (MVP = Mini App launch only; chat binding deferred) for visibility.
- [ ] Use superpowers:finishing-a-development-branch.

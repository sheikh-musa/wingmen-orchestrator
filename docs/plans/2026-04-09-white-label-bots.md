# White-Label Bot System -- Implementation Plan

**Date:** 2026-04-09
**Spec:** `docs/specs/2026-04-09-white-label-bots-design.md`
**Status:** Ready for implementation

## Parallel Execution Map

```
Track 1: DB Schema + Multi-Bot Webhook Engine (first -- foundation)
    |
    |-->  Track 2: Client Onboarding Flow (after Track 1)
    |-->  Track 3: Team Invite + Role System (after Track 1)
    |
    |-->  Track 4: Conversation State Machine + Flows (after 2-3)
    |       |-- Ordering flow
    |       |-- Qurban booking flow
    |       |-- Site editing flow
    |       \-- Team management flow
    |
    \-->  Track 5: ihsanOS API endpoints + Analytics + Tests (after 4)
```

---

## Codebase Patterns (Reference)

These patterns were extracted from the existing codebase. Every new file must follow them exactly.

**Supabase client:** Lazy async singleton via `get_supabase()` using `acreate_client` (see `cto_bot.py:100`). All DB calls use `await supabase.table("x").select/insert/update/upsert(...).execute()`.

**AI calls:** Always through `call_ai(prompt, system=..., model=..., json_mode=...)` from `ai_provider.py`. Never import anthropic directly. Use `extract_json()` to parse AI responses.

**Router pattern:** `build_router_prompt()` returns a prompt string, `parse_router_response()` parses JSON output. Router returns `{"intent": "...", "repo": "...", "detail": "..."}`. Valid intents are enforced via a set.

**Telegram handlers:** Registered in `main()` via `app.add_handler(CommandHandler/MessageHandler/CallbackQueryHandler(...))`. Handlers are `async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE)`. Callbacks use pattern matching on `callback_data` strings (e.g., `pattern=r"^bug_"`).

**Bug pipeline pattern:** State machine with `VALID_TRANSITIONS` dict. Status updates via `_update_status()` with transition validation. Background tasks via `asyncio.create_task()`. Supabase for persistence.

**Heartbeat pattern:** `write_heartbeat()` upserts into `bot_heartbeat` table keyed by `service` name. Runs every 3 minutes via APScheduler.

**Schema pattern:** Tables use `bigint generated always as identity primary key`, `timestamptz not null default now()`, RLS enabled with service role full access policy. Indexes on frequently queried columns.

**Polling mode:** `cto_bot.py` currently uses `app.run_polling()` with `python-telegram-bot` v22.5. The webhook server must coexist -- admin bot can stay on polling as fallback, client bots use webhooks.

---

## Track 1: Foundation

### Task 1: Database Schema

**Modify:** `schema.sql`
**Apply to:** Supabase project `tscuymavysscrvoberrr`

Extend the `clients` table and create two new tables (`bot_users`, `bot_conversations`) matching the spec data model.

- [ ] Add columns to `clients` table:
  ```sql
  alter table clients add column if not exists telegram_bot_token text;
  alter table clients add column if not exists bot_username text;
  alter table clients add column if not exists bot_display_name text;
  alter table clients add column if not exists personality text;
  alter table clients add column if not exists welcome_message text;
  ```
- [ ] Create `bot_users` table:
  ```sql
  create table bot_users (
    id bigint generated always as identity primary key,
    client_id bigint references clients(id) not null,
    telegram_chat_id text not null,
    telegram_username text,
    name text not null,
    role text not null check (role in ('owner', 'manager', 'staff', 'customer')),
    permissions text[] default '{}',
    invite_code text,
    invite_expires_at timestamptz,
    status text not null default 'active' check (status in ('pending', 'active', 'deactivated')),
    added_by bigint references bot_users(id),
    is_active boolean default true,
    created_at timestamptz not null default now(),
    unique(client_id, telegram_chat_id)
  );
  alter table bot_users enable row level security;
  create policy "service role full access" on bot_users
    using (true) with check (true);
  create index idx_bot_users_client on bot_users(client_id);
  create index idx_bot_users_chat on bot_users(telegram_chat_id);
  create index idx_bot_users_invite on bot_users(invite_code) where invite_code is not null;
  ```
- [ ] Create `bot_conversations` table:
  ```sql
  create table bot_conversations (
    id bigint generated always as identity primary key,
    client_id bigint references clients(id) not null,
    telegram_chat_id text not null,
    flow text not null,
    step text not null,
    state_data jsonb default '{}',
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    unique(client_id, telegram_chat_id)
  );
  alter table bot_conversations enable row level security;
  create policy "service role full access" on bot_conversations
    using (true) with check (true);
  create index idx_bot_conversations_lookup on bot_conversations(client_id, telegram_chat_id);
  ```
- [ ] Append all SQL to `schema.sql` under a `-- White-label bot system` section header
- [ ] Run the migration on Supabase project `tscuymavysscrvoberrr` via MCP `execute_sql` or SQL Editor
- [ ] Verify tables exist: query `bot_users`, `bot_conversations`, and `clients` columns

---

### Task 2: Multi-Bot Webhook Engine

**Create:** `webhook_server.py`
**Create:** `bot_manager.py`
**Modify:** `cto_bot.py`
**Test:** `tests/test_bot_manager.py`

The webhook server is a **separate aiohttp process** from cto_bot.py. Rationale: `cto_bot.py` uses `app.run_polling()` which blocks the event loop. The webhook server runs as its own async service, imports the same handler logic, and routes messages by token hash. The admin bot (@ihsanosbot) stays on polling as a fallback per the spec.

#### `bot_manager.py`

Manages the lifecycle of all client bot tokens -- loading, hashing, webhook registration, and hot-reload.

- [ ] Create `bot_manager.py` at project root (same level as `cto_bot.py`)
- [ ] Implement `BotManager` class:
  ```python
  class BotManager:
      def __init__(self, webhook_base_url: str):
          self.webhook_base = webhook_base_url  # e.g. "https://orch.wingmen.dev"
          self.webhook_map: dict[str, dict] = {}  # token_hash -> client record
          self.bot_instances: dict[str, Bot] = {}  # token_hash -> telegram.Bot
  ```
- [ ] `async def load_all(self, supabase) -> int` -- query `clients` where `telegram_bot_token IS NOT NULL AND active = true`, compute `sha256(token)[:16]` for each, call `setWebhook`, populate `webhook_map` and `bot_instances`. Return count loaded.
- [ ] `async def register_bot(self, supabase, client: dict) -> str` -- register a single new bot (called during onboarding hot-reload). Compute hash, call `setWebhook(url=self.webhook_base + "/webhook/" + token_hash)`, add to maps. Return token_hash.
- [ ] `async def deregister_bot(self, client_id: int) -> None` -- call `deleteWebhook()` on the bot, remove from maps.
- [ ] `def resolve(self, token_hash: str) -> dict | None` -- lookup client from `webhook_map`. Returns None if not found.
- [ ] `def get_bot(self, token_hash: str) -> Bot | None` -- get the `telegram.Bot` instance for sending replies.
- [ ] Helper: `_hash_token(token: str) -> str` -- `hashlib.sha256(token.encode()).hexdigest()[:16]`
- [ ] Type hints on all methods, `from __future__ import annotations`
- [ ] Logging via `logger = logging.getLogger("wingmen.bot_manager")`

#### `webhook_server.py`

Async aiohttp web server that receives Telegram webhook POSTs and dispatches to handlers.

- [ ] Create `webhook_server.py` at project root
- [ ] Use `aiohttp.web` (already available -- aiohttp is a dependency of supabase-py)
- [ ] Implement webhook endpoint:
  ```python
  async def handle_webhook(request: web.Request) -> web.Response:
      token_hash = request.match_info["token_hash"]
      client = bot_manager.resolve(token_hash)
      if not client:
          return web.Response(status=404)
      payload = await request.json()
      update = Update.de_json(payload, bot_manager.get_bot(token_hash))
      asyncio.create_task(process_client_message(update, client))
      return web.Response(status=200)
  ```
- [ ] Route: `app.router.add_post("/webhook/{token_hash}", handle_webhook)`
- [ ] Health check: `app.router.add_get("/health", health_handler)` -- returns JSON with `{"status": "ok", "bots_active": len(bot_manager.webhook_map)}`
- [ ] Startup hook: `async def on_startup(app)` -- init supabase, init bot_manager, call `bot_manager.load_all()`
- [ ] `async def process_client_message(update: Update, client: dict)` -- the main routing function:
  1. Extract `telegram_chat_id` from update
  2. Call `bot_user_resolver.resolve()` to get user + role
  3. Check `conversation.get_state()` for active flow
  4. If active flow: resume via conversation handler
  5. If no flow: route intent via router, check permissions, dispatch to handler
  6. Build context with `personality.build_system_prompt(client, user)`
  7. All handler responses sent via `bot_manager.get_bot(token_hash)` to the chat
- [ ] `__main__` block: `web.run_app(app, host="0.0.0.0", port=8443)`
- [ ] Logging: `logger = logging.getLogger("wingmen.webhook_server")`

#### `cto_bot.py` modifications

Minimal changes -- extract shared logic so webhook_server can reuse it.

- [ ] Extract `_route_message()` into a standalone function that can be imported (currently it depends on `user` dict structure from cto_bot's own user resolution -- needs to work with bot_users too)
- [ ] Extract `_send_reply()` into a utility that accepts a `Bot` instance parameter instead of relying on the `update` object
- [ ] Add a `/setupbot` command handler for the onboarding flow (Task 5)
- [ ] Keep `run_polling()` for the admin bot -- no webhook conversion needed for @ihsanosbot

#### `tests/test_bot_manager.py`

- [ ] Test `_hash_token()` returns consistent 16-char hex string
- [ ] Test `resolve()` returns None for unknown hash
- [ ] Test `resolve()` returns client dict for known hash
- [ ] Test `register_bot()` adds to `webhook_map` (mock Telegram API)
- [ ] Test `deregister_bot()` removes from `webhook_map` (mock Telegram API)
- [ ] Test `load_all()` populates map from mock Supabase data

---

### Task 3: User Identification Layer

**Create:** `bot_user_resolver.py`
**Test:** `tests/test_bot_user_resolver.py`

Resolves every incoming message to a `bot_user` record. This is the identity layer for all client bots.

- [ ] Create `bot_user_resolver.py` at project root
- [ ] Implement `async def resolve(supabase, client_id: int, telegram_chat_id: str, telegram_username: str | None = None, display_name: str | None = None) -> dict`:
  1. Query `bot_users` by `(client_id, telegram_chat_id)`
  2. If found + `status='active'`: return the row as dict
  3. If found + `status='pending'`: return the row (caller checks if message is invite claim)
  4. If found + `status='deactivated'`: return `{"deactivated": True}` sentinel
  5. If not found: auto-register as customer:
     ```python
     await supabase.table("bot_users").insert({
         "client_id": client_id,
         "telegram_chat_id": telegram_chat_id,
         "telegram_username": telegram_username or "",
         "name": display_name or telegram_username or "Customer",
         "role": "customer",
         "status": "active",
     }).execute()
     ```
     Return the new row
- [ ] Implement `async def activate_invite(supabase, invite_code: str, telegram_chat_id: str, telegram_username: str | None = None) -> dict | None`:
  1. Query `bot_users` where `invite_code = code AND status = 'pending'`
  2. If not found or expired (`invite_expires_at < now()`): return None
  3. Update row: `status='active'`, `telegram_chat_id=chat_id`, `telegram_username=username`, `invite_code=None`
  4. Return the activated row
- [ ] Implement `async def deactivate_user(supabase, client_id: int, user_id: int) -> bool`:
  1. Update `bot_users` set `status='deactivated', is_active=false` where `id=user_id AND client_id=client_id`
  2. Return True on success
- [ ] In-memory cache: `_user_cache: dict[tuple[int, str], dict] = {}` with 5-minute TTL to avoid hitting Supabase on every message. Use `time.monotonic()` for expiry checks.
- [ ] Cache invalidation: `clear_cache(client_id, telegram_chat_id)` for use after role changes, deactivation, invite activation.
- [ ] Logging: `logger = logging.getLogger("wingmen.bot_user_resolver")`
- [ ] Tests in `tests/test_bot_user_resolver.py`:
  - [ ] Test resolve returns existing active user
  - [ ] Test resolve auto-registers unknown user as customer
  - [ ] Test resolve returns deactivated sentinel for deactivated users
  - [ ] Test activate_invite with valid code
  - [ ] Test activate_invite with expired code returns None
  - [ ] Test activate_invite with already-used code returns None
  - [ ] Test cache hit avoids DB query (mock supabase, assert call count)

---

## Track 2: Client Onboarding

### Task 4: Bot Token Validation

**Create:** `bot_onboarding.py`
**Test:** `tests/test_bot_onboarding.py`

Handles the technical side of onboarding: token validation, Bot API configuration, and webhook registration.

- [ ] Create `bot_onboarding.py` at project root
- [ ] Implement `async def validate_token(token: str) -> dict | None`:
  - Call `GET https://api.telegram.org/bot{token}/getMe` via `httpx.AsyncClient`
  - If 200: return the `result` dict (`{"id": ..., "username": ..., "first_name": ...}`)
  - If error: return None
  - Strip whitespace from token before validation
- [ ] Implement `async def configure_bot(token: str, display_name: str, description: str, capabilities: list[str]) -> bool`:
  - Call `setMyName(name=display_name)` (max 64 chars)
  - Call `setMyDescription(description=description)` (max 512 chars)
  - Call `setMyShortDescription(short_description=...)` (max 120 chars, first sentence of description)
  - Call `setMyCommands(commands=...)` -- build command list from capabilities:
    - Always: `/start` (Start), `/help` (Get help)
    - If `storefront` in capabilities: `/order` (Place an order), `/menu` (View menu)
    - If `qurban` in capabilities: `/qurban` (Book qurban)
  - Return True on success, False on any failure
  - All calls via `httpx.AsyncClient` to `https://api.telegram.org/bot{token}/...`
- [ ] Implement `async def register_webhook(token: str, webhook_base: str) -> str`:
  - Compute `token_hash = sha256(token)[:16]`
  - Call `setWebhook(url=webhook_base + "/webhook/" + token_hash)`
  - Return token_hash
- [ ] Implement `async def detect_capabilities(supabase, ihsanos_org_id: str | None) -> list[str]`:
  - If `ihsanos_org_id` is None: return `["support"]` (base only)
  - Query ihsanOS Supabase for org modules (via the `org_role_permissions` or `organizations.settings` pattern)
  - Map modules to capabilities: `storefront -> ["storefront"]`, `qurban -> ["qurban"]`, etc.
  - Always include `["support", "bug_report"]` as base capabilities
  - Gracefully handle query failure: return base capabilities
- [ ] Implement `async def full_onboard(supabase, bot_manager, token: str, client_id: int, display_name: str, owner_chat_id: str, owner_username: str | None, personality: str | None = None) -> dict`:
  - Orchestrates the full flow: validate -> store token in clients -> configure bot -> register webhook -> create owner in bot_users -> hot-add to bot_manager
  - Returns `{"success": True, "bot_username": "...", "token_hash": "..."}` or `{"success": False, "error": "..."}`
- [ ] Tests in `tests/test_bot_onboarding.py`:
  - [ ] Test validate_token with valid response (mock httpx)
  - [ ] Test validate_token with invalid token returns None
  - [ ] Test configure_bot calls all 4 Bot API methods
  - [ ] Test register_webhook computes correct hash and calls setWebhook
  - [ ] Test detect_capabilities returns base caps when no org_id
  - [ ] Test full_onboard happy path (mock everything)

---

### Task 5: Onboarding Conversation Flow

**Modify:** `cto_bot.py`
**Depends on:** Task 4 (`bot_onboarding.py`)

Adds the guided "set up my bot" conversation to the admin bot (@ihsanosbot). This is a multi-step flow within the existing `cto_bot.py` since it happens in the admin bot, not in client bots.

- [ ] Add in-memory state: `_onboarding_state: dict[str, dict] = {}` keyed by `chat_id`
  ```python
  # State shape:
  # {
  #   "step": "awaiting_token" | "awaiting_name" | "awaiting_personality" | "confirming",
  #   "client_id": int,
  #   "token": str,
  #   "bot_info": dict,
  #   "display_name": str,
  # }
  ```
- [ ] Add new intent `"setup_bot"` to the router (modify router's `VALID_INTENTS` and prompt)
- [ ] Add handler in `_process_message()` -- before routing, check if `chat_id in _onboarding_state`:
  - If yes: pass to `_handle_onboarding_step(update, chat_id, user_msg)`
  - The step handler processes the current step and advances `_onboarding_state[chat_id]["step"]`
- [ ] Step 1 -- trigger: router classifies as `setup_bot` OR user sends `/setupbot`
  - Check user is Musa or an existing client with admin rights
  - If new client: create `clients` row first
  - Send BotFather guidance message:
    ```
    Let's set up your branded bot. 3 steps:
    1. Tap -> t.me/BotFather?start
    2. Send /newbot, pick a name and username
    3. Paste the token here

    I'll handle the rest.
    ```
  - Set `_onboarding_state[chat_id] = {"step": "awaiting_token", "client_id": client_id}`
- [ ] Step 2 -- awaiting_token: user pastes token
  - Call `bot_onboarding.validate_token(token)`
  - If invalid: reply "That token didn't work. Make sure you copy the full token from BotFather." Stay on same step.
  - If valid: store token + bot_info in state, move to `"awaiting_name"`
  - Reply: "Token valid! I found @{username}. What display name should it use? (e.g., 'Hadramawt Kitchen')"
- [ ] Step 3 -- awaiting_name: user provides display name
  - Store `display_name` in state
  - Move to `"awaiting_personality"` (or skip if not Musa)
  - Reply: "Got it. Want to set a custom personality? (e.g., 'Friendly, food-loving, knows the menu') or say 'skip' for the default."
- [ ] Step 4 -- awaiting_personality:
  - If "skip": use default personality template
  - Else: store custom personality
  - Call `bot_onboarding.full_onboard(...)` with all collected data
  - On success: reply with completion message including the bot username and deep link
  - On failure: reply with error, clean up state
  - Clear `_onboarding_state[chat_id]`
- [ ] Add `/setupbot` CommandHandler in `main()`:
  ```python
  app.add_handler(CommandHandler("setupbot", cmd_setupbot))
  ```
- [ ] Timeout: if no response for 10 minutes, clear onboarding state (check in `_cleanup_caches()`)

---

## Track 3: Team + Roles

### Task 6: Invite Code System

**Create:** `invite_manager.py`
**Test:** `tests/test_invite_manager.py`

Generates, validates, and claims single-use invite codes for team member onboarding.

- [ ] Create `invite_manager.py` at project root
- [ ] Implement `generate_code() -> str`:
  - 8-char alphanumeric, uppercase: `"".join(random.choices(string.ascii_uppercase + string.digits, k=8))`
  - Avoid ambiguous chars (0/O, 1/I/L): filter from character set
- [ ] Implement `async def create_invite(supabase, client_id: int, name: str, role: str, added_by: int) -> dict`:
  - Validate role is in `('manager', 'staff')` -- cannot invite as owner or customer
  - Generate code via `generate_code()`
  - Insert into `bot_users`: `status='pending'`, `invite_code=code`, `invite_expires_at=now()+24h`, `telegram_chat_id='pending'`, `name=name`, `role=role`, `added_by=added_by`
  - Return `{"code": code, "expires_at": ..., "deep_link": ...}`
- [ ] Implement `build_deep_link(bot_username: str, code: str) -> str`:
  - Return `f"https://t.me/{bot_username}?start=invite_{code}"`
- [ ] Implement `async def claim_invite(supabase, code: str, telegram_chat_id: str, telegram_username: str | None = None) -> dict | None`:
  - Query `bot_users` where `invite_code = code AND status = 'pending'`
  - If not found: return None
  - If expired (`invite_expires_at < now()`): return None
  - If already claimed (status != 'pending'): return None
  - Update: `status='active'`, `telegram_chat_id=chat_id`, `telegram_username=username`, `invite_code=NULL`
  - Clear `bot_user_resolver` cache for this user
  - Return the activated user row
- [ ] Implement `async def expire_stale_invites(supabase) -> int`:
  - Delete from `bot_users` where `status='pending' AND invite_expires_at < now()`
  - Return count deleted
  - Called periodically via scheduler
- [ ] Tests in `tests/test_invite_manager.py`:
  - [ ] Test generate_code returns 8 chars, no ambiguous chars
  - [ ] Test create_invite inserts pending user with code and expiry
  - [ ] Test build_deep_link format
  - [ ] Test claim_invite with valid code activates user
  - [ ] Test claim_invite with expired code returns None
  - [ ] Test claim_invite with already-claimed code returns None
  - [ ] Test expire_stale_invites deletes old pending records

---

### Task 7: Team Management Handler

**Create:** `handlers/team_handler.py`
**Depends on:** Task 6 (`invite_manager.py`)

Handles owner commands like "Add Fatimah as manager", "Remove Ali", "List team" within client bots.

- [ ] Create `handlers/` directory at project root
- [ ] Create `handlers/__init__.py` (empty)
- [ ] Create `handlers/team_handler.py`
- [ ] Implement `async def handle_team_command(supabase, client: dict, user: dict, message: str, bot: Bot) -> str`:
  - Uses `call_ai()` to parse natural language into structured operation:
    ```python
    system = "Parse this team management message. Return JSON: {\"action\": \"add|remove|list|change_role\", \"name\": \"...\", \"role\": \"manager|staff\"}"
    response = await call_ai(message, system=system, model="fast", json_mode=True)
    parsed = extract_json(response)
    ```
  - **add**: call `invite_manager.create_invite()`, return message with deep link
  - **remove**: call `bot_user_resolver.deactivate_user()`, return confirmation
  - **list**: query `bot_users` where `client_id AND role != 'customer' AND status = 'active'`, format as list
  - **change_role**: update `bot_users.role`, clear cache, return confirmation
- [ ] Permission check: only `owner` role can use this handler (checked before calling)
- [ ] Handle `/start invite_{CODE}` payload in webhook server:
  - In `process_client_message()`, check if update has `/start` command with `invite_` prefix
  - Call `invite_manager.claim_invite()`
  - Send welcome message with role description
- [ ] Error messages:
  - "I couldn't understand that team command. Try 'Add Fatimah as manager' or 'List team'."
  - "Only owners can manage the team."
  - "That invite code is invalid or has expired."

---

### Task 8: Permission System

**Create:** `permissions.py`
**Test:** `tests/test_permissions.py`

Central permission checking for all intents across all roles. Replaces ad-hoc `is_admin()` checks.

- [ ] Create `permissions.py` at project root
- [ ] Define the role-intent matrix as a constant:
  ```python
  ROLE_INTENTS: dict[str, set[str]] = {
      "owner":    {"chat", "help", "place_order", "track_order", "book_qurban", "site_edit", "bug_report", "manage_team", "view_orders", "analytics"},
      "manager":  {"chat", "help", "place_order", "track_order", "view_orders"},
      "staff":    {"chat", "help", "place_order", "track_order", "view_orders"},
      "customer": {"chat", "help", "place_order", "track_order", "book_qurban"},
  }
  ```
- [ ] Define capability-intent mapping:
  ```python
  CAPABILITY_INTENTS: dict[str, set[str]] = {
      "storefront": {"site_edit", "place_order", "track_order"},
      "qurban":     {"book_qurban"},
      "bug_report": {"bug_report"},
      "support":    {"help", "chat"},
      "orders":     {"view_orders"},
  }
  ```
- [ ] Implement `def can_access(role: str, intent: str, capabilities: list[str]) -> bool`:
  1. Check `intent in ROLE_INTENTS.get(role, set())` -- role allows it
  2. Check intent is unlocked by at least one of the client's capabilities
  3. Both must be True (role allows AND capability enabled)
  4. Exception: `chat` and `help` are always allowed regardless of capabilities
- [ ] Implement `def get_allowed_intents(role: str, capabilities: list[str]) -> set[str]`:
  - Returns the intersection of role-allowed and capability-unlocked intents
  - Used by the help handler to show available actions
- [ ] Implement `def get_denial_message(intent: str, role: str) -> str`:
  - Returns a human-friendly message explaining why access is denied
  - e.g., "Only the owner can edit the site. Ask your owner for access."
  - e.g., "This feature isn't enabled for your business. The owner can enable it in settings."
- [ ] Tests in `tests/test_permissions.py`:
  - [ ] Test owner can access all intents when capabilities enabled
  - [ ] Test customer cannot access site_edit
  - [ ] Test staff cannot access manage_team
  - [ ] Test intent denied when capability not in client's list
  - [ ] Test chat and help always allowed
  - [ ] Test get_allowed_intents returns correct set for manager with storefront cap
  - [ ] Test get_denial_message returns role-specific message

---

## Track 4: Conversation Flows

### Task 9: Conversation State Machine

**Create:** `conversation.py`
**Test:** `tests/test_conversation.py`

Generic state machine that persists multi-turn flows in `bot_conversations`. Used by ordering, qurban, site editing, and team management flows.

- [ ] Create `conversation.py` at project root
- [ ] Implement `ConversationManager` class:
  ```python
  class ConversationManager:
      def __init__(self):
          self._cache: dict[tuple[int, str], dict] = {}  # (client_id, chat_id) -> state

      async def start_flow(self, supabase, client_id: int, chat_id: str, flow: str, initial_step: str, state_data: dict | None = None) -> dict
      async def get_state(self, supabase, client_id: int, chat_id: str) -> dict | None
      async def update_step(self, supabase, client_id: int, chat_id: str, step: str, state_data: dict | None = None) -> dict
      async def clear(self, supabase, client_id: int, chat_id: str) -> None
      async def expire_stale(self, supabase) -> int
  ```
- [ ] `start_flow()`:
  - Upsert into `bot_conversations` with `flow`, `step=initial_step`, `state_data`, `expires_at=now()+1h`
  - Update `_cache`
  - Return the conversation record
- [ ] `get_state()`:
  - Check `_cache` first (with expiry check)
  - If not cached or expired: query DB
  - If DB record has `expires_at < now()`: delete and return None
  - Cache and return
- [ ] `update_step()`:
  - Update `step` and merge `state_data` (deep merge via `{**existing, **new_data}`)
  - Reset `expires_at = now() + 1h` (extend on activity)
  - Update cache
- [ ] `clear()`:
  - Delete from DB, remove from cache
- [ ] `expire_stale()`:
  - `DELETE FROM bot_conversations WHERE expires_at < now()`
  - Clear matching cache entries
  - Return count deleted
  - Run via scheduler every 15 minutes
- [ ] Module-level singleton: `conversation_manager = ConversationManager()`
- [ ] Tests in `tests/test_conversation.py`:
  - [ ] Test start_flow creates record in DB
  - [ ] Test get_state returns active flow
  - [ ] Test get_state returns None for expired flow
  - [ ] Test update_step merges state_data
  - [ ] Test update_step extends expires_at
  - [ ] Test clear removes record
  - [ ] Test expire_stale deletes old records
  - [ ] Test cache hit avoids DB query

---

### Task 10: Ordering Flow Handler

**Create:** `handlers/order_handler.py`
**Test:** `tests/test_order_handler.py`
**Depends on:** Task 9 (`conversation.py`), Task 15 (ihsanOS API)

Conversational ordering flow: show menu -> collect items -> fulfillment -> address -> confirm -> place order.

- [ ] Create `handlers/order_handler.py`
- [ ] Define flow steps as constants:
  ```python
  STEPS = ["show_menu", "collect_items", "fulfillment_type", "address", "confirm", "placed"]
  ```
- [ ] Implement `async def handle_order(supabase, client: dict, user: dict, message: str, bot: Bot, conversation: ConversationManager) -> str`:
  - Get current state via `conversation.get_state()`
  - If no state: start flow at `show_menu`
  - Dispatch to step handler based on current step
- [ ] Step `show_menu`:
  - Fetch products from ihsanOS API: `GET /api/products/{org_id}`
  - Format as readable menu with prices
  - Store products in `state_data["products"]`
  - Advance to `collect_items`
  - Return formatted menu string
- [ ] Step `collect_items`:
  - Pass user message + products to `call_ai()` for natural language parsing:
    ```python
    system = "Parse the customer's order against this menu. Return JSON: {\"items\": [{\"product_id\": ..., \"name\": ..., \"quantity\": ..., \"price\": ...}], \"needs_clarification\": false}"
    ```
  - If `needs_clarification`: reply with question, stay on step
  - If parsed: store in `state_data["cart"]`, calculate total
  - Show order summary, advance to `fulfillment_type`
- [ ] Step `fulfillment_type`:
  - Parse "pickup" or "delivery" from message (AI or keyword match)
  - If delivery: advance to `address`
  - If pickup: advance to `confirm`
  - Store `state_data["fulfillment"] = "pickup" | "delivery"`
- [ ] Step `address`:
  - Store `state_data["address"] = message`
  - Advance to `confirm`
  - Show full order summary for confirmation
- [ ] Step `confirm`:
  - Parse confirmation ("yes", "confirm", "ok") or cancellation ("no", "cancel")
  - If cancel: clear conversation, return "Order cancelled."
  - If confirm: call ihsanOS `POST /api/storefront/place-order` with cart data
  - Store order reference in `state_data["order_ref"]`
  - Clear conversation
  - Return confirmation with order number and tracking link
- [ ] Error handling: if ihsanOS API fails, reply with apology and clear conversation
- [ ] Tests in `tests/test_order_handler.py`:
  - [ ] Test show_menu fetches and formats products
  - [ ] Test collect_items parses "2 briyani and 1 mandi" into structured cart
  - [ ] Test fulfillment_type recognizes "delivery" and "pickup"
  - [ ] Test confirm with "yes" calls place order API
  - [ ] Test confirm with "cancel" clears conversation
  - [ ] Test full flow end-to-end with mocked APIs

---

### Task 11: Qurban Booking Flow Handler

**Create:** `handlers/qurban_handler.py`
**Test:** `tests/test_qurban_handler.py`
**Depends on:** Task 9 (`conversation.py`), Task 15 (ihsanOS API)

Conversational qurban booking: show animals -> select -> collect niyyah -> distribution -> confirm -> book.

- [ ] Create `handlers/qurban_handler.py`
- [ ] Define flow steps:
  ```python
  STEPS = ["show_animals", "select_animal", "collect_niyyah", "distribution_mode", "confirm", "booked"]
  ```
- [ ] Implement `async def handle_qurban(supabase, client: dict, user: dict, message: str, bot: Bot, conversation: ConversationManager) -> str`
- [ ] Step `show_animals`:
  - Fetch from ihsanOS API: `GET /api/qurban-animals/{org_id}`
  - Format with animal type, origin, price, availability
  - Advance to `select_animal`
- [ ] Step `select_animal`:
  - AI parse: match user's choice to available animal
  - Store `state_data["animal"] = {"id": ..., "type": ..., "price": ...}`
  - Advance to `collect_niyyah`
  - Reply: "Who is this qurban for?"
- [ ] Step `collect_niyyah`:
  - AI parse natural language niyyah:
    ```python
    system = "Parse qurban niyyah. Return JSON: {\"entries\": [{\"name\": \"...\", \"relationship\": \"self|parent|spouse|child|other\", \"is_deceased\": true|false}], \"needs_more\": false}"
    ```
  - Store in `state_data["niyyah"]`
  - Ask "Add another or confirm?"
  - If user adds more: stay on step, append to entries
  - If confirm: advance to `distribution_mode`
- [ ] Step `distribution_mode`:
  - Ask distribution preference (if applicable to org's qurban setup)
  - Store `state_data["distribution"]`
  - Advance to `confirm`
  - Show full booking summary
- [ ] Step `confirm`:
  - If confirmed: call ihsanOS `createQbnBookingAction` equivalent API
  - Return booking reference, payment link, tracking URL
  - Clear conversation
- [ ] Tests in `tests/test_qurban_handler.py`:
  - [ ] Test show_animals formats correctly
  - [ ] Test select_animal matches "cow share" to correct animal
  - [ ] Test collect_niyyah parses "my late father Ahmad bin Ismail"
  - [ ] Test confirm calls booking API
  - [ ] Test full flow end-to-end with mocked APIs

---

### Task 12: Site Edit Flow Handler

**Create:** `handlers/site_edit_handler.py`
**Test:** `tests/test_site_edit_handler.py`
**Depends on:** Task 9 (`conversation.py`), Task 15 (ihsanOS API)

Owner edits storefront conversationally: describe change -> AI identifies field -> confirm -> update.

- [ ] Create `handlers/site_edit_handler.py`
- [ ] Define flow steps:
  ```python
  STEPS = ["describe_change", "confirm_change", "applied"]
  ```
- [ ] Implement `async def handle_site_edit(supabase, client: dict, user: dict, message: str, bot: Bot, conversation: ConversationManager) -> str`
- [ ] Step `describe_change` (also handles first message when no active flow):
  - Fetch current `StorefrontConfig` from ihsanOS API: `GET /api/storefront-config/{org_id}`
  - Pass to `call_ai()` with the user's request:
    ```python
    system = """You are a storefront editor. Given the current config and the user's request, identify:
    1. Which field(s) to change (dot-notation path, e.g., hero.headline, about.text, theme.primaryColor)
    2. The new value(s)
    Return JSON: {"changes": [{"field_path": "...", "old_value": "...", "new_value": "..."}], "needs_clarification": false, "question": ""}"""
    ```
  - If `needs_clarification`: ask the question, stay on step
  - If complex request ("make it more modern"): use AI to generate new config sections, show preview
  - If URL-based ("make it look like [url]"): flag for GLM-5V pipeline (out of scope for now, reply with "URL-based cloning coming soon. Can you describe what you'd like to change?")
  - Store `state_data["changes"]`, show preview to user
  - Advance to `confirm_change`
- [ ] Step `confirm_change`:
  - If confirmed: iterate over changes, call `POST /api/storefront-update` for each
  - Return "Updated! Preview: {preview_url}. Anything else?"
  - Clear conversation (but stay responsive for follow-up edits)
  - If rejected: "No problem, what would you like instead?" -- back to `describe_change`
- [ ] Tests in `tests/test_site_edit_handler.py`:
  - [ ] Test describe_change identifies "change hero text" as `hero.headline`
  - [ ] Test confirm_change calls storefront-update API
  - [ ] Test needs_clarification keeps flow on same step
  - [ ] Test rejection loops back to describe_change

---

### Task 13: Help Handler

**Create:** `handlers/help_handler.py`

Role-aware help: customer gets ordering help, owner gets management help. Reads storefront config for business-specific answers.

- [ ] Create `handlers/help_handler.py`
- [ ] Implement `async def handle_help(supabase, client: dict, user: dict, message: str) -> str`:
  - Build context from:
    1. `client.personality` (bot personality)
    2. `user.role` (what they can do)
    3. `client.capabilities` (what the business offers)
    4. StorefrontConfig (business hours, contact info, etc.) -- fetched from ihsanOS API if org_id exists
  - Pass to `call_ai()`:
    ```python
    system = f"""You are the help assistant for {client['name']}.
    User role: {user['role']}
    Available features: {capabilities_description}
    Business info: {storefront_summary}

    Answer their question helpfully. If it's about business operations (hours, location, menu), use the business info.
    If it's about bot features, explain what they can do based on their role."""
    ```
  - Return AI response
- [ ] Specific business queries (e.g., "what time do you close?"):
  - Extract from `storefront.contact.hours` if available
  - AI uses this context to answer accurately
- [ ] Role-specific guidance:
  - Customer: "You can order food, track orders, and ask questions."
  - Owner: "You can edit your site, manage your team, view orders, and report bugs."
  - Staff: "You can view and manage orders assigned to you."
- [ ] No test file needed -- thin wrapper around `call_ai()` with context assembly

---

## Track 5: Integration

### Task 14: Enhanced Router

**Modify:** `agents/router.py`

Add new intents for white-label bot flows and conversation state awareness.

- [ ] Update `VALID_INTENTS` set:
  ```python
  VALID_INTENTS = {
      "chat", "audit", "fix", "build", "data", "todo", "bug_report",
      # White-label bot intents:
      "site_edit", "place_order", "track_order", "book_qurban",
      "manage_team", "help", "setup_bot",
  }
  ```
- [ ] Update `build_router_prompt()` -- add new intent descriptions:
  ```
  - "site_edit": owner wants to change their storefront (hero text, colors, about page, menu items)
  - "place_order": customer wants to order food/products, view menu, or add to cart
  - "track_order": check order status, where's my order, delivery update
  - "book_qurban": book qurban, sacrificial animal, niyyah, eid al-adha booking
  - "manage_team": add/remove team members, list team, change roles, invite
  - "help": how to use the bot, what can I do, business hours, contact info, general questions
  - "setup_bot": set up a new bot, configure my bot, onboard (admin bot only)
  ```
- [ ] Add `capabilities` parameter to `build_router_prompt()`:
  ```python
  def build_router_prompt(user_msg, repos, history, *, role="admin", capabilities=None):
  ```
  - When `capabilities` is provided, add to prompt: "Available capabilities for this business: {caps}. Only classify into intents that match these capabilities."
  - This prevents routing to `book_qurban` when the client doesn't have qurban enabled
- [ ] Add `active_flow` parameter:
  ```python
  def build_router_prompt(user_msg, repos, history, *, role="admin", capabilities=None, active_flow=None):
  ```
  - When `active_flow` is set: "The user is currently in a {flow} flow at step {step}. If their message relates to this flow, classify as the matching intent. If they clearly want something else, classify normally."
- [ ] Keep backward compatibility: existing calls without new params still work (defaults to None)
- [ ] No new test file needed -- update existing test if one exists, or verify via integration tests

---

### Task 15: ihsanOS API Endpoints

**Working directory:** `~/wingmen/projects/ihsanos` (NOT the orchestrator repo)
**Language:** TypeScript (Next.js App Router)

Create REST API endpoints that the orchestrator calls. These are thin wrappers around existing server actions.

- [ ] **`src/app/api/storefront-update/route.ts`**
  - Method: `POST`
  - Body: `{ org_id: string, field_path: string, new_value: any }`
  - Auth: API key in `Authorization: Bearer {ORCHESTRATOR_API_KEY}` header
  - Logic: validate field_path against allowed paths, update `organizations.settings.storefront` JSONB at the specified path using Supabase `update()`
  - Allowed field paths: `hero.headline`, `hero.subheadline`, `about.text`, `about.image`, `theme.primaryColor`, `theme.font`, `contact.hours`, `contact.phone`, `contact.address`, etc.
  - Response: `{ success: true, preview_url: "..." }` or `{ success: false, error: "..." }`
  - Use existing Supabase admin client pattern from other API routes in this codebase

- [ ] **`src/app/api/storefront-config/[orgId]/route.ts`**
  - Method: `GET`
  - Auth: same API key pattern
  - Logic: query `organizations` where `id = orgId`, return `settings.storefront` JSONB
  - Response: the full StorefrontConfig object

- [ ] **`src/app/api/products/[orgId]/route.ts`**
  - Method: `GET`
  - Auth: same API key pattern
  - Logic: query `pos_products` where `org_id = orgId AND show_on_storefront = true`
  - Response: array of products with `id`, `name`, `price`, `description`, `image_url`, `category`

- [ ] **`src/app/api/qurban-animals/[orgId]/route.ts`**
  - Method: `GET`
  - Auth: same API key pattern
  - Logic: wrap existing `getQbnAvailableAnimalsAction()` or query directly
  - Response: array of animals with `id`, `type`, `origin`, `price`, `available_shares`, `total_shares`

- [ ] **Auth middleware pattern:**
  ```typescript
  const apiKey = request.headers.get("authorization")?.replace("Bearer ", "");
  if (apiKey !== process.env.ORCHESTRATOR_API_KEY) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  ```
- [ ] Add `ORCHESTRATOR_API_KEY` to ihsanOS `.env` and orchestrator `.env`

---

### Task 16: Personality + Context Builder

**Create:** `personality.py`

Builds the system prompt injected into every `call_ai()` call for client bot messages.

- [ ] Create `personality.py` at project root
- [ ] Implement `def build_system_prompt(client: dict, user: dict, capabilities: list[str]) -> str`:
  - Assembles prompt from:
    1. **Base identity:** "You are {client.bot_display_name or client.name}'s assistant on Telegram."
    2. **Personality:** `client.personality` or default template
    3. **Role instructions:** based on `user.role`:
       - owner: "This user is the business owner. They can edit the site, manage team, view analytics, and report bugs."
       - manager: "This user is a manager. They can view and manage orders."
       - staff: "This user is staff. They can view assigned orders."
       - customer: "This is a customer. Help them browse, order, and track."
    4. **Capability context:** "This business offers: {capability descriptions}"
    5. **Behavioral rules:** "Keep responses concise for Telegram. Use short paragraphs. Don't use markdown headers."
  - Return the assembled system prompt string
- [ ] Implement `def get_default_personality(client_name: str) -> str`:
  - Returns the default template from the spec:
    ```
    You are a helpful assistant for {client_name}. Be friendly and professional.
    Answer questions about the business and help with orders and support.
    ```
- [ ] Implement `def build_welcome_message(client: dict, user_role: str) -> str`:
  - For customers: "Welcome to {name}! {welcome_message or default}. How can I help you?"
  - For team: "Welcome {user_name}! You've been added as {role}. Here's what you can do: ..."
- [ ] No test file needed -- simple string assembly, tested via integration tests

---

### Task 17: Per-Bot Heartbeat + Monitoring

**Modify:** `heartbeat.py`

Extend heartbeat to track per-bot metrics, not just the orchestrator and cto_bot.

- [ ] Add `async def write_client_bot_heartbeat(supabase, client_id: int, bot_username: str, **metrics) -> None`:
  - Service name: `f"client_bot:{bot_username}"` (e.g., `client_bot:hadramawt_bot`)
  - Metrics to track:
    - `messages_received`: counter since last heartbeat (maintained in-memory)
    - `active_conversations`: count from `bot_conversations` where `client_id AND expires_at > now()`
    - `last_activity`: timestamp of most recent message
    - `team_count`: count from `bot_users` where `client_id AND role != 'customer' AND status = 'active'`
    - `customer_count`: count from `bot_users` where `client_id AND role = 'customer' AND status = 'active'`
- [ ] Add in-memory message counter: `_bot_message_counts: dict[int, int] = {}` (client_id -> count). Reset on each heartbeat write.
- [ ] Integrate with scheduler in `cto_bot.py` post_init or in `webhook_server.py` startup:
  - Every 3 minutes: iterate over all active client bots, write heartbeat for each
- [ ] Modify `write_bot_heartbeat()` to also include aggregate stats:
  - `total_client_bots`: count of active client bots
  - `total_messages_today`: sum across all bots

---

### Task 18: Integration Tests + Analytics

**Create:** `tests/test_white_label_integration.py`
**Modify:** `STATUS.md`

Full end-to-end flow tests and PostHog analytics instrumentation.

- [ ] Create `tests/test_white_label_integration.py`:
  - [ ] **Test full onboarding flow:**
    1. Simulate admin sending "set up my bot" to @ihsanosbot
    2. Simulate pasting a token (mock Telegram getMe)
    3. Verify client record updated with token + bot_username
    4. Verify webhook registered
    5. Verify owner created in bot_users
  - [ ] **Test team invite flow:**
    1. Owner sends "Add Fatimah as manager" to client bot
    2. Verify invite code generated, pending bot_user created
    3. Simulate Fatimah tapping deep link (`/start invite_{CODE}`)
    4. Verify bot_user activated with correct role
    5. Verify welcome message sent
  - [ ] **Test customer ordering flow:**
    1. New user sends message to client bot
    2. Verify auto-registered as customer
    3. Send "I want to order" -> verify menu shown
    4. Send "2 briyani" -> verify cart parsed
    5. Send "delivery" -> verify address requested
    6. Send "123 Main St" -> verify confirmation shown
    7. Send "confirm" -> verify order placed via API
  - [ ] **Test cross-bot isolation:**
    1. Register two client bots
    2. Send message to bot A
    3. Verify only bot A's client data is accessible
    4. Send message to bot B
    5. Verify only bot B's client data is accessible
  - [ ] **Test permission enforcement:**
    1. Customer tries "change the hero text" -> denied
    2. Staff tries "add Ali as manager" -> denied
    3. Owner tries "change the hero text" -> allowed

- [ ] Add PostHog analytics calls (add to relevant handler files, not a separate file):
  - [ ] In `bot_onboarding.py`: `posthog.capture("bot_onboarded", {"client_id": ..., "bot_username": ..., "capabilities": ...})`
  - [ ] In `webhook_server.py`: `posthog.capture("bot_message_received", {"client_id": ..., "user_role": ..., "intent": ..., "response_time_ms": ...})`
  - [ ] In `handlers/order_handler.py`: `posthog.capture("bot_order_placed", {"client_id": ..., "order_total": ..., "items_count": ..., "channel": "telegram"})`
  - [ ] In `handlers/qurban_handler.py`: `posthog.capture("bot_qurban_booked", {"client_id": ..., "animal_type": ..., "channel": "telegram"})`
  - [ ] In `invite_manager.py`: `posthog.capture("bot_team_member_added", {"client_id": ..., "role": ..., "method": "invite_code"})`
  - [ ] In `handlers/site_edit_handler.py`: `posthog.capture("bot_site_edited", {"client_id": ..., "field_changed": ...})`
  - [ ] In `conversation.py`: `posthog.capture("bot_conversation_completed", {"client_id": ..., "flow": ..., "steps_count": ..., "duration_seconds": ...})`
  - Pattern: use `try/except` around all posthog calls so analytics never breaks core functionality

- [ ] Update `STATUS.md`:
  - Add "White-Label Bot System" section
  - Note: webhook server running at `orch.wingmen.dev`
  - Note: client bots active count
  - Note: conversation flows available

---

## File Inventory

### New files (orchestrator)

| File | Track | Task |
|------|-------|------|
| `bot_manager.py` | 1 | 2 |
| `webhook_server.py` | 1 | 2 |
| `bot_user_resolver.py` | 1 | 3 |
| `bot_onboarding.py` | 2 | 4 |
| `invite_manager.py` | 3 | 6 |
| `permissions.py` | 3 | 8 |
| `conversation.py` | 4 | 9 |
| `personality.py` | 5 | 16 |
| `handlers/__init__.py` | 3 | 7 |
| `handlers/team_handler.py` | 3 | 7 |
| `handlers/order_handler.py` | 4 | 10 |
| `handlers/qurban_handler.py` | 4 | 11 |
| `handlers/site_edit_handler.py` | 4 | 12 |
| `handlers/help_handler.py` | 4 | 13 |
| `tests/test_bot_manager.py` | 1 | 2 |
| `tests/test_bot_user_resolver.py` | 1 | 3 |
| `tests/test_bot_onboarding.py` | 2 | 4 |
| `tests/test_invite_manager.py` | 3 | 6 |
| `tests/test_permissions.py` | 3 | 8 |
| `tests/test_conversation.py` | 4 | 9 |
| `tests/test_order_handler.py` | 4 | 10 |
| `tests/test_qurban_handler.py` | 4 | 11 |
| `tests/test_site_edit_handler.py` | 4 | 12 |
| `tests/test_white_label_integration.py` | 5 | 18 |

### New files (ihsanOS -- separate repo)

| File | Task |
|------|------|
| `src/app/api/storefront-update/route.ts` | 15 |
| `src/app/api/storefront-config/[orgId]/route.ts` | 15 |
| `src/app/api/products/[orgId]/route.ts` | 15 |
| `src/app/api/qurban-animals/[orgId]/route.ts` | 15 |

### Modified files

| File | Task | Change |
|------|------|--------|
| `schema.sql` | 1 | Add bot_users, bot_conversations tables + clients columns |
| `cto_bot.py` | 2, 5 | Extract shared logic, add /setupbot handler, onboarding flow |
| `agents/router.py` | 14 | Add new intents, capabilities + active_flow params |
| `heartbeat.py` | 17 | Add per-bot heartbeat writing |
| `STATUS.md` | 18 | Document white-label system status |

---

## Execution Order (Strict Dependency Chain)

```
Task 1  (schema)           -- no deps, do first
Task 2  (webhook engine)   -- no deps, parallel with 1
Task 3  (user resolver)    -- depends on Task 1 (bot_users table)
  |
  v
Task 4  (token validation) -- depends on Task 1 (clients columns)
Task 6  (invite codes)     -- depends on Task 1 (bot_users table)
Task 8  (permissions)      -- no deps, can parallel with 4+6
  |
  v
Task 5  (onboarding flow)  -- depends on Task 2 + 4
Task 7  (team handler)     -- depends on Task 3 + 6
Task 9  (conversation SM)  -- depends on Task 1
  |
  v
Task 10 (ordering)         -- depends on Task 9 + 15
Task 11 (qurban)           -- depends on Task 9 + 15
Task 12 (site edit)        -- depends on Task 9 + 15
Task 13 (help)             -- depends on Task 8 + 16
Task 14 (router)           -- depends on Task 8
Task 15 (ihsanOS APIs)     -- no Python deps, can start early
Task 16 (personality)      -- no deps, can start early
  |
  v
Task 17 (heartbeat)        -- depends on Task 2 (bot_manager)
Task 18 (integration)      -- depends on ALL above
```

**Optimal parallel groups:**

1. **Group A (start immediately):** Task 1, Task 2, Task 8, Task 15, Task 16
2. **Group B (after Task 1):** Task 3, Task 4, Task 6, Task 9
3. **Group C (after Group B):** Task 5, Task 7, Task 14
4. **Group D (after Group B + 15):** Task 10, Task 11, Task 12, Task 13
5. **Group E (after all):** Task 17, Task 18

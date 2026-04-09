# White-Label Bot System Design

**Date:** 2026-04-09
**Status:** Approved for implementation
**Owner:** Musa / Wingmen
**Codebase:** ~/wingmen/orchestrator (Python) + ~/wingmen/projects/ihsanos (API endpoints)

## Goal

Every Wingmen client gets their own branded Telegram bot that routes to the single orchestrator backend. Clients onboard via guided BotFather flow, team members join via invite codes, and customers interact through conversational ordering, qurban booking, site editing, and support. One process, many bots, zero duplicated infrastructure.

## Architecture

```
@bapa_bot          ─┐
@gazzabyte_bot     ─┤    Telegram Webhook
@hadramawt_bot     ─┤──→ POST https://orch.wingmen.dev/webhook/{token_hash}
@tdu_bot           ─┤         ↓
@ihsanosbot (admin)─┘    Orchestrator (single Python process)
                              ├── token_hash → client lookup (in-memory map)
                              ├── telegram_chat_id → bot_user lookup (role detection)
                              ├── conversation state machine (multi-turn flows)
                              ├── intent router → handler
                              └── calls ihsanOS API / existing server actions
```

**Core principle:** The orchestrator is one process that listens to all client bot webhooks. Each message is routed by: which bot received it → which client → which user → which role → which capabilities → which handler.

**Webhook architecture:** Replaces current polling. Single aiohttp/FastAPI endpoint handles all bots. Each bot registered with `setWebhook(url=WEBHOOK_BASE/webhook/{sha256(token)[:16]})`. Token hash in URL prevents token exposure in logs. cloudflared tunnel at orch.wingmen.dev already exists.

**Fallback:** If tunnel goes down, admin bot (@ihsanosbot) temporarily switches to polling. Client bots queue messages until tunnel recovers.

## Data Model

### Modified table: `clients`

Add columns:

| Column | Type | Notes |
|--------|------|-------|
| telegram_bot_token | TEXT | the bot's API token (from BotFather) |
| bot_username | TEXT | @hadramawt_bot (auto-detected on token validation) |
| bot_display_name | TEXT | "Hadramawt Kitchen" (set via Bot API setMyName) |
| personality | TEXT | system prompt personality injection for this bot |
| welcome_message | TEXT | first message new users see |

### New table: `bot_users`

Team members and customers per client bot.

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| client_id | BIGINT FK clients | which business |
| telegram_chat_id | TEXT NOT NULL | Telegram user ID |
| telegram_username | TEXT | @fatimah_h |
| name | TEXT NOT NULL | display name |
| role | TEXT NOT NULL CHECK ('owner', 'manager', 'staff', 'customer') | |
| permissions | TEXT[] DEFAULT '{}' | granular overrides beyond role defaults |
| invite_code | TEXT | single-use, expires in 24h |
| invite_expires_at | TIMESTAMPTZ | when the invite code expires |
| status | TEXT NOT NULL DEFAULT 'active' CHECK ('pending', 'active', 'deactivated') | pending = invited but hasn't claimed |
| added_by | BIGINT FK bot_users | who invited them |
| is_active | BOOLEAN DEFAULT true | can be revoked |
| created_at | TIMESTAMPTZ | |
| UNIQUE(client_id, telegram_chat_id) | | one role per bot per person |

### New table: `bot_conversations`

State machine persistence for multi-turn flows.

| Column | Type | Notes |
|--------|------|-------|
| id | BIGSERIAL PK | |
| client_id | BIGINT FK clients | |
| telegram_chat_id | TEXT NOT NULL | |
| flow | TEXT NOT NULL | 'ordering', 'qurban_booking', 'site_edit', 'bug_report', 'team_manage' |
| step | TEXT NOT NULL | current step in the flow |
| state_data | JSONB DEFAULT '{}' | accumulated data (cart items, niyyah entries, etc.) |
| expires_at | TIMESTAMPTZ | auto-cleanup after 1 hour of inactivity |
| created_at | TIMESTAMPTZ | |
| UNIQUE(client_id, telegram_chat_id) | | one active conversation per user per bot |

## Client Bot Onboarding

Happens conversationally via @ihsanosbot (the admin bot):

```
Step 1: Client messages @ihsanosbot
Bot: "Welcome! Let's set up your branded bot."

Step 2: Guided BotFather flow
Bot: "3 steps:
      1. Tap → t.me/BotFather?start
      2. Send /newbot, pick a name and username
      3. Paste the token here"

Step 3: Client pastes token
Bot validates: GET https://api.telegram.org/bot{token}/getMe
If valid → stores token + bot_username in clients table

Step 4: Auto-configure via Bot API
Using the new token:
  - setMyName → client's business name
  - setMyDescription → personality-based description
  - setMyCommands → command menu based on capabilities
  - setMyShortDescription → one-liner

Step 5: Capability detection
If client has ihsanos_org_id → auto-detect enabled modules
If not → ask: "What does your business need?"
  [Storefront] [Bug Reports] [Support] [Orders] [Qurban]
Store in clients.capabilities

Step 6: Register webhook
setWebhook(url=WEBHOOK_BASE/webhook/{sha256(token)[:16]})
Add to in-memory webhook_map

Step 7: Owner registration
Auto-create bot_users row: role=owner, telegram_chat_id from the onboarding user

Step 8: Done
Bot: "@hadramawt_bot is live! Share it with your team and customers."
```

## Message Routing

```
Webhook received at /webhook/{token_hash}
    ↓
Step 1: Identify client
  webhook_map[token_hash] → client record (id, repo_name, capabilities, personality)
    ↓
Step 2: Identify user
  telegram_chat_id → bot_users table lookup
  ├── Found + active → use stored role + permissions
  ├── Found + pending → check if message is invite code → activate
  └── Not found → register as customer in bot_users
    ↓
Step 3: Check conversation state
  bot_conversations lookup by (client_id, telegram_chat_id)
  ├── Active conversation → resume flow at current step
  └── No conversation → route as new intent
    ↓
Step 4: Build context
  system_prompt = base_prompt + personality + role_instructions + capability_list
    ↓
Step 5: Route intent (via existing router agent + call_ai)
  Intents: chat, bug_report, site_edit, place_order, track_order,
           book_qurban, manage_team, help
    ↓
Step 6: Permission check
  role → allowed intents matrix (see below)
    ↓
Step 7: Execute handler
  Handler may start a multi-turn conversation → create bot_conversations record
```

### Permission Matrix

| Intent | Owner | Manager | Staff | Customer |
|--------|-------|---------|-------|----------|
| chat | Yes | Yes | Yes | Yes |
| help | Yes | Yes | Yes | Yes |
| place_order | Yes | Yes | Yes | Yes |
| track_order | Yes | Yes | Yes | Yes |
| book_qurban | Yes | Yes | No | Yes |
| site_edit | Yes | No | No | No |
| bug_report | Yes | No | No | No |
| manage_team | Yes | No | No | No |
| view_orders | Yes | Yes | Yes (own) | No |
| analytics | Yes | No | No | No |

## Conversational Flows

### Storefront Editing (site_edit)

Owner or authorized role modifies the storefront conversationally.

```
Owner: "Change the hero text"
Bot: "What should the new hero headline be?"
Owner: "Welcome to Hadramawt Kitchen — Authentic Yemeni Cuisine"
Bot: "Updated! Preview: ihsanos.com/shop/hadramawt
      Anything else?"
```

**Implementation:**
- Fetch current StorefrontConfig from `organizations.settings.storefront`
- AI parses request → identifies field to change (hero.headline, about.text, theme.primary_color, etc.)
- Update via ihsanOS API: `POST /api/storefront-update { org_id, field_path, new_value }`
- For complex changes ("make it more modern"): AI regenerates config sections
- For reference-based ("make it look like this [URL]"): triggers existing GLM-5V clone pipeline

**New ihsanOS API:** `POST /api/storefront-update` — validates field path, updates org settings JSONB, returns preview URL.

**State machine steps:** `describe_change → confirm_change → (optional: iterate)`

### Conversational Ordering (place_order)

Customer orders through the bot instead of the web checkout.

```
Customer: "I want to order"
Bot: "Here's our menu:
      🍗 Nasi Briyani — $12
      🍖 Lamb Mandi — $15
      🥘 Chicken Kabsa — $10
      What would you like?"
Customer: "2 briyani and 1 mandi"
Bot: "2x Nasi Briyani — $24
      1x Lamb Mandi — $15
      Total: $39
      Pickup or delivery?"
Customer: "Delivery"
Bot: "Your address?"
Customer: "123 Jalan Besar"
Bot: "Confirmed! #ORD-2026-042
      Total: $39 | Delivery ~45 min
      Track: ihsanos.com/shop/hadramawt/orders/ORD-2026-042"
```

**Implementation:**
- Fetch products from `pos_products` where `org_id = client.ihsanos_org_id AND show_on_storefront = true`
- AI parses natural language into structured items: `[{product_id, quantity}]`
- Calls existing `placeOrder()` action on ihsanOS

**State machine steps:** `show_menu → collect_items → fulfillment_type → address (if delivery) → confirm → placed`

### Qurban Booking (book_qurban)

Customer books qurban conversationally.

```
Customer: "I want to book qurban"
Bot: "Qurban 2026:
      🐄 Indonesian Cow (1/7) — $420
      🐐 Indonesian Goat — $350
      🐑 NZ Sheep — $450
      Which would you like?"
Customer: "Cow share"
Bot: "Who is this qurban for?"
Customer: "My late father Ahmad bin Ismail"
Bot: "Niyyah: Ahmad bin Ismail (parent, deceased)
      Add another or confirm?"
Customer: "Confirm"
Bot: "Booked! #QBN-2026-006 — $420
      Pay: [PayNow link]
      Track: ihsanos.com/shop/bapa/qurban/track/QBN-2026-006"
```

**Implementation:**
- Fetch available animals via `getQbnAvailableAnimalsAction()`
- AI parses niyyah from natural language ("my late father Ahmad") → structured `{name, relationship: "parent", is_deceased: true}`
- Calls existing `createQbnBookingAction()`

**State machine steps:** `show_animals → select_animal → collect_niyyah → distribution_mode → confirm → booked`

### Team Management (manage_team)

Owner adds/removes team members.

```
Owner: "Add Fatimah as manager"
Bot: "Share this invite link with Fatimah:
      t.me/hadramawt_bot?start=invite_HDKT7X2M
      Expires in 24 hours."

--- Fatimah taps link ---

Bot: "Welcome Fatimah! Ahmad added you as Manager.
      You can view orders and update the menu."
```

**Implementation:**
- Generate 8-char invite code, store in `bot_users` as pending record
- Telegram deep link: `t.me/{bot_username}?start=invite_{code}`
- On `/start invite_XXXX` → validate code, activate user, link telegram_chat_id
- Single-use, expires in 24 hours

**Owner commands:** "Add [name] as [role]", "Remove [name]", "List team", "Change [name] to [role]"

### Bug Report (bug_report)

Uses the existing bug pipeline — no changes. The bot identifies the client's repo from the `clients` table and creates the report.

### Help (help)

Context-aware responses based on role:
- Customer: "What time do you close?" → reads storefront contact.hours
- Owner: "How do I change my menu?" → guides through site_edit flow
- Staff: "How do I mark an order as ready?" → explains order management

Powered by AI with storefront config + CLAUDE.md as context.

## Invite Verification Flow

Team member identity verified via single-use invite codes:

```
1. Owner: "Add Fatimah as manager"
2. Bot generates code: HDKT-7X2M (8 chars, alphanumeric)
3. Stored in bot_users: status='pending', invite_code='HDKT7X2M',
   invite_expires_at=now()+24h, role='manager', invited_name='Fatimah'
4. Bot returns deep link: t.me/hadramawt_bot?start=invite_HDKT7X2M
5. Owner shares link with Fatimah (WhatsApp, SMS, in person)
6. Fatimah taps link → opens bot → /start command with payload
7. Bot reads payload → finds pending bot_users row → matches code
8. Bot activates: status='active', telegram_chat_id=Fatimah's ID
9. Code invalidated (single-use)
10. Bot welcomes Fatimah with role-appropriate message
```

**Security:**
- Code expires after 24 hours
- Single-use — cannot be reused after claiming
- Owner can revoke any team member: "Remove Fatimah"
- Deactivated members get "Your access has been revoked" on next message

## Bot Personality

Each client's bot has a configurable personality stored as a text field in the `clients` table. This gets injected into the system prompt for every AI call.

**Examples:**

BAPA (madrasah):
```
You are BAPA Assistant, the friendly helper for BAPA Madrasah.
Greet with Assalamu'alaikum. Speak warmly about education and Islamic values.
Use respectful language appropriate for a madrasah community.
```

Hadramawt Kitchen (F&B):
```
You are Hadramawt Kitchen's assistant. Friendly, casual, food-loving.
Know the menu inside out. Suggest popular dishes. Use food emojis.
Keep responses short and appetizing.
```

TDU (military):
```
You are TDU Support Bot. Professional, concise, mission-focused.
Help with training tool issues. No small talk. Direct answers.
```

**Default personality** (if none configured):
```
You are a helpful assistant for {client.name}. Be friendly and professional.
Answer questions about the business and help with orders and support.
```

## Capability Detection

For ihsanOS clients (have `ihsanos_org_id`):
- Query `org_role_permissions` to see which modules are enabled
- Map modules to bot capabilities: storefront → site_edit + place_order, school → help, qurban → book_qurban, etc.
- Auto-update when org toggles modules in ihsanOS settings

For non-ihsanOS clients (COSEM, WordPress, custom):
- Set manually during onboarding or via @ihsanosbot admin commands
- Stored in `clients.capabilities` TEXT[] (already exists)

**Capability → intent mapping:**

| Capability | Unlocked intents |
|-----------|-----------------|
| storefront | site_edit, place_order, track_order |
| qurban | book_qurban |
| bug_report | bug_report (existing pipeline) |
| support | help, chat |
| orders | view_orders (for managers/staff) |

All bots always have: chat, help (basic).

## Dual-Mode Operation

Each bot serves two audiences automatically:

| Audience | Identification | Access |
|----------|---------------|--------|
| Team (owner/manager/staff) | `telegram_chat_id` found in `bot_users` with internal role | Management capabilities |
| Customer | Not in `bot_users` OR role='customer' | Customer capabilities (order, track, ask) |

First message from an unknown user → auto-registered as `customer` in `bot_users`. No friction.

## Roles & Workflows

### Owner

**Discovery:** Onboarded via @ihsanosbot guided flow.
**Workflow:** Edit site, view orders, manage team, report bugs, view analytics, book qurban (if applicable).
**Notifications:** New orders, bug fixes deployed, team member joined, daily summary.
**Restrictions:** Full access to their bot's capabilities. Cannot see other clients' data.

### Manager

**Discovery:** Owner shares invite link → taps → auto-verified.
**Workflow:** View and manage orders, update menu, respond to customer questions.
**Notifications:** New orders, customer inquiries.
**Restrictions:** No site editing, team management, bug reporting, or analytics.

### Staff

**Discovery:** Owner or manager shares invite link.
**Workflow:** View assigned orders, mark as ready/delivered.
**Notifications:** New orders assigned.
**Restrictions:** Order viewing only.

### Customer

**Discovery:** Finds bot via storefront link, QR code, Telegram search, or word of mouth.
**Workflow:** Browse menu, place order, track order, book qurban, ask questions.
**Notifications:** Order confirmed, order ready, delivery update.
**Restrictions:** Own orders only. No internal data visibility.

### Musa (Super Admin)

**Discovery:** @ihsanosbot always available.
**Workflow:** Onboard clients, monitor all bots via super admin dashboard, handle escalated bugs, platform management.
**Notifications:** Client onboarded, bug escalations, system health.
**Restrictions:** None.

## Webhook Infrastructure

### Registration

On orchestrator startup:
1. Query `clients` table for all rows with `telegram_bot_token IS NOT NULL AND active = true`
2. For each client: compute `token_hash = sha256(token)[:16]`
3. Call Telegram `setWebhook(url=WEBHOOK_BASE/webhook/{token_hash})` per bot
4. Build in-memory `webhook_map: dict[token_hash → client_record]`
5. Also register @ihsanosbot (admin bot) webhook

### Endpoint

Single aiohttp or FastAPI endpoint:
```
POST /webhook/{token_hash}
  → lookup client from webhook_map
  → parse Telegram Update from request body
  → route to message handler with client context
```

### Hot-reload

When a new client bot is onboarded:
1. Insert client record with token
2. Compute hash, register webhook via Bot API
3. Add to in-memory webhook_map
4. No restart needed

When a client bot is deactivated:
1. Call `deleteWebhook()` on the token
2. Remove from webhook_map
3. Client's bot stops responding

### Fallback

If cloudflared tunnel goes down:
- Admin bot (@ihsanosbot) switches to polling as fallback
- Client bots queue messages at Telegram (Telegram retries webhooks for ~24h)
- When tunnel recovers, queued messages are delivered

## Reuse from Existing Orchestrator

| Existing | Reuse |
|----------|-------|
| Router agent | Enhanced with new intents (site_edit, place_order, book_qurban, manage_team) |
| Brainstorm agent | Powers the chat intent with client personality |
| Bug pipeline | Bug report flow unchanged — just needs client_id context |
| ai_provider.py | All AI calls through call_ai() — model-agnostic |
| context_loader.py | Loads repo context for bug diagnosis |
| ralph_runner.py | Applies bug fixes |
| deploy_manager.py | Deploys after bug fix approval |
| heartbeat.py | Per-bot heartbeat for monitoring |
| clients table | Extended with bot fields |
| cloudflared tunnel | Webhook endpoint |

## New ihsanOS API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /api/storefront-update` | Update StorefrontConfig field by path. Used by site_edit flow. |
| `GET /api/storefront-config/{org_id}` | Fetch current config for the bot to reference. |
| `GET /api/products/{org_id}` | Fetch products for ordering flow. |
| `GET /api/qurban-animals/{org_id}` | Fetch available qurban animals. |

These are thin wrappers around existing server actions, exposed as REST for the orchestrator to call.

## Testing

### Unit Tests
- `test_multi_bot.py` — webhook routing, client lookup by token hash, hot-reload
- `test_conversation_state.py` — state machine transitions, expiry, data accumulation, resume
- `test_invite_flow.py` — code generation, deep link parsing, expiry, single-use, revocation
- `test_permissions.py` — role → intent access matrix, capability gating
- `test_personality.py` — system prompt construction with personality injection

### Integration Tests
- Full onboarding: paste token → bot configured → webhook registered → owner registered
- Team invite: owner adds → code generated → member claims via deep link → role active
- Ordering: customer sends items → state machine → placeOrder API called → order created
- Cross-bot isolation: message to @hadramawt_bot never accesses @bapa_bot data
- Capability gating: customer can't trigger site_edit, staff can't trigger manage_team

### PostHog Analytics
- `bot_onboarded` — client_id, bot_username, capabilities
- `bot_message_received` — client_id, user_role, intent, response_time
- `bot_order_placed` — client_id, order_total, items_count, channel: "telegram"
- `bot_qurban_booked` — client_id, animal_type, country, channel: "telegram"
- `bot_team_member_added` — client_id, role, method (invite_code)
- `bot_site_edited` — client_id, field_changed
- `bot_conversation_completed` — client_id, flow, steps_count, duration

## Quranic Foundations

| Decision | Foundation | Rationale |
|----------|-----------|-----------|
| Branded bots per client | Ihsan | Excellence — each client gets a professional, personalized experience |
| Invite verification | Amanah | Trust — no unauthorized access to business operations |
| Conversation state | Ihsan | Excellence — don't make the customer repeat themselves |
| Webhook over polling | Ihsan | Technical excellence — efficient, scalable, real-time |
| Role-based permissions | Adl | Justice — each person sees only what they need |
| Owner controls everything | Amanah | Trust — the business owner has full authority over their bot |
| Customer auto-registration | Ihsan | Zero friction — serve the customer immediately |
| Personality per client | Ihsan | Each bot speaks the client's language and brand voice |
| Single infrastructure | Amanah | Responsible resource use — don't duplicate what can be shared |

## Out of Scope (future)

- WhatsApp Business API integration (same flows, different channel)
- Voice messages (Whisper transcription exists but not wired to flows)
- Payment processing within Telegram (Telegram Payments API)
- Customer loyalty / reward points
- Multi-language bot responses
- Bot analytics dashboard per client (currently only in super admin)
- Automated marketing messages (broadcast to customers)
- Integration with third-party delivery services (Grab, Lalamove)

# Self-Service Automation Design — Billing Engine + WordPress Migration + Custom Domains

**Date:** 2026-04-10
**Status:** Approved for implementation
**Owner:** Musa / Wingmen
**Codebases:** ~/wingmen/orchestrator (Python) + ~/wingmen/projects/ihsanos (TypeScript)

## Goal

Enable fully self-service client onboarding: a client gives their WordPress URL → bot migrates their site → they set up a custom domain → billing automatically tracks revenue and enforces plan limits. Zero human intervention for the standard path. Flexible billing engine supports transaction cuts, subscriptions, upfront fees, and hybrid models with pluggable payment processors — designed for eventual shariah-compliant rails.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  BILLING ENGINE                      │
│  billing_profiles: per-client pricing model          │
│  transaction_ledger: every payment + margin          │
│  Plan limit enforcement at server action level       │
│  Auto-invoice via ihsanOS inv_invoices               │
│  Processor abstraction: Stripe/PayNow/Hitpay/future  │
├─────────────────────────────────────────────────────┤
│           WORDPRESS AUTO-MIGRATION                   │
│  Bot: "migrate my site" + URL                        │
│  → screenshot + GLM-5V design analysis               │
│  → WC Store API product fetch                        │
│  → create ihsanOS org + storefront                   │
│  → client previews + iterates → live                 │
├─────────────────────────────────────────────────────┤
│           CUSTOM DOMAIN AUTOMATION                   │
│  Bot: "set up my domain"                             │
│  → DNS instructions (CNAME to Vercel)                │
│  → verify DNS resolution                             │
│  → Vercel API: add domain + auto-SSL                 │
│  → live on custom domain                             │
└─────────────────────────────────────────────────────┘
```

## Long-Term Vision: Shariah-Compliant Payment Rails

The billing engine is designed for a 4-stage evolution:

| Stage | Timeline | What Changes | Licensing |
|-------|----------|-------------|-----------|
| **1. Aggregator** | Now | Use Stripe/PayNow/Hitpay as-is. Billing engine tracks margins. | None needed |
| **2. Payment Facilitator** | 6-18 months | Become PayFac under Stripe Connect or Hitpay Partner. Onboard sub-merchants. Negotiate bulk rates. | Processor partnership |
| **3. Licensed PI** | 18-36 months | MAS Major Payment Institution license. Direct PayNow integration. Own fee schedules. | MAS MPI license ($250K capital) |
| **4. Shariah Rails** | 36+ months | Own settlement layer. No riba (interest on float). Instant settlement. Transparent ujrah (service fee). Shariah advisory board. | Shariah certification |

The billing engine's processor abstraction means upgrading from Stage 1 to Stage 4 only changes the processor config — not the billing logic, not the client-facing fees, not the reporting.

## Data Model

### `billing_profiles` — one per client

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| client_id | BIGINT FK clients UNIQUE | one profile per client |
| model | TEXT NOT NULL CHECK ('transaction_cut', 'subscription', 'upfront', 'hybrid', 'free') | |
| status | TEXT NOT NULL DEFAULT 'active' CHECK ('active', 'trial', 'expired', 'suspended') | |
| trial_ends_at | TIMESTAMPTZ | null = no trial |
| total_fee_percent | NUMERIC(5,3) | client's total fee rate (e.g., 2.500%) |
| total_fee_fixed | NUMERIC(10,2) | fixed per-transaction fee (e.g., 0.30) |
| volume_tier | TEXT DEFAULT 'standard' CHECK ('standard', 'growth', 'enterprise', 'custom') | |
| cumulative_volume | NUMERIC(15,2) NOT NULL DEFAULT 0 | lifetime transaction volume for tier progression |
| custom_rate_override | BOOLEAN NOT NULL DEFAULT false | if true, ignore tier rules, use total_fee directly |
| monthly_amount | NUMERIC(10,2) | for subscription model |
| billing_cycle | TEXT CHECK ('monthly', 'quarterly', 'annually') | |
| next_billing_date | DATE | |
| upfront_amount | NUMERIC(15,2) | for upfront model |
| support_ends_at | DATE | when upfront support period expires |
| processor | TEXT NOT NULL DEFAULT 'paynow' | 'stripe', 'paynow', 'hitpay', 'direct' |
| processor_account_id | TEXT | client's processor account (Stripe Connect etc.) |
| processor_cost_percent | NUMERIC(5,3) | what the processor charges us |
| processor_cost_fixed | NUMERIC(10,2) | |
| notes | TEXT | negotiation notes, special terms |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### `transaction_ledger` — every payment through ihsanOS

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| client_id | BIGINT FK clients | |
| org_id | UUID FK organizations | |
| source | TEXT NOT NULL | 'donation', 'pos', 'invoice', 'qurban', 'storefront_order' |
| source_id | UUID NOT NULL | the donation/transaction/invoice/booking ID |
| amount | NUMERIC(15,2) NOT NULL | transaction amount |
| currency | TEXT NOT NULL DEFAULT 'SGD' | |
| processor | TEXT NOT NULL | which processor handled this |
| processor_reference | TEXT | payment reference (Stripe PI, PayNow ref) |
| total_fee | NUMERIC(10,2) NOT NULL | fee client paid |
| processor_cost | NUMERIC(10,2) NOT NULL | what processor charged us |
| platform_margin | NUMERIC(10,2) NOT NULL | our revenue = total_fee - processor_cost |
| recorded_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

### `billing_tiers` — configurable default tiers (super admin manages)

| Column | Type | Notes |
|--------|------|-------|
| id | UUID PK | |
| name | TEXT NOT NULL | 'standard', 'growth', 'enterprise' |
| min_volume | NUMERIC(15,2) NOT NULL | cumulative threshold |
| fee_percent | NUMERIC(5,3) NOT NULL | rate at this tier |
| fee_fixed | NUMERIC(10,2) NOT NULL | fixed fee at this tier |
| sort_order | INTEGER NOT NULL | |
| created_at | TIMESTAMPTZ | |

Default data:
```
standard:   min_volume: 0,       fee: 2.5% + $0.30
growth:     min_volume: 50000,   fee: 1.5% + $0.20
enterprise: min_volume: 200000,  fee: 0.8% + $0.10
```

### Modified: `clients` table

Add columns:
```sql
billing_profile_id UUID REFERENCES billing_profiles(id)
custom_domain TEXT
domain_status TEXT CHECK ('none', 'pending_dns', 'verified', 'active', 'failed') DEFAULT 'none'
```

### Margin Model

The client sees ONE fee. Our margin is the difference between what the client pays and what the processor charges us.

```
Transaction: $100 donation at BAPA

Client billing profile:
  total_fee_percent: 2.5%
  total_fee_fixed: $0.30
  processor: paynow
  processor_cost_percent: 0.5%  (PayNow business rate)
  processor_cost_fixed: $0.00

Calculation:
  total_fee = ($100 × 2.5%) + $0.30 = $2.80
  processor_cost = ($100 × 0.5%) + $0.00 = $0.50
  platform_margin = $2.80 - $0.50 = $2.30

Ledger entry:
  amount: 100.00, total_fee: 2.80, processor_cost: 0.50, platform_margin: 2.30
```

When we get our own rails (Stage 4):
```
  processor_cost_percent: 0.0%
  processor_cost_fixed: $0.10  (bank transfer fee only)

  total_fee = $2.80 (unchanged for client)
  processor_cost = $0.10
  platform_margin = $2.70  (margin jumps from $2.30 to $2.70)
```

## Volume Tier Progression

Cumulative — never resets. Client's rate permanently improves as volume grows.

```
After processing $50K lifetime:
  cumulative_volume updated to 50000
  volume_tier auto-upgraded from 'standard' to 'growth'
  total_fee_percent auto-reduced from 2.5 to 1.5
  total_fee_fixed auto-reduced from 0.30 to 0.20

  Client notified: "Your rate just improved to 1.5%! Thanks for growing with us."
```

When `custom_rate_override = true`, tier auto-progression is disabled. The manually set rate stays.

## Plan Limits (Free Tier Enforcement)

| Limit | Free | Starter ($49) | Growth ($149) | Scale ($399) |
|-------|------|--------------|--------------|-------------|
| Products | 5 | Unlimited | Unlimited | Unlimited |
| Donations/month | 50 | Unlimited | Unlimited | Unlimited |
| Users (org_members) | 1 | 3 | 10 | Unlimited |
| Orders/month | 100 | Unlimited | Unlimited | Unlimited |
| Custom domain | No | Yes | Yes | Yes |
| All modules | No (donations + POS only) | Yes | Yes | Yes |
| Branded bot | No | No | Yes | Yes |
| "Powered by ihsanOS" footer | Yes | No | No | No |
| Priority bug fixes | No | No | Yes | Yes |
| Dedicated support | No | No | No | Yes |

**Enforcement:** Server actions check limits before mutations:
```typescript
// In createProductAction:
const limit = await getPlanLimit(orgId, "products");
if (limit !== null) {
  const current = await getProductCount(orgId);
  if (current >= limit) {
    return { data: null, error: { code: "PLAN_LIMIT", message: "You've reached the free plan limit of 5 products. Upgrade to add more." } };
  }
}
```

Limit config stored in `billing_profiles` or derived from the model + tier. Super admin can override any limit per client.

## WordPress Auto-Migration

### Bot Conversation Flow

```
Client: "I want to migrate my WordPress site"
Bot: "What's your site URL?"
Client: "hadramawtkitchen.sg"
Bot: "Scanning hadramawtkitchen.sg... 🔍"

[System: screenshot site → GLM-5V analyzes design → WC Store API fetches products]

Bot: "Found:
      🛒 45 products across 7 categories
      📝 Hero, About, Contact sections
      📱 WhatsApp: +65 8123 4567
      🎨 Design analyzed — creating your store..."

[System: creates ihsanOS org + StorefrontConfig + imports products]

Bot: "Preview ready!
      https://ihsanos.com/shop/hadramawt-kitchen

      ✅ 45 products imported
      ✅ Storefront designed
      ✅ Contact details configured

      Does this look right?"

Client: "The hero image is wrong"
Bot: "Send me the image you want"
Client: [sends photo]
Bot: "Updated! Check: https://ihsanos.com/shop/hadramawt-kitchen"
Client: "Looks good!"
Bot: "Your site is live! 🎉

      Next steps:
      • Set up your branded bot: say 'create bot'
      • Use your own domain: say 'set up domain'
      • Dashboard: ihsanos.com/dashboard"
```

### Implementation

**Handler:** `handlers/migration_handler.py`
**State machine:** `get_url → scanning → preview → iterate → finalize`

**Process:**
1. Validate URL (check it's accessible, check for WooCommerce)
2. Screenshot the homepage via Playwright headless
3. Send screenshot to GLM-5V (via OpenRouter) → get design analysis (colors, layout, mood)
4. Fetch products via WC Store API (`/wp-json/wc/store/v1/products`)
5. Scrape HTML for hero text, about section, contact info, WhatsApp
6. Create ihsanOS organization (name from site title, type from content analysis)
7. Create StorefrontConfig from GLM-5V analysis + scraped content
8. Import products into `pos_products` with categories
9. Create `clients` record with billing profile (free trial)
10. Return preview URL
11. Client iterates via site_edit handler
12. On approval, mark storefront as published

**Reuses:**
- Existing `scripts/migrate-wordpress.ts` logic (refactored into callable functions)
- GLM-5V clone feature from storefront settings
- Site edit handler for iteration
- Bot onboarding for "create bot" next step

### Non-WooCommerce Sites

For sites without WooCommerce (pure HTML/WordPress blogs):
- Skip product import
- Still clone design via GLM-5V
- Scrape content (text, images, contact info)
- Create storefront-only site (no POS/ordering)
- Bot says "No products found — your site is set up as an informational page. You can add products later from the dashboard."

## Custom Domain Automation

### Bot Conversation Flow

```
Client: "I want to use my own domain"
Bot: "What domain? (e.g., hadramawtkitchen.sg)"
Client: "hadramawtkitchen.sg"
Bot: "Add this DNS record at your domain registrar:

      Type: CNAME
      Name: @ (or www)
      Value: cname.vercel-dns.com

      For apex domains (no www), some registrars need an A record instead:
      Type: A
      Value: 76.76.21.21

      Once added, say 'done' and I'll verify.
      (DNS can take up to 48 hours, but usually 5-10 minutes)"

Client: "done"
Bot: "Checking DNS... ⏳"

[System: verify DNS resolution]

If not ready:
Bot: "DNS not propagated yet. I'll check again in 5 minutes."
[Auto-retry every 5 min for 1 hour, then every 30 min for 48 hours]

When ready:
Bot: "✅ DNS verified! Provisioning SSL certificate...

      Your site is now live at:
      https://hadramawtkitchen.sg

      🔒 SSL active. Fully secure."
```

### Implementation

**Handler:** `handlers/domain_handler.py`
**State machine:** `get_domain → instruct_dns → verify → provision → done`

**Vercel API calls:**
1. Validate domain format
2. `POST /v10/projects/{projectId}/domains` — add domain
3. Poll `GET /v10/projects/{projectId}/domains/{domain}` — check verification
4. Vercel auto-provisions SSL via Let's Encrypt

**DNS Verification:**
- Python `dns.resolver` to check CNAME/A record
- Retry with backoff: 5 min × 12, then 30 min × 48
- After 48 hours with no resolution → notify client + escalate

**Plan gating:** Only for Starter+ plans. Free plan gets `slug.ihsanos.com` only. Handler checks billing profile before proceeding.

**Stored in clients table:**
```
custom_domain: "hadramawtkitchen.sg"
domain_status: "pending_dns" → "verified" → "active"
```

## Super Admin Billing Management

### New page: `/super-admin/billing`

**Revenue Dashboard:**
- Total MRR (sum of all subscription billing profiles)
- Total transaction volume this month (from ledger)
- Total platform margin this month
- Clients by billing model (pie chart: transaction cut / subscription / upfront / free)
- Top 10 clients by revenue
- Volume trend (monthly chart)

### New page: `/super-admin/billing/settings`

**Default Configuration:**
- Volume tier table (editable: threshold, percent, fixed fee per tier)
- Default processor cost assumptions
- Free tier limits (products, donations, users, orders — all editable)
- Trial duration default (days)

### Extended org detail: `/super-admin/orgs/[id]` — new "Billing" tab

- Current billing model + status
- Revenue from this client (lifetime, this month)
- Transaction ledger (scrollable, filterable)
- Edit billing profile form:
  - Model selector (transaction_cut / subscription / upfront / hybrid / free)
  - Custom rates (with override toggle)
  - Processor selector + config
  - Trial dates
  - Subscription amount + cycle
  - Upfront amount + support end date
- Generate invoice button (for sub/upfront)
- Volume tier: current tier, cumulative volume, progress bar to next tier

## Invoicing Integration

For subscription and upfront clients, Wingmen invoices are generated using ihsanOS's own invoicing module:

**Monthly auto-invoice (subscription clients):**
1. Scheduled task runs on `next_billing_date`
2. Creates `inv_invoices` record in the CLIENT's org (or a Wingmen platform org)
3. Line item: "ihsanOS {plan} Plan — {month}"
4. Sends invoice email with PDF
5. Updates `next_billing_date` to next cycle

**Transaction cut clients:**
1. Monthly reconciliation statement generated
2. Shows: total transactions, fees, margin
3. If using auto-deduct: already settled, statement is informational
4. If manual billing: invoice generated for accumulated platform fees

**Hook for future PayNow auto-reconciliation:**
```python
# In transaction_ledger recording:
async def record_transaction(supabase, **kwargs):
    # ... record to ledger ...

    # Hook: check for auto-payment notification
    # When PayNow auto-reconciliation is built, this is where it plugs in
    await _check_payment_notification(kwargs.get("processor_reference"))
```

## Roles & Workflows (Spec Review Checklist)

### Client (new, migrating from WordPress)
**Discovery:** Messages bot or finds ihsanos.com
**Workflow:** Give URL → preview → iterate → live → set up domain → set up bot
**Notifications:** Migration complete, domain verified, trial ending, plan limit approaching
**Restrictions:** Free tier limits until upgrade

### Client (existing, hitting plan limits)
**Discovery:** Server action returns PLAN_LIMIT error → UI shows upgrade prompt
**Workflow:** See limit message → tap "Upgrade" → choose plan → pay (invoice or future self-serve Stripe) → limits removed
**Notifications:** Limit at 80%, limit hit, upgrade confirmation

### Super Admin (Musa)
**Discovery:** `/super-admin/billing` dashboard
**Workflow:** View revenue, set billing profiles, override rates, manage trials, generate invoices
**Notifications:** Monthly revenue summary, payment overdue alerts, trial expirations

### Support Admin (Syukor)
**Discovery:** Same billing pages, read-only
**Workflow:** View billing profiles and revenue, cannot change rates
**Notifications:** None (billing is Musa's domain)

### System (automated)
**Workflow:** Record transactions → calculate fees → progress volume tiers → enforce limits → generate monthly invoices → send notifications
**Restrictions:** Never changes billing model automatically (only tier progression within a model)

## Reuse from Existing Systems

| Existing | Reuse |
|----------|-------|
| `scripts/migrate-wordpress.ts` | Product fetching, HTML scraping logic |
| GLM-5V clone (storefront settings) | Design analysis for migration |
| `inv_invoices` + `inv_payments` | Monthly billing invoices |
| `deploy_manager.py` | Vercel API patterns |
| White-label bot handlers | Migration + domain conversation flows |
| `conversation.py` state machine | Multi-turn migration/domain flows |
| Super admin org detail | Billing tab added to existing page |
| PostHog analytics | Billing events |

## New Components

### Orchestrator (Python)
| File | Purpose |
|------|---------|
| `billing_engine.py` | Fee calculation, tier progression, limit checking |
| `handlers/migration_handler.py` | WordPress migration conversation flow |
| `handlers/domain_handler.py` | Custom domain setup conversation flow |
| `nervous_system/billing_scheduler.py` | Monthly invoice generation, tier checks, trial expiry |

### ihsanOS (TypeScript)
| File | Purpose |
|------|---------|
| `src/shared/lib/plan-limits.ts` | Plan limit constants + check functions |
| `src/shared/lib/billing-utils.ts` | Fee calculation, tier lookup |
| `src/actions/billing.ts` | Billing profile CRUD, ledger recording |
| `src/app/super-admin/billing/page.tsx` | Revenue dashboard |
| `src/app/super-admin/billing/settings/page.tsx` | Default rate + limit config |
| Migration: `026_billing_engine.sql` | billing_profiles, transaction_ledger, billing_tiers tables |

### Shared API endpoints (ihsanOS, called by orchestrator)
| Endpoint | Purpose |
|----------|---------|
| `POST /api/migrate-wordpress` | Trigger migration (receives URL, returns preview) |
| `GET /api/plan-limits/[orgId]` | Check current plan limits for an org |
| `POST /api/billing/record-transaction` | Record to ledger after payment |

## Testing (Quality Pyramid)

### Unit Tests
- `billing-engine.test.ts` — fee calculation (all tiers, custom override, hybrid), margin computation, volume tier auto-progression
- `plan-limits.test.ts` — all 4 plan tiers, every limit type, edge cases (exactly at limit, over limit)
- `migration-utils.test.ts` — URL validation, WooCommerce detection, product mapping
- `domain-utils.test.ts` — domain format validation, CNAME verification logic

### E2E Tests
- Migration: bot receives URL → products found → preview loads → site live
- Domain: client gives domain → DNS instructions shown → (mock) verified → active
- Billing: super admin creates billing profile → transaction recorded → margin correct → invoice generated
- Plan limits: free client adds 5 products ✓ → tries 6th → gets PLAN_LIMIT error → upgrades → 6th succeeds

### PostHog Analytics
- `site_migrated` — source_url, products_count, has_woocommerce, time_to_complete
- `domain_configured` — domain, verification_time_minutes
- `billing_model_set` — client_id, model, rates
- `plan_upgraded` — client_id, old_plan, new_plan, trigger (limit_hit / voluntary)
- `plan_limit_hit` — client_id, limit_type, current_count, limit_value
- `transaction_recorded` — source, amount, processor, margin
- `monthly_invoice_generated` — client_id, amount
- `volume_tier_upgraded` — client_id, old_tier, new_tier, cumulative_volume

## Notifications

| Event | Recipient | Channel |
|-------|-----------|---------|
| Migration complete | Client | Bot + email |
| Migration failed | Client + Musa | Bot + Telegram |
| Domain verified | Client | Bot |
| Domain failed (48h) | Client + Musa | Bot + Telegram |
| Plan limit at 80% | Client | Email |
| Plan limit hit | Client | In-app banner + email |
| Trial ending (3 days) | Client | Email + bot |
| Trial expired | Client | Email + in-app |
| Monthly invoice | Client | Email with PDF |
| Payment overdue (7 days) | Client + Musa | Email + Telegram |
| Volume tier upgraded | Client | Email + bot |
| Monthly revenue summary | Musa | Telegram |

## Quranic Foundations

| Decision | Foundation | Rationale |
|----------|-----------|-----------|
| Transparent fee structure | Adl | Client sees one rate, no hidden charges |
| Cumulative tiers (never reset) | Ihsan | Reward loyalty permanently, don't punish seasonal variation |
| Free tier is genuinely useful | Ihsan | Not a demo — real value for small orgs |
| Margin model (not stacking fees) | Adl | Fair pricing — client pays market rate, we earn from efficiency |
| Processor abstraction | Amanah | Prepared for shariah-compliant rails |
| Own rails vision | Ihsan | Excellence — build the best, most ethical payment system |
| Client previews migration | Amanah | Trust — they approve before it goes live |
| PayNow auto-reconciliation hook | Ihsan | Forward-thinking — infrastructure ready when capability arrives |
| Super admin override | Adl | Flexibility for special cases (charity, beta, partnership) |

## Out of Scope (future)

- Stripe self-serve checkout (client upgrades without contacting you)
- PayNow auto-reconciliation (DBS/OCBC API or notification scraping)
- Multi-currency billing (non-SGD)
- Affiliate/referral program (client refers another client, gets discount)
- Usage-based billing beyond simple limits (charge per API call, per GB storage)
- Automated churn prevention (detect declining usage, send win-back)
- Client billing portal (self-serve invoice history, payment method management)
- Tax invoice generation for Wingmen's revenue (GST filing)

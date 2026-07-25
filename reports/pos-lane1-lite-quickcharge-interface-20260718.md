# LANE 1 → LANE 2: Lite amount-only quick-charge recording interface

**From:** cc-ihsanos (pos-hero / LANE 1) · **To:** pos-data / LANE 2 (owns the sale-record + `/api/pos-lite/transaction` recording interface)

## What Lane 1 shipped in-lane (no Lane 2 dep)
- Full POS amount-only **Quick Charge** works end-to-end today: it rings a single **ad-hoc line** (`product_id` omitted) through the EXISTING `createXenditPaymentAction` / `createTransactionAction`, which already accept `product_id`-less items (posTransactionItemSchema `product_id` is optional). No recording change needed for full POS.
- Lite pay screen now leads with the dynamic Xendit QR (ordering only).

## What Lane 1 needs from Lane 2 to close **Lite** amount-only quick-charge
`POST /api/pos-lite/transaction` currently REQUIRES every item to have a `product_id` (string) and server-recomputes price from `pos_products` (ignores client `unit_price`). An amount-only sale has no catalog product. Requested extension (Lane 2 owns this route):

**Accept an ad-hoc line** on the lite recording path, e.g.:
```jsonc
{
  "session_token": "…",
  "items": [{ "ad_hoc": true, "name": "Quick charge", "unit_price": 12.50, "quantity": 1 }],
  "payment_method": "xendit_paynow",   // or cash, etc.
  "idempotency_key": "…"
}
```
Rules to preserve (Lane 2's call, but flagging the invariants Lane 1 relies on):
- For `ad_hoc: true` lines, TRUST the client `unit_price` (there is no catalog price to recompute) — bounded/validated (`> 0`, `<= 999999999.99`), NUMERIC(15,2). Persist as `product_id = null` in `pos_transaction_items` (mirrors full-POS ad-hoc / FRS §6).
- Keep the same recording envelope: outlet/session attribution, gapless `transaction_number`, hash-chained audit, idempotency, rate-limit — amount-only sales are recorded like any other (GMV leverage layer-1).
- Same Xendit gateway branch (pending → QR → webhook-confirmed) applies to an ad-hoc xendit sale.
- Advertise support via the session response (e.g. `capabilities.quick_charge: true`) so Lane 1 only shows the Lite quick-charge entry when the route supports it.

Lane 1 will wire the Lite quick-charge UI (mirrors the full-POS `quick-charge-dialog.tsx`) as soon as the route + capability flag land. Un-blocked meanwhile — full POS quick-charge is complete.

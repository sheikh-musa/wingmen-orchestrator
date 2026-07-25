# POS backlog — payment fast-follows (op#5235, via Nazim #9716)

**Status:** POST-sprint backlog. NOT in the Monday sprint; sprint lanes (feat/pos-sprint) do not touch these. Surface for prioritization once the sprint lands.

**Firm principle (op#5223/#5235):** dynamic QR (PayNow SG / QRIS ID) via Xendit = the cheap universal default + the volume/leverage/moat play. Wallets/cards are supplementary one-tap options only where the rail exists — never promoted over the QR rail.

## 1. NFC tap-to-pay (strong fit — keeps volume on QR rails)
- Static per-counter **NFC sticker encoding a URL** (same pattern as the Lite counter-token link) → server resolves live → generates the **dynamic Xendit QR / pay-link** with amount + ref.
- Customer **taps** phone → native NFC-URL open on **BOTH iOS + Android** (no app, no Web-NFC-write — sidesteps the Android-Chrome-only Web NFC API).
- = "tap instead of scan", same dynamic-Xendit-QR rail underneath. High value, low platform risk.

## 2. Google Pay (SG-only, CARD rail)
- Xendit-supported in **Singapore** (+ PH/MY/TH/VN/HK/MX) but **NOT Indonesia** (verified docs.xendit.co/docs/google-pay).
- Routes through **CARDS** (card fees, NOT the cheap QR rail) — integrates via Payment Sessions / hosted links / Xendit Components.
- = a SG-only **one-tap card** option. Supplementary, not the volume play (card economics ≠ QR).

## 3. Apple Pay — PARKED
- **Xendit does NOT support Apple Pay** (no docs). Would need a non-Xendit processor → against the keep-volume-on-Xendit principle. Do NOT build against Xendit for Apple Pay. Parked unless the strategy changes.

## 4. Indonesian e-wallets (OVO / DANA / ShopeePay) — AWAITING operator confirm
- Xendit-supported for **Indonesia** = the real wallet equivalent on the ID side; keeps volume on Xendit. Nazim offered it to the operator; pending his confirm to add.

## Prioritization note
When the sprint lands: NFC tap-to-pay is the strongest fast-follow (extends the QR-volume rail, cross-platform, low risk). Google Pay (SG cards) + ID e-wallets are market-coverage adds. Apple Pay stays parked.

## 5. Asset / inventory tagging (op#5250, Nazim #9751) — new backlog track
- Each asset gets an **NFC (or QR) sticker** encoding a **dumb URL** `app/asset/{id}` → tap/scan → server resolves to the asset record → **view + edit** (stock/status/location), edit **role-gated**, updates **queue + sync offline** (backroom wifi).
- Keep tags **dumb (ID only)**; all meaning server-side. Support **QR + NFC on the same label**.
- **Generalizes the proven irsyad tabung-tin pattern** (tins already have serial_number + barcode → scan → status → update). Strategic: widens the platform from POS to "every physical thing has a digital record" (more ops captured = stickiness + data).
- **NOT** the Decathlon-style GROUP/basket scan: that is **UHF RFID** (860–960MHz, many tags at range) = dedicated **reader hardware**, not a phone, pricier tags → FAR-FUTURE enterprise/high-throughput tier only. Do NOT conflate with the NFC one-tap single-item play.
- POST-Monday-sprint backlog; sprint lanes untouched.

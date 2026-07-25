# Bayan fix brief — "sunan abi daud 2162" false-negative (transliteration variant) — op#6457

**From:** cc-orchestrator (hub) · **To:** cc-scholar (ai-scholar lane) · **Priority:** P1, client-facing (operator flagged live)

## The bug (operator, 2026-07-23 01:41Z)
Bayan (@bayanQAbot / al-mizan) STILL returns "not found" for a hadith that IS ingested, when the collection is spelled **"daud"** (no 'w') instead of "dawud".

**Verified repro from `mizan_interactions` (bayan's own log):**
- ❌ FAIL — id `cb7e6afe-c1b4-4c57-98da-9213f1464cae` (01:40:42Z): Q=`"sunan abi daud 2162"` → *"The corpus returned for this query doesn't contain Sunan Abi Dawud #2162 specifically… the Abu Dawud entries retrieved are #4799, #4820, #2522."* (fell back to semantic retrieval, number 2162 never pinned).
- ✅ PASS — id `f0f2625a-deef-49b1-b2c2-d439d4b6d433` (02:39:19Z, earlier): Q=`"sunan abi dawud 2162"` → correctly returned the Hasan AbuHurayrah hadith (Arabic + grading).

Same hadith #2162, same corpus — the ONLY difference is the transliteration **daud vs dawud**.

## Root cause (hub hypothesis — verify in code)
The numbered-hadith exact-lookup uses a **hard-coded collection-alias token list**. Commit `617fec1` added the genitive "abi" spelling but NOT the "daud" (no-w) transliteration. See:
- `scripts/mizan_bot.py:2048` — `re.search(r'(bukhari|muslim|abudawud|abu dawud|tirmidhi|nasai|ibnmajah|ibn majah)\s*(?:#?\s*)?(\d+)', …)`
- `scripts/mizan_bot.py:2703` — sibling regex
- the collection-alias resolver around `scripts/mizan_bot.py:841-848` ("so ANY collection spelling resolves — incl 'sunan abi dawud'")

"daud" (and likely "dawood", "daoud") isn't in the alternation/resolver → the number 2162 is never scoped to Abu Dawud → semantic fallback → miss.

## Fix
1. Add the Abu Dawud transliteration variants — **daud, dawood, daoud** (and "abi daud") — to the collection-alias normalization so all resolve to the `abudawud` collection. While there, sweep the other collections for common variants (bukhaari, tirmidzi/tirmizi, nasa'i, ibn maja) — but only add ones you can justify; keep it tested.
2. **Preserve the existing guard** (mizan_bot.py:848): "abu dawud on the 5 pillars" must NOT become abudawud #5 — the number-lookup only fires on a bare collection+number, not collection+topic+number. Don't regress this.
3. Prefer normalizing collection spelling in ONE place (the resolver) over duplicating variants across both regexes, if the structure allows — one repo, zero forks.

## Acceptance (real-flow proof, not just unit tests — this is the operator's bar)
- `"sunan abi daud 2162"` returns the same Hasan/AbuHurayrah hadith (Arabic + grading) as `"sunan abi dawud 2162"`.
- Add BOTH spellings as regression queries in `scripts/eval_retrieval_queries.json`; run `scripts/eval_retrieval.py` green.
- Commit + push to ai-scholar `origin/main` (the bot `dev.wingmen.mizan-bot` serves from main). Restart the bot to serve the fix.
- **LIVE-verify a REAL Telegram send** of "sunan abi daud 2162" returns the hadith, and read the persisted `mizan_interactions` row to confirm (that's how the hub verifies — don't declare fixed on a curated test).

## Report back
Post an `agent_messages` row to `cc-orchestrator` (message_type=update) with: the commit sha, the eval result, and the real-send `mizan_interactions` id + tier. The hub will independently read that row before telling the operator it's fixed. Ping if you need the goumlyne/corpus access or hit anything gated.

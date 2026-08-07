# Zahidah Study Bot — domain-expert learning companion

**Status:** spec / not-yet-built (gated on operator inputs). **Owner:** cc-orchestrator (bot + substrate).
**Origin:** operator 2026-06-27/28 — a dedicated bot for Zahidah (Musa's fiancée) to help with her school projects; then "this can be applicable to all knowledge… an expert at nutrition science, ingesting all her notes and even creating novel ways of visual memorization of dense topics". operator: "proceed" (fold the domain-expert shape into the Zahidah spec).
**Governance:** personal data → RLS-isolated to her, sensitivity-tagged, never leaks to any other slice; cai-gated schema; build-for-the-need. Dedicated scoped bot identity (never a fleet identity), like the bug-bot / cai-bot pattern.

## The shape — a reusable TEMPLATE (ingest → graph+RAG → generate)
This is the same pipeline as mizan (Islamic knowledge) and the life-context layer, pointed at a study domain. It becomes the **substrate-as-product template** for any domain-expert bot (Zahidah's nutrition bot, the tarbiyah aqidah bot, a future client's domain).

1. **INGEST** her corpus — her notes + textbooks/papers for the subject (e.g. nutrition science). Chunk + store.
2. **TWO RECALL LAYERS** (the mizan-vs-life-graph split):
   - **pgvector / RAG** — semantic recall: "what do my notes say about X", pull the right passage.
   - **domain knowledge graph** (entities + edges) — structure: e.g. nutrients ↔ functions ↔ foods ↔ conditions; enables connecting ideas + multi-hop ("which deficiencies link to X, and what foods fix them").
3. **GENERATE** (the value layer, on top of the knowledge):
   - **Visual memorization** — render the knowledge graph as a **mind-map / concept-map**: the graph *is* a spatial memory structure (closest thing to a memory palace) — turns dense material into the form that aids recall.
   - Auto **spaced-repetition flashcards** built from the graph, **mnemonics**, analogies, quizzes, fresh explanations of a hard topic.

## The bot
- Dedicated BotFather token; distinct scoped identity; she DMs it; **scope-locked to her study domain** — NO fleet / PII-of-others / money / other-vertical access.
- She uploads notes → it ingests → answers + generates study aids (mind-maps, flashcards, quizzes) on request.

## Data model
Supabase/Postgres (same substrate): domain `entities` + `relationships` (the knowledge graph) + chunked-notes table with **pgvector** embeddings; **RLS-isolated to Zahidah** (personal corpus, sensitivity-tagged). cai-gated schema like any substrate change.

## Gated on operator inputs (the build can't start without)
- A **BotFather token** for the bot.
- Her **subject(s) + level** — so I scope the domain graph + ingest the right corpus.
- Her **notes** (the corpus to ingest).

## Honest note
The recall/structure (RAG + graph) is the reliable, easy part. Genuinely *great* learning aids (not generic flashcards) need good source notes + careful generation — do it properly, iterate with her on what actually helps her learn. Relates to [[life-context-layer]] (shared graph+pgvector machinery) and the substrate-as-product vision.

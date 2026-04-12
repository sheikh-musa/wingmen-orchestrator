// supabase/functions/cto-strategist/index.ts
//
// ══════════════════════════════════════════════════════════════════════
// CTO Strategist Edge Function
// ══════════════════════════════════════════════════════════════════════
//
// The "second mind" in every strategic discussion. Triggered by Postgres
// trigger on cto_council INSERT where role='claude_code'. Reads the full
// thread, pulls wingmen_brain context, reads the latest claude_code row's
// context field (repo state at write-time), loads the bundled system
// prompt, calls Anthropic API with prompt caching on the stable prefix,
// writes a claude_ai response row back, updates session state + circuit
// breaker counters.
//
// Deployed via: supabase functions deploy cto-strategist --project-ref tscuymavysscrvoberrr
//
// Invoked by the Postgres trigger via pg_net.http_post. Can also be
// invoked manually for debugging:
//   curl -X POST https://tscuymavysscrvoberrr.supabase.co/functions/v1/cto-strategist \
//     -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
//     -H "Content-Type: application/json" \
//     -d '{"session_id": 42}'
//
// ══════════════════════════════════════════════════════════════════════
// ANTI-STALENESS CONTRACT
// ══════════════════════════════════════════════════════════════════════
//
// This function is the load-bearing piece of the anti-staleness
// architecture documented in migration 20260412000001_cto_council.sql.
// On every invocation it rebuilds a fresh view of three layers:
//
//   1. Static layer (CTO_STRATEGIST_PROMPT.md) — bundled with this
//      function, loaded at cold start. Hard constraints only.
//   2. Ecosystem layer (wingmen_brain snapshot) — queried live on every
//      call. Dynamic state across all Wingmen repos and clients.
//   3. Repo layer (latest claude_code row's `context` field) — read
//      from the row that just triggered this function. Repo state at
//      the moment Claude Code wrote it.
//
// If any of these is missing or stale, the function tells the strategist
// explicitly via the system message so it refuses to speculate about
// unseen state. See `buildStalenessWarning()` and the handling of the
// empty-context case below.

// deno-lint-ignore-file no-explicit-any

import { createClient, SupabaseClient } from "jsr:@supabase/supabase-js@2";
import { STRATEGIST_PROMPT } from "./cto_strategist_prompt.ts";

// ── Config ────────────────────────────────────────────────────────────

// Model ID defaults to Haiku 4.5. Override via env var to upgrade.
const STRATEGIST_MODEL =
  Deno.env.get("STRATEGIST_MODEL") ?? "claude-haiku-4-5-20251001";
const STRATEGIST_MAX_TOKENS = Number(
  Deno.env.get("STRATEGIST_MAX_TOKENS") ?? "2000"
);

// Circuit breaker thresholds. Overridable via env vars.
const SESSION_TOKEN_HARD_CAP = Number(
  Deno.env.get("STRATEGIST_SESSION_TOKEN_HARD_CAP") ?? "60000"
);
const SESSION_USD_HARD_CAP = Number(
  Deno.env.get("STRATEGIST_SESSION_USD_HARD_CAP") ?? "5.00"
);

// Brain snapshot staleness thresholds (hours)
const BRAIN_STALE_WARN_HOURS = 4;
const BRAIN_STALE_HARD_HOURS = 8;

// Anthropic API
const ANTHROPIC_API_KEY = Deno.env.get("ANTHROPIC_API_KEY");
const ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_API_VERSION = "2023-06-01";

// Supabase
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

// ── System prompt ─────────────────────────────────────────────────────
// Imported from cto_strategist_prompt.ts, which is generated from
// cto_strategist_prompt.md by a pre-deploy script. The .md is the
// human-editable source of truth; the .ts is the bundled artifact.
// The supabase CLI's --use-api bundler follows TS imports but does NOT
// bundle arbitrary file assets, so Deno.readTextFile("./x.md") fails at
// runtime. This indirection makes the prompt a TS module, which the
// bundler happily includes.

const systemPromptText = STRATEGIST_PROMPT;

// ── Types ─────────────────────────────────────────────────────────────

interface CouncilSession {
  id: number;
  public_id: string;
  started_at: string;
  ended_at: string | null;
  ended_reason: string | null;
  max_rounds: number;
  current_round: number;
  opening_prompt: string;
  repo: string | null;
  tags: string[];
  token_count: number;
  usd_estimated: number;
  model_used: string;
  had_pushback: boolean;
  context_version: string;
}

interface CouncilRow {
  id: number;
  session_id: number;
  round: number;
  role: "musa" | "claude_code" | "claude_ai" | "system";
  message: string;
  tags: string[];
  context: Record<string, unknown>;
  created_at: string;
}

interface AnthropicUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
}

interface AnthropicResponse {
  content: Array<{ type: string; text?: string }>;
  usage: AnthropicUsage;
  stop_reason: string;
}

// ── Entry point ───────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  try {
    return await handleRequest(req);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const stack = err instanceof Error ? err.stack ?? "" : "";
    console.error(`[cto-strategist] unhandled error: ${msg}\n${stack}`);
    return jsonResponse({ error: msg }, 500);
  }
});

async function handleRequest(req: Request): Promise<Response> {
  // Health probe
  const url = new URL(req.url);
  if (url.searchParams.get("mode") === "health") {
    return jsonResponse({
      ok: true,
      model: STRATEGIST_MODEL,
      identity: "al-mushtashir",
      prompt_bytes: systemPromptText.length,
      anthropic_key_set: !!ANTHROPIC_API_KEY,
      ts: new Date().toISOString(),
    });
  }

  // Full self-test: exercises module load, Supabase client construction,
  // a real DB query, and a real (minimal) Anthropic call. Writes nothing.
  // This is the mode `scripts/deploy_function.sh` polls after a deploy to
  // catch boot-fine-broken-on-first-request failures. Cost per run is
  // ~$0.0005 on Haiku (the prompt is synthetic, not the full system prompt).
  // See council session 1 for the design rationale.
  if (url.searchParams.get("mode") === "full-self-test") {
    const assertions: string[] = [];
    const started = Date.now();
    try {
      if (!ANTHROPIC_API_KEY) {
        return jsonResponse({ ok: false, failed_at: "anthropic_key_check", error: "ANTHROPIC_API_KEY not set" }, 500);
      }
      assertions.push("anthropic_key_set");

      if (!systemPromptText || systemPromptText.length < 1000) {
        return jsonResponse({ ok: false, failed_at: "prompt_check", error: `prompt_bytes=${systemPromptText?.length ?? 0}` }, 500);
      }
      assertions.push(`prompt_bytes=${systemPromptText.length}`);

      const sb = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });
      const { error: dbErr } = await sb.from("wingmen_brain").select("id").limit(1);
      if (dbErr) {
        return jsonResponse({ ok: false, failed_at: "supabase_query", error: dbErr.message }, 500);
      }
      assertions.push("supabase_query_ok");

      const anthropicStart = Date.now();
      const probeResp = await fetch(ANTHROPIC_API_URL, {
        method: "POST",
        headers: {
          "x-api-key": ANTHROPIC_API_KEY,
          "anthropic-version": ANTHROPIC_API_VERSION,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          model: "claude-haiku-4-5-20251001",
          max_tokens: 10,
          system: "You are a deploy-gate self-test probe. Reply with exactly one word: ok",
          messages: [{ role: "user", content: "probe" }],
        }),
      });
      if (!probeResp.ok) {
        const body = await probeResp.text();
        return jsonResponse({ ok: false, failed_at: "anthropic_call", status: probeResp.status, error: body.slice(0, 300) }, 500);
      }
      const probeBody = await probeResp.json();
      const probeText = probeBody?.content?.[0]?.text ?? "";
      assertions.push("anthropic_call_ok");
      assertions.push(`anthropic_latency_ms=${Date.now() - anthropicStart}`);

      return jsonResponse({
        ok: true,
        mode: "full-self-test",
        assertions_passed: assertions,
        probe_response: probeText.slice(0, 50),
        probe_usage: probeBody?.usage ?? null,
        total_ms: Date.now() - started,
        writes_performed: 0,
        ts: new Date().toISOString(),
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return jsonResponse({ ok: false, failed_at: "unhandled_exception", error: msg, assertions_passed: assertions }, 500);
    }
  }

  // Validate request body
  if (req.method !== "POST") {
    return jsonResponse({ error: "POST required" }, 405);
  }
  const body = await req.json().catch(() => null);
  if (!body?.session_id) {
    return jsonResponse({ error: "session_id required" }, 400);
  }
  const sessionId: number = body.session_id;

  // Validate required env
  if (!ANTHROPIC_API_KEY) {
    return jsonResponse({ error: "ANTHROPIC_API_KEY not configured" }, 500);
  }

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false },
  });

  // 1. Load session
  const { data: session, error: sessionErr } = await supabase
    .from("cto_council_sessions")
    .select("*")
    .eq("id", sessionId)
    .single<CouncilSession>();
  if (sessionErr || !session) {
    return jsonResponse(
      { error: `session ${sessionId} not found: ${sessionErr?.message}` },
      404,
    );
  }

  // 2. Short-circuit if session already ended
  if (session.ended_at) {
    return jsonResponse({
      skipped: true,
      reason: "session_ended",
      ended_at: session.ended_at,
      ended_reason: session.ended_reason,
    });
  }

  // 3. Defense-in-depth circuit breaker checks (the pg trigger already
  //    checked these, but it costs nothing to verify before spending
  //    money on an Anthropic call)
  if (session.current_round >= session.max_rounds) {
    await endSession(supabase, sessionId, "max_rounds");
    return jsonResponse({ skipped: true, reason: "max_rounds_reached" });
  }
  if (session.token_count >= SESSION_TOKEN_HARD_CAP) {
    await endSession(supabase, sessionId, "circuit_breaker_tokens");
    return jsonResponse({ skipped: true, reason: "token_cap_reached" });
  }
  if (Number(session.usd_estimated) >= SESSION_USD_HARD_CAP) {
    await endSession(supabase, sessionId, "circuit_breaker_usd");
    return jsonResponse({ skipped: true, reason: "usd_cap_reached" });
  }

  // 4. Load the full thread in order
  const { data: thread, error: threadErr } = await supabase
    .from("cto_council")
    .select("id, session_id, round, role, message, tags, context, created_at")
    .eq("session_id", sessionId)
    .order("created_at", { ascending: true })
    .returns<CouncilRow[]>();
  if (threadErr) throw threadErr;
  if (!thread || thread.length === 0) {
    return jsonResponse({ skipped: true, reason: "empty_thread" });
  }

  // 5. Pull the latest claude_code row's context field. This is the
  //    repo-layer anti-staleness source — a snapshot Claude Code
  //    populated at the moment of writing.
  const latestClaudeCode = [...thread]
    .reverse()
    .find((r) => r.role === "claude_code");
  const claudeCodeContext = latestClaudeCode?.context ?? {};
  const contextIsThin = isContextThin(claudeCodeContext);

  // 6. Query wingmen_brain for the most recent snapshot (ecosystem layer)
  const { data: brain } = await supabase
    .from("wingmen_brain")
    .select("repos, clients, context_notes, sync_health, snapshot_at, job_queue")
    .order("id", { ascending: false })
    .limit(1)
    .maybeSingle();

  const brainAgeHours = brain?.snapshot_at
    ? (Date.now() - new Date(brain.snapshot_at).getTime()) / 3_600_000
    : null;

  // 7. Build the messages array from thread history.
  //    Claude Code + Musa rows become user messages; strategist rows
  //    become assistant messages; system rows are skipped (they're
  //    meta-annotations, not part of the dialogue).
  const messages: Array<{ role: "user" | "assistant"; content: string }> = [];
  for (const row of thread) {
    if (row.role === "claude_code") {
      // Prefix with the author label. Don't inline the context blob
      // here — it goes in the system prompt via its own ephemeral cache
      // block to avoid cache invalidation on every message.
      messages.push({
        role: "user",
        content: `[CLAUDE CODE — round ${row.round}]\n${row.message}`,
      });
    } else if (row.role === "musa") {
      messages.push({
        role: "user",
        content: `[MUSA — round ${row.round}]\n${row.message}`,
      });
    } else if (row.role === "claude_ai") {
      messages.push({ role: "assistant", content: row.message });
    }
    // 'system' role rows are deliberately skipped
  }

  // 8. Build the system prompt blocks. Three blocks, each with its own
  //    cache_control strategy:
  //
  //    Block A: the static system prompt — CACHED (5-minute ephemeral
  //             TTL). Stable across an entire session, so re-reads are
  //             charged at 10% of normal input rate.
  //
  //    Block B: the wingmen_brain snapshot — CACHED. Refreshes every
  //             ~4 hours via the brain_sync cron, so within a 5-minute
  //             cache window it's fixed. Within a single multi-round
  //             discussion the cache hits every time.
  //
  //    Block C: the claude_code context snapshot + session meta —
  //             NOT CACHED. Invalidates on every new claude_code row
  //             (which is what triggers each new strategist turn), so
  //             caching would be wasted.
  //
  //    The combined effect: ~75% of input tokens per turn land on the
  //    cached path after the first round. Cost math in CLAUDE_CODE
  //    discussions projected this at ~$0.017 per round on Haiku 4.5.

  const systemBlocks: any[] = [
    {
      type: "text",
      text: systemPromptText,
      cache_control: { type: "ephemeral" },
    },
    {
      type: "text",
      text: buildBrainBlock(brain, brainAgeHours),
      cache_control: { type: "ephemeral" },
    },
    {
      type: "text",
      text: buildVolatileBlock(session, claudeCodeContext, contextIsThin),
      // No cache_control — this block changes every turn
    },
  ];

  // 9. Call the Anthropic API
  const anthropicStart = Date.now();
  const anthropicBody = {
    model: STRATEGIST_MODEL,
    max_tokens: STRATEGIST_MAX_TOKENS,
    system: systemBlocks,
    messages,
  };
  const anthropicResp = await fetch(ANTHROPIC_API_URL, {
    method: "POST",
    headers: {
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": ANTHROPIC_API_VERSION,
      "content-type": "application/json",
    },
    body: JSON.stringify(anthropicBody),
  });

  if (!anthropicResp.ok) {
    const errorBody = await anthropicResp.text();
    console.error(
      `[cto-strategist] Anthropic API ${anthropicResp.status}: ${errorBody}`,
    );
    // Write a system row so the thread reflects the failure
    await supabase.from("cto_council").insert({
      session_id: sessionId,
      round: session.current_round + 1,
      role: "system",
      message: `Strategist call failed (${anthropicResp.status}). The session is paused — Musa can manually resume or escalate.`,
      tags: ["STRATEGIST_ERROR"],
      context: { anthropic_status: anthropicResp.status, error: errorBody.slice(0, 500) },
    });
    return jsonResponse(
      { error: `Anthropic ${anthropicResp.status}` },
      502,
    );
  }

  const result = (await anthropicResp.json()) as AnthropicResponse;
  const anthropicMs = Date.now() - anthropicStart;
  const responseText = result.content?.[0]?.text ?? "";
  const usage = result.usage ?? {};

  // 10. Parse tags from the response — scoped to the "## Tag" section.
  //
  // Bug history (session 2, 2026-04-12): the previous implementation used
  // /\[CONCUR\]/i.test(responseText) which matched body mentions like
  // "I cannot tag [CONCUR] until you address X." That falsely triggered
  // consensus on a session whose strategist response was pure pushback.
  // Fix: parse only the content AFTER the "## Tag" header (the canonical
  // location per the system prompt's Response Format section), and
  // additionally enforce tag precedence: any blocking tag (PUSHBACK,
  // ESCALATE, INSUFFICIENT_CONTEXT) wins over CONCUR even if both
  // somehow appear in the tag section.
  const tags = parseResponseTags(responseText);

  // 11. Compute cost estimate
  const costUsd = computeCostUsd(STRATEGIST_MODEL, usage);

  // 12. Insert the claude_ai row
  const newRound = session.current_round + 1;
  const { error: insertErr } = await supabase.from("cto_council").insert({
    session_id: sessionId,
    round: newRound,
    role: "claude_ai",
    message: responseText,
    tags,
    context: {
      model: STRATEGIST_MODEL,
      latency_ms: anthropicMs,
      brain_snapshot_age_hours: brainAgeHours,
      claude_code_context_was_thin: contextIsThin,
    },
    usage_input_tokens: usage.input_tokens ?? 0,
    usage_output_tokens: usage.output_tokens ?? 0,
    usage_cache_read_tokens: usage.cache_read_input_tokens ?? 0,
    usage_cache_write_tokens: usage.cache_creation_input_tokens ?? 0,
    usage_usd: costUsd,
  });
  if (insertErr) throw insertErr;

  // 13. Update session state.
  //     - increment current_round
  //     - add this turn's token count
  //     - add this turn's usd
  //     - flip had_pushback if the strategist raised pushback
  //     - maybe end the session if consensus/escalation/caps triggered
  const newTokenCount = session.token_count +
    (usage.input_tokens ?? 0) +
    (usage.output_tokens ?? 0) +
    (usage.cache_read_input_tokens ?? 0);
  const newUsdEstimated = Number(session.usd_estimated) + costUsd;
  const newHadPushback = session.had_pushback || tags.includes("PUSHBACK");

  // Consensus logic: CONCUR tag is only valid if (a) pushback has been
  // raised in the session, (b) we're past round 0, (c) NO blocking tag
  // is also present in this response. Blocking tags are PUSHBACK,
  // ESCALATE, and INSUFFICIENT_CONTEXT — their presence means the
  // strategist is not concurring even if CONCUR somehow also appears.
  // This enforces the "PUSHBACK wins over CONCUR" precedence documented
  // in the system prompt's Canonical Tags table.
  let endedReason: string | null = null;
  const hasBlockingTag =
    tags.includes("PUSHBACK") ||
    tags.includes("ESCALATE") ||
    tags.includes("INSUFFICIENT_CONTEXT");
  const canConsense =
    tags.includes("CONCUR") &&
    !hasBlockingTag &&
    newHadPushback &&
    newRound >= 2;

  if (canConsense) {
    endedReason = "consensus";
  } else if (tags.includes("ESCALATE")) {
    endedReason = "escalated";
  } else if (newRound >= session.max_rounds) {
    endedReason = "max_rounds";
  } else if (newUsdEstimated >= SESSION_USD_HARD_CAP) {
    endedReason = "circuit_breaker_usd";
  } else if (newTokenCount >= SESSION_TOKEN_HARD_CAP) {
    endedReason = "circuit_breaker_tokens";
  }

  // If the strategist tagged [CONCUR] but had_pushback was false, we
  // DELIBERATELY ignore the concur — that's a rubber-stamp and the
  // whole architecture is designed to prevent it. Log it for observability.
  if (tags.includes("CONCUR") && !newHadPushback) {
    console.warn(
      `[cto-strategist] session ${sessionId} round ${newRound}: ` +
        `strategist tagged CONCUR without prior PUSHBACK. Ignored — session continues.`,
    );
  }

  await supabase
    .from("cto_council_sessions")
    .update({
      current_round: newRound,
      token_count: newTokenCount,
      usd_estimated: newUsdEstimated,
      had_pushback: newHadPushback,
      ended_at: endedReason ? new Date().toISOString() : null,
      ended_reason: endedReason,
    })
    .eq("id", sessionId);

  // 14. Return the summary
  return jsonResponse({
    ok: true,
    session_id: sessionId,
    round: newRound,
    tags,
    ended_reason: endedReason,
    usage: {
      input_tokens: usage.input_tokens ?? 0,
      output_tokens: usage.output_tokens ?? 0,
      cache_read: usage.cache_read_input_tokens ?? 0,
      cache_write: usage.cache_creation_input_tokens ?? 0,
      usd: costUsd,
    },
    session_totals: {
      token_count: newTokenCount,
      usd_estimated: newUsdEstimated,
    },
    latency_ms: anthropicMs,
    claude_code_context_was_thin: contextIsThin,
    brain_snapshot_age_hours: brainAgeHours,
  });
}

// ── Helpers ───────────────────────────────────────────────────────────

/** Parse canonical tags from a strategist response.
 *
 *  The system prompt's Response Format section instructs the strategist
 *  to put exactly one tag in a "## Tag" section at the end. This parser
 *  scopes tag detection to that section so body mentions of a tag (e.g.,
 *  "I cannot tag [CONCUR] until you address X") do not match.
 *
 *  Fallback: if no "## Tag" header is found, scan the last 400 characters
 *  of the response. A forgotten section header should not defeat tag
 *  detection entirely, but we still want to avoid matching tags buried
 *  in the middle of a long response body.
 *
 *  If multiple tags appear in the scanned region, all are returned. The
 *  consensus logic in handleRequest enforces precedence (blocking tags
 *  win over CONCUR).
 *
 *  Bug history: session 2 on 2026-04-12 was falsely closed on consensus
 *  because the old parser used /\[CONCUR\]/i.test(responseText) which
 *  matched a refusal phrase in the response body.
 */
function parseResponseTags(text: string): string[] {
  // Find the last "## Tag" header (case-insensitive, tolerant of
  // extra whitespace). We take the LAST occurrence because a well-formed
  // response has it at the end; this also handles responses that mention
  // "## Tag" earlier as literal text.
  const headerRegex = /##\s*Tag\s*\n/gi;
  let headerMatch: RegExpExecArray | null = null;
  let lastMatch: RegExpExecArray | null = null;
  while ((headerMatch = headerRegex.exec(text)) !== null) {
    lastMatch = headerMatch;
  }

  let scanText: string;
  if (lastMatch) {
    scanText = text.slice(lastMatch.index + lastMatch[0].length);
  } else {
    // No section header — fall back to the tail of the response.
    scanText = text.slice(-400);
  }

  const tags: string[] = [];
  if (/\[PUSHBACK\]/i.test(scanText)) tags.push("PUSHBACK");
  if (/\[ESCALATE\]/i.test(scanText)) tags.push("ESCALATE");
  if (/\[INSUFFICIENT_CONTEXT\]/i.test(scanText)) tags.push("INSUFFICIENT_CONTEXT");
  if (/\[SYNTHESIS\]/i.test(scanText)) tags.push("SYNTHESIS");
  if (/\[SPEC_APPROVED\]/i.test(scanText)) tags.push("SPEC_APPROVED");
  if (/\[CONCUR\]/i.test(scanText)) tags.push("CONCUR");
  return tags;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function endSession(
  supabase: SupabaseClient,
  sessionId: number,
  reason: string,
) {
  await supabase
    .from("cto_council_sessions")
    .update({
      ended_at: new Date().toISOString(),
      ended_reason: reason,
    })
    .eq("id", sessionId);
}

/** Determine if the claude_code row's context field is too thin to
 *  reason from. A "thin" context is missing the repo name or lacks
 *  any repo-state signals (commits, files, STATUS.md). The strategist
 *  is instructed (in its system prompt) to request more context rather
 *  than speculate when this is true. */
function isContextThin(ctx: Record<string, unknown>): boolean {
  if (!ctx || Object.keys(ctx).length === 0) return true;
  const hasRepo = typeof ctx.repo === "string" && (ctx.repo as string).length > 0;
  const hasCommits =
    Array.isArray(ctx.recent_commits) && (ctx.recent_commits as unknown[]).length > 0;
  const hasStatus = typeof ctx.STATUS_md === "string" && (ctx.STATUS_md as string).length > 20;
  const hasBranch = typeof ctx.branch === "string";
  // Need at least: a repo name, AND one of (commits, status, branch + changed files)
  return !(hasRepo && (hasCommits || hasStatus || hasBranch));
}

/** Build the brain snapshot system block, including a staleness
 *  warning if the snapshot is older than 4h (warn) or 8h (hard). */
function buildBrainBlock(brain: any, ageHours: number | null): string {
  let header = "# Wingmen Brain Snapshot";
  if (ageHours !== null) {
    if (ageHours > BRAIN_STALE_HARD_HOURS) {
      header +=
        `\n\n⚠️ **STALE CONTEXT — ${Math.round(ageHours)}h old.** ` +
        "Any claim derived from this snapshot must be prefixed with " +
        "`[UNVERIFIED — brain stale]`. If you need a fresh fact about " +
        "a repo or client and the brain is stale, ask Claude Code to " +
        "confirm it in the next turn rather than speculating.";
    } else if (ageHours > BRAIN_STALE_WARN_HOURS) {
      header += `\n\nℹ️ Brain snapshot is ${Math.round(ageHours)}h old.`;
    }
  } else {
    header += "\n\n⚠️ No brain snapshot found. Treat all ecosystem-level " +
      "claims as unverified until Claude Code confirms them.";
  }
  const brainJson = JSON.stringify(
    {
      repos: brain?.repos ?? null,
      clients: brain?.clients ?? null,
      sync_health: brain?.sync_health ?? "unknown",
      snapshot_at: brain?.snapshot_at ?? null,
      context_notes: brain?.context_notes ?? null,
    },
    null,
    2,
  );
  return `${header}\n\n\`\`\`json\n${brainJson}\n\`\`\``;
}

/** Build the volatile (non-cached) system block containing: the
 *  session meta (round counter, pushback state, budget remaining),
 *  the latest claude_code context field (repo state at write-time),
 *  and explicit behavioral rules for this specific round. */
function buildVolatileBlock(
  session: CouncilSession,
  claudeCodeContext: Record<string, unknown>,
  contextIsThin: boolean,
): string {
  const nextRound = session.current_round + 1;
  const remainingRounds = session.max_rounds - nextRound;

  let roundGuidance = "";
  if (nextRound === 1) {
    roundGuidance =
      "**Round 1 rule:** This is your first response. Default to " +
      "skepticism. Do NOT tag [CONCUR] — a round-1 concurrence is a " +
      "failure mode of this system. If you have no concern to raise, " +
      "ask a question that forces Claude Code to justify one of its " +
      "design choices.";
  } else if (!session.had_pushback) {
    roundGuidance =
      "**Critical:** No real disagreement has been raised yet in this " +
      "session. Your next response MUST include at least one [PUSHBACK] " +
      "tag if there is any substantive concern. If there is genuinely " +
      "no concern after careful review, ask a clarifying question " +
      "instead — do NOT tag [CONCUR] on a thread with no prior pushback.";
  } else if (remainingRounds <= 1) {
    roundGuidance =
      "**Final rounds.** Either synthesize a decision ([CONCUR] if the " +
      "strategist concurs with Claude Code's latest position, or " +
      "[ESCALATE] if you genuinely cannot resolve the disagreement — " +
      "Musa will rule). Do not introduce new concerns at this stage " +
      "unless they are blocking.";
  }

  const contextWarning = contextIsThin
    ? "\n\n## ⚠️ CONTEXT IS THIN\n\n" +
      "The latest claude_code row's `context` field is missing or incomplete " +
      "(no repo/commits/STATUS.md). **You cannot reason about code you have " +
      "not been shown.** Your response MUST tag [INSUFFICIENT_CONTEXT] and " +
      "ask Claude Code for specific facts before forming a strategic " +
      "opinion. Do not speculate about the repo state based on what you " +
      "assume is there — that's the failure mode this architecture was " +
      "built to prevent."
    : "";

  const contextBlock = contextIsThin
    ? ""
    : `\n\n## Claude Code Repo Context (at moment of latest claude_code message)\n\n\`\`\`json\n${JSON.stringify(claudeCodeContext, null, 2)}\n\`\`\``;

  return `# Session State (round ${nextRound} of ${session.max_rounds})

- session_id: ${session.id}
- public_id: ${session.public_id}
- opening_prompt: ${session.opening_prompt}
- repo: ${session.repo ?? "cross-repo / strategic-only"}
- had_pushback_yet: ${session.had_pushback}
- token_count so far: ${session.token_count}
- usd_estimated so far: ${Number(session.usd_estimated).toFixed(4)}
- rounds remaining: ${remainingRounds}
- model: ${STRATEGIST_MODEL}

${roundGuidance}${contextWarning}${contextBlock}
`;
}

/** Estimate the USD cost of an Anthropic API call from the usage stats.
 *  Prices as of 2026-04, per 1M tokens. Update if Anthropic pricing
 *  changes. */
function computeCostUsd(model: string, usage: AnthropicUsage): number {
  const pricing: Record<
    string,
    { input: number; output: number; cacheWrite: number; cacheRead: number }
  > = {
    "claude-haiku-4-5-20251001": {
      input: 0.80,
      output: 4.00,
      cacheWrite: 1.00,
      cacheRead: 0.08,
    },
    "claude-sonnet-4-6": {
      input: 3.00,
      output: 15.00,
      cacheWrite: 3.75,
      cacheRead: 0.30,
    },
    "claude-opus-4-6": {
      input: 15.00,
      output: 75.00,
      cacheWrite: 18.75,
      cacheRead: 1.50,
    },
  };

  // Conservative fallback: assume Haiku pricing if model unknown
  const p = pricing[model] ?? pricing["claude-haiku-4-5-20251001"];
  const input = (usage.input_tokens ?? 0) / 1_000_000;
  const output = (usage.output_tokens ?? 0) / 1_000_000;
  const cacheWrite = (usage.cache_creation_input_tokens ?? 0) / 1_000_000;
  const cacheRead = (usage.cache_read_input_tokens ?? 0) / 1_000_000;

  return (
    input * p.input +
    output * p.output +
    cacheWrite * p.cacheWrite +
    cacheRead * p.cacheRead
  );
}

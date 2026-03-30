# Persona-Aware Routing — Design Spec

## Problem

The agent split works well for Musa (admin/developer), but non-technical clients get developer-facing output: file paths, severity ratings, raw JSON reports. Clients also get routed directly to the fixer agent when they say "fix X", which can be dangerous without confirming what they actually mean.

## Solution

Make the router and output formatting persona-aware. Two changes:
1. Router applies role-specific rules (clients can't bypass brainstorm to go directly to fixer)
2. Audit results get translated to plain language for non-admin users, with permission requested before auto-fixing

## Router Changes

`build_router_prompt` receives `role` in addition to existing parameters.

Role-specific routing rules:

| Intent | Admin | Client |
|--------|-------|--------|
| chat | as-is | as-is |
| build | as-is | as-is |
| data | as-is | as-is |
| audit | as-is (raw report) | allowed, but output translated to plain language |
| fix | direct to fixer | reclassified as "chat" (brainstorm confirms intent first) |

## Client Audit Flow (Option C — Permission Before Fixing)

```
Client: "my site looks weird on mobile"
  |
Router: intent=audit, role=client
  |
Bot: "Checking your site now, I'll have a look..."
  |
Auditor runs (same technical agent — unchanged)
  |
Python separates: high-confidence fixes vs needs-decision
  |
Brainstorm agent translates report to plain language:
  "I found a few things:

  I can fix these right now:
  - There's a typo on your menu page
  - The spacing on your header looks off on phones

  And I'd need your input on:
  - Your logo looks blurry on larger screens

  Should I go ahead with the easy fixes?"
  |
Client: "yes please"
  |
Fixer runs -> push -> deploy
  |
Bot: "Done! Take a look and let me know if it's better."
```

## Admin Flow — Unchanged

Admin audit continues to: run auditor -> auto-fix high-confidence -> report raw results with file paths and severity. No permission step, no translation.

## Implementation Details

### `agents/router.py` — Add role parameter

`build_router_prompt(user_msg, repos, history, role)` adds a rule block for clients:

```
If the user role is "client":
- Never classify as "fix" — use "chat" instead (let the advisor confirm intent)
- "audit" is allowed but the output will be reformatted
```

`parse_router_response` unchanged — it just parses JSON.

### `cto_bot.py:_run_audit()` — Branch on role

After auditor returns and issues are classified:

For admin (`is_admin(user)`):
- Current behavior: auto-fix high-confidence, send raw report

For client (not admin):
- Store pending fixes in a dict keyed by chat_id: `_pending_fixes[chat_id] = {"issues": high_conf, "repo": repo_name}`
- Call brainstorm agent to translate the full report to plain language, ending with "Should I go ahead with the easy fixes?"
- When client replies "yes" / "go ahead" / confirms, the router classifies as "chat", brainstorm recognizes the confirmation context, and Python checks `_pending_fixes[chat_id]` to execute the fixes

### `cto_bot.py:_process_message()` — Handle pending fixes

Before routing, check if `_pending_fixes.get(chat_id)` exists and the message looks like confirmation:
- Simple heuristic: message is short (<50 chars) and contains affirmative words (yes, go ahead, sure, ok, do it, please)
- If confirmed: execute pending fixes, clear `_pending_fixes[chat_id]`, send friendly completion message
- If denied or unrelated: clear `_pending_fixes[chat_id]`, route normally

### `cto_bot.py` — New `_translate_audit_for_client()` function

Takes the auditor's issues list and calls brainstorm agent with a translation prompt:

```
You are explaining a website audit to a non-technical client.
Translate these findings into plain, friendly language.
No file paths, no code, no severity ratings, no technical jargon.

Group into:
1. "I can fix these right now" (high confidence fixes)
2. "I'd need your input on" (needs decision)

End with: "Should I go ahead with the easy fixes?"

Issues: {json_issues}
```

This reuses the brainstorm agent (no new agent). The client persona is already warm and non-technical.

### Progress messages — Persona-aware

The `_keep_typing` progress message changes based on role:
- Admin: "Working on it — using tools to check things. Hang tight..."
- Client: "Checking your site now, one moment..."

## Files Changed

- `agents/router.py` — add `role` parameter to `build_router_prompt`
- `cto_bot.py` — modify `_route_message`, `_run_audit`, `_process_message`; add `_translate_audit_for_client`, `_pending_fixes` dict, confirmation detection
- `tests/test_agents.py` — update router tests for role parameter

## Files Unchanged

- `agents/brainstorm.py` — already persona-aware
- `agents/auditor.py` — always technical
- `agents/fixer.py` — always surgical
- `wingmen_orch.py` — build pipeline unchanged
- Database schema — no changes

## Success Criteria

1. Client never sees file paths, severity ratings, or technical terms in audit reports
2. Client is asked for permission before any auto-fixes happen
3. Client "yes" triggers the pending fixes and sends a friendly completion message
4. Admin flow is completely unchanged — no regression
5. Router correctly reclassifies client "fix" intent as "chat"
6. Progress messages match the user's persona

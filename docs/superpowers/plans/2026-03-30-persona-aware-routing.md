# Persona-Aware Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the router and audit output persona-aware so clients get plain-language reports with permission before fixes, while admin flow stays unchanged.

**Architecture:** Router gets `role` parameter to apply client-specific rules (fix→chat reclassification). `_run_audit` branches on `is_admin()`: admin gets raw report, client gets brainstorm-translated output with pending fix confirmation. A `_pending_fixes` dict holds fixes awaiting client approval.

**Tech Stack:** Python 3.9, existing agents module, Claude CLI

**Spec:** `docs/superpowers/specs/2026-03-30-persona-aware-routing-design.md`

---

## File Structure

```
agents/router.py       — Modified: add role parameter to build_router_prompt
cto_bot.py             — Modified: _route_message, _run_audit, _process_message, _keep_typing; add _pending_fixes, _translate_audit_for_client, _execute_pending_fixes
tests/test_agents.py   — Modified: update router tests, add persona-aware tests
```

---

### Task 1: Add role parameter to Router Agent

**Files:**
- Modify: `agents/router.py:11` (`build_router_prompt` signature)
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents.py`:

```python
def test_router_client_fix_becomes_chat():
    """Client 'fix' intent should include rule to reclassify as 'chat'."""
    prompt = build_router_prompt("fix the homepage", ["dookana"], [], role="client")
    assert "client" in prompt.lower()
    assert "chat" in prompt  # rule says to use chat instead of fix


def test_router_admin_allows_fix():
    """Admin should have no restriction on fix intent."""
    prompt = build_router_prompt("fix the homepage", ["dookana"], [], role="admin")
    assert "never classify as" not in prompt.lower()


def test_router_default_role_is_admin():
    """Omitting role should behave like admin (backward compatible)."""
    prompt = build_router_prompt("fix the homepage", ["dookana"], [])
    assert "never classify as" not in prompt.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "test_router_client_fix or test_router_admin_allows or test_router_default_role" -v`
Expected: FAIL — `build_router_prompt() got an unexpected keyword argument 'role'`

- [ ] **Step 3: Update `build_router_prompt` in `agents/router.py`**

Change the function signature and add a role-specific rules block:

```python
def build_router_prompt(user_msg: str, repos: list[str], history: list[dict], *, role: str = "admin") -> str:
    """Build the Router Agent prompt with minimal context."""
    repo_list = ", ".join(repos)

    context_lines = ""
    recent = history[-3:]
    for msg in recent[:-1]:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:200]
        context_lines += f"{role_label}: {content}\n"

    role_rules = ""
    if role == "client":
        role_rules = """
IMPORTANT — this user is a non-technical client:
- Never classify as "fix" — use "chat" instead (the advisor will confirm intent first)
- "audit" is allowed
"""

    return f"""You are a message classifier. Given a user message and recent context, return a JSON object.

Intents:
- "chat": questions, brainstorming, discussion, status checks, planning, follow-ups to conversation
- "audit": requests to check, crawl, verify, test, or review live pages or code quality
- "fix": explicit request to fix a specific known issue (e.g. "fix the duplicate card on homepage")
- "build": request to create a new feature or page (will go through build pipeline)
- "data": request to update data (prices, text, toggles) in the database
{role_rules}
Repos: {repo_list}

Recent conversation:
{context_lines}
Current message: {user_msg}

Return ONLY valid JSON: {{"intent": "...", "repo": "...", "detail": "..."}}
"repo" should be null if no specific repo is mentioned or inferrable.
"detail" is a brief summary of what the user wants."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/test_agents.py -k "router" -v`
Expected: All 8 router tests PASS

- [ ] **Step 5: Update `_route_message` in `cto_bot.py` to pass role**

In `cto_bot.py`, change `_route_message` (line ~2044):

```python
async def _route_message(user_msg: str, user: dict, history: list[dict]) -> dict:
    """Use Router Agent to classify message intent. Falls back to 'chat' on failure."""
    role = "admin" if is_admin(user) else "client"
    prompt = build_router_prompt(user_msg, user["repos"], history, role=role)
    raw = await _call_claude(prompt, timeout=30)
    if not raw:
        return {"intent": "chat", "repo": None, "detail": user_msg}
    result = parse_router_response(raw)
    logger.info(f"Router: {user['name']} -> {result['intent']} (repo={result.get('repo')}, role={role})")
    return result
```

- [ ] **Step 6: Verify syntax**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python3 -m py_compile cto_bot.py`
Expected: No output (clean)

- [ ] **Step 7: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add agents/router.py cto_bot.py tests/test_agents.py
git commit -m "feat: add role parameter to Router Agent for persona-aware routing"
```

---

### Task 2: Add pending fixes and client audit flow

**Files:**
- Modify: `cto_bot.py` (add `_pending_fixes`, `_translate_audit_for_client`, modify `_run_audit`)

- [ ] **Step 1: Add `_pending_fixes` dict**

In `cto_bot.py`, after the `_active_repo` dict definition (around line 810), add:

```python
# Pending audit fixes awaiting client confirmation {chat_id: {"issues": [...], "repo": str}}
_pending_fixes: dict[str, dict] = {}
```

- [ ] **Step 2: Add `_translate_audit_for_client` function**

In `cto_bot.py`, after `_run_fix` (around line 2170), add:

```python
async def _translate_audit_for_client(issues: list[dict], repo_name: str) -> str:
    """Translate a technical audit report into plain client-friendly language."""
    high_conf = [i for i in issues if i.get("fix_confidence") == "high"]
    needs_decision = [i for i in issues if i.get("fix_confidence") != "high"]

    prompt = f"""You are a friendly project advisor explaining website issues to a non-technical client.
Translate these findings into plain, warm language. NO file paths, NO code, NO severity ratings, NO technical jargon.

Group into two sections:
1. "I can fix these right now" — list the easy fixes in simple terms
2. "I'd like your input on" — list items that need their decision

End with exactly: "Should I go ahead with the easy fixes?"

Easy fixes ({len(high_conf)}):
"""
    for i in high_conf:
        prompt += f"- {i['description']}\n"

    prompt += f"\nNeeds input ({len(needs_decision)}):\n"
    for i in needs_decision:
        prompt += f"- {i['description']}"
        if i.get("suggested_fix"):
            prompt += f" (suggestion: {i['suggested_fix']})"
        prompt += "\n"

    if not high_conf and not needs_decision:
        return f"I checked your site ({repo_name}) and everything looks good!"

    if not high_conf:
        prompt = prompt.replace("Should I go ahead with the easy fixes?", "Let me know which of these you'd like me to look into.")

    result = await _call_claude(prompt, timeout=60)
    return result or "I found some things on your site — let me put together a summary for you."
```

- [ ] **Step 3: Modify `_run_audit` to branch on persona**

Replace the current `_run_audit` function body from the `await update.message.reply_text(f"Auditing` line through the end of the function. The new version:

```python
async def _run_audit(update: Update, user: dict, chat_id: str, repo_name: str, detail: str) -> str:
    """Run Auditor Agent, then handle results based on persona."""
    try:
        config = context_loader.get_repo_config(repo_name)
    except ValueError:
        return f"I don't have a repo called '{repo_name}' configured."

    deploy_url = config.get("deploy_url", "")
    repo_path = os.path.expanduser(config.get("local_path", ""))

    if not deploy_url:
        return f"No deploy URL configured for {repo_name}. Can't crawl pages without it."

    # Get file tree for route list
    ctx_block = await _load_repo_context_block(repo_name)
    file_tree = ""
    if "--- FILES" in ctx_block:
        start = ctx_block.index("--- FILES")
        rest = ctx_block[start + 10:]
        end_offset = rest.find("\n---")
        if end_offset >= 0:
            file_tree = ctx_block[start:start + 10 + end_offset].strip()
        else:
            file_tree = ctx_block[start:].strip()

    # Persona-aware progress message
    if is_admin(user):
        await update.message.reply_text(f"Auditing {repo_name} — crawling all pages. This will take a few minutes...")
    else:
        await update.message.reply_text("Checking your site now, one moment...")

    prompt = build_auditor_prompt(
        deploy_url=deploy_url,
        repo_path=repo_path,
        file_tree=file_tree,
        detail=detail,
    )

    raw = await _call_claude(prompt, tools="WebFetch,WebSearch,Read,Glob,Grep,Bash", timeout=600)
    if not raw:
        if is_admin(user):
            return "Audit failed — Claude didn't return results. Try again?"
        else:
            return "I had trouble checking your site. Want me to try again?"

    issues, summary = parse_auditor_response(raw)
    high_conf = [i for i in issues if i.get("fix_confidence") == "high"]
    needs_decision = [i for i in issues if i.get("fix_confidence") != "high"]

    # ── ADMIN FLOW: auto-fix and raw report ──
    if is_admin(user):
        fix_results = []
        if high_conf:
            await update.message.reply_text(f"Found {len(issues)} issue(s). Auto-fixing {len(high_conf)} obvious one(s)...")
            for issue in high_conf:
                fix_reply = await _run_fix(repo_name, issue)
                fix_results.append(f"- {issue['description']}: {fix_reply}")
            if fix_results:
                push_result = await _call_claude(
                    f"Run these commands:\ncd {repo_path} && git push origin main 2>&1 | tail -3",
                    tools="Bash",
                    timeout=60,
                )
                logger.info(f"Batch push for {repo_name}: {push_result[:100]}")

        parts = [f"Audit complete for {repo_name} — {len(issues)} issue(s) found.\n"]
        if fix_results:
            parts.append(f"Fixed {len(fix_results)} automatically:")
            parts.extend(fix_results)
        if needs_decision:
            parts.append(f"\n{len(needs_decision)} need your call:")
            for i in needs_decision:
                sev = {"high": "!!!", "medium": "!!", "low": "!"}.get(i.get("severity", ""), "?")
                parts.append(f"{sev} {i['description']}")
                if i.get("suggested_fix"):
                    parts.append(f"   Suggestion: {i['suggested_fix']}")
        if not issues:
            parts = ["All pages look good — no issues found."]
        return "\n".join(parts)

    # ── CLIENT FLOW: translate and ask permission ──
    if high_conf:
        _pending_fixes[chat_id] = {"issues": high_conf, "repo": repo_name}

    translated = await _translate_audit_for_client(issues, repo_name)
    return translated
```

- [ ] **Step 4: Verify syntax**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python3 -m py_compile cto_bot.py`
Expected: No output (clean)

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add cto_bot.py
git commit -m "feat: persona-aware audit flow with client translation and pending fixes"
```

---

### Task 3: Handle client fix confirmation in `_process_message`

**Files:**
- Modify: `cto_bot.py:_process_message` (add confirmation check before routing)

- [ ] **Step 1: Add `_execute_pending_fixes` function**

In `cto_bot.py`, after `_translate_audit_for_client`, add:

```python
_CONFIRM_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "do it", "please", "proceed", "fix it", "go for it", "yea"}


async def _execute_pending_fixes(update: Update, user: dict, chat_id: str) -> str:
    """Execute pending audit fixes for a client and return a friendly summary."""
    pending = _pending_fixes.pop(chat_id, None)
    if not pending:
        return ""

    issues = pending["issues"]
    repo_name = pending["repo"]

    try:
        config = context_loader.get_repo_config(repo_name)
    except ValueError:
        return "I couldn't find the project to fix. Let me know if you'd like to try again."

    repo_path = os.path.expanduser(config.get("local_path", ""))

    await update.message.reply_text(f"On it! Fixing {len(issues)} thing{'s' if len(issues) != 1 else ''} now...")

    fixed_count = 0
    for issue in issues:
        result = await _run_fix(repo_name, issue)
        if not result.startswith("SKIP"):
            fixed_count += 1

    # Batch push
    if fixed_count > 0:
        await _call_claude(
            f"Run these commands:\ncd {repo_path} && git push origin main 2>&1 | tail -3",
            tools="Bash",
            timeout=60,
        )

    if fixed_count == len(issues):
        return f"All done! I've fixed all {fixed_count} issue{'s' if fixed_count != 1 else ''}. Take a look at your site and let me know if it looks better."
    elif fixed_count > 0:
        return f"I fixed {fixed_count} out of {len(issues)}. A couple were trickier than expected — I'll flag those for the team. Check your site and let me know!"
    else:
        return "I ran into some trouble with the fixes. Let me flag this for the team to look at."
```

- [ ] **Step 2: Add confirmation check at the top of `_process_message`**

In `_process_message`, right after `await save_message(chat_id, "user", user_msg)` and before `async with _chat_semaphore:`, add:

```python
    # Check for pending fix confirmation (client flow)
    if chat_id in _pending_fixes and not is_admin(user):
        msg_lower = user_msg.lower().strip().rstrip("!.")
        if any(word in msg_lower for word in _CONFIRM_WORDS):
            async with _chat_semaphore:
                try:
                    await update.message.chat.send_action("typing")
                    reply = await _execute_pending_fixes(update, user, chat_id)
                    await save_message(chat_id, "assistant", reply)
                    await _send_reply(update, reply)
                except Exception as e:
                    logger.error(f"Pending fix error for {user['name']}: {e}")
                    try:
                        await update.message.reply_text("Something went wrong with the fixes. Let me try again later.")
                    except Exception:
                        pass
                return
        else:
            # Client said something other than confirmation — clear pending and route normally
            _pending_fixes.pop(chat_id, None)
```

- [ ] **Step 3: Verify syntax**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python3 -m py_compile cto_bot.py`
Expected: No output (clean)

- [ ] **Step 4: Run all tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python -m pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add cto_bot.py
git commit -m "feat: handle client fix confirmation with pending fixes flow"
```

---

### Task 4: Persona-aware progress messages and restart

**Files:**
- Modify: `cto_bot.py` (`_keep_typing` in `_process_message`)

- [ ] **Step 1: Make progress message persona-aware**

In `_process_message`, update the `_keep_typing` function (around line 2260) to use the user's role:

```python
            async def _keep_typing():
                notified = False
                progress_msg = "Checking on that for you, one moment..." if not is_admin(user) else "Working on it — using tools to check things. Hang tight..."
                try:
                    elapsed = 0
                    while True:
                        await asyncio.sleep(8)
                        elapsed += 8
                        await update.message.chat.send_action("typing")
                        if not notified and elapsed >= 30:
                            notified = True
                            try:
                                await update.message.reply_text(progress_msg)
                            except Exception:
                                pass
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
```

- [ ] **Step 2: Verify syntax and tests**

Run: `cd /Users/sheikhmusa/wingmen/orchestrator && .venv/bin/python3 -m py_compile cto_bot.py && .venv/bin/python -m pytest tests/ -q`
Expected: Clean compile, all tests pass

- [ ] **Step 3: Commit**

```bash
cd /Users/sheikhmusa/wingmen/orchestrator
git add cto_bot.py
git commit -m "feat: persona-aware progress messages for client vs admin"
```

- [ ] **Step 4: Restart bot**

```bash
launchctl unload ~/Library/LaunchAgents/dev.wingmen.ctobot.plist && sleep 2 && launchctl load ~/Library/LaunchAgents/dev.wingmen.ctobot.plist
```

- [ ] **Step 5: Verify bot started**

```bash
sleep 8 && tail -5 /Users/sheikhmusa/wingmen/orchestrator/logs/cto_bot.log
```

Expected: "Wingmen CTO Bot starting (long-polling mode)..." and "Whisper model pre-loaded"

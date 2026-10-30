# DeepSeek console: one-time login (Musa, over Screen Sharing)

1. **Run** (on the Mini, over Screen Sharing so a real window can open — not over SSH/tmux): `cd ~/wingmen/orchestrator && python3 scripts/deepseek_console.py login --headed`
2. **What you'll see**: a Chromium window opens on the DeepSeek API Keys page. Sign in by hand (your normal DeepSeek login), wait until the API Keys page is actually showing, then switch back to the terminal and press Enter.
3. **Confirm it persisted**: the script itself prints `Login saved to the persistent profile. Future runs can be headless.` and exits 0 — if it instead says the session still looks logged out, just rerun step 1. (I separately re-verify headlessly with `deepseek_console.py list-clients` before relying on it for anything live.)

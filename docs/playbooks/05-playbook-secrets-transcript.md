# PB-05 — Secrets & transcript hygiene
`version:` 2  ·  `owner:` cc-quality  ·  `last-reviewed:` 2026-11-01  ·  `risk:` P1

### 1. WHEN THIS FIRES
You are about to run a command that could surface a secret: reading/echoing a token
or DSN, dumping env, connecting with a DSN on the command line, or reviewing code
that handles secrets. (This playbook mostly DOCUMENTS the contract the existing
hooks already enforce — it's 🟢, included so a lane knows WHY the guard refused it.)

### 2. THE FAILURE IT PREVENTS
- **A real secret rendered into the transcript/tool-output** (then cached/indexed by
  anything that ingests transcripts). During review, fake or hash the value — never
  display real secret output. (CLAUDE.md; `never-fetch-real-secrets-during-review`.)
- **A DSN passed as a psql positional arg** → the full connection string (with
  password) lands in the command line + process list. Use `PG*` env vars parsed from
  the DSN instead.
- **An env dump with no hash/length sink** → bulk secret exposure.

### 3. STEPS
1. **Never display a real secret** — fake it, or emit `sha256[:12]` / a length, never
   the value. **[hook: secrets_output_scanner (post)] + [manual]**
2. **No DSN as a positional arg** — parse the DSN → `PGHOST/PGUSER/PGPASSWORD/…` env,
   then `psql -X -Aqt -c …` with no connstring on the line. **[hook:
   secrets_transcript_guard (pre)]** *(Blocks: connstring-in-cmdline.)*
3. **Env dump only with a hash/length sink** — `env | … | sha256`/count, never raw.
   **[hook: secrets_transcript_guard]**
4. **Known false-positive to work around, not defeat:** the guard also trips on DDL
   keywords (`CREATE FUNCTION`, `GRANT`, `REVOKE`, the literal "DDL") appearing in an
   `echo`/comment string of an otherwise read-only command — drop the DDL-looking
   words from the echo text; do NOT restructure to evade the real secret checks.
   **[manual]**
5. **Never pull a client credential out of the store it lives in** (the 30 Oct
   WooCommerce leak): no `SELECT *` on `wp_*` tables, no `option_name LIKE '…%'`
   sweeps, no `mysqldump`/`pg_dump`/`COPY … TO STDOUT`/`wp db export` of a client
   site database, no `cat wp-config.php`, no `firebase functions:secrets:access`
   left unredirected. A narrow, explicitly-named NON-secret option is still fine
   (`SELECT option_value FROM wp_options WHERE option_name = 'woocommerce_currency'`).
   To move a credential, pipe it into the vault so it is never displayed:
   `… | scripts/put_client_secret.py NAME --reason 'why'` (or `| sha256sum` /
   `| wc -c` when you only need to compare or measure).
   **[hook: credential_store_guard (pre)]**

### 4. ENFORCING HOOK
- **Hooks (EXISTING, 🟢):** `scripts/hooks/secrets_transcript_guard.py` (PreToolUse —
  refuses DSN-as-arg / unhashed-env-dump, and is also the DDL `--gate` enforcer),
  `scripts/hooks/secrets_output_scanner.py` (PostToolUse — scans tool output for
  secret shapes), `scripts/hooks/secret_shape_patterns.py` (shared patterns),
  `scripts/secret_hash_sweep.py`. Registered GLOBALLY (`~/.claude/settings.json`) →
  fire in every session, fleet-wide.
- **New layer (2026-11-01, bus #87265):** `scripts/hooks/credential_store_guard.py`
  (PreToolUse) refuses *client-store* reads — the WordPress/WooCommerce options and
  users tables, `wp-config.php`, non-dotfile `.env` files, store settings exports,
  token/netrc caches, and secret-reading CLIs. It deliberately does NOT re-implement
  this playbook's file/inline-literal rules: `secrets_transcript_guard` owns those.
  `scripts/put_client_secret.py` is the sanctioned way through: value in via stdin /
  `--from-env` / `--from-file`, out to the vault, and the only thing printed is
  `sha256[:8]` + length.

- **A resume is an export (2026-11-01, bus #87276):** `claude --resume <id>` /
  `--continue` replays the WHOLE prior conversation to this run's provider on every
  turn. Switching a lane's token or model to a DIFFERENT provider and resuming is
  therefore a data export, not a convenience (31 Oct: a DeepSeek transcript resumed
  on an Anthropic model). `scripts/lib/session_provider_guard.py` (called by
  `launch_dangerous_cc.sh` — and so by `launch_lane_as.sh` and `switch_lane_token.sh`
  — plus `boot_orch.sh`, `boot_mirror.sh`, `boot_nazim.sh`) refuses the resume when
  the provider changed and the transcript carries any secret-shaped value, and boots
  FRESH with a handoff note instead. A changed provider with a CLEAN transcript
  resumes (the decision records the transcript's `sha256` prefix). Measured: 12 of 25
  real fleet transcripts carry such a value, so in practice a provider switch usually
  means a fresh start — scrub first with `scripts/secret_hash_sweep.py` if you need
  the old conversation back. **[hook: launch-path guard]**

- **What it CANNOT enforce (residual — keep this list honest):** a secret a reviewer
  chooses to paste into a *narrative* (not tool output) — human discipline; the DDL
  false-positive above; a store read whose credential row is not identifiable from
  the command text (a `SELECT option_value` filtered by a numeric option_id, say);
  **cross-stage dataflow** — `echo wp-config.php | xargs cat` puts the store name in
  one pipeline stage and the printing verb in the next, so neither stage matches
  (pinned as a known-allow test); and **Rule 2 recognises a client SITE by
  WordPress markers**, so a non-WP store's bare table names (`select * from
  stripe_events` against an unnamed DSN) are not refused — its DSN *env var*
  (`$STRIPE_DSN`) is, and Rule 1 covers the platform's files. The guard stops the
  careless 30 Oct dump and its obvious variants with near-zero false positives
  (measured: 0 new denials across 36,931 real fleet commands after the cc-quality
  hardening); it is not bypass-proof against a shell-literate agent. The
  hash-matched transcript sweep (`scripts/secret_hash_sweep.py`) is the backstop
  for all of the above.
  **Resume guard, interactive picker (cc-quality 87538 F2):** a BARE
  `claude --resume` (no id) opens a picker, and the guard can only judge the
  NEWEST transcript in that directory — the operator may arrow to an older,
  cross-provider, dirty one and resume it unjudged. Every automated path
  (`--continue`, an id-bearing `--resume`, the lane/boot scripts) is judged
  correctly, so this is an interactive edge only: **when picking a session by
  hand, pick from the directory you are already in, and scrub first with
  `scripts/secret_hash_sweep.py` if you are switching providers.**


### 5. DISCOVERY
Index row; `secrets_transcript_guard`/`secrets_output_scanner` refuse messages cite
`PB-05`; CLAUDE.md secret rule → `see PB-05`.

### 6. PROVENANCE
`never-fetch-real-secrets-during-review`, CLAUDE.md substrate/security rules,
`security-hook-regex-adversarial-probe`, `rule-g-heredoc-and-env-assignment-bypass`.

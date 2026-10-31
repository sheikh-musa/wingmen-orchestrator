#!/usr/bin/env python3
"""PreToolUse guard: an agent must not pull a CLIENT CREDENTIAL out of the store
it lives in and into its own context (orch-console bus #87265/#87268/#87276, P0,
Musa op 36795; the 30 Oct Modesty Hut leak).

What went wrong: a body reasoned about a client's WordPress/WooCommerce store by
dumping the `wp_options` rows whose names matched `wc_facebook_*`. The secret
came back as ordinary SELECT output, landed in the transcript, and from then on
was re-sent to the model provider every turn. Redaction is cleanup; this guard is
prevention — the value never reaches a tool result at all.

WHAT THIS GUARD OWNS (and what it deliberately does NOT):

  Rule 1 — client-store FILES that carry the whole store's credentials:
      wp-config*.php, *.env files that are not dotfiles (client.env, woo.env),
      client-store config/export JSON (woocommerce|wordpress|wp-|shopify|stripe|
      magento names with config/settings/export/credential/secret in them).
      Read/Grep-content, and the Bash verbs that print a file's bytes, are refused.

  Rule 2 — the 30 Oct vector: BROAD or SECRET-NAMED reads of a client-site
      database. Refused: `SELECT *`, `pg_dump`/`mysqldump`/`COPY … TO STDOUT`/
      `wp db export|query|cli`/`wp option list`, any wildcard sweep of
      option_value/meta_value, and ANY reference to a secret-named field or
      option (`…secret`, `…token`, `…api_key`, `consumer_key`, `app_secret`, …).
      ALLOWED: a narrow, explicitly-named NON-secret column on an explicitly-named
      non-secret option — `SELECT option_value FROM wp_options WHERE option_name =
      'woocommerce_currency'` still works. That line is the whole point of the
      "narrow named non-secret columns stay allowed" requirement: a guard that
      blocks ordinary store work gets disabled, and a disabled guard protects
      nothing.

  Rule 3 — secret-READING CLIs whose output has no fixed shape any value-pattern
      could match (firebase functions:secrets:access, gcloud secrets versions
      access, aws secretsmanager get-secret-value, az keyvault secret show). The
      PostToolUse scanner already redacts these after the fact
      (secrets_output_scanner.py, bus #74676); this refuses them before the value
      is ever printed.

  It does NOT re-implement what sibling guards already own:
    - secrets_transcript_guard.py owns inline literal secret VALUES (its Rule E)
      and the general secret-FILE read rule (its Rule A) — every file class it
      already matches is left alone here.
    - secrets_output_scanner.py owns post-hoc redaction.
    - lane_prod_key_guard.py owns the cosem PROD service-account key for lanes.
  Double-guarding the same thing would only double the false-positive surface.

THE SANCTIONED WAY THROUGH (all of them end with the value OUTSIDE the transcript):
    psql "$WOO_DSN" -tAc '…' | scripts/put_client_secret.py NAME --reason '…'
    psql "$WOO_DSN" -tAc '…' | sha256sum        (hash only)
    … | wc -c                                    (length only)
`scripts/put_client_secret.py` reads the value from stdin or an env var, writes it
straight into the fleet vault, and prints only a sha256 prefix + length. The
segment containing the store read is allowed when the pipeline's LAST stage is one
of those sinks — the credential moves to the vault without ever being displayed.

FAIL-CLOSED: unreadable/malformed tool input refuses the call (exit 2). A guard
that fails open on a parse error fails open exactly when something is mangled.

PROVIDER-AGNOSTIC BY CONSTRUCTION: the verdict is computed from the tool call
only — no model, provider or route is consulted — so a DeepSeek-/GLM-routed lane
gets the identical decision as an Anthropic one (asserted by test).

Exit 2 + stderr = the call is refused and the reason is shown to the model.
"""
from __future__ import annotations

import json
import os
import re
import sys

# ---- Rule 1: client-store files ------------------------------------------------

# Dotfile `.env` and the credentials/secrets JSON/YAML class already belong to
# secrets_transcript_guard's Rule A; this class is the CLIENT-STORE shape it does
# not match: wp-config.php, and non-dotfile env files (client.env, woo.env).
CLIENT_STORE_FILE_RES = (
    re.compile(r"(^|/)wp-config[\w.-]*\.php[\w.]*$", re.IGNORECASE),
    re.compile(r"(^|/)[\w-]+\.env$", re.IGNORECASE),
    re.compile(
        r"(^|/)[\w.-]*(woocommerce|wordpress|magento|shopify|stripe|wp-|woo-)[\w.-]*"
        r"(config|settings|export|credential|secret)[\w.-]*\.(json|ya?ml|ini|txt|xml)$",
        re.IGNORECASE,
    ),
    # Package-manager auth files. These usually hold a registry mapping AND MAY hold
    # an _authToken, so they are a SOFT class: a whole-file print is refused, but a
    # pattern-scoped grep (how a repo actually inspects its .npmrc: `grep -n
    # functions-shared functions/.npmrc`) is not — measured against 92,569 real fleet
    # commands, that alone was 8 false positives and would get the guard switched off.
    re.compile(r"(^|/)\.docker/config\.json$"),
    re.compile(r"(^|/)\.aws/credentials$"),
    # Token / netrc caches (probed: .netrc, oauth_token.json, tokens.json all pass
    # secrets_transcript_guard's Rule A, so this guard is the only cover). Scoped to
    # the cache shapes — `*_token(s).json` / `tokens.json`, never token_usage.json,
    # which is telemetry rather than a credential.
    re.compile(r"(^|/)\.netrc$"),
    re.compile(r"(^|/)[\w.-]*_?tokens?\.json$", re.IGNORECASE),
)
SOFT_FILE_RES = (
    re.compile(r"(^|/)(\.npmrc|\.pypirc)$"),
)
# Verbs that put a WHOLE file in the transcript. `grep`/`sed`/`awk` are deliberately
# absent: they emit only what the pattern selects, which for a soft-class file is
# usually the registry line, not the token. `scp`/`rsync` are absent too — they move
# bytes between files/hosts, they never print them, and a later read is caught anyway.
WHOLE_FILE_PRINT_RE = re.compile(
    r"\b(cat|head|tail|less|more|bat|nl|tac|strings|xxd|od|hexdump|base64|dd)\b"
    r"|\bpython3?\b|\bnode\b|\bruby\b|\bperl\b|\btar\b|\bgzip\b|\bgit\s+(show|diff|log)\b"
)
# Bash verbs that can put a file's bytes in the transcript at all.
FILE_PRINT_RE = re.compile(
    WHOLE_FILE_PRINT_RE.pattern + r"|\bgrep\b|\bsed\b|\bawk\b|\bzip\b"
)
# An `-o` grep whose pattern ends at `=` can only emit key NAMES, never a value:
# `grep -oh "^[A-Z_]*=" /tmp/prod-env-check.env` is how a lane lists which keys a
# deploy has, and blocking it teaches nothing. The match provably stops before the
# value, so the credential cannot be in the output.
NAME_ONLY_GREP_RE = re.compile(r"grep\b[^;&|]*\s-o\w*\b[^;&|]*['\"][^'\"]*=\s*['\"]")
# Code idioms, not filenames: `process.env` / `import.meta.env` appear in every JS
# command and must never be read as a `.env` path (5 false positives per 92k real
# commands, all of them `grep -rln "process.env" tests/`).
_NOT_A_PATH = frozenset({"process.env", "import.meta.env", "$process.env"})
# Every CLIENT_STORE_FILE_RES / SOFT_FILE_RES pattern needs at least one of these
# literals to be present for a match to be possible — see _matching_store_res.
_STORE_FILE_ANCHORS = (
    ".env", "wp-config", ".npmrc", ".pypirc", ".netrc",
    ".docker/config.json", ".aws/credentials", "token.json", "tokens.json", "_token",
    "woocommerce", "wordpress", "magento", "shopify", "stripe", "wp-", "woo-",
)

# ---- Rule 2: client-site databases ---------------------------------------------

# Rule 2 only applies when the statement's own command IS a database client (after
# any leading VAR=value assignments). A message that merely quotes a query is not a
# database read.
DB_CLIENT_RE = re.compile(
    r"^(?:sudo\s+(?:-u\s+\S+\s+)?)?"
    r"(?:psql|mysql|mariadb|sqlite3|pg_dump|pg_dumpall|mysqldump|mongodump|mongosh)\b"
    r"|^(?:sudo\s+(?:-u\s+\S+\s+)?)?wp\s+(?:db|option|core|plugin|post|eval)\b"
)

# A WordPress/WooCommerce store: its tables, its DSN env vars, or its CLI.
STORE_MARKER_RE = re.compile(
    r"\bwp_[a-z][a-z0-9_]*\b"                     # wp_options, wp_usermeta, …
    r"|\bwp\s+(db|option|config)\b"                # wp db query / wp option list
    r"|\b(woocommerce|wordpress)\b"
    r"|\$\{?[A-Z0-9_]*(WOO|WORDPRESS|WORDPRESS_DB|WPDB|WPBLOG)[A-Z0-9_]*\b"
    r"|\b(WOO|WORDPRESS)_[A-Z0-9_]*\b",
    re.IGNORECASE,
)
BROAD_SELECT_RE = re.compile(r"\bselect\s+(?:distinct\s+)?\*", re.IGNORECASE)
DUMP_RE = re.compile(
    r"\b(pg_dump|pg_dumpall|mysqldump|mongodump|sqlite3)\b"
    r"|\bcopy\b[\s\S]{0,200}?\bto\s+stdout\b"
    r"|\bwp\s+db\s+(export|dump|query|cli)\b"
    r"|\bwp\s+option\s+(list|pluck)\b",
    re.IGNORECASE,
)
VALUE_COLUMN_RE = re.compile(r"\b(option_value|meta_value|user_pass|user_activation_key)\b",
                             re.IGNORECASE)
_READ_VERB_RE = re.compile(r"\b(select|get|list|export|dump|show|desc|describe)\b", re.IGNORECASE)
WILDCARD_RE = re.compile(r"\b(like|ilike|regexp|rlike|similar\s+to)\b|~[*]?", re.IGNORECASE)
# A field/option NAME that says it holds a credential. Deliberately broad: an exact
# non-secret name is the allowed path, so a false negative here costs a leaked
# secret while a false positive costs one rewritten query.
SECRET_IDENT_RE = re.compile(
    r"(secret|token|passw(?:or)?d|api[_-]?key|apikey|private[_-]?key|credential"
    r"|consumer[_-]?(?:key|secret)|app[_-]?secret|auth[_-]?(?:key|token)|salt"
    r"|webhook|access[_-]?key|client[_-]?secret|bearer|hmac|nonce|signature"
    r"|user_pass|user_activation_key)",  # WP's own password-hash columns
    re.IGNORECASE,
)

# ---- Rule 3: secret-reading CLIs ------------------------------------------------

SECRET_READING_CLI_RE = re.compile(
    r"\bfirebase\s+functions:secrets:access\b"
    r"|\bgcloud\s+secrets\s+versions\s+access\b"
    r"|\baws\s+secretsmanager\s+(get|batch-get)-secret-value\b"
    r"|\baz\s+keyvault\s+secret\s+show\b",
    re.IGNORECASE,
)

# ---- sanctioned escapes ----------------------------------------------------------

# A pipeline whose LAST stage is one of these never displays the value: the vault
# helper consumes it, or only a hash/length is printed.
SAFE_SINK_RE = re.compile(
    r"\bput_client_secret\.py\b"
    r"|\b(?:sha256sum|shasum|md5sum|md5)\b"
    r"|\bopenssl\s+dgst\b"
    r"|\bwc\s+-[lc]\b",
)
# A plain `>` redirect or `tee` keeps a second copy of the value that a later tool
# call can read, so a pipeline that also writes it out is NOT a safe sink. `2>` and
# `>/dev/null` discard rather than copy and are ignored.
_REDIRECT_COPY_RE = re.compile(r"(?<![0-9&])>>?\s*(?!/dev/null)")
_TEE_RE = re.compile(r"(^|\|)\s*tee\b")

# Bash verbs that only touch file METADATA — they cannot put a credential in the
# transcript, so a Rule-1 path reference inside one of them is not a violation.
METADATA_ONLY_RE = re.compile(
    r"^\s*(?:sudo\s+)?(ls|stat|test|chmod|chown|touch|rm|mv|mkdir|file|ln|du|find)\b"
    r"|^\s*(?:sudo\s+)?git\s+(add|status|checkout|restore|reset|rm|mv)\b"
    r"|^\s*(?:sudo\s+)?(source|\.)\s"
    r"|^\s*set\s+-a\b"
    r"|^\s*export\s+\$\([^)]*\)\s*$"
)

STATEMENT_SPLIT_RE = re.compile(r"\n|;|&&|\|\|")
ASSIGNMENT_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")

REFUSAL_HEAD = "BLOCKED by credential_store_guard: "
REFUSAL_TAIL = (
    " A client credential must never enter an agent transcript; it is re-sent to the "
    "model provider on every later turn. Move it straight into the vault without "
    "printing it:  … | scripts/put_client_secret.py NAME --reason 'why'  (or pipe to "
    "sha256sum / wc -c when you only need to compare or measure). Ask the console if "
    "you believe this refusal is wrong — do not work around it."
)


def _deny(reason: str) -> int:
    sys.stderr.write(REFUSAL_HEAD + reason + REFUSAL_TAIL + "\n")
    return 2


def _statements(command: str) -> list[str]:
    return [s.strip() for s in STATEMENT_SPLIT_RE.split(command) if s.strip()]


def _leading(segment: str) -> str:
    return ASSIGNMENT_PREFIX_RE.sub("", segment.strip())


def _last_stage_is_sink(statement: str) -> bool:
    """True when the value the statement produces can only end up in a sink."""
    if _REDIRECT_COPY_RE.search(statement) or _TEE_RE.search(statement):
        return False
    last = statement.split("|")[-1].strip()
    return bool(SAFE_SINK_RE.search(last))


def _store_tokens(text: str) -> tuple[set, set]:
    """The tokens of `text` that name a hard / soft credential-store file."""
    # Cheap sound prefilter: every pattern below requires one of these literals, so a
    # text containing none of them cannot match — this runs on EVERY Bash call,
    # including 100 KB heredocs, and the per-token loop is not free.
    low = text.lower()
    if not any(a in low for a in _STORE_FILE_ANCHORS):
        return set(), set()
    hard, soft = set(), set()
    # Text may be a bare path (Read/Grep) or a shell command (Bash): split on the
    # characters a shell would separate tokens with, then test each token.
    for token in re.split(r"[\s'\"`]+", text):
        tok = token.rstrip(",;)")
        if not tok or tok in _NOT_A_PATH:
            continue
        if any(rx.search(tok) for rx in CLIENT_STORE_FILE_RES):
            hard.add(tok)
        elif any(rx.search(tok) for rx in SOFT_FILE_RES):
            soft.add(tok)
    return hard, soft


def _matching_store_res(text: str) -> tuple[bool, bool]:
    hard, soft = _store_tokens(text)
    return bool(hard), bool(soft)


def _is_client_store_file(text: str) -> bool:
    hard, soft = _matching_store_res(text)
    return hard or soft


def _rule1_violation(statement: str) -> str | None:
    # Judged per pipeline stage, and only when a store path is an ARGUMENT of a
    # printing command in that stage. Both halves matter and were measured on 92,701
    # real fleet commands: `vercel env pull /tmp/x.env | tail -3` prints tail's own
    # output, not the file, and a python heredoc that merely MENTIONS `.env` in a
    # comment is prose. Blocking those gets the guard switched off.
    for stage in statement.split("|"):
        if METADATA_ONLY_RE.match(_leading(stage)):
            continue
        hard, soft = _store_tokens(stage)
        if not (hard or soft):
            continue
        if hard and NAME_ONLY_GREP_RE.search(stage):
            continue  # key-name-only extraction cannot emit a value
        verbs = FILE_PRINT_RE if hard else WHOLE_FILE_PRINT_RE
        for m in verbs.finditer(stage):
            args = stage[m.end():].split("<<")[0]  # a heredoc BODY is not an argument
            arg_hard, arg_soft = _store_tokens(args)
            if (hard & arg_hard) or (soft & arg_soft):
                return (
                    "this command prints a file that holds credentials (wp-config.php / "
                    "a non-dotfile .env / a store settings export / a token or registry "
                    "cache). Print only what you need — or pipe the whole thing into a "
                    "hash/length sink."
                )
    return None


def _rule2_violation(statement: str) -> str | None:
    # Scoped to a statement whose OWN command is a database client. Without this, any
    # message that merely QUOTES a query (`bus_send --body "… SELECT * FROM wp_options
    # …"`) would be refused — measured on 92,569 real fleet commands that was the
    # single largest false-positive source, and a guard that blocks discussion of the
    # incident gets switched off.
    if not DB_CLIENT_RE.match(_leading(statement)):
        return None
    if not STORE_MARKER_RE.search(statement):
        return None
    if SECRET_IDENT_RE.search(statement):
        verb = "READS" if _READ_VERB_RE.search(statement) else "NAMES"
        consequence = (
            "reading it puts the credential into the transcript"
            if verb == "READS"
            else "writing it from an agent shell is client-credential entry, and the value "
                 "is already in this transcript"
        )
        return (
            f"this statement {verb} a SECRET-named field or option in a client store "
            "(option/meta names containing secret/token/api_key/consumer_key/…); "
            f"{consequence}. Named non-secret fields are fine — this one is the credential."
        )
    if DUMP_RE.search(statement):
        return (
            "this is an unscoped dump / arbitrary query against a client site database "
            "(pg_dump, mysqldump, COPY … TO STDOUT, wp db export|query|cli, wp option list)."
        )
    if BROAD_SELECT_RE.search(statement):
        return (
            "this is a SELECT * against a client site database. Name the non-secret "
            "columns you actually need."
        )
    if VALUE_COLUMN_RE.search(statement) and WILDCARD_RE.search(statement):
        return (
            "this sweeps option_value/meta_value with a wildcard (LIKE/REGEXP), which is "
            "how a whole family of credential rows comes back at once."
        )
    return None


def _rule3_violation(statement: str) -> str | None:
    if not SECRET_READING_CLI_RE.search(statement):
        return None
    return (
        "this is a secret-reading CLI whose output has no fixed shape, so the "
        "post-hoc scanner can only redact it AFTER it is already in the transcript. "
        "Send it to the vault or a hash sink in the same command."
    )


def _bash_violation(command: str) -> str | None:
    for statement in _statements(command):
        if _last_stage_is_sink(statement):
            continue
        for rule in (_rule1_violation, _rule2_violation, _rule3_violation):
            reason = rule(statement)
            if reason:
                return reason
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — any unreadable input is fail-closed
        sys.stderr.write(
            REFUSAL_HEAD + "could not read the tool input; refusing (fail-closed).\n"
        )
        return 2
    if not isinstance(payload, dict):
        sys.stderr.write(REFUSAL_HEAD + "tool input was not an object; refusing (fail-closed).\n")
        return 2

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool == "Bash":
        reason = _bash_violation(tool_input.get("command") or "")
        return _deny(reason) if reason else 0

    if tool in ("Read", "Grep"):
        path = tool_input.get("path") or tool_input.get("file_path") or ""
        # Grep in files_with_matches / count mode returns names or a number — no
        # credential content — so it is allowed even on a store file.
        if tool == "Grep" and (tool_input.get("output_mode") or "") in (
                "files_with_matches", "count"):
            return 0
        if _is_client_store_file(str(path)):
            return _deny(
                f"{tool} on a client-store credential file would put the store's "
                "credentials into the transcript."
            )
        return 0

    # Every other tool is out of scope for this guard.
    return 0


if __name__ == "__main__":
    sys.exit(main())

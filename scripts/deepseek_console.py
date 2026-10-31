#!/usr/bin/env python3
"""deepseek_console.py -- browser automation over the DeepSeek console, so
per-client key provisioning + usage export stop being a manual Musa chore
(op#35851, bus #82526; scale-to-N refinement bus #82540/op#35853).

PHASE 2 (op#36768): the selectors in _SEL are now the REAL DOM, verified
against the live authenticated console on 2026-10-31. Two findings drove the
shape of this file and are worth knowing before editing it:

  1. Every "button" on the console is a `div.ds-button` with role=button --
     not a <button>. Text-based `:has-text()` still works on them (it
     searches descendants); a `button:` selector silently matches nothing.
     Filter triggers carry a CSS-module hash class (`d215a2f1`) that changes
     between builds -- never key off it.

  2. /usage has NO per-day table to scrape (summary tiles + two charts only).
     Per-key daily usage comes from the page's own Export button, which
     downloads a zip containing amount-<from>_<to>.csv with one row per
     (day, api_key_name, model, usage type). usage-export drives that
     download, parses the zip IN MEMORY, and emits JSONL. That is deliberately
     NOT an API reconstruction: the console's JSON endpoints require an
     Authorization token injected by the SPA, and /auth-api/v0/users/current
     hands that token back in its response body -- this script must never
     call that endpoint, so it never holds a session token at all.

Session: Musa's one-time headed login lives in the persistent profile at
~/.wingmen/deepseek_browser/ (created 0700). Verified 2026-10-31 that it
reopens HEADLESS with no re-login, so no headed pass is needed for routine
reads. No credential ever touches this script, the vault, or any bus row.

Hard constraints (console #82526, binding, enforced in code below, not just
documented):
  - NO top-up, NO settings changes, NO key deletion. This script's allowlist
    of pages/actions is the enforcement: it simply has no code path to reach
    those pages. Musa revokes/tops-up himself in the real console.
  - A created key's VALUE is never printed, logged, or screenshotted. It is
    read from the page into a local Python variable and piped straight into
    vault.put() in the same process; the variable is not referenced again
    after that call returns.
  - --screenshot/--trace do not exist as flags, and the Playwright tracing
    API is never invoked anywhere in this file (grep for "tracing" to
    confirm: the only hits are this sentence and the ban it describes).
  - FAIL CLOSED on anything unexpected: a missing allowlisted marker element
    (layout changed under us) or a logged-out session both abort the
    action with a clear stderr message and a nonzero exit -- never a best-
    effort guess at a different selector.

split-key (bus #87091 GO) is the migration off the leak-flagged shared
DEEPSEEK_CODING_KEY: one console key per family slug, named exactly the slug,
vaulted as DEEPSEEK_KEY_<SLUG>, with the map row flipped LAST. The order is
load-bearing -- provider_routing.sh fails closed on a missing vault secret, so
vault-before-map is what keeps every crash point bootable. It never revokes
the shared key; that is a separate step gated on orch-console's explicit go
after every family is verified.

Usage:
    deepseek_console.py login --headed
    deepseek_console.py add-client <slug>
    deepseek_console.py split-key <slug> [--flip-only] [--dry-run]
    deepseek_console.py list-clients
    deepseek_console.py usage-export [--since YYYY-MM-DD] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ORCH_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _ORCH_ROOT)

from scripts.lib.lane_token_resolver import family_of  # noqa: E402

_MAP_FILE = os.path.join(_SCRIPT_DIR, "lib", "deepseek_key_map.json")
_CHECK_MAP_SCRIPT = os.path.join(_SCRIPT_DIR, "check_deepseek_key_map.py")
_PROFILE_DIR = os.path.expanduser("~/.wingmen/deepseek_browser")

# The one key every family pointed at before the split (bus #84011 interim
# state). split-key only ever repoints a slug that is on THIS key: anything
# else was set by hand and is not ours to overwrite.
_SHARED_KEY_NAME = "DEEPSEEK_CODING_KEY"

# amount x price must reproduce the console's own cost export to within this.
# Observed live diff is exactly 0.0 (2026-11-01), so anything above float
# noise means the export shape changed or the per-key split double-counts --
# see the enforcement comment in parse_export_zip.
_COST_CROSS_CHECK_TOLERANCE_USD = 1e-6
_USAGE_JSONL_DEFAULT = os.path.expanduser(
    "~/wingmen/fleet-health/state/deepseek_usage_by_key.jsonl"
)

_CONSOLE_URL = "https://platform.deepseek.com"

# --- allowlist: exactly the pages/actions this script may ever visit. Every
# action function below checks its own entry's marker before doing anything,
# and navigates to nothing outside this dict. Selectors verified against the
# live console 2026-10-31 (op#36768) -- see the module docstring for the two
# markup facts that matter (div.ds-button, and no per-day table on /usage).
_SEL = {
    "api_keys_page": {
        "url": f"{_CONSOLE_URL}/api_keys",
        # The introductory paragraph is stable page copy and only renders for
        # a signed-in user; the nav item "API keys" renders even logged out.
        "logged_in_marker": "text=Your API keys are listed below",
        "keys_table": "table",
        "create_button": "div.ds-button:has-text('Create new API key')",
        "dialog_title": "div.ds-modal-content__title",
        "name_input": "input.ds-input__input[placeholder='Name of the API key']",
        # The footer primary is rendered --disabled until the name field has
        # content, so waiting on the non-disabled form doubles as "the form
        # actually accepted the name".
        "confirm_create": (
            "div.ds-modal-content__footer "
            "div[role='button'].ds-button--primary:not(.ds-button--disabled)"
        ),
        # DeepSeek shows the raw key exactly once, as text in the dialog.
        # Matched by pattern rather than by a class/testid because there is no
        # stable attribute on the node; a miss here fails closed before any
        # vault write.
        "revealed_key_value": "text=/^sk-[A-Za-z0-9]{20,}$/",
        # Dismissal text is not pinned by a stable attribute either; tried in
        # order, then Escape as the last resort.
        "dialog_done_candidates": (
            "div.ds-modal-content div[role='button']:has-text('Done')",
            "div.ds-modal-content div[role='button']:has-text('OK')",
            "div.ds-modal-content div[role='button']:has-text('Close')",
            "div.ds-modal-content div[role='button']:has-text('Got it')",
        ),
    },
    "usage_page": {
        "url": f"{_CONSOLE_URL}/usage",
        # Console copy states the display timezone inline; a signed-out
        # visitor never reaches /usage and sees neither this nor the tiles.
        "logged_in_marker": "text=All dates and times are GMT+8",
        # aria-label is stable where the visible text is nested/spans.
        "export_button": "div.ds-button[aria-label='Export']",
    },
    "login_page": {
        "url": f"{_CONSOLE_URL}/sign_in",
        # presence of this on /api_keys (after redirect-if-logged-in) is how
        # every other action's logged-out check works -- see _require_logged_in.
        "login_form_marker": "input[type='password']",
    },
}


class LayoutMismatch(RuntimeError):
    """The page didn't show what the allowlist expects. Fail closed, never guess."""


class LoggedOut(RuntimeError):
    """No session cookie survived in the persistent profile. Fail closed."""


def _context(playwright, headed: bool, accept_downloads: bool = False):
    os.makedirs(_PROFILE_DIR, exist_ok=True, mode=0o700)
    os.chmod(_PROFILE_DIR, 0o700)
    return playwright.chromium.launch_persistent_context(
        _PROFILE_DIR,
        headless=not headed,
        # Real Google Chrome (installed on the Mini), not the bundled
        # Playwright Chromium -- DeepSeek's human check was flagging the
        # bundled Chromium's automation fingerprint (orch-console bus
        # #84081: Musa's one-time headed login kept failing the check).
        # The two args below strip the two most common automation tells
        # (the "Chrome is being controlled by automated software" infobar /
        # navigator.webdriver); verified locally that navigator.webdriver
        # reads False under this config before relying on it here.
        channel="chrome",
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
        # Only the usage export needs this; a download is never a capture of
        # credential material (the exported zip strips nothing on its own, so
        # the parser below is what keeps masked key values out of our output).
        accept_downloads=accept_downloads,
        # Deliberately no record_video_dir / no tracing.start() anywhere in
        # this module -- see the no-capture constraint in the module docstring.
    )


def _require_logged_in(page, sel: dict) -> None:
    """Fail closed on EITHER a logged-out session OR a layout we don't
    recognize -- these look identical from the outside (both are "the marker
    I expected isn't there"), and this script must never guess which."""
    try:
        page.wait_for_selector(sel["logged_in_marker"], timeout=8000)
    except Exception as exc:
        if page.locator(_SEL["login_page"]["login_form_marker"]).count() > 0:
            raise LoggedOut(
                "DeepSeek console session is logged out. Run "
                "`deepseek_console.py login --headed` once to sign in; that "
                "login persists in ~/.wingmen/deepseek_browser/ for every "
                "later headless run."
            ) from exc
        raise LayoutMismatch(
            f"expected marker {sel['logged_in_marker']!r} not found within "
            "timeout, and no login form was detected either -- the page "
            "layout may have changed. Refusing to guess; aborting."
        ) from exc


def cmd_login(args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _context(p, headed=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(_SEL["api_keys_page"]["url"])
        print(
            "Browser window open -- sign in by hand, then come back here and "
            "press Enter once you're on the API Keys page.",
            file=sys.stderr,
        )
        input()
        try:
            _require_logged_in(page, _SEL["api_keys_page"])
        except LoggedOut:
            print(
                "Still looks logged out -- the session did not save. Try again.",
                file=sys.stderr,
            )
            ctx.close()
            return 1
        ctx.close()
        print("Login saved to the persistent profile. Future runs can be headless.")
        return 0


def _load_map() -> dict:
    with open(_MAP_FILE) as f:
        return json.load(f)


def _write_map(mapping: dict) -> None:
    tmp = _MAP_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, _MAP_FILE)


_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def _key_name_for_slug(slug: str) -> str:
    """Vault key NAME for a slug. Hyphens fold to underscores: vault names are
    the identifier half of the resolver chain (deepseek_key_for_family ->
    provider_vault_fetch), and `-` is the lane-family separator, so it must
    not leak into the secret name."""
    return f"DEEPSEEK_KEY_{slug.upper().replace('-', '_')}"


def _vault_has_key(key_name: str) -> bool:
    """Name-only existence check. Deliberately NOT vault.get(): that decrypts
    and returns the value, and a key value must never enter this process
    unless it is on its way into vault.put()."""
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL not set in this process's environment (source "
            "~/wingmen/orchestrator/.env first)."
        )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM public.vault_secrets WHERE name = %s", (key_name,))
        return cur.fetchone() is not None


def _create_console_key(slug: str, headed: bool) -> str:
    """Drive /api_keys, create ONE key named `slug`, return its value.

    Shared by add-client and split-key so the split path can never drift from
    the creation path that was verified against the live DOM. The return value
    exists only to be handed to vault.put() by the caller in the same frame --
    it is never printed, logged, or stored anywhere else.
    """
    from playwright.sync_api import sync_playwright

    sel = _SEL["api_keys_page"]
    with sync_playwright() as p:
        ctx = _context(p, headed=headed)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(sel["url"])
            _require_logged_in(page, sel)

            page.click(sel["create_button"])
            try:
                page.wait_for_selector(sel["dialog_title"], timeout=8000)
                page.fill(sel["name_input"], slug)
                # Doubles as the form-accepted-the-name check: the footer
                # primary stays --disabled until the input has content.
                page.wait_for_selector(sel["confirm_create"], timeout=8000)
            except Exception as exc:
                raise LayoutMismatch(
                    "create-key dialog did not appear in the expected shape "
                    "(title / name input / enabled confirm button) -- aborting "
                    "before anything is created or written"
                ) from exc
            page.click(sel["confirm_create"])

            try:
                page.wait_for_selector(sel["revealed_key_value"], timeout=15000)
            except Exception as exc:
                raise LayoutMismatch(
                    "key-created dialog didn't show the expected revealed-value "
                    "selector -- the console may have created the key but its "
                    f"value was NOT captured. Check /api_keys for a duplicate "
                    f"named {slug!r} before re-running; nothing was vaulted."
                ) from exc

            key_value = page.locator(sel["revealed_key_value"]).first.inner_text().strip()
            # Dismiss the dialog before returning -- the caller vaults
            # immediately and never reads key_value again.
            for candidate in sel["dialog_done_candidates"]:
                if page.locator(candidate).count() > 0:
                    page.click(candidate)
                    break
            else:
                page.keyboard.press("Escape")
        finally:
            ctx.close()

    if not key_value:
        raise LayoutMismatch(
            f"revealed key field was empty for {slug!r} -- the console may have "
            "created the key but no value was captured; nothing was vaulted."
        )
    return key_value


def cmd_add_client(args) -> int:
    slug = args.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        print(
            f"invalid slug {args.slug!r}: must be lowercase, start with a "
            "letter, alnum/hyphen only, <=41 chars",
            file=sys.stderr,
        )
        return 1

    mapping = _load_map()
    if slug in mapping:
        print(f"refusing: slug {slug!r} already mapped to {mapping[slug]!r}", file=sys.stderr)
        return 1
    # family_of() is the SAME derivation every other per-family tier in the
    # fleet uses (lane_token_resolver, deepseek_key_for_family). A slug that
    # collides with an EXISTING lane family under a different spelling would
    # silently fork the mapping that family already resolves through -- e.g.
    # adding "cosem" here while cosem's lanes already resolve "cosem" via
    # family_of(cc-cosem-*-N) would double-map the same family to two keys
    # depending on which code path asked. Refuse instead of guessing.
    existing_families = {family_of(f"cc-{fam}-1") for fam in mapping}
    if family_of(f"cc-{slug}-1") in existing_families:
        print(
            f"refusing: slug {slug!r} resolves to the same lane-family as an "
            "existing mapped client -- would fork the mapping",
            file=sys.stderr,
        )
        return 1

    key_name = _key_name_for_slug(slug)

    from nervous_system.vault import vault

    key_value = _create_console_key(slug, args.headed)

    vault.put(
        key_name,
        key_value,
        reason=f"deepseek_console.py add-client {slug} (op#35851/op#35853)",
    )
    key_value = None  # noqa: F841 -- scrubbed from this frame now that vault holds it

    mapping[slug] = key_name
    _write_map(mapping)
    print(f"client {slug!r} -> vault key {key_name!r} (map updated, value never printed)")
    return 0


def _run_key_map_check() -> tuple[int, str]:
    """Shell out to the standing map/vault consistency check
    (scripts/check_deepseek_key_map.py, bus #84005/#84011). Self-locating, so
    it reads the same deepseek_key_map.json this script just wrote."""
    proc = subprocess.run(
        [sys.executable, _CHECK_MAP_SCRIPT],
        capture_output=True, text=True, cwd=_ORCH_ROOT,
    )
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


def _boot_check(slug: str, vault_key: str) -> tuple[bool, str]:
    """Walk the REAL launch-time key chain for one family and report only
    whether it survives.

    provider_routing.sh resolves the vault key NAME via
    deepseek_key_for_family.py(<agent_id>) and then fetches its value via
    provider_vault_fetch.py. We do exactly the same two hops. The second hop
    prints the raw key on stdout, so its stdout goes to DEVNULL and is never
    read into this process -- only the exit status counts. The first hop
    prints a key NAME, which is not a secret and is compared to what we just
    wrote, catching the case where the map flip silently didn't take.
    """
    agent_id = f"cc-{slug}-1"
    env = dict(os.environ, AGENT_ID=agent_id)

    resolver = os.path.join(_ORCH_ROOT, "scripts", "lib", "deepseek_key_for_family.py")
    fetch = os.path.join(_ORCH_ROOT, "scripts", "lib", "provider_vault_fetch.py")
    for path in (resolver, fetch):
        if not os.path.exists(path):
            return False, f"missing {path}"

    resolved = subprocess.run(
        [sys.executable, resolver, agent_id],
        capture_output=True, text=True, cwd=_ORCH_ROOT, env=env,
    )
    name = (resolved.stdout or "").strip()
    if resolved.returncode != 0 or not name:
        return False, f"family resolver failed for {agent_id}: {(resolved.stderr or '').strip()}"
    if name != vault_key:
        return False, f"resolver returned {name!r} for {agent_id}, expected {vault_key!r}"

    fetched = subprocess.run(
        [sys.executable, fetch, vault_key, "deepseek"],
        stdout=subprocess.DEVNULL, text=True, cwd=_ORCH_ROOT, env=env,
        stderr=subprocess.PIPE,
    )
    if fetched.returncode != 0:
        return False, f"vault fetch failed for {vault_key!r}: {(fetched.stderr or '').strip()}"
    return True, f"{agent_id} -> {vault_key!r} -> value present"


def cmd_split_key(args) -> int:
    """Repoint ONE existing client slug from the shared DEEPSEEK_CODING_KEY to
    its own console key (bus #87091 GO: 16 keys, one per family slug, named
    exactly the slug).

    Order is the whole point, and it is NOT negotiable: create the console key
    -> vault the value -> flip the map row -> re-run the consistency check.
    provider_routing.sh FAILS CLOSED when the resolved vault key is missing,
    so flipping the map before the secret exists would stop that family's
    lanes from booting. Every intermediate crash therefore leaves a state that
    still boots: before the flip the map points at the shared key, which is
    still valid.

    --flip-only exists for the one recovery case: a previous run crashed after
    the vault write but before the map flip (vault has the secret, map still
    on the shared key). Without it the natural retry would create a SECOND
    console key with the same name.

    The shared key itself is NEVER revoked here -- that is a separate,
    explicitly-gated step after every family has been verified.
    """
    slug = args.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        print(
            f"invalid slug {args.slug!r}: must be lowercase, start with a "
            "letter, alnum/hyphen only, <=41 chars",
            file=sys.stderr,
        )
        return 1

    mapping = _load_map()
    if slug not in mapping:
        print(
            f"refusing: slug {slug!r} is not in the map. split-key only "
            "re-points an EXISTING family; use add-client for a brand-new client.",
            file=sys.stderr,
        )
        return 1

    target = _key_name_for_slug(slug)
    current = mapping[slug]

    if current == target:
        print(f"already split: {slug!r} -> {target!r}. Nothing to do.", file=sys.stderr)
        return 1
    if current != _SHARED_KEY_NAME:
        print(
            f"refusing: {slug!r} is mapped to {current!r}, not the shared "
            f"{_SHARED_KEY_NAME!r}. That row was set outside this script; "
            "not repointing it.",
            file=sys.stderr,
        )
        return 1

    vault_has = _vault_has_key(target)
    if vault_has and not args.flip_only:
        print(
            f"refusing: vault already holds {target!r} but the map still "
            f"points at {_SHARED_KEY_NAME!r} -- an earlier run stopped after "
            "the vault write. Re-run with --flip-only to finish the flip "
            "without creating a SECOND console key.",
            file=sys.stderr,
        )
        return 1
    if args.flip_only and not vault_has:
        print(
            f"refusing --flip-only: vault has no {target!r}. Flipping now "
            "would make provider_routing.sh fail closed and the family's "
            "lanes would not boot. Run without --flip-only.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(
            f"dry run -- {slug!r}\n"
            f"  1. {'(skip: --flip-only) ' if args.flip_only else ''}"
            f"create console API key named {slug!r}, read its value once\n"
            f"  2. {'(skip: --flip-only) ' if args.flip_only else ''}"
            f"vault.put({target!r}, <value>)  -- value never printed\n"
            f"  3. map row {slug!r}: {current!r} -> {target!r}\n"
            f"  4. re-run check_deepseek_key_map.py\n"
            f"  5. boot check: cc-{slug}-1 resolves to {target!r} and its "
            "value loads (stdout discarded)\n"
            f"  6. {_SHARED_KEY_NAME!r} is NOT revoked (separate gated step)"
        )
        return 0

    from nervous_system.vault import vault

    if not args.flip_only:
        key_value = _create_console_key(slug, args.headed)
        try:
            vault.put(
                target,
                key_value,
                reason=f"deepseek_console.py split-key {slug} (op#36768, bus #87091)",
            )
        finally:
            key_value = None  # noqa: F841 -- scrubbed even if the write raised
        print(f"1/4 created console key {slug!r} and vaulted {target!r} (value never printed)")
    else:
        print(f"1/4 --flip-only: reusing existing vault secret {target!r}, no console key created")

    mapping[slug] = target
    _write_map(mapping)
    print(f"2/4 map row flipped: {slug!r}: {current!r} -> {target!r}")

    rc, out = _run_key_map_check()
    if rc != 0:
        print(
            "3/4 check_deepseek_key_map.py FAILED (exit %d):\n%s\n\n"
            "ROLLBACK: set scripts/lib/deepseek_key_map.json[%r] back to %r "
            "(the shared key is still valid, so that restores the previous "
            "booting state). The vault secret %r is harmless if left in place."
            % (rc, out or "(no output)", slug, _SHARED_KEY_NAME, target),
            file=sys.stderr,
        )
        return 2
    print("3/4 check_deepseek_key_map.py: every referenced vault key exists")

    if args.skip_boot_check:
        print("4/4 boot check SKIPPED (--skip-boot-check)")
        return 0

    ok, detail = _boot_check(slug, target)
    if not ok:
        print(
            f"4/4 boot check FAILED: {detail}\n"
            f"This family would fail closed at launch. Roll back the map row "
            f"{slug!r} -> {_SHARED_KEY_NAME!r} before leaving it.",
            file=sys.stderr,
        )
        return 2
    print(f"4/4 boot check OK: {detail}")
    print(
        f"\nNext: confirm real spend lands under api_key_name={slug!r} by "
        "running `deepseek_console.py usage-export` once a request has gone "
        f"through this family. {_SHARED_KEY_NAME!r} is still live and is NOT "
        "revoked by this command."
    )
    return 0


def cmd_list_clients(args) -> int:
    import psycopg

    mapping = _load_map()
    if not mapping:
        print("(no clients mapped yet)")
        return 0

    conn = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name, created_at FROM public.vault_secrets WHERE name = ANY(%s)",
            (list(mapping.values()),),
        )
        created = {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()

    for slug, key_name in sorted(mapping.items()):
        created_at = created.get(key_name, "?")
        print(f"{slug}\t{key_name}\t{created_at}")
    return 0


def parse_export_zip(zip_bytes: bytes, since: str | None = None):
    """Turn the console's usage export into per-(day, key, model) records.

    Source of truth is amount-<from>_<to>.csv, whose columns are
    user_id,start_time_iso,end_time_iso,model,name,api_key_name,api_key,type,
    price,amount -- one row per usage type. `api_key` (masked) and `user_id`
    are DROPPED on the way in: neither is needed to attribute spend, and
    keeping a key-shaped string out of the artifact is the point.

    Cost is amount x price, summed per record. cost-<from>_<to>.csv is the
    console's own per-(day, model) total and carries the currency, so it is
    read both for the currency label and as an ENFORCED cross-check: a delta
    above _COST_CROSS_CHECK_TOLERANCE_USD raises LayoutMismatch and writes
    nothing, because a mismatch means either the export shape changed or the
    per-key split double-counts, and the JSONL is consumed as authoritative.
    Rows that cannot be attributed raise rather than being skipped for the
    same reason.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        members = zf.namelist()
        amount_name = next(
            (n for n in members if os.path.basename(n).startswith("amount-")), None
        )
        cost_name = next(
            (n for n in members if os.path.basename(n).startswith("cost-")), None
        )
        if not amount_name:
            raise LayoutMismatch(
                f"usage export has no amount-*.csv (members: {sorted(members)}) -- "
                "the export format may have changed. Refusing to guess; aborting."
            )
        amount_rows = list(
            csv.DictReader(io.StringIO(zf.read(amount_name).decode("utf-8-sig")))
        )
        cost_rows = (
            list(csv.DictReader(io.StringIO(zf.read(cost_name).decode("utf-8-sig"))))
            if cost_name
            else []
        )

    agg: dict = {}
    unattributable: list = []
    for lineno, row in enumerate(amount_rows, start=2):
        day = (row.get("start_time_iso") or "")[:10]
        key_name = (row.get("api_key_name") or "").strip()
        model = (row.get("model") or "").strip()
        usage_type = (row.get("type") or "").strip()
        if not day or not key_name or not usage_type:
            unattributable.append(f"amount csv line {lineno}: missing day/key/type")
            continue
        try:
            amount = float(row.get("amount") or 0)
            price = float(row.get("price") or 0)
        except (TypeError, ValueError):
            unattributable.append(f"amount csv line {lineno}: non-numeric amount/price")
            continue
        rec = agg.setdefault(
            (day, key_name, model),
            {
                "date": day,
                "api_key_name": key_name,
                "model": model,
                "usage": {},
                "cost_usd": 0.0,
            },
        )
        rec["usage"][usage_type] = int(amount)
        rec["cost_usd"] = round(rec["cost_usd"] + amount * price, 10)

    console_cost: dict = {}
    currency = None
    for lineno, row in enumerate(cost_rows, start=2):
        day = (row.get("start_time_iso") or "")[:10]
        model = (row.get("model") or "").strip()
        currency = row.get("currency") or currency
        try:
            console_cost[(day, model)] = (
                console_cost.get((day, model), 0.0) + float(row.get("cost") or 0)
            )
        except (TypeError, ValueError):
            unattributable.append(f"cost csv line {lineno}: non-numeric cost")
            continue

    if unattributable:
        # Fail closed instead of dropping the row. A skipped row does not
        # disappear -- it silently moves its spend into somebody else's
        # bucket or out of the total entirely, and the JSONL is written as
        # authoritative. Same posture as the shape checks above: abort rather
        # than guess (cc-quality F1 on PR#744).
        raise LayoutMismatch(
            f"usage export has {len(unattributable)} row(s) this parser cannot "
            "attribute, so any per-key total we computed would be a guess "
            f"({len(amount_rows)} amount rows, {len(cost_rows)} cost rows): "
            + "; ".join(unattributable[:5])
            + (" ..." if len(unattributable) > 5 else "")
        )

    our_cost: dict = {}
    for (day, _key, model), rec in agg.items():
        our_cost[(day, model)] = our_cost.get((day, model), 0.0) + rec["cost_usd"]
    max_abs_diff = max(
        (abs(our_cost.get(k, 0.0) - v) for k, v in console_cost.items()), default=0.0
    )

    if max_abs_diff > _COST_CROSS_CHECK_TOLERANCE_USD:
        # ENFORCED, not just printed (cc-quality F1 on PR#744): this delta is
        # the only detector of a changed export shape or a per-key split that
        # double-counts. Reporting it in a summary only helps whoever reads
        # that summary; a consumer of the JSONL sees authoritative-looking
        # rows either way. Refuse to write.
        worst = sorted(
            ((abs(our_cost.get(k, 0.0) - v), k) for k, v in console_cost.items()),
            reverse=True,
        )[:3]
        detail = ", ".join(
            f"{k[0]}/{k[1]}: ours={our_cost.get(k, 0.0):.10f} "
            f"console={console_cost[k]:.10f}"
            for _diff, k in worst
        )
        raise LayoutMismatch(
            "cost cross-check FAILED: summing amount x price does not reproduce "
            f"the console's own cost export (max abs diff ${max_abs_diff:.10f}, "
            f"tolerance ${_COST_CROSS_CHECK_TOLERANCE_USD:g}). Worst: {detail}. "
            "Per-key attribution would be wrong if written; refusing to write."
        )

    if since:
        _validate_iso_date(since)
        agg = {k: v for k, v in agg.items() if v["date"] >= since}

    records = sorted(agg.values(), key=lambda r: (r["date"], r["api_key_name"], r["model"]))
    for rec in records:
        rec["currency"] = currency or "USD"
        rec["source"] = "console_export"

    dates = sorted({r["date"] for r in agg.values()}) or sorted(
        (row.get("start_time_iso") or "")[:10] for row in amount_rows
    )
    meta = {
        "currency": currency or "USD",
        "data_range": {"from": dates[0] if dates else None, "to": dates[-1] if dates else None},
        "amount_rows": len(amount_rows),
        "keys": sorted({r["api_key_name"] for r in records}),
        "max_abs_diff_usd": round(max_abs_diff, 10),
    }
    return records, meta


def _validate_iso_date(value: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise LayoutMismatch(
            f"--since {value!r} is not a YYYY-MM-DD date"
        ) from exc


def merge_jsonl(path: str, records: list, currency: str) -> dict:
    """Upsert records into the append-only-visible JSONL keyed by
    (date, api_key_name, model). Re-running an overlapping export window must
    never duplicate a row: a later run simply refreshes the same key, keeping
    the original first_seen_at. Rewritten atomically (tmp + os.replace) so a
    reader never sees a half-written file."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing: dict = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                existing[(rec.get("date"), rec.get("api_key_name"), rec.get("model"))] = rec

    added = updated = 0
    for rec in records:
        ident = (rec["date"], rec["api_key_name"], rec["model"])
        if ident in existing:
            rec["first_seen_at"] = existing[ident].get("first_seen_at", now)
            updated += 1
        else:
            rec["first_seen_at"] = now
            added += 1
        rec["updated_at"] = now
        existing[ident] = rec

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for ident in sorted(existing):
            f.write(json.dumps(existing[ident], sort_keys=True) + "\n")
    os.replace(tmp, path)
    return {"records_added": added, "records_updated": updated, "records_total": len(existing)}


def cmd_usage_export(args) -> int:
    """Download the console's own usage export and write per-key daily usage.

    Reads ALL mapped keys from the live map file each call (bus #82540's
    explicit "not a hardcoded list of 5" requirement) -- though in practice the
    export reports whatever keys the console has, which is the honest source of
    truth: a key in the map that the console doesn't know simply has no rows.
    """
    mapping = _load_map()
    sel = _SEL["usage_page"]
    out_path = os.path.expanduser(args.out or _USAGE_JSONL_DEFAULT)

    # Validate the date BEFORE driving a browser: a typo should cost a second,
    # not a full export download (cc-quality, PR#744 minor note).
    if args.since:
        _validate_iso_date(args.since)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _context(p, headed=args.headed, accept_downloads=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(sel["url"])
        _require_logged_in(page, sel)
        try:
            with page.expect_download(timeout=90000) as dl_info:
                page.click(sel["export_button"])
            download = dl_info.value
        except Exception as exc:
            raise LayoutMismatch(
                "the Export button produced no download -- the usage page "
                "layout may have changed. Refusing to guess; aborting."
            ) from exc

        # Saved only long enough to read the bytes; the zip carries a masked
        # api_key column and an internal user_id that must not reach either
        # the JSONL or this process's leftovers on disk.
        tmpdir = tempfile.mkdtemp(prefix="deepseek_usage_")
        try:
            zpath = os.path.join(tmpdir, download.suggested_filename or "usage.zip")
            download.save_as(zpath)
            with open(zpath, "rb") as f:
                zip_bytes = f.read()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        ctx.close()

    records, meta = parse_export_zip(zip_bytes, since=args.since)
    summary = merge_jsonl(out_path, records, currency=meta["currency"])
    summary.update(
        {
            "jsonl": out_path,
            "data_range": meta["data_range"],
            "records_in_export": len(records),
            "amount_rows_parsed": meta["amount_rows"],
            "keys_in_export": meta["keys"],
            "clients_mapped": len(mapping),
            "cost_cross_check_max_abs_diff_usd": meta["max_abs_diff_usd"],
        }
    )
    print(json.dumps(summary, indent=2))
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="one-time interactive sign-in into the persistent profile")
    p_login.add_argument("--headed", action="store_true", default=True, help="always headed for login (flag kept for symmetry)")
    p_login.set_defaults(func=cmd_login)

    p_add = sub.add_parser("add-client", help="create a console API key for a new client slug, vault it, map it")
    p_add.add_argument("slug")
    p_add.add_argument("--headed", action="store_true", help="show the browser (debugging only; default headless)")
    p_add.set_defaults(func=cmd_add_client)

    p_split = sub.add_parser(
        "split-key",
        help="repoint ONE existing client slug onto its own console key "
             "(create -> vault -> flip map row -> consistency check -> boot check)",
    )
    p_split.add_argument("slug")
    p_split.add_argument("--headed", action="store_true", help="show the browser (debugging only; default headless)")
    p_split.add_argument(
        "--flip-only",
        action="store_true",
        help="skip key creation; finish a run that crashed after the vault write but before the map flip",
    )
    p_split.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    p_split.add_argument(
        "--skip-boot-check",
        action="store_true",
        help="skip walking the real launch-time resolver/fetch chain after the flip",
    )
    p_split.set_defaults(func=cmd_split_key)

    p_list = sub.add_parser("list-clients", help="list mapped clients: slug, vault key NAME, created_at (no values)")
    p_list.set_defaults(func=cmd_list_clients)

    p_usage = sub.add_parser(
        "usage-export",
        help="export per-key DAILY usage to a JSONL (drives the console's own Export download)",
    )
    p_usage.add_argument("--since", default=None, help="only write days on/after YYYY-MM-DD")
    p_usage.add_argument(
        "--out",
        default=None,
        help="JSONL path (default ~/wingmen/fleet-health/state/deepseek_usage_by_key.jsonl)",
    )
    p_usage.add_argument("--headed", action="store_true")
    p_usage.set_defaults(func=cmd_usage_export)

    args = ap.parse_args(argv[1:])
    try:
        return args.func(args)
    except (LayoutMismatch, LoggedOut) as exc:
        print(f"deepseek_console.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

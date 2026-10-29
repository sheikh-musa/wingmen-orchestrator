#!/usr/bin/env python3
"""deepseek_console.py -- browser automation over the DeepSeek console, so
per-client key provisioning + usage export stop being a manual Musa chore
(op#35851, bus #82526; scale-to-N refinement bus #82540/op#35853).

PHASE 1 (this commit): scaffold only. The persistent-context profile dir,
CLI shape, allowlist/fail-closed/no-capture guardrails, and the
deepseek_key_map.json read/write path are real and tested. The CSS
SELECTORS in _SEL below are PLACEHOLDERS -- nobody has driven a real
authenticated session against the live DeepSeek console yet, so they are
best-guess only and WILL need correcting against the real DOM.

PHASE 2 (next, after Musa's one-time login): run `--headed login` once so
Musa signs in by hand into the persistent profile at ~/.wingmen/deepseek_browser/
(created 0700). Every subsequent invocation (including launchd/cron calls)
reuses that saved session headlessly -- no credential ever touches this
script, the vault, or any bus row.

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

Usage:
    deepseek_console.py login --headed
    deepseek_console.py add-client <slug>
    deepseek_console.py list-clients
    deepseek_console.py usage-export [--since YYYY-MM-DD]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ORCH_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _ORCH_ROOT)

from scripts.lib.lane_token_resolver import family_of  # noqa: E402

_MAP_FILE = os.path.join(_SCRIPT_DIR, "lib", "deepseek_key_map.json")
_PROFILE_DIR = os.path.expanduser("~/.wingmen/deepseek_browser")

_CONSOLE_URL = "https://platform.deepseek.com"

# --- allowlist: exactly the pages/actions this script may ever visit. Every
# action function below checks its own entry's marker before doing anything,
# and navigates to nothing outside this dict. PLACEHOLDER selectors (Phase 1).
_SEL = {
    "api_keys_page": {
        "url": f"{_CONSOLE_URL}/api_keys",
        "logged_in_marker": "text=API Keys",
        "create_button": "button:has-text('Create new API key')",
        "name_input": "input[placeholder='Name']",
        "confirm_create": "button:has-text('Create')",
        # DeepSeek shows the raw key exactly once, in a copy field, on creation.
        "revealed_key_value": "[data-testid='api-key-value'], code.api-key-value",
        "dialog_done": "button:has-text('Done'), button:has-text('Close')",
    },
    "usage_page": {
        "url": f"{_CONSOLE_URL}/usage",
        "logged_in_marker": "text=Usage",
        "table_rows": "table tbody tr",
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


def _context(playwright, headed: bool):
    os.makedirs(_PROFILE_DIR, exist_ok=True, mode=0o700)
    os.chmod(_PROFILE_DIR, 0o700)
    return playwright.chromium.launch_persistent_context(
        _PROFILE_DIR,
        headless=not headed,
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

    key_name = f"DEEPSEEK_KEY_{slug.upper().replace('-', '_')}"

    from playwright.sync_api import sync_playwright
    from nervous_system.vault import vault

    sel = _SEL["api_keys_page"]
    with sync_playwright() as p:
        ctx = _context(p, headed=args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(sel["url"])
        _require_logged_in(page, sel)

        page.click(sel["create_button"])
        page.fill(sel["name_input"], slug)
        page.click(sel["confirm_create"])

        try:
            page.wait_for_selector(sel["revealed_key_value"], timeout=10000)
        except Exception as exc:
            raise LayoutMismatch(
                "key-created dialog didn't show the expected revealed-value "
                "selector -- aborting before any vault write, no key value "
                "was captured"
            ) from exc

        key_value = page.locator(sel["revealed_key_value"]).first.inner_text().strip()
        # Dismiss the dialog BEFORE releasing key_value from scope -- no
        # screenshot, no further read of key_value past the vault.put below.
        if page.locator(sel["dialog_done"]).count() > 0:
            page.click(sel["dialog_done"])
        ctx.close()

    if not key_value:
        print("revealed key field was empty -- aborting, nothing written", file=sys.stderr)
        return 1

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


def cmd_usage_export(args) -> int:
    """Phase 1: scaffold only, left for Phase 2's live selector correction.
    Reads ALL mapped keys from the live map file each call (bus #82540's
    explicit "not a hardcoded list of 5" requirement) -- just not wired to a
    real page yet."""
    mapping = _load_map()
    sel = _SEL["usage_page"]

    from playwright.sync_api import sync_playwright

    rows = []
    with sync_playwright() as p:
        ctx = _context(p, headed=args.headed)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(sel["url"])
        _require_logged_in(page, sel)

        for tr in page.locator(sel["table_rows"]).all():
            rows.append(tr.inner_text())
        ctx.close()

    out = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "clients_known": sorted(mapping.keys()),
        "raw_rows": rows,
    }
    print(json.dumps(out, indent=2))
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

    p_list = sub.add_parser("list-clients", help="list mapped clients: slug, vault key NAME, created_at (no values)")
    p_list.set_defaults(func=cmd_list_clients)

    p_usage = sub.add_parser("usage-export", help="export usage across every mapped key (Phase 1 scaffold)")
    p_usage.add_argument("--since", default=None)
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

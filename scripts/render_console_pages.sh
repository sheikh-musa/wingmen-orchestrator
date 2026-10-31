#!/usr/bin/env bash
# render_console_pages.sh <out-dir> — render the console's fleet.html + lanes.html
# + asks.html + status.html + builds.html + resources.html with REAL /api data into
# PNGs, so a human/agent can EYEBALL them before deploy. Used by
# scripts/deploy_console.sh's render gate (op#12457 — verify in code, not by
# promise). Fail (non-zero) if a page won't render — a page that errors on render
# must never ship.
#
# AND fail if the DATA is not real (cc-quality #87225): `curl -s` exits 0 on
# 401/404/5xx, so this script used to bake whatever came back, paint a benign
# "NO DATA" panel, and let Gate 3 PASS — fc-v94's resources.png looked broken
# while the live page was fine. Every endpoint must now answer HTTP 200 with the
# payload keys its page actually reads, or the render fails and the deploy is
# refused. Without a live console there is no render gate: that is the point.
set -euo pipefail
OUT="${1:?usage: render_console_pages.sh <out-dir>}"
# ORCH_DIR / CHROME_BIN / CONSOLE_URL are overridable so the fail-loud contract
# can be exercised against a local server (tests/test_render_console_pages_fails_loud.py).
# In production (deploy_console.sh) none of them are set and every default below
# is exactly the previous hard-coded value.
ORCH="${ORCH_DIR:-$HOME/wingmen/orchestrator}"
cd "$ORCH"
mkdir -p "$OUT"
set -a; source .env 2>/dev/null; set +a
CHROME="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || { echo "render: Chrome not found at $CHROME" >&2; exit 2; }
# Playwright browsers: default to the reclaimer-exempt store (PR#504 kept the
# ~/wingmen/.playwright copy; ~/Library/Caches/ms-playwright keeps getting
# PARTIALLY wiped — outer dirs survive, inner executables vanish, so
# p.chromium.launch() fails with "Executable doesn't exist" mid-gate. Respects
# an explicit PLAYWRIGHT_BROWSERS_PATH override.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/wingmen/.playwright}"
H="${CONSOLE_URL:-http://100.83.21.34:8787}"
STATIC="$PWD/nervous_system/console/static"
TOK="${CONSOLE_TOKEN:-}"

# Validate one baked payload: HTTP 200 is checked by the caller; this checks the
# BODY is the shape the page needs. A missing/empty key means the harness would
# paint "NO DATA" over a broken console and the gate would still go green.
validate_json() { # $1 spec slug  $2 api path  $3 file
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

spec, path, fp = sys.argv[1:4]

# Required top-level keys and their types, per baked endpoint — the keys each
# page actually reads (nervous_system/console/app.py route handlers).
REQUIRED = {
    "fleet":     (("pulse", dict), ("lanes", list)),
    "tt":        (("rows", list), ("summary", dict)),
    "asks":      (("open", list), ("counts", dict)),
    "status":    (("items", list), ("counts", dict)),
    "builds":    (("programmes", list),),
    "resources": (("hosts", list),),
}
# Collections that must be NON-EMPTY: an empty one renders as a benign NO DATA
# panel, which is exactly how a broken payload shipped looking merely "broken".
NONEMPTY = {
    "fleet": ("pulse", "lanes"),
    "tt": ("rows",),
    "builds": ("programmes",),
    "resources": ("hosts",),
}
# Boards that carry their own count in the payload. An empty collection is
# allowed ONLY when the server's own count agrees it is empty ("0 open asks" is
# a real state); a truncated/error body has no consistent counts. Requiring
# non-empty there would make the gate cry wolf on a genuinely clean board, and
# a gate that cries wolf gets bypassed.
COUNT_RULE = {
    "asks": ("counts", "open", "open"),
    "status": ("counts", "total", "items"),
}


def die(msg):
    print(f"render: {path} — {msg}", file=sys.stderr)
    raise SystemExit(1)


if spec not in REQUIRED:
    die(f"internal: no validation spec for {spec!r} in render_console_pages.sh")

raw = open(fp, encoding="utf-8", errors="replace").read()
try:
    body = json.loads(raw)
except Exception as exc:  # noqa: BLE001 — any parse failure is a bad body
    die(f"body is not JSON ({exc}); starts: {raw[:80]!r}")
if not isinstance(body, dict):
    die(f"top-level JSON is a {type(body).__name__}, expected an object")

for key, typ in REQUIRED[spec]:
    if key not in body:
        die(f"missing top-level key {key!r} (body has {sorted(body)[:8]})")
    if not isinstance(body[key], typ):
        die(f"top-level key {key!r} is {type(body[key]).__name__}, "
            f"expected {typ.__name__}")

for key in NONEMPTY.get(spec, ()):
    if not body[key]:
        die(f"top-level key {key!r} is empty — that renders as NO DATA, "
            f"not as live data")

rule = COUNT_RULE.get(spec)
if rule:
    counts_key, field, coll = rule
    got = body[counts_key].get(field)
    if not isinstance(got, int):
        die(f"{counts_key}[{field!r}] missing or not an integer")
    if got != len(body[coll]):
        die(f"{counts_key}[{field!r}]={got} disagrees with "
            f"len({coll})={len(body[coll])}")

print(f"  fetched {path} OK ({len(raw)} bytes)")
PY
}

fetch_json() { # $1 spec slug  $2 api path
  local slug="$1" path="$2" code out
  out="$OUT/_$slug.json"
  if ! code=$(curl -sS --connect-timeout 10 --max-time 120 \
        -H "Authorization: Bearer $TOK" -o "$out" -w '%{http_code}' \
        "$H$path"); then
    echo "render: GET $path — curl could not reach $H" >&2
    return 1
  fi
  if [ "$code" != "200" ]; then
    # The body is deliberately NOT echoed: a 5xx body can carry a traceback with
    # a connection string. The file is beside the render log if a human needs it.
    echo "render: GET $path — HTTP $code (expected 200); body left in $out" >&2
    return 1
  fi
  validate_json "$slug" "$path" "$out"
}

# Pull real data once. Any of these failing fails the render, on purpose.
fetch_json fleet     /api/fleet        || exit 1
fetch_json tt        /api/token-truth  || exit 1
fetch_json asks      /api/asks-board   || exit 1
# fc-v85 (bus 77019): the "nothing goes dark" status view joins the render set.
fetch_json status    /api/status-board || exit 1
# fc-v87 (op#34918/34919): the build-status view joins the render set.
fetch_json builds    /api/build-status || exit 1
# op#36664: the machines-resources view joins the render set.
fetch_json resources /api/resources    || exit 1
SWVER=$(grep -oE 'const VERSION = "fc-v[0-9]+"' "$STATIC/sw.js" | grep -oE 'fc-v[0-9]+') \
  || { echo "render: cannot read the fc-vNN version from $STATIC/sw.js" >&2; exit 2; }

render() { # $1 page-basename (fleet|lanes)  $2 primary-data-json  $3 route
  local page="$1" data="$2" route="$3"
  python3 - "$STATIC" "$OUT" "$page" "$data" "$SWVER" "$route" <<'PY'
import sys, pathlib, json
static, out, page, data_path, swver, route = sys.argv[1:7]
static, out = pathlib.Path(static), pathlib.Path(out)
html = (static / f"{page}.html").read_text()
data = pathlib.Path(data_path).read_text().strip() or "{}"
ver = json.dumps({"version": swver, "sha": "render"})
stub = ('<script>localStorage.setItem("console_token","render");'
        'var __D=%s;var __V=%s;'
        'window.fetch=function(u,o){u=String(u);'
        'var d=u.indexOf("/api/version")>=0?__V:(u.indexOf("/api/backlog")>=0?(__D.backlog||[]):__D);'
        'return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(d);},'
        'text:function(){return Promise.resolve(JSON.stringify(d));}});};'
        'if(window.EventSource){window.EventSource=function(){return{addEventListener:function(){},close:function(){}};};}</script>'
        ) % (data, ver)
src = f'src="/static/{page}.js"'
localsrc = f'src="file://{static}/{page}.js"'
html = html.replace(src, localsrc)
html = html.replace(f'<script {localsrc}>', stub + f'<script {localsrc}>')
(out / f"_{page}-harness.html").write_text(html)
print(str(out / f"_{page}-harness.html"))
PY
  local harness="$OUT/_${page}-harness.html"
  # REAL device emulation via Playwright (iPhone 13) — not a desktop shell
  # (op#12477). Closes the gate blind spot that let the Lane-manager button ship
  # invisible on a real phone while a --window-size render looked fine.
  "$ORCH/.venv/bin/python3" \
    "$ORCH/scripts/render_console_playwright.py" \
    "$harness" "$OUT/${page}.png" "iPhone 13" 2>&1 || {
      echo "render: playwright render failed for ${page}" >&2; return 1; }
  [ -s "$OUT/${page}.png" ] || { echo "render: ${page}.png not produced" >&2; return 1; }
  echo "  rendered ${page} -> $OUT/${page}.png ($(stat -f%z "$OUT/${page}.png") bytes)"
}

echo "[render_console_pages] rendering fleet + lanes + asks + status + builds + resources with live data ($SWVER)..."
render fleet "$OUT/_fleet.json" "/"      || exit 1
render lanes "$OUT/_tt.json"    "/lanes" || exit 1
render asks  "$OUT/_asks.json"  "/asks"  || exit 1
render status "$OUT/_status.json" "/status" || exit 1
render builds "$OUT/_builds.json" "/builds" || exit 1
render resources "$OUT/_resources.json" "/resources" || exit 1
echo "[render_console_pages] OK — eyeball: $OUT/fleet.png + $OUT/lanes.png + $OUT/asks.png + $OUT/status.png + $OUT/builds.png + $OUT/resources.png"

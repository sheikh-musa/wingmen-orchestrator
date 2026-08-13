#!/usr/bin/env bash
# render_console_pages.sh <out-dir> — render the console's fleet.html + lanes.html
# with REAL /api data into PNGs, so a human/agent can EYEBALL them before deploy.
# Used by scripts/deploy_console.sh's render gate (op#12457 — verify in code, not
# by promise). Fail (non-zero) if a page won't render — a page that errors on
# render must never ship.
set -euo pipefail
OUT="${1:?usage: render_console_pages.sh <out-dir>}"
cd "$HOME/wingmen/orchestrator"
mkdir -p "$OUT"
set -a; source .env 2>/dev/null; set +a
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || { echo "render: Chrome not found at $CHROME" >&2; exit 2; }
H="http://100.83.21.34:8787"
STATIC="$PWD/nervous_system/console/static"
TOK="${CONSOLE_TOKEN:-}"

# Pull real data once (falls back to empty objects if the console is down —
# a shell render still proves the CSS/JS parse, but we prefer live data).
curl -s -H "Authorization: Bearer $TOK" "$H/api/fleet" > "$OUT/_fleet.json" 2>/dev/null || echo '{}' > "$OUT/_fleet.json"
curl -s -H "Authorization: Bearer $TOK" "$H/api/token-truth" > "$OUT/_tt.json" 2>/dev/null || echo '{}' > "$OUT/_tt.json"
SWVER=$(grep -oE 'const VERSION = "fc-v[0-9]+"' "$STATIC/sw.js" | grep -oE 'fc-v[0-9]+')

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
  "$HOME/wingmen/orchestrator/.venv/bin/python3" \
    "$HOME/wingmen/orchestrator/scripts/render_console_playwright.py" \
    "$harness" "$OUT/${page}.png" "iPhone 13" 2>&1 || {
      echo "render: playwright render failed for ${page}" >&2; return 1; }
  [ -s "$OUT/${page}.png" ] || { echo "render: ${page}.png not produced" >&2; return 1; }
  echo "  rendered ${page} -> $OUT/${page}.png ($(stat -f%z "$OUT/${page}.png") bytes)"
}

echo "[render_console_pages] rendering fleet + lanes with live data ($SWVER)..."
render fleet "$OUT/_fleet.json" "/"      || exit 1
render lanes "$OUT/_tt.json"    "/lanes" || exit 1
echo "[render_console_pages] OK — eyeball: $OUT/fleet.png + $OUT/lanes.png"

// Fleet Console service worker — HARDENED update flow.
//
// The failure mode this exists to prevent: an adcda PWA blanked the operator
// mid-session on update. Root causes that class of bug, all addressed below:
//   1. skipWaiting() firing before the new cache is actually populated ->
//      activate takes over with an empty/partial cache.
//   2. The app shell (navigation HTML) being served cache-first, so a stale
//      or half-migrated shell wins a race against the fresh one.
//   3. clients.claim() handing control to open pages with no reload, so the
//      page keeps running with the old assets still loaded even though a
//      new SW is now in charge — a half-old-half-new state.
//   4. A reload-on-update handler with no guard, causing an infinite
//      refresh loop (itself a distinct blank/thrash failure).
//
// Never touches /api/*, /docs*, /media* — those are live, auth-sensitive
// data and must always hit the network.

// Bump whenever a static asset changes. The bump is what an already-installed
// PWA detects (sw.js is served no-cache) — it invalidates the old shell cache
// so a phone can't stay stuck on a stale shell (the exact bug: operator's
// iPhone kept serving the removed breakglass input after 80bf1a6). v2 also
// makes app.js network-first (below) so future JS changes propagate without a
// manual bump — the bump is now only a belt-and-suspenders cache reset.
//
// RATE-LIMIT (op#10291 — the Aug-3 incident): BATCH a day's console changes into
// ONE version bump. Each bump triggers the client's hard-reset (confirm-then-
// clear); shipping many bumps in a day (v22->v27 in one day stranded the operator)
// re-churns his PWA cache repeatedly over the marginal AD<->SG relay. So: bump
// this + fleet.js APP_BUILD ONCE per deploy-day, not per-change. `scripts/
// check_console_version_cadence.py` warns if you're about to re-bump same-day.
const VERSION = "fc-v49";

// Network-first with a HARD deadline. The failure this fixes (2026-07-11): the
// navigation/shell fetch was network-first but had NO timeout, so a slow-but-
// alive tailnet made respondWith() hang until iOS WebKit's own internal timeout
// fired its native "Could not connect to the server" (X_X) page — the operator
// never fell back to the perfectly-good cached shell. With a deadline, a slow
// network aborts fast and we serve cache instantly; the app then handles its
// own live-data retry. Kept short (well under WebKit's native timeout) so the
// cached shell always wins the race on a bad connection.
const NET_TIMEOUT_MS = 3000;
function fetchDeadline(req) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), NET_TIMEOUT_MS);
  return fetch(req, { signal: ctrl.signal }).finally(() => clearTimeout(timer));
}
const SHELL_CACHE = `fleet-console-shell-${VERSION}`;
const SHELL_ASSETS = [
  // "/" now serves the Fleet view (operator #3440), so fleet.js is the primary
  // shell script and must be precached for offline; app.js still backs /classic.
  "/",
  "/static/fleet.js",
  "/static/app.js",
  "/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/favicon.ico",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      // skipWaiting only AFTER the precache promise resolves — an empty or
      // half-populated cache must never be what activate() takes over with.
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Live, auth-sensitive data: never intercept. Let the browser hit the
  // network directly, every time.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/docs") ||
    url.pathname.startsWith("/media")
  ) {
    return;
  }

  // App-shell navigation: NETWORK-FIRST. The shell must never be served
  // stale-first — that race (cache wins, then the SW/page disagree about
  // what's current) is the direct mechanism behind the blank-screen bug.
  // Cache is fallback-only, for offline.
  if (req.mode === "navigate" || url.pathname === "/") {
    event.respondWith(
      fetchDeadline(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
          return res;
        })
        // Slow OR failed network -> cached shell immediately, so the operator
        // sees the app (with last-good data) instead of WebKit's error page.
        .catch(() => caches.match(req).then((cached) => cached || caches.match("/")))
    );
    return;
  }

  // app.js + manifest: NETWORK-FIRST (like the shell). These carry the app's
  // behaviour, so a stale copy is as bad as a stale shell — serving them
  // cache-first (the old stale-while-revalidate) meant a JS change needed a
  // second reload to land, and a version that never bumped never landed at
  // all. Network-first => fresh on every online load; cache is offline-only
  // fallback. Files are tiny and tailnet-local, so the cost is negligible.
  if (url.pathname === "/static/app.js" || url.pathname === "/static/fleet.js" || url.pathname === "/manifest.json") {
    event.respondWith(
      fetchDeadline(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
          return res;
        })
        // Slow/failed -> serve the cached copy so the app still boots offline
        // or on a degraded tailnet (never a broken script -> blank page).
        .catch(() => caches.match(req))
    );
    return;
  }

  // Other static assets (icons, etc.): stale-while-revalidate — instant from
  // cache, refreshed in the background. Fine for rarely-changing binary assets.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(SHELL_CACHE).then((cache) =>
        cache.match(req).then((cached) => {
          const network = fetch(req)
            .then((res) => {
              cache.put(req, res.clone());
              return res;
            })
            .catch(() => cached);
          return cached || network;
        })
      )
    );
    return;
  }
  // Everything else: default browser behavior (no interception).
});

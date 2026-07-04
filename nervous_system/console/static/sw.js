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

const VERSION = "fc-v1"; // bump whenever a static asset changes
const SHELL_CACHE = `fleet-console-shell-${VERSION}`;
const SHELL_ASSETS = [
  "/",
  "/static/app.js",
  "/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
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
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then((cached) => cached || caches.match("/")))
    );
    return;
  }

  // Static assets (JS/manifest/icons): stale-while-revalidate — instant from
  // cache, refreshed in the background for next time.
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.json") {
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

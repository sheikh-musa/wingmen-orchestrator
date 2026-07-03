// Fleet Docs SPA — read-only doc browser (vanilla JS, fetch).
// Auth: operator token sent ONLY in the Authorization header (never the URL),
// reusing the same localStorage key as the main console so the operator enters
// it ONCE per device. Routing is path-based (/docs, /docs/<repo>/<path>) so a
// deep link is directly tappable; the SPA reads window.location and fetches the
// doc with the stored bearer token.
(function () {
  "use strict";

  var token = localStorage.getItem("console_token") || sessionStorage.getItem("console_token") || "";

  var $ = function (id) { return document.getElementById(id); };
  function authHeaders() { return { Authorization: "Bearer " + token }; }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? esc(iso) : d.toLocaleString();
  }
  function fmtSize(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  // Parse the current path. "/docs" -> catalog; "/docs/<repo>/<rel...>" -> doc.
  function route() {
    var p = window.location.pathname;
    var m = p.match(/^\/docs\/([^/]+)\/(.+)$/);
    if (m) return { view: "doc", repo: decodeURIComponent(m[1]), path: decodeURIComponent(m[2]) };
    return { view: "catalog" };
  }

  function go(path) {
    window.history.pushState({}, "", path);
    render();
  }

  function unauthorized() {
    $("main").innerHTML = '<div class="empty">Unauthorized — check the operator token and Connect.</div>';
  }

  function renderCatalog() {
    var main = $("main");
    main.innerHTML = '<div class="empty">Loading docs…</div>';
    fetch("/api/docs", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { unauthorized(); throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (groups) {
        if (!groups.length) { main.innerHTML = '<div class="empty">No docs found.</div>'; return; }
        var total = groups.reduce(function (a, g) { return a + g.count; }, 0);
        var html = '<div class="repo-head" style="position:static">' +
          '<input id="filter" class="search" placeholder="filter ' + total + ' docs…" autocapitalize="off" autocorrect="off" spellcheck="false" style="margin-top:6px" /></div>';
        html += groups.map(function (g) {
          var items = g.docs.map(function (d) {
            var href = "/docs/" + encodeURIComponent(g.repo) + "/" +
              d.path.split("/").map(encodeURIComponent).join("/");
            return '<a class="doc" data-search="' + esc((g.repo + " " + d.title + " " + d.path).toLowerCase()) + '" href="' + href + '">' +
              '<div class="title">' + esc(d.title) + '</div>' +
              '<div class="meta">' + esc(d.path) + ' &middot; ' + esc(fmtSize(d.size)) +
              ' &middot; ' + esc(fmtDate(d.mtime)) + '</div>' +
            '</a>';
          }).join("");
          return '<div class="repo-head">' + esc(g.repo) +
            ' <span class="count">· ' + g.count + '</span></div>' + items;
        }).join("");
        main.innerHTML = html;

        // intercept doc taps for SPA nav (deep link still works on hard load)
        main.querySelectorAll("a.doc").forEach(function (a) {
          a.addEventListener("click", function (e) {
            e.preventDefault();
            go(a.getAttribute("href"));
          });
        });
        // live client-side filter
        var f = $("filter");
        if (f) {
          f.addEventListener("input", function () {
            var q = f.value.trim().toLowerCase();
            main.querySelectorAll("a.doc").forEach(function (a) {
              a.style.display = (!q || a.getAttribute("data-search").indexOf(q) !== -1) ? "" : "none";
            });
          });
        }
      })
      .catch(function () {});
  }

  function renderDoc(repo, path) {
    var main = $("main");
    main.innerHTML = '<div class="empty">Loading…</div>';
    var url = "/api/docs/" + encodeURIComponent(repo) + "/" +
      path.split("/").map(encodeURIComponent).join("/");
    fetch(url, { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { unauthorized(); throw new Error("unauthorized"); }
        if (r.status === 404) { main.innerHTML = '<div class="empty">Doc not found.</div>'; throw new Error("404"); }
        return r.json();
      })
      .then(function (doc) {
        main.innerHTML =
          '<div class="docview">' +
            '<div class="backbar">' +
              '<button class="ghost" id="back">&larr; All docs</button>' +
              '<span class="crumb">' + esc(repo) + ' / ' + esc(path) +
                ' &middot; ' + esc(fmtDate(doc.mtime)) + '</span>' +
            '</div>' +
            '<article class="article">' + doc.html + '</article>' +
          '</div>';
        var b = $("back");
        if (b) b.addEventListener("click", function () { go("/docs"); });
        window.scrollTo(0, 0);
      })
      .catch(function () {});
  }

  function render() {
    if (!token) {
      $("main").innerHTML = '<div class="empty">Enter the operator token and Connect.</div>';
      return;
    }
    var r = route();
    if (r.view === "doc") renderDoc(r.repo, r.path);
    else renderCatalog();
  }

  function connect() {
    token = $("token").value.trim();
    if (!token) return;
    localStorage.setItem("console_token", token);
    render();
  }

  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", function (e) { if (e.key === "Enter") connect(); });
  window.addEventListener("popstate", render);

  if (token) { $("token").value = token; }
  render();
})();

// Fleet Screenshots SPA — read-only media browser (vanilla JS, fetch).
// Auth: operator token sent ONLY in the Authorization header (never the URL),
// reusing the same localStorage key as the main console + docs, so the operator
// enters it ONCE per device. Images can't carry an auth header on a bare <img>,
// so each thumbnail/full image is blob-fetched WITH the header and shown via an
// object URL — keeping auth header-only end to end (matches docs.js).
(function () {
  "use strict";

  var token = localStorage.getItem("console_token") || sessionStorage.getItem("console_token") || "";
  var $ = function (id) { return document.getElementById(id); };
  function authHeaders() { return { Authorization: "Bearer " + token }; }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtDate(iso) { if (!iso) return ""; var d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString(); }
  function fmtSize(n) {
    if (n == null) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return Math.round(n / 1024) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }
  function fileUrl(project, path) {
    return "/api/media-file/" + encodeURIComponent(project) + "/" +
      path.split("/").map(encodeURIComponent).join("/");
  }
  // blob-fetch a media file with the bearer header -> object URL (or null on 401/err)
  function fetchBlobURL(url) {
    return fetch(url, { headers: authHeaders() }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.blob();
    }).then(function (b) { return URL.createObjectURL(b); });
  }

  function unauthorized() {
    $("main").innerHTML = '<div class="empty">Unauthorized — check the operator token and Connect.</div>';
  }

  var observer = null;
  function lazyObserve() {
    if (observer) observer.disconnect();
    if (!("IntersectionObserver" in window)) {
      // no IO support: load all eagerly
      document.querySelectorAll("img.thumb[data-url]").forEach(loadThumb);
      return;
    }
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { loadThumb(en.target); observer.unobserve(en.target); }
      });
    }, { rootMargin: "300px" });
    document.querySelectorAll("img.thumb[data-url]").forEach(function (img) { observer.observe(img); });
  }
  function loadThumb(img) {
    var url = img.getAttribute("data-url");
    if (!url) return;
    img.removeAttribute("data-url");
    fetchBlobURL(url).then(function (obj) { img.src = obj; }).catch(function () {});
  }

  function openLightbox(project, file) {
    var lb = document.createElement("div");
    lb.className = "lb";
    lb.innerHTML =
      '<div class="bar">' +
        '<button class="ghost" id="lbclose">&larr; Back</button>' +
        '<span class="crumb">' + esc(project) + ' / ' + esc(file.path) +
          ' &middot; ' + esc(fmtSize(file.size)) + ' &middot; ' + esc(fmtDate(file.mtime)) + '</span>' +
        '<button class="ghost" id="lbdl">Open raw</button>' +
      '</div>' +
      '<div class="body" id="lbbody"><div class="empty">Loading…</div></div>';
    document.body.appendChild(lb);
    var objURL = null;
    function teardown() {
      if (objURL) URL.revokeObjectURL(objURL);
      lb.remove();
    }
    $("lbclose").addEventListener("click", teardown);
    var url = fileUrl(project, file.path);
    fetchBlobURL(url).then(function (obj) {
      objURL = obj;
      var body = $("lbbody");
      if (file.kind === "pdf") body.innerHTML = '<iframe src="' + obj + '"></iframe>';
      else body.innerHTML = '<img src="' + obj + '" alt="' + esc(file.name) + '" />';
      $("lbdl").addEventListener("click", function () { window.open(obj, "_blank"); });
    }).catch(function () {
      $("lbbody").innerHTML = '<div class="empty">Could not load (check token).</div>';
    });
  }

  function renderCatalog() {
    var main = $("main");
    main.innerHTML = '<div class="empty">Loading screenshots…</div>';
    fetch("/api/media", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { unauthorized(); throw new Error("401"); } return r.json(); })
      .then(function (groups) {
        if (!groups.length) { main.innerHTML = '<div class="empty">No screenshots found.</div>'; return; }
        var total = groups.reduce(function (a, g) { return a + g.count; }, 0);
        var html = '<div class="repo-head" style="position:static">' +
          '<input id="filter" class="search" placeholder="filter ' + total + ' files…" autocapitalize="off" autocorrect="off" spellcheck="false" style="margin-top:6px" /></div>';
        html += groups.map(function (g) {
          var cards = g.files.map(function (f, i) {
            var u = fileUrl(g.project, f.path);
            var search = esc((g.project + " " + f.path).toLowerCase());
            var thumb = f.kind === "pdf"
              ? '<div class="thumb pdf">PDF</div>'
              : '<img class="thumb" loading="lazy" alt="' + esc(f.name) + '" data-url="' + esc(u) + '" />';
            return '<a class="card" data-search="' + search + '" data-proj="' + esc(g.project) +
              '" data-idx="' + i + '" href="#">' + thumb +
              '<div class="cap"><span class="nm">' + esc(f.name) + '</span>' +
              esc(fmtSize(f.size)) + '</div></a>';
          }).join("");
          return '<div class="repo-head">' + esc(g.project) +
            ' <span class="count">· ' + g.count + '</span></div><div class="grid">' + cards + '</div>';
        }).join("");
        main.innerHTML = html;

        // map for click -> file object
        var byProj = {};
        groups.forEach(function (g) { byProj[g.project] = g.files; });
        main.querySelectorAll("a.card").forEach(function (a) {
          a.addEventListener("click", function (e) {
            e.preventDefault();
            var files = byProj[a.getAttribute("data-proj")] || [];
            var f = files[parseInt(a.getAttribute("data-idx"), 10)];
            if (f) openLightbox(a.getAttribute("data-proj"), f);
          });
        });
        // live client-side filter (hides whole cards)
        var fl = $("filter");
        if (fl) fl.addEventListener("input", function () {
          var q = fl.value.trim().toLowerCase();
          main.querySelectorAll("a.card").forEach(function (a) {
            a.style.display = (!q || a.getAttribute("data-search").indexOf(q) !== -1) ? "" : "none";
          });
        });
        lazyObserve();
      })
      .catch(function () {});
  }

  function render() {
    if (!token) { $("main").innerHTML = '<div class="empty">Enter the operator token and Connect.</div>'; return; }
    renderCatalog();
  }
  function connect() {
    token = $("token").value.trim();
    if (!token) return;
    localStorage.setItem("console_token", token);
    render();
  }
  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", function (e) { if (e.key === "Enter") connect(); });
  if (token) { $("token").value = token; }
  render();
})();

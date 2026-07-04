"""Read-only fleet-docs catalog + renderer (DOCS section).

Surfaces fleet markdown docs to the operator's phone over the tailnet, browsable
+ openable, read-only. Companion to db.py — but this reads the filesystem, not
the DB.

Doc roots (each becomes a "repo" group):
  * orchestrator's own  docs/**/*.md
  * each managed project  ~/wingmen/projects/<repo>/docs/**/*.md

Design constraints (mirror the rest of the console):
  * Stdlib-only. No markdown dependency exists in the venv, so we ship a tiny,
    safe markdown->HTML renderer (headings, lists, code, links, emphasis). It
    HTML-escapes first, so a doc can never inject markup.
  * Read-only by construction: only ever opens *.md files for reading, never
    writes, and is hard-scoped to the configured roots with traversal guards.
  * node_modules (and other vendored dirs) are excluded.
"""
from __future__ import annotations

import html
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# --- doc roots ----------------------------------------------------------------

# orchestrator root = .../orchestrator (this file is nervous_system/console/docs.py)
_ORCH_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PROJECTS_ROOT = _ORCH_ROOT.parent / "projects"

# Dirs we never descend into.
_EXCLUDE_DIRS = {"node_modules", ".git", ".next", "dist", "build", "vendor", ".venv"}


def _doc_roots() -> List[Tuple[str, pathlib.Path]]:
    """Return [(repo_label, abs_docs_dir)]. CONSOLE_DOCS_ROOTS can override
    (colon-separated 'label=path' pairs) for tests / alternate layouts."""
    override = os.environ.get("CONSOLE_DOCS_ROOTS")
    if override:
        roots: List[Tuple[str, pathlib.Path]] = []
        for chunk in override.split(":"):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            label, _, path = chunk.partition("=")
            roots.append((label.strip(), pathlib.Path(path.strip()).resolve()))
        return roots

    roots = [("orchestrator", (_ORCH_ROOT / "docs").resolve())]
    if _PROJECTS_ROOT.is_dir():
        for child in sorted(_PROJECTS_ROOT.iterdir()):
            d = child / "docs"
            if d.is_dir():
                roots.append((child.name, d.resolve()))
    return roots


def _root_for(repo: str) -> Optional[pathlib.Path]:
    for label, path in _doc_roots():
        if label == repo:
            return path
    return None


# --- title extraction ---------------------------------------------------------

_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*#*\s*$")


def _extract_title(path: pathlib.Path) -> str:
    """First markdown H1, else the filename stem (humanised)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(40):  # title is near the top; don't scan whole file
                line = fh.readline()
                if not line:
                    break
                m = _H1_RE.match(line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return path.stem.replace("-", " ").replace("_", " ")


# --- catalog ------------------------------------------------------------------


def list_docs() -> List[dict]:
    """Return doc groups: [{repo, count, docs:[{path,title,mtime,size}]}].

    `path` is the doc's path RELATIVE to its repo docs root (POSIX), used to
    build the /docs/<repo>/<path> link. Sorted by repo, then path.
    """
    groups: List[dict] = []
    for repo, root in _doc_roots():
        if not root.is_dir():
            continue
        docs: List[dict] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # prune excluded dirs in place so os.walk skips them
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
            for name in filenames:
                if not name.lower().endswith(".md"):
                    continue
                full = pathlib.Path(dirpath) / name
                try:
                    st = full.stat()
                except OSError:
                    continue
                rel = full.relative_to(root).as_posix()
                docs.append(
                    {
                        "path": rel,
                        "title": _extract_title(full),
                        "mtime": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                        "size": st.st_size,
                    }
                )
        if docs:
            docs.sort(key=lambda d: d["path"].lower())
            groups.append({"repo": repo, "count": len(docs), "docs": docs})
    return groups


def _safe_resolve(repo: str, rel_path: str) -> Optional[pathlib.Path]:
    """Resolve repo+rel_path to an absolute *.md inside the repo root, or None.

    Hard traversal guard: the resolved path must live under the repo root and be
    an existing .md file.
    """
    root = _root_for(repo)
    if root is None:
        return None
    if not rel_path.lower().endswith(".md"):
        return None
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)  # raises if outside root
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def read_doc(repo: str, rel_path: str) -> Optional[dict]:
    """Return {repo, path, title, mtime, html} for one doc, or None if not found.

    The body is rendered to safe HTML (escaped-first). Caller is auth-gated.
    """
    full = _safe_resolve(repo, rel_path)
    if full is None:
        return None
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
        st = full.stat()
    except OSError:
        return None
    return {
        "repo": repo,
        "path": rel_path,
        "title": _extract_title(full),
        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "html": render_markdown(text),
    }


# --- tiny, safe markdown -> HTML ----------------------------------------------
# Escape-first: the source is HTML-escaped before any markup is emitted, so a
# doc can never inject script/markup. We then re-introduce a SAFE subset.

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\*])\*(?!\s)([^*]+?)\*(?![\*])")
# [text](url) — url is escaped already; only allow http(s)/relative, no javascript:
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")


def _inline(text: str) -> str:
    text = _INLINE_CODE_RE.sub(lambda m: "<code>" + m.group(1) + "</code>", text)
    text = _BOLD_RE.sub(lambda m: "<strong>" + m.group(1) + "</strong>", text)
    text = _ITALIC_RE.sub(lambda m: "<em>" + m.group(1) + "</em>", text)

    def _link(m: "re.Match") -> str:
        label, url = m.group(1), m.group(2)
        low = url.lower()
        # block dangerous schemes (url is already HTML-escaped). Allow http(s),
        # anchors, and relative paths only.
        if low.startswith(("http://", "https://", "#", "/", "./", "../")) or (
            ":" not in low
        ):
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
        return label

    text = _LINK_RE.sub(_link, text)
    return text


def render_markdown(src: str) -> str:
    """Render a SAFE HTML subset from markdown. Escape-first; no raw HTML passes
    through. Supports headings, fenced + indented code, ul/ol lists, blockquotes,
    paragraphs, inline code/bold/italic/links, hr."""
    # 1) escape everything up front — quote=True so `"`/`'` are escaped too,
    #    closing the href attribute-injection vector in _link (a crafted link
    #    URL containing a quote can't break out of href="{url}").
    escaped = html.escape(src, quote=True)
    lines = escaped.split("\n")

    out: List[str] = []
    in_code = False
    list_stack: List[str] = []  # 'ul' / 'ol'

    def close_lists() -> None:
        while list_stack:
            out.append(f"</{list_stack.pop()}>")

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # fenced code ```
        if stripped.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                close_lists()
                out.append("<pre><code>")
                in_code = True
            i += 1
            continue
        if in_code:
            out.append(line + "\n")
            i += 1
            continue

        # blank line
        if not stripped:
            close_lists()
            i += 1
            continue

        # horizontal rule
        if re.match(r"^([-*_])\1{2,}$", stripped):
            close_lists()
            out.append("<hr/>")
            i += 1
            continue

        # heading
        m = _HEADING_RE.match(line)
        if m:
            close_lists()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # blockquote
        if stripped.startswith("&gt;"):
            close_lists()
            quote = re.sub(r"^\s*&gt;\s?", "", line)
            out.append(f"<blockquote>{_inline(quote)}</blockquote>")
            i += 1
            continue

        # ordered list
        mo = _OL_RE.match(line)
        if mo:
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                out.append("<ol>")
                list_stack.append("ol")
            out.append(f"<li>{_inline(mo.group(1))}</li>")
            i += 1
            continue

        # unordered list
        mu = _UL_RE.match(line)
        if mu:
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                out.append("<ul>")
                list_stack.append("ul")
            out.append(f"<li>{_inline(mu.group(1))}</li>")
            i += 1
            continue

        # paragraph (greedy across consecutive non-special lines)
        close_lists()
        para = [line]
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            ns = nxt.strip()
            if (
                not ns
                or ns.startswith("```")
                or _HEADING_RE.match(nxt)
                or _OL_RE.match(nxt)
                or _UL_RE.match(nxt)
                or ns.startswith("&gt;")
                or re.match(r"^([-*_])\1{2,}$", ns)
            ):
                break
            para.append(nxt)
            j += 1
        out.append("<p>" + "<br/>".join(_inline(p.strip()) for p in para) + "</p>")
        i = j

    if in_code:
        out.append("</code></pre>")
    close_lists()
    return "\n".join(out)

"""memory_recall.py — op#34635/34637's option-3 stopgap: ranked recall over a lane's
memory/*.md files when the exact keyword to grep for is unknown.

WHY: grep-at-boot only surfaces a memory when the agent already knows the right word.
A real semantic index (embeddings) is the proposed long-term fix, but it depends on a
reachable embedding service and a new substrate table — both currently blocked (the
Mac Studio BGE-M3 endpoint is Tailscale-offline, the migration awaits console's vault
confirm gate). This stopgap needs NEITHER: it is a pure-stdlib BM25-style ranker over
the files already on disk, so an agent can query by topic/phrase instead of one exact
word and get a ranked shortlist to Read — same files stay the single source of truth,
this only ranks and points at them.

Deliberately NOT true semantic matching (no query expansion, no embeddings) — it is
scoped to be the cheap, zero-new-dependency, zero-new-infra interim step, not a
replacement for option 1 once that's unblocked.

Usage:
  python scripts/memory_recall.py "<query>" [--lane /path/or/slug] [--top N]
"""
from __future__ import annotations

import argparse
import math
import pathlib
import re
import sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
_INDEX_FILES = {"MEMORY.md", "MEMORY-INDEX.md"}
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Headline fields score far above body prose — a term in the one-line description is a
# much stronger relevance signal than the same term buried once in a long memory body.
_DESCRIPTION_WEIGHT = 4.0
_NAME_WEIGHT = 3.0
_BODY_WEIGHT = 1.0

# BM25 constants (standard defaults — Robertson/Sparck Jones). No tuning done against
# real recall data yet; revisit once this stopgap has real usage to measure against.
_BM25_K1 = 1.5
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _parse_frontmatter(text: str) -> dict:
    """name/description/metadata.type only — the fields this ranker and its callers need.
    Not a general YAML parser; memory_index_audit.py's own regex-field-extraction style,
    reused rather than adding a yaml dependency for 3 scalar fields."""
    name_m = re.search(r"^name:\s*(.+?)\s*$", text, re.M)
    desc_m = re.search(r'^description:\s*"?(.*?)"?\s*$', text, re.M)
    type_m = re.search(r"^\s+type:\s*(\w+)\s*$", text, re.M)
    return {
        "name": name_m.group(1).strip() if name_m else "",
        "description": desc_m.group(1).strip() if desc_m else "",
        "type": type_m.group(1).strip() if type_m else "",
    }


def _corpus(mem_dir: pathlib.Path) -> list[dict]:
    docs = []
    for path in sorted(mem_dir.glob("*.md")):
        if path.name in _INDEX_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        body = text.split("---", 2)[-1] if text.startswith("---") else text
        weighted_tokens = (
            _tokenize(fm["name"]) * int(_NAME_WEIGHT)
            + _tokenize(fm["description"]) * int(_DESCRIPTION_WEIGHT)
            + _tokenize(body) * int(_BODY_WEIGHT)
        )
        docs.append({"file": path.name, "type": fm["type"], "description": fm["description"],
                     "tokens": weighted_tokens})
    return docs


def _bm25_scores(docs: list[dict], query_terms: list[str]) -> list[float]:
    n = len(docs)
    if n == 0:
        return []
    lengths = [len(d["tokens"]) for d in docs]
    avg_len = sum(lengths) / n
    doc_freq = {t: 0 for t in set(query_terms)}
    term_counts = []
    for d in docs:
        counts = {}
        for tok in d["tokens"]:
            counts[tok] = counts.get(tok, 0) + 1
        term_counts.append(counts)
        for t in doc_freq:
            if t in counts:
                doc_freq[t] += 1

    scores = [0.0] * n
    for t in set(query_terms):
        df = doc_freq[t]
        if df == 0:
            continue
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for i, counts in enumerate(term_counts):
            f = counts.get(t, 0)
            if f == 0:
                continue
            denom = f + _BM25_K1 * (1 - _BM25_B + _BM25_B * lengths[i] / avg_len)
            scores[i] += idf * (f * (_BM25_K1 + 1)) / denom
    return scores


def recall(mem_dir: pathlib.Path, query: str, top_n: int = 5) -> list[dict]:
    docs = _corpus(mem_dir)
    query_terms = _tokenize(query)
    if not docs or not query_terms:
        return []
    scores = _bm25_scores(docs, query_terms)
    hits = [
        {"file": d["file"], "type": d["type"], "description": d["description"], "score": s}
        for d, s in zip(docs, scores)
        if s > 0
    ]
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_n]


def lane_dir_for(cwd: str) -> pathlib.Path:
    """The same slug scheme the harness uses for ~/.claude/projects/<slug>: every path
    separator becomes a hyphen."""
    slug = cwd.replace("/", "-")
    return PROJECTS / slug / "memory"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--lane", default=None,
                         help="lane working-directory path or memory/ dir; defaults to cwd")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args(argv)

    cwd = args.lane or str(pathlib.Path.cwd())
    mem_dir = lane_dir_for(cwd)
    if not mem_dir.is_dir() and args.lane:
        # Fallback for a caller who already passed a literal memory/ dir (or its
        # parent) rather than a lane working-directory path.
        literal = pathlib.Path(args.lane)
        mem_dir = literal if literal.name == "memory" else literal / "memory"
    if not mem_dir.is_dir():
        print(f"no memory dir at {mem_dir}", file=sys.stderr)
        return 1

    hits = recall(mem_dir, args.query, top_n=args.top)
    if not hits:
        print("no matches")
        return 0
    for h in hits:
        print(f"{h['score']:.3f}\t{h['file']}\t({h['type']})\t{h['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

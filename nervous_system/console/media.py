"""Read-only screenshot/media catalog + byte server (SCREENSHOTS section).

Companion to docs.py — but instead of markdown it surfaces the operator's
screenshot/asset archive (logs/tg_media), already organised into per-project
subfolders by scripts/sort_tg_media.sh. Browsable + openable from the phone over
the tailnet, read-only.

Design constraints (mirror docs.py / the rest of the console):
  * Stdlib-only.
  * Read-only by construction: only ever opens existing files for reading under
    the single configured media root, with a hard traversal guard.
  * Serves image + pdf bytes with the right Content-Type; the catalog lists those
    kinds. Auth is enforced by the caller (app.py), header-only.
"""
from __future__ import annotations

import mimetypes
import os
import pathlib
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# orchestrator root = .../orchestrator (this file is nervous_system/console/media.py)
_ORCH_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _media_root() -> pathlib.Path:
    """logs/tg_media; CONSOLE_MEDIA_ROOT can override for tests."""
    override = os.environ.get("CONSOLE_MEDIA_ROOT")
    if override:
        return pathlib.Path(override).resolve()
    return (_ORCH_ROOT / "logs" / "tg_media").resolve()


# Extensions we surface, and how the UI should treat each.
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_PDF_EXT = {".pdf"}
_EXCLUDE_DIRS = {".git", "node_modules"}


def _kind(name: str) -> Optional[str]:
    ext = pathlib.Path(name).suffix.lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _PDF_EXT:
        return "pdf"
    return None


def list_media() -> List[dict]:
    """Return project groups: [{project, count, files:[{path,name,kind,mtime,size}]}].

    Each immediate subfolder of the media root is one "project" group; files are
    walked recursively within it (so nested folders like cosem-adcda/gtemplates
    are included, with `path` relative to the project folder). Loose files at the
    media root are grouped under "_unsorted". Sorted by project, then path.
    """
    root = _media_root()
    if not root.is_dir():
        return []

    groups: List[dict] = []

    def _collect(base: pathlib.Path, project: str) -> None:
        files: List[dict] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
            for nm in filenames:
                kind = _kind(nm)
                if kind is None:
                    continue
                full = pathlib.Path(dirpath) / nm
                try:
                    st = full.stat()
                except OSError:
                    continue
                files.append(
                    {
                        "path": full.relative_to(base).as_posix(),
                        "name": nm,
                        "kind": kind,
                        "mtime": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                        "size": st.st_size,
                    }
                )
        if files:
            files.sort(key=lambda d: d["path"].lower())
            groups.append({"project": project, "count": len(files), "files": files})

    subdirs = sorted([c for c in root.iterdir() if c.is_dir()], key=lambda p: p.name.lower())
    for child in subdirs:
        _collect(child, child.name)

    # loose files directly under the root
    loose: List[dict] = []
    for c in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if c.is_file() and _kind(c.name):
            try:
                st = c.stat()
            except OSError:
                continue
            loose.append(
                {
                    "path": c.name,
                    "name": c.name,
                    "kind": _kind(c.name),
                    "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "size": st.st_size,
                }
            )
    if loose:
        groups.append({"project": "_unsorted", "count": len(loose), "files": loose})

    return groups


def _safe_resolve(project: str, rel_path: str) -> Optional[pathlib.Path]:
    """Resolve project+rel_path to an existing media file under the root, or None.

    Hard traversal guard: the resolved path must live under the media root, under
    the named project folder (or the root itself for _unsorted), and be a file of
    a surfaced kind.
    """
    root = _media_root()
    if _kind(rel_path) is None:
        return None
    base = root if project == "_unsorted" else (root / project)
    try:
        base = base.resolve()
        base.relative_to(root)  # project must be inside root (blocks ../ in project)
    except ValueError:
        return None
    candidate = (base / rel_path).resolve()
    try:
        candidate.relative_to(base)  # blocks ../ in rel_path
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def read_media_bytes(project: str, rel_path: str) -> Optional[Tuple[bytes, str]]:
    """Return (raw_bytes, content_type) for one media file, or None if not found.
    Caller is auth-gated."""
    full = _safe_resolve(project, rel_path)
    if full is None:
        return None
    ctype, _ = mimetypes.guess_type(full.name)
    if not ctype:
        ctype = "application/octet-stream"
    try:
        return full.read_bytes(), ctype
    except OSError:
        return None

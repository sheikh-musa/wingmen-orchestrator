#!/usr/bin/env python3
"""Deep repo context dump → Supabase repo_memory.

Reads a repository from its local path and upserts structured context
entries into the Supabase repo_memory table. These entries power all
orchestrator agents with rich per-repo knowledge.

Usage:
    python scripts/repo_context_dump.py cosem-tdu
    python scripts/repo_context_dump.py --all
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ORCHESTRATOR_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ORCHESTRATOR_ROOT / ".env")


def load_repos():
    with open(ORCHESTRATOR_ROOT / "REPOS.json") as f:
        return json.load(f)["repos"]


def get_repo_config(name):
    for r in load_repos():
        if r["name"] == name:
            return r
    raise ValueError(f"Repo '{name}' not found in REPOS.json")


def run_cmd(cmd, cwd, timeout=10):
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def read_file(path, max_chars=10000):
    try:
        return Path(path).read_text(encoding="utf-8")[:max_chars]
    except Exception:
        return ""


def extract_section(text, header):
    """Extract content between ## header and the next ## (or EOF)."""
    pattern = rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not m:
        return ""
    lines = m.group(1).strip().splitlines()
    cleaned = [re.sub(r"^-\s*", "", l).strip() for l in lines if l.strip()]
    return "; ".join(cleaned)


def truncate(value, limit=500):
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


# ── Extraction functions ──────────────────────────────────────────────


def extract_tech_stack(p):
    pkg_path = p / "package.json"
    if not pkg_path.exists():
        return "Unknown (no package.json)"
    pkg = json.loads(pkg_path.read_text())
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    parts = []
    key_deps = [
        ("react", "React"),
        ("next", "Next.js"),
        ("vue", "Vue"),
        ("angular", "Angular"),
        ("vite", "Vite"),
        ("firebase", "Firebase"),
        ("tailwindcss", "Tailwind"),
        ("@capacitor/core", "Capacitor"),
        ("vitest", "Vitest"),
        ("@playwright/test", "Playwright"),
        ("zod", "Zod"),
        ("@sentry/react", "Sentry"),
        ("posthog-js", "PostHog"),
        ("supabase", "Supabase"),
    ]
    for dep_key, label in key_deps:
        if dep_key in deps:
            ver = deps[dep_key].lstrip("^~>=<")
            major = ver.split(".")[0]
            parts.append(f"{label} {major}" if major.isdigit() else label)

    if (p / "firebase.json").exists() and "Firebase" not in " ".join(parts):
        parts.append("Firebase")
    if (p / "capacitor.config.ts").exists() or (p / "capacitor.config.json").exists():
        if "Capacitor" not in " ".join(parts):
            parts.append("Capacitor")

    return " + ".join(parts) if parts else "Unknown"


def extract_architecture(p):
    top = sorted(
        d
        for d in os.listdir(p)
        if (p / d).is_dir() and not d.startswith(".") and d != "node_modules"
    )
    src = p / "src"
    if src.is_dir():
        src_dirs = sorted(
            d for d in os.listdir(src) if (src / d).is_dir() and not d.startswith(".")
        )
        return f"Top: {', '.join(top[:10])}. src/: {', '.join(src_dirs[:12])}"
    return f"Top-level dirs: {', '.join(top[:15])}"


def extract_routes(p):
    app_jsx = p / "src" / "App.jsx"
    app_tsx = p / "src" / "App.tsx"
    target = app_jsx if app_jsx.exists() else (app_tsx if app_tsx.exists() else None)

    if target:
        content = target.read_text(encoding="utf-8")
        paths = re.findall(r'path=["\']([^"\']+)["\']', content)
        paths = sorted(set(paths))
        return ", ".join(paths) + f" ({len(paths)} routes)"

    # Next.js app router fallback
    app_dir = p / "src" / "app"
    if not app_dir.exists():
        app_dir = p / "app"
    if app_dir.exists():
        pages = []
        for page_file in app_dir.rglob("page.*"):
            route = "/" + str(page_file.parent.relative_to(app_dir)).replace("\\", "/")
            if route == "/.":
                route = "/"
            pages.append(route)
        pages = sorted(set(pages))
        return ", ".join(pages) + f" ({len(pages)} routes)"

    return "No routes found"


def extract_services(p):
    svc_dir = p / "src" / "services"
    if not svc_dir.exists():
        return "No src/services/ directory"
    files = sorted(
        f.name
        for f in svc_dir.iterdir()
        if f.is_file()
        and f.suffix in (".js", ".ts", ".jsx", ".tsx")
        and ".test." not in f.name
    )
    return ", ".join(files) + f" ({len(files)} modules)"


def extract_data_model(p):
    claude_md = p / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        section = re.search(
            r"## Firestore Collections\s*\n(.*?)(?=^## |\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if section:
            text = section.group(1).strip()
            collections = re.findall(r"`([a-zA-Z_/{}]+)`", text)
            if collections:
                primary = [c for c in collections if "/" not in c and "{" not in c]
                return "Firestore: " + ", ".join(primary[:20])

    # Fallback: grep service files
    svc_dir = p / "src" / "services"
    if svc_dir.exists():
        collections = set()
        for f in svc_dir.glob("*.js"):
            content = f.read_text(encoding="utf-8", errors="ignore")
            found = re.findall(r'collection\(["\']([^"\']+)["\']', content)
            collections.update(found)
        if collections:
            return "Firestore: " + ", ".join(sorted(collections)[:20])

    return "No data model detected"


def extract_roles(p):
    # Check CLAUDE.md first
    claude_md = p / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        section = re.search(
            r"## Roles & Permissions\s*\n(.*?)(?=^## |\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if section:
            roles = re.findall(r"`(\w+)`\s*\|", section.group(1))
            if roles:
                return ", ".join(roles)

    # Fallback: scan source for FALLBACK_PERMISSIONS / role definitions
    known_roles = {"super_admin", "admin", "regular", "trainer", "trainee", "part_timer", "user"}
    for search_path in [p / "src", p / "functions"]:
        if not search_path.exists():
            continue
        for f in search_path.rglob("*.js"):
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "FALLBACK" in content or "PERMISSION" in content or "role" in content.lower():
                found = set(re.findall(
                    r"\b(super_admin|admin|regular|trainer|trainee|part_timer|user)\b",
                    content,
                ))
                matched = found & known_roles
                if len(matched) >= 2:
                    return ", ".join(sorted(matched))

    return "No role definitions found"


def extract_features(p):
    status = read_file(p / "STATUS.md")
    if not status:
        return "No STATUS.md"
    return extract_section(status, "Completed") or "No completed features listed"


def extract_deploy_config(p):
    parts = []
    if (p / "firebase.json").exists():
        parts.append("Firebase Hosting")
    if (p / "vercel.json").exists() or (p / "vercel.ts").exists():
        parts.append("Vercel")

    workflows_dir = p / ".github" / "workflows"
    if workflows_dir.exists():
        wf_files = [f.name for f in workflows_dir.iterdir() if f.suffix in (".yml", ".yaml")]
        if wf_files:
            parts.append(f"GH Actions ({', '.join(wf_files[:4])})")

    return "; ".join(parts) if parts else "No deploy config detected"


def extract_test_coverage(p):
    test_files = list(p.rglob("*.test.*")) + list(p.rglob("*.spec.*"))
    # Exclude node_modules
    test_files = [f for f in test_files if "node_modules" not in str(f)]

    unit_tests = [f for f in test_files if "e2e" not in str(f) and "spec" not in f.suffix.replace(".", "")]
    # More accurate: .test. files are unit, .spec. files in tests/e2e are E2E
    unit_count = len([f for f in test_files if ".test." in f.name])
    e2e_count = len([f for f in test_files if ".spec." in f.name])

    status = read_file(p / "STATUS.md")
    counts_from_status = ""
    if status:
        m = re.search(r"(\d+)\s*unit\s*test", status, re.IGNORECASE)
        if m:
            counts_from_status += f"{m.group(1)} unit tests (reported)"
        m = re.search(r"(\d+)\+?\s*E2E", status, re.IGNORECASE)
        if m:
            counts_from_status += f", {m.group(1)}+ E2E (reported)"

    parts = [f"{unit_count} unit test files, {e2e_count} E2E spec files"]
    if counts_from_status:
        parts.append(counts_from_status)
    return "; ".join(parts)


def extract_blocked(p):
    status = read_file(p / "STATUS.md")
    if not status:
        return "No STATUS.md"
    return extract_section(status, "Blocked") or "Nothing blocked"


def extract_next_up(p):
    status = read_file(p / "STATUS.md")
    if not status:
        return "No STATUS.md"
    return extract_section(status, "Next Up") or "No next-up items listed"


# ── Main extraction ───────────────────────────────────────────────────


def extract_context(repo_name, repo_path):
    p = Path(repo_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Repo path does not exist: {p}")

    extractors = {
        "tech_stack": extract_tech_stack,
        "architecture": extract_architecture,
        "routes": extract_routes,
        "services": extract_services,
        "data_model": extract_data_model,
        "roles": extract_roles,
        "features": extract_features,
        "deploy_config": extract_deploy_config,
        "test_coverage": extract_test_coverage,
        "blocked": extract_blocked,
        "next_up": extract_next_up,
    }

    entries = {}
    for key, fn in extractors.items():
        try:
            value = fn(p)
            entries[key] = truncate(value.strip()) if value else "(empty)"
        except Exception as e:
            entries[key] = f"(extraction error: {e})"

    return entries


def upsert_entries(supabase, repo_name, entries):
    for key, value in entries.items():
        supabase.table("repo_memory").upsert(
            {"repo_name": repo_name, "key": key, "value": value},
            on_conflict="repo_name,key",
        ).execute()


def log_to_build_log(supabase, repo_name, entry_count):
    try:
        supabase.table("build_log").insert(
            {
                "repo_name": repo_name,
                "phase": "repo_context_dump",
                "message": f"Dumped {entry_count} context entries to repo_memory",
                "level": "info",
            }
        ).execute()
    except Exception as e:
        print(f"  ⚠ build_log insert failed (non-fatal): {e}")


def print_summary(repo_name, entries):
    print(f"\nrepo_memory dump for {repo_name}:")
    for key, value in entries.items():
        display = value[:80] + "..." if len(value) > 80 else value
        print(f"  {key:20s} {display}")
    print(f"\n  Total: {len(entries)} entries, all under 500 chars: "
          f"{'YES' if all(len(v) <= 500 for v in entries.values()) else 'NO'}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/repo_context_dump.py <repo_name>")
        print("       python scripts/repo_context_dump.py --all")
        sys.exit(1)

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        sys.exit(1)

    supabase = create_client(url, key)

    if sys.argv[1] == "--all":
        repos = [r for r in load_repos() if r["status"] == "active"]
        targets = [(r["name"], r["local_path"]) for r in repos]
    else:
        repo_name = sys.argv[1]
        config = get_repo_config(repo_name)
        targets = [(config["name"], config["local_path"])]

    for repo_name, repo_path in targets:
        print(f"\n{'='*60}")
        print(f"Extracting context for: {repo_name}")
        print(f"Path: {repo_path}")
        print(f"{'='*60}")

        entries = extract_context(repo_name, repo_path)
        upsert_entries(supabase, repo_name, entries)
        log_to_build_log(supabase, repo_name, len(entries))
        print_summary(repo_name, entries)

    print(f"\nDone. Processed {len(targets)} repo(s) at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.")


if __name__ == "__main__":
    main()

"""Deploy the development checkout to the live M5 release, safely.

Follows SERVER_README.md "Future deployment":
  build locally → pause scheduler → back up SQLite + current code → sync code
  (never touching live data/.env/node_modules) → swap frontend build → restart
  backend, then frontend, with health checks → resume scheduler last.

Usage (from the repo root):
  .runtime/m5/venv/bin/python scripts/m5/deploy-release.py --plan        # show what would change
  .runtime/m5/venv/bin/python scripts/m5/deploy-release.py --deploy      # do it
  .runtime/m5/venv/bin/python scripts/m5/deploy-release.py --rollback <backup-dir>

Environment additions for the live .env are passed with --set KEY=VALUE (the
value is never printed). The frontend must already be built locally
(`cd ai-blog-generator-app && npm run build`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime" / "m5"
RELEASE = RUNTIME / "release"
SNAPSHOTS = RUNTIME / "snapshots"
UID = os.getuid()
LABEL = "com.joebains.shopify-blog.{}"
AGENTS = Path.home() / "Library" / "LaunchAgents"

DATABASES = [
    "ai-blog-generator-python-server/data/ai_blog_server.db",
    "ai-blog-generator-app/prisma/prod.sqlite",
]

# Paths in the release that are live data or environment — never overwritten or deleted.
PROTECT = [
    ".env", "*.env", ".env.*",
    "ai-blog-generator-python-server/data/",
    "ai-blog-generator-python-server/logs/",
    "ai-blog-generator-python-server/social/",
    "ai-blog-generator-python-server/static/",
    "ai-blog-generator-python-server/config.json",
    "ai-blog-generator-python-server/services/landing_pages/output/",
    "ai-blog-generator-python-server/revenuemindproai.priv",
    "ai-blog-generator-app/prisma/*.sqlite*",
    "ai-blog-generator-app/node_modules/",
    "ai-blog-generator-app/build/",
    "ai-blog-generator-app/.shopify/",
    "logs/",
    "landing_pages/",
]
# Development-only files that should not be copied to the release.
EXCLUDE = [
    ".git/", ".runtime/", ".venv/", "__pycache__/", ".pytest_cache/", ".ruff_cache/", ".DS_Store",
    "*.pyc", "*.bak", "node_modules/", ".react-router/", "postiz-api-access.txt", "*-api-access.txt",
    ".claude/", ".cursor/", ".gemini/",
]


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def rsync_args(dry_run: bool) -> list[str]:
    # Keep the release private (owner-only) like the original migration made it.
    args = ["rsync", "-a", "--no-perms", "--executability", "--chmod=Dgo=,Fgo=", "--delete", "--itemize-changes"]
    if dry_run:
        args.append("--dry-run")
    for pattern in PROTECT:
        # Paths containing "/" are anchored at the repo root; bare names match at any depth.
        rule = f"/{pattern}" if "/" in pattern else pattern
        args += ["--filter", f"protect {rule}", "--exclude", rule]
    for pattern in EXCLUDE:
        args += ["--exclude", pattern]
    return args + [f"{ROOT}/", f"{RELEASE}/"]


def plan() -> list[str]:
    result = run(rsync_args(dry_run=True), capture=True)
    changes = [line for line in result.stdout.splitlines() if line and not line.startswith(".d..t")]
    return changes


def http_status(url: str, headers: dict[str, str] | None = None, timeout: float = 10) -> int:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def wait_for(url: str, *, expect: int = 200, seconds: int = 90, headers: dict[str, str] | None = None) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http_status(url, headers) == expect:
            return True
        time.sleep(2)
    return False


def backup(stamp: str) -> Path:
    target = SNAPSHOTS / f"pre-deploy-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    os.chmod(target, 0o700)
    for name in DATABASES:
        source_path = RELEASE / name
        if not source_path.exists():
            continue
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        dest = sqlite3.connect(target / Path(name).name)
        with source, dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise SystemExit(f"Integrity check failed for backup of {name}")
        source.close()
        dest.close()
        log(f"backed up {name}")
    with tarfile.open(target / "release-code.tar.gz", "w:gz") as archive:
        def skip(info: tarfile.TarInfo):
            parts = Path(info.name).parts
            if "node_modules" in parts or "__pycache__" in parts or info.name.endswith((".sqlite", ".db")):
                return None
            if len(parts) > 1 and parts[1] in ("data", "logs", "social") and parts[0] == "ai-blog-generator-python-server":
                return None
            return info
        archive.add(RELEASE, arcname="release", filter=skip)
    (target / "MANIFEST.json").write_text(json.dumps({
        "created": stamp, "release": str(RELEASE), "databases": DATABASES,
        "git_head": run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture=True).stdout.strip(),
    }, indent=2))
    log(f"backup complete: {target}")
    return target


def set_env(pairs: list[str]) -> None:
    env_path = RELEASE / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    lines = text.splitlines()
    for pair in pairs:
        key, _, value = pair.partition("=")
        key = key.strip()
        if not key or not value:
            raise SystemExit(f"--set expects KEY=VALUE, got {key!r}")
        lines = [line for line in lines if not line.startswith(f"{key}=")]
        lines.append(f"{key}={value}")
        log(f"live .env: set {key} (value hidden)")
    env_path.write_text("\n".join(lines).rstrip("\n") + "\n")
    os.chmod(env_path, 0o600)


def launch(action: str, component: str) -> None:
    label = LABEL.format(component)
    if action == "stop":
        run(["launchctl", "bootout", f"gui/{UID}/{label}"], check=False)
    elif action == "start":
        run(["launchctl", "bootstrap", f"gui/{UID}", str(AGENTS / f"{label}.plist")], check=False)
    elif action == "restart":
        run(["launchctl", "kickstart", "-k", f"gui/{UID}/{label}"])
    log(f"{action} {component}")


def sync_frontend_build() -> None:
    source = ROOT / "ai-blog-generator-app" / "build"
    if not (source / "server" / "index.js").exists():
        raise SystemExit("Frontend build missing: run `npm run build` in ai-blog-generator-app first.")
    target = RELEASE / "ai-blog-generator-app" / "build"
    staging = target.with_name("build.next")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    previous = target.with_name("build.previous")
    if previous.exists():
        shutil.rmtree(previous)
    if target.exists():
        target.rename(previous)
    staging.rename(target)
    log("frontend build swapped (previous kept as build.previous)")


def check_lockfile() -> None:
    dev = (ROOT / "ai-blog-generator-app" / "package-lock.json").read_bytes()
    live = (RELEASE / "ai-blog-generator-app" / "package-lock.json").read_bytes()
    if dev != live:
        raise SystemExit("package-lock.json differs from the live release: Node dependencies changed. "
                         "Prepare node_modules separately before deploying (see SERVER_README).")


def deploy(env_pairs: list[str], api_key_header: dict[str, str]) -> None:
    if not (RUNTIME / "PRODUCTION_ACTIVE").exists():
        raise SystemExit("PRODUCTION_ACTIVE marker missing — refusing to touch an unknown runtime.")
    check_lockfile()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log("pausing scheduler (scheduled/background work)")
    launch("stop", "scheduler")
    backup_dir = backup(stamp)
    try:
        log("syncing code")
        run(rsync_args(dry_run=False), capture=True)
        sync_frontend_build()
        if env_pairs:
            set_env(env_pairs)
        launch("restart", "backend")
        if not wait_for("http://127.0.0.1:4000/health"):
            raise RuntimeError("backend did not become healthy")
        log("backend healthy")
        if api_key_header and not wait_for("http://127.0.0.1:4000/api/social-engine/overview", headers=api_key_header):
            raise RuntimeError("Social Engine API did not respond")
        launch("restart", "frontend")
        if not wait_for("http://127.0.0.1:3001/", seconds=120) and http_status("http://127.0.0.1:3001/") not in (200, 302, 410):
            raise RuntimeError("frontend did not respond")
        log("frontend responding")
        launch("start", "scheduler")
        log(f"deploy complete. Rollback with: --rollback {backup_dir}")
    except Exception as exc:
        log(f"DEPLOY FAILED: {exc}. Rolling back from {backup_dir}")
        rollback(backup_dir)
        raise SystemExit(1)


def rollback(backup_dir: Path) -> None:
    backup_dir = Path(backup_dir)
    archive = backup_dir / "release-code.tar.gz"
    if not archive.exists():
        raise SystemExit(f"No code archive in {backup_dir}")
    launch("stop", "scheduler")
    staging = RUNTIME / f"rollback-{int(time.time())}"
    with tarfile.open(archive) as tar:
        tar.extractall(staging, filter="data")
    restored = staging / "release"
    run(["rsync", "-a", "--delete", *sum((["--filter", f"protect /{p}", "--exclude", f"/{p}"] for p in (
        "ai-blog-generator-python-server/data/", "ai-blog-generator-python-server/logs/",
        "ai-blog-generator-python-server/social/", "ai-blog-generator-app/node_modules/",
        "ai-blog-generator-app/prisma/prod.sqlite")), []), f"{restored}/", f"{RELEASE}/"])
    shutil.rmtree(staging, ignore_errors=True)
    for name in DATABASES:
        saved = backup_dir / Path(name).name
        if saved.exists():
            source = sqlite3.connect(saved)
            dest = sqlite3.connect(RELEASE / name)
            with source, dest:
                source.backup(dest)
            source.close()
            dest.close()
            log(f"restored {name}")
    launch("restart", "backend")
    launch("restart", "frontend")
    launch("start", "scheduler")
    log("rollback complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--deploy", action="store_true")
    group.add_argument("--rollback", metavar="BACKUP_DIR")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="add/replace a variable in the live release .env")
    parser.add_argument("--set-from-env", action="append", default=[], metavar="KEY",
                        help="copy KEY's value from this process environment into the live .env")
    args = parser.parse_args()

    if args.plan:
        changes = plan()
        print("\n".join(changes) or "(no changes)")
        print(f"\n{len(changes)} change(s). Protected live paths: {', '.join(PROTECT)}")
        return
    if args.rollback:
        rollback(Path(args.rollback))
        return
    pairs = list(args.set)
    for key in args.set_from_env:
        value = os.environ.get(key, "")
        if not value:
            raise SystemExit(f"{key} is not set in this environment")
        pairs.append(f"{key}={value}")
    from dotenv import dotenv_values

    live_env = {**dotenv_values(RELEASE / ".env"), **dotenv_values(RELEASE / "ai-blog-generator-python-server" / ".env")}
    api_key = live_env.get("AI_BLOG_BACKEND_API_KEY") or live_env.get("BLOG_GENERATOR_API_KEY") or ""
    deploy(pairs, {"x-api-key": api_key} if api_key else {})


if __name__ == "__main__":
    main()

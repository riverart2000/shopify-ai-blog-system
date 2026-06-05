#!/usr/bin/env bash
# =============================================================================
# deploy_shopify_ai_blog_generator.sh
#   One-command full deploy from local machine to production for the
#   Shopify AI Blog Generator system (FastAPI backend + scheduler + React app).
#
# What it does:
#   1. Commits any uncommitted local changes (auto or custom message)
#   2. Pushes to GitHub (origin/main)
#   3. SSHes into production, fast-forwards the repo, and:
#        • pip-installs only if requirements.txt changed
#        • npm install + build + prisma migrate only if the React app changed
#        • restarts services via service.sh (FastAPI, scheduler, React, Caddy)
#   4. Runs health checks on the backend (:4000) and frontend (:3001)
#   5. Exits with a clear success or failure message
#
# Usage:
#   ./deploy_shopify_ai_blog_generator.sh                      # auto commit msg
#   ./deploy_shopify_ai_blog_generator.sh "fix: description"   # custom msg
#   ./deploy_shopify_ai_blog_generator.sh --no-push            # deploy current HEAD, skip git push
#   ./deploy_shopify_ai_blog_generator.sh --rebuild-frontend   # force npm build even if unchanged
#   ./deploy_shopify_ai_blog_generator.sh --skip-frontend      # never touch the React app
#   ./deploy_shopify_ai_blog_generator.sh --no-restart         # pull/build only, don't restart
#
# Smart detection: the React app is only rebuilt when files under
# ai-blog-generator-app/ change; Python deps are only reinstalled when
# requirements.txt changes. Backend-only edits take the fast path (restart only).
#
# No passwords required — connection details are read from this script.
# =============================================================================

set -euo pipefail

# ── Local config ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROD_HOST="18.134.80.37"
PROD_USER="ubuntu"
BRANCH="main"

# Path to the cloned repo on the production server
REMOTE_APP_DIR="/home/ubuntu/shopify-ai-blog-system"
REMOTE_SERVER_DIR="${REMOTE_APP_DIR}/ai-blog-generator-python-server"
REMOTE_FRONTEND_DIR="${REMOTE_APP_DIR}/ai-blog-generator-app"

# Health probes (run on the server, against localhost)
BACKEND_PORT="4000"      # FastAPI / uvicorn — any HTTP response means up (303 = redirect to /login)
FRONTEND_PORT="3001"     # React app — expects HTTP 200
PUBLIC_URL="https://revenuemindproai.com:8443"

# ── Locate the SSH private key ─────────────────────────────────────────────────
# The key is gitignored, so probe the known local locations (or use $SSH_KEY).
SSH_KEY="${SSH_KEY:-}"
if [[ -z "$SSH_KEY" ]]; then
  for _candidate in \
    "${SCRIPT_DIR}/ai-blog-generator-python-server/revenuemindproai.priv" \
    "${SCRIPT_DIR}/revenuemindproai.priv" \
    "${HOME}/revenuemindproai/revenuemindproai.priv"; do
    if [[ -f "$_candidate" ]]; then SSH_KEY="$_candidate"; break; fi
  done
fi

# ── Colours ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log()  { printf "${CYAN}[local]${NC}  %s\n" "$1"; }
ok()   { printf "${GREEN}[local] ✓${NC}  %s\n" "$1"; }
err()  { printf "${RED}[local] ✗${NC}  %s\n" "$1" >&2; }
warn() { printf "${YELLOW}[local] ⚠${NC}  %s\n" "$1"; }

printf '\n%b\n' "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
printf '%b\n'   "${BOLD}  Shopify AI Blog Generator — Local → Production Deploy${NC}"
printf '%b\n\n' "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# ── Validate SSH key ───────────────────────────────────────────────────────────
[[ -n "$SSH_KEY" && -f "$SSH_KEY" ]] || {
  err "SSH key not found. Set SSH_KEY=/path/to/revenuemindproai.priv and retry."
  exit 1
}
chmod 600 "$SSH_KEY"
ok "SSH key found: ${SSH_KEY}"

# ── Parse flags ────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"

NO_PUSH=0
FORCE_FRONTEND=0
SKIP_FRONTEND=0
NO_RESTART=0
COMMIT_MSG=""

for arg in "$@"; do
  case "$arg" in
    --no-push)          NO_PUSH=1 ;;
    --rebuild-frontend) FORCE_FRONTEND=1 ;;
    --skip-frontend)    SKIP_FRONTEND=1 ;;
    --no-restart)       NO_RESTART=1 ;;
    --*)                err "Unknown flag: $arg"; exit 1 ;;
    *)                  [[ -z "$COMMIT_MSG" ]] && COMMIT_MSG="$arg" ;;
  esac
done

[[ -z "$COMMIT_MSG" ]] && COMMIT_MSG="chore: deploy $(date '+%Y-%m-%d %H:%M')"

# ── Step 1: Stage & commit any local changes ───────────────────────────────────
if [[ -n "$(git status --porcelain)" ]]; then
  log "Staging all local changes..."
  git add -A
  git commit -m "$COMMIT_MSG"
  ok "Committed: ${COMMIT_MSG}"
else
  ok "Working tree clean — no local changes to commit"
fi

# ── Step 2: Push to GitHub ─────────────────────────────────────────────────────
if [[ $NO_PUSH -eq 1 ]]; then
  warn "Skipping git push (--no-push) — deploying whatever is already on origin/${BRANCH}"
else
  log "Pushing to GitHub (branch: ${BRANCH})..."
  git push origin "$BRANCH"
  ok "GitHub up to date at $(git rev-parse --short HEAD)"
fi

# ── Step 3: Remote update + restart ────────────────────────────────────────────
printf '\n%b\n\n' "${BOLD}  ── Production server output ──────────────────────────${NC}"

# The remote routine is sent over stdin so we can interpolate local flags while
# keeping the server-side logic self-contained and idempotent.
ssh \
  -o ConnectTimeout=15 \
  -o StrictHostKeyChecking=accept-new \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=10 \
  -i "$SSH_KEY" \
  "${PROD_USER}@${PROD_HOST}" \
  "FORCE_FRONTEND='${FORCE_FRONTEND}' SKIP_FRONTEND='${SKIP_FRONTEND}' NO_RESTART='${NO_RESTART}' \
   BRANCH='${BRANCH}' APP_DIR='${REMOTE_APP_DIR}' SERVER_DIR='${REMOTE_SERVER_DIR}' \
   FRONTEND_DIR='${REMOTE_FRONTEND_DIR}' BACKEND_PORT='${BACKEND_PORT}' \
   FRONTEND_PORT='${FRONTEND_PORT}' bash -s" <<'REMOTE'
set -euo pipefail

C_GREEN='\033[0;32m'; C_RED='\033[0;31m'; C_CYAN='\033[0;36m'; C_YEL='\033[1;33m'; C_NC='\033[0m'
rlog()  { printf "${C_CYAN}[prod]${C_NC}  %s\n" "$1"; }
rok()   { printf "${C_GREEN}[prod] ✓${C_NC}  %s\n" "$1"; }
rerr()  { printf "${C_RED}[prod] ✗${C_NC}  %s\n" "$1" >&2; }
rwarn() { printf "${C_YEL}[prod] ⚠${C_NC}  %s\n" "$1"; }

[[ -d "${APP_DIR}/.git" ]] || { rerr "Not a git repository: ${APP_DIR}"; exit 1; }
cd "$APP_DIR"

# ── Sync with GitHub ──────────────────────────────────────────────────────────
PREV_COMMIT="$(git rev-parse HEAD)"
rlog "Fetching origin/${BRANCH}..."
git fetch --all --prune
git reset --hard "origin/${BRANCH}"
NEW_COMMIT="$(git rev-parse HEAD)"
rlog "  $(git --no-pager log --oneline -1)"

if [[ "$PREV_COMMIT" == "$NEW_COMMIT" ]]; then
  rok "Already up to date (${NEW_COMMIT:0:8})"
  CHANGED=""
else
  rok "Updated ${PREV_COMMIT:0:8} → ${NEW_COMMIT:0:8}"
  CHANGED="$(git diff --name-only "$PREV_COMMIT" "$NEW_COMMIT")"
fi

# ── Resolve venv python ───────────────────────────────────────────────────────
if [[ -x "${SERVER_DIR}/.venv/bin/python" ]]; then
  PIP="${SERVER_DIR}/.venv/bin/pip"
else
  rwarn "No virtualenv found — running service.sh setup to create it"
  ( cd "$SERVER_DIR" && ./service.sh setup )
  PIP="${SERVER_DIR}/.venv/bin/pip"
fi

# ── Python deps: only when requirements.txt changed ──────────────────────────
if [[ -z "$PREV_COMMIT" ]] || grep -q '^ai-blog-generator-python-server/requirements\.txt$' <<<"$CHANGED"; then
  rlog "requirements.txt changed — installing Python dependencies..."
  "$PIP" install --quiet -r "${SERVER_DIR}/requirements.txt"
  rok "Python dependencies up to date"
else
  rok "requirements.txt unchanged — skipping pip install"
fi

# ── React app: rebuild only when its files changed ───────────────────────────
FRONTEND_TOUCHED=0
if grep -qE '^ai-blog-generator-app/' <<<"$CHANGED"; then FRONTEND_TOUCHED=1; fi

if [[ "$SKIP_FRONTEND" == "1" ]]; then
  rwarn "Skipping React app build (--skip-frontend)"
elif [[ "$FORCE_FRONTEND" == "1" || "$FRONTEND_TOUCHED" == "1" ]]; then
  if [[ "$FORCE_FRONTEND" == "1" ]]; then
    rlog "Rebuilding React app (forced)..."
  else
    rlog "React app changed — installing deps and rebuilding..."
  fi
  cd "$FRONTEND_DIR"
  npm install --silent
  npm run build
  npx prisma generate >/dev/null 2>&1 || true
  npx prisma migrate deploy
  rok "React app rebuilt and migrations applied"
  cd "$APP_DIR"
else
  rok "React app unchanged — skipping npm build"
fi

# ── Restart services ──────────────────────────────────────────────────────────
if [[ "$NO_RESTART" == "1" ]]; then
  rwarn "Skipping restart (--no-restart)"
else
  rlog "Restarting services via service.sh..."
  ( cd "$SERVER_DIR" && ./service.sh restart )
  rok "Services restarted"
fi

# ── Health checks ─────────────────────────────────────────────────────────────
rlog "Running health checks..."

_probe() {  # name port [expected_code]
  local name="$1" port="$2" expect="${3:-}"
  local code attempt=0
  while (( attempt < 12 )); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:${port}/" || echo 000)"
    if [[ "$code" != "000" ]]; then
      if [[ -z "$expect" || "$code" == "$expect" ]]; then
        rok "${name} healthy on :${port} (HTTP ${code})"
        return 0
      fi
    fi
    attempt=$(( attempt + 1 )); sleep 3
  done
  rerr "${name} unhealthy on :${port} (last HTTP ${code:-000})"
  return 1
}

HEALTH_OK=1
_probe "Backend (FastAPI)" "$BACKEND_PORT"          || HEALTH_OK=0
_probe "Frontend (React)"  "$FRONTEND_PORT" "200"    || HEALTH_OK=0

if [[ "$NO_RESTART" == "1" ]]; then
  rwarn "Restart was skipped — health result is informational only"
  exit 0
fi

if [[ "$HEALTH_OK" == "1" ]]; then
  rok "All production health checks passed"
  exit 0
else
  rerr "One or more services failed their health check"
  rerr "Inspect logs: tail -n 80 ${SERVER_DIR}/logs/main.log ${SERVER_DIR}/logs/frontend.log"
  exit 1
fi
REMOTE

SSH_EXIT=$?

# ── Local summary ──────────────────────────────────────────────────────────────
printf '\n%b\n' "${BOLD}  ── Local summary ─────────────────────────────────────${NC}"

if [[ $SSH_EXIT -eq 0 ]]; then
  ok "Production deploy completed successfully"
  printf '%b\n\n' "  ${GREEN}${BOLD}Live at: ${PUBLIC_URL}${NC}"
else
  err "Production deploy failed (exit code: ${SSH_EXIT})"
  err "Check the server logs:"
  err "  ssh -i ${SSH_KEY} ${PROD_USER}@${PROD_HOST} 'tail -n 80 ${REMOTE_SERVER_DIR}/logs/main.log'"
  exit 1
fi

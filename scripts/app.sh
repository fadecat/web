#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
TARGET="${2:-all}"
if [[ -n "${APP_ROOT:-}" ]]; then
  ROOT="$APP_ROOT"
else
  SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
  SCRIPT_SOURCE="${SCRIPT_SOURCE//\\//}"
  SCRIPT_DIR="$(cd -- "${SCRIPT_SOURCE%/*}" && pwd)"
  ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
fi
SERVICE="${APP_SERVICE:-webapp}"
HEALTH_URL="${APP_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
DRY_RUN="${APP_OPS_DRY_RUN:-0}"
FRONTEND_DIR="$ROOT/frontend"
NEXT_DIR="$FRONTEND_DIR/.dist-next"
PREV_DIR="$FRONTEND_DIR/.dist-prev"
DIST_DIR="$FRONTEND_DIR/dist"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit "${2:-2}"
}

case "$ACTION" in start|stop|restart|status|build) ;; *) fail "unsupported action: $ACTION" ;; esac
case "$TARGET" in all|backend|frontend) ;; *) fail "unsupported target: $TARGET" ;; esac
if [[ "$ACTION" == build && "$TARGET" != frontend ]]; then
  fail "build only supports target 'frontend'."
fi
if [[ "$TARGET" == frontend && ( "$ACTION" == start || "$ACTION" == stop ) ]]; then
  fail "frontend is static on ECS; use 'build frontend' or 'restart frontend'."
fi

systemctl_action() {
  local verb="$1"
  if [[ "$DRY_RUN" == 1 ]]; then
    printf 'DRY-RUN systemctl %s %s\n' "$verb" "$SERVICE"
  else
    systemctl "$verb" "$SERVICE"
  fi
}

wait_health() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf 'DRY-RUN health %s\n' "$HEALTH_URL"
    return 0
  fi
  local attempt
  for attempt in {1..20}; do
    if curl --fail --silent --show-error --max-time 2 "$HEALTH_URL" >/dev/null; then
      printf 'backend healthy: %s\n' "$HEALTH_URL"
      return 0
    fi
    sleep 1
  done
  fail "backend health check failed: $HEALTH_URL" 1
}

restore_interrupted_build() {
  if [[ ! -d "$DIST_DIR" && -d "$PREV_DIR" ]]; then
    mv -- "$PREV_DIR" "$DIST_DIR"
  fi
  rm -rf -- "$NEXT_DIR"
}

build_frontend() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf 'DRY-RUN frontend build: %s\n' "$FRONTEND_DIR"
    return 0
  fi
  printf 'frontend build: %s\n' "$FRONTEND_DIR"
  [[ -d "$FRONTEND_DIR" ]] || fail "frontend directory not found: $FRONTEND_DIR" 1
  command -v pnpm >/dev/null 2>&1 || fail "pnpm is not installed on ECS." 1

  if [[ ! -d "$DIST_DIR" && -d "$PREV_DIR" ]]; then mv -- "$PREV_DIR" "$DIST_DIR"; fi
  rm -rf -- "$NEXT_DIR" "$PREV_DIR"
  (
    cd -- "$FRONTEND_DIR"
    pnpm install --frozen-lockfile
    pnpm exec vite build --outDir .dist-next --emptyOutDir
  )
  [[ -f "$NEXT_DIR/index.html" ]] || fail "frontend build produced no index.html." 1

  trap restore_interrupted_build EXIT INT TERM
  if [[ -d "$DIST_DIR" ]]; then mv -- "$DIST_DIR" "$PREV_DIR"; fi
  if ! mv -- "$NEXT_DIR" "$DIST_DIR"; then
    [[ -d "$PREV_DIR" ]] && mv -- "$PREV_DIR" "$DIST_DIR"
    fail "failed to publish frontend build." 1
  fi
  rm -rf -- "$PREV_DIR"
  trap - EXIT INT TERM
  printf 'frontend build published: %s\n' "$DIST_DIR"
}

backend_status() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf 'DRY-RUN systemctl status %s\n' "$SERVICE"
    printf 'DRY-RUN health %s\n' "$HEALTH_URL"
    return 0
  fi
  if ! systemctl is-active --quiet "$SERVICE"; then
    printf 'backend STOPPED: %s\n' "$SERVICE"
    return 1
  fi
  if ! curl --fail --silent --show-error --max-time 2 "$HEALTH_URL" >/dev/null; then
    printf 'backend UNHEALTHY: %s\n' "$HEALTH_URL"
    return 1
  fi
  printf 'backend RUNNING: %s (%s)\n' "$SERVICE" "$HEALTH_URL"
}

frontend_status() {
  if [[ "$DRY_RUN" == 1 ]]; then
    printf 'DRY-RUN frontend status %s\n' "$DIST_DIR/index.html"
    return 0
  fi
  if [[ ! -f "$DIST_DIR/index.html" ]]; then
    printf 'frontend NOT BUILT: %s\n' "$DIST_DIR/index.html"
    return 1
  fi
  printf 'frontend BUILT: %s\n' "$DIST_DIR/index.html"
}

start_backend() { systemctl_action start; wait_health; }
stop_backend() { systemctl_action stop; }
restart_backend() { systemctl_action restart; wait_health; }

case "$ACTION:$TARGET" in
  start:backend) start_backend ;;
  stop:backend) stop_backend ;;
  restart:backend) restart_backend ;;
  status:backend) backend_status ;;
  build:frontend) build_frontend ;;
  restart:frontend) build_frontend; restart_backend ;;
  status:frontend) frontend_status ;;
  start:all)
    if [[ "$DRY_RUN" == 1 || ! -f "$DIST_DIR/index.html" ]]; then build_frontend; fi
    start_backend
    ;;
  stop:all) stop_backend ;;
  restart:all) build_frontend; restart_backend ;;
  status:all)
    result=0
    backend_status || result=1
    frontend_status || result=1
    exit "$result"
    ;;
  *) fail "unsupported combination: $ACTION $TARGET" ;;
esac

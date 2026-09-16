#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

require_root
assert_install_root "$MYPROXY_ROOT"

readonly TARGET=${1:-all}
case "$TARGET" in
  all|backend|frontend) ;;
  *) die "usage: $0 [all|backend|frontend]" ;;
esac

readonly VERSION_FILE="$MYPROXY_ROOT/.runtime-versions"
PYTHON_VERSION=$(read_pinned_version PYTHON_VERSION "$VERSION_FILE")
readonly PYTHON_VERSION
readonly UV_BIN="$MYPROXY_ROOT/.runtime/uv/bin/uv"
readonly NODE_BIN_DIR="$MYPROXY_ROOT/.runtime/node/bin"
readonly UV_PYTHON_INSTALL_DIR="$MYPROXY_ROOT/.runtime/python"
readonly UV_CACHE_DIR="$MYPROXY_ROOT/.runtime/cache/uv"
readonly NPM_CACHE_DIR="$MYPROXY_ROOT/.runtime/cache/npm"
readonly BUILD_ROOT="$MYPROXY_ROOT/.build"
FRONTEND_STAGE=''

# The installer is commonly launched from a root-only source checkout. Do not
# let unprivileged build commands inherit an inaccessible caller directory.
cd -- "$MYPROXY_ROOT"

seal_build_outputs() {
  local original_status=$?
  local cleanup_failed=0
  trap - EXIT
  set +e
  if [[ -n "$FRONTEND_STAGE" && "$FRONTEND_STAGE" == "$BUILD_ROOT/frontend."* ]]; then
    rm -rf -- "$FRONTEND_STAGE" || cleanup_failed=1
  fi
  [[ ! -d "$MYPROXY_ROOT/backend/.venv" ]] || \
    chown_tree_nofollow root "$MYPROXY_GROUP" "$MYPROXY_ROOT/backend/.venv" || cleanup_failed=1
  if [[ -d "$MYPROXY_ROOT/backend/.venv" ]]; then
    chmod -R go-w "$MYPROXY_ROOT/backend/.venv" || cleanup_failed=1
    chmod 0750 "$MYPROXY_ROOT/backend/.venv" || cleanup_failed=1
  fi
  [[ ! -d "$UV_PYTHON_INSTALL_DIR" ]] || \
    chown_tree_nofollow root "$MYPROXY_GROUP" "$UV_PYTHON_INSTALL_DIR" || cleanup_failed=1
  [[ ! -d "$UV_PYTHON_INSTALL_DIR" ]] || \
    chmod -R go-w "$UV_PYTHON_INSTALL_DIR" || cleanup_failed=1
  chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime" || cleanup_failed=1
  [[ ! -d "$MYPROXY_ROOT/.runtime/cache" ]] || \
    chown_tree_nofollow "$MYPROXY_USER" "$MYPROXY_GROUP" \
      "$MYPROXY_ROOT/.runtime/cache" || cleanup_failed=1
  if [[ "$original_status" == 0 && "$cleanup_failed" != 0 ]]; then
    original_status=1
  fi
  exit "$original_status"
}
trap seal_build_outputs EXIT

warn_if_low_memory() {
  local available_kib
  available_kib=$(awk '$1 == "MemAvailable:" {print $2}' /proc/meminfo 2>/dev/null || true)
  if [[ "$available_kib" =~ ^[0-9]+$ && "$available_kib" -lt 716800 ]]; then
    warn "less than 700 MiB of memory is available; the sequential build may be slow"
  fi
}

build_backend() {
  [[ -x "$UV_BIN" ]] || die "project-local uv is missing: $UV_BIN"
  [[ -f "$MYPROXY_ROOT/backend/pyproject.toml" ]] || die "backend/pyproject.toml is missing"
  [[ -f "$MYPROXY_ROOT/backend/uv.lock" ]] || \
    die "backend/uv.lock is required for a reproducible production install"

  install -d -m 0750 -o "$MYPROXY_USER" -g "$MYPROXY_GROUP" \
    "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR" "$MYPROXY_ROOT/backend/.venv"
  chown_tree_nofollow "$MYPROXY_USER" "$MYPROXY_GROUP" \
    "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR" "$MYPROXY_ROOT/backend/.venv" || \
    die "could not prepare backend build ownership"
  log "installing managed Python $PYTHON_VERSION through verified uv metadata"
  run_as_myproxy env \
    UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
    UV_CACHE_DIR="$UV_CACHE_DIR" \
    UV_PYTHON_PREFERENCE=only-managed \
    "$UV_BIN" --no-config python install "$PYTHON_VERSION"

  log "syncing backend validation dependencies"
  (cd -- "$MYPROXY_ROOT/backend" && \
    run_as_myproxy env \
      UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
      UV_CACHE_DIR="$UV_CACHE_DIR" \
      UV_PYTHON_PREFERENCE=only-managed \
      UV_PROJECT_ENVIRONMENT="$MYPROXY_ROOT/backend/.venv" \
      "$UV_BIN" sync --frozen --all-groups)

  log "running backend tests and static checks sequentially"
  (cd -- "$MYPROXY_ROOT/backend" && \
    run_as_myproxy env \
      PYTHONDONTWRITEBYTECODE=1 \
      "$MYPROXY_ROOT/backend/.venv/bin/python" -m pytest -q \
        -o "cache_dir=$MYPROXY_ROOT/run/pytest-cache")
  (cd -- "$MYPROXY_ROOT/backend" && \
    run_as_myproxy env PYTHONDONTWRITEBYTECODE=1 \
      "$MYPROXY_ROOT/backend/.venv/bin/python" -m ruff check app migrations ../tests --no-cache)

  log "removing development dependencies from the production environment"
  (cd -- "$MYPROXY_ROOT/backend" && \
    run_as_myproxy env \
      UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" \
      UV_CACHE_DIR="$UV_CACHE_DIR" \
      UV_PYTHON_PREFERENCE=only-managed \
      UV_PROJECT_ENVIRONMENT="$MYPROXY_ROOT/backend/.venv" \
      "$UV_BIN" sync --frozen --no-dev)

  run_as_myproxy "$MYPROXY_ROOT/backend/.venv/bin/python" -c \
    'import sys; expected = tuple(map(int, sys.argv[1].split("."))); raise SystemExit(0 if sys.version_info[:3] == expected else 1)' \
    "$PYTHON_VERSION" || die "managed Python version mismatch"
}

build_frontend() {
  [[ -x "$NODE_BIN_DIR/node" && -x "$NODE_BIN_DIR/npm" ]] || \
    die "project-local Node.js is missing: $NODE_BIN_DIR"
  [[ -f "$MYPROXY_ROOT/frontend/package-lock.json" ]] || \
    die "frontend/package-lock.json is required for npm ci"

  install -d -m 0750 -o "$MYPROXY_USER" -g "$MYPROXY_GROUP" "$NPM_CACHE_DIR"
  install -d -m 0710 -o root -g "$MYPROXY_GROUP" "$BUILD_ROOT"
  FRONTEND_STAGE="$BUILD_ROOT/frontend.$$"
  assert_under_root "$FRONTEND_STAGE"
  [[ ! -e "$FRONTEND_STAGE" ]] || die "frontend staging path already exists"
  install -d -m 0750 -o "$MYPROXY_USER" -g "$MYPROXY_GROUP" "$FRONTEND_STAGE"
  run_as_myproxy tar -C "$MYPROXY_ROOT/frontend" \
    --exclude='./node_modules' --exclude='./dist' -cf - . | \
    run_as_myproxy tar --no-same-owner --no-same-permissions \
      -C "$FRONTEND_STAGE" -xf -
  chown_tree_nofollow "$MYPROXY_USER" "$MYPROXY_GROUP" "$FRONTEND_STAGE" || \
    die "could not prepare frontend build ownership"

  log "installing frontend dependencies sequentially"
  run_as_myproxy env \
    PATH="$NODE_BIN_DIR:/usr/bin:/bin" \
    npm_config_cache="$NPM_CACHE_DIR" \
    npm_config_jobs=1 \
    npm_config_audit=false \
    npm_config_fund=false \
    "$NODE_BIN_DIR/npm" ci --prefix "$FRONTEND_STAGE"

  log "linting, then building the frontend with a conservative memory limit"
  run_as_myproxy env \
    PATH="$NODE_BIN_DIR:/usr/bin:/bin" \
    NODE_OPTIONS=--max-old-space-size=384 \
    npm_config_cache="$NPM_CACHE_DIR" \
    "$NODE_BIN_DIR/npm" run lint --prefix "$FRONTEND_STAGE"
  run_as_myproxy env \
    PATH="$NODE_BIN_DIR:/usr/bin:/bin" \
    NODE_OPTIONS=--max-old-space-size=384 \
    npm_config_cache="$NPM_CACHE_DIR" \
    "$NODE_BIN_DIR/npm" run build --prefix "$FRONTEND_STAGE"
  [[ -f "$FRONTEND_STAGE/dist/index.html" ]] || die "frontend build did not produce dist/index.html"
  if find "$FRONTEND_STAGE/dist" -type l -print -quit | grep -q .; then
    die "frontend build produced an unexpected symbolic link"
  fi

  local dist_stage="$MYPROXY_ROOT/frontend/.dist.install.$$.tmp"
  local dist_previous="$MYPROXY_ROOT/frontend/.dist.previous.$$.tmp"
  assert_under_root "$dist_stage"
  assert_under_root "$dist_previous"
  [[ ! -e "$dist_stage" && ! -e "$dist_previous" ]] || die "stale frontend install path exists"
  cp -a -- "$FRONTEND_STAGE/dist" "$dist_stage"
  chown_tree_nofollow root root "$dist_stage" || die "could not seal frontend build ownership"
  find "$dist_stage" -type d -exec chmod 0755 {} +
  find "$dist_stage" -type f -exec chmod 0644 {} +
  if [[ -e "$MYPROXY_ROOT/frontend/dist" ]]; then
    mv -- "$MYPROXY_ROOT/frontend/dist" "$dist_previous"
  fi
  if ! mv -- "$dist_stage" "$MYPROXY_ROOT/frontend/dist"; then
    [[ ! -e "$dist_previous" ]] || mv -- "$dist_previous" "$MYPROXY_ROOT/frontend/dist"
    rm -rf -- "$dist_stage"
    die "failed to install the frontend build"
  fi
  [[ ! -e "$dist_previous" ]] || rm -rf -- "$dist_previous"
  rm -rf -- "$FRONTEND_STAGE"
  FRONTEND_STAGE=''
}

warn_if_low_memory
case "$TARGET" in
  all)
    build_backend
    build_frontend
    ;;
  backend) build_backend ;;
  frontend) build_frontend ;;
esac

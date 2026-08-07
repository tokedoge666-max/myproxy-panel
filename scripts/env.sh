#!/usr/bin/env bash
set -Eeuo pipefail

MYPROXY_PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
export MYPROXY_PROJECT_ROOT

if [[ -r "$MYPROXY_PROJECT_ROOT/config/app.env" ]]; then
  declare -A myproxy_seen_env_keys=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" && "$line" != \#* ]] || continue
    [[ "$line" != *$'\r'* && "$line" == *=* ]] || {
      printf 'Malformed line in config/app.env\n' >&2
      return 1 2>/dev/null || exit 1
    }
    key=${line%%=*}
    value=${line#*=}
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || {
      printf 'Invalid key in config/app.env: %s\n' "$key" >&2
      return 1 2>/dev/null || exit 1
    }
    [[ -z "${myproxy_seen_env_keys[$key]:-}" ]] || {
      printf 'Duplicate key in config/app.env: %s\n' "$key" >&2
      return 1 2>/dev/null || exit 1
    }
    myproxy_seen_env_keys[$key]=1
    [[ "$value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]] || {
      printf 'Unsafe value for %s in config/app.env\n' "$key" >&2
      return 1 2>/dev/null || exit 1
    }
    case "$key" in
      MYPROXY_ENV|MYPROXY_HOME|MYPROXY_DB|MYPROXY_SINGBOX|MYPROXY_SINGBOX_CONFIG|\
      MYPROXY_LOG_DIR|MYPROXY_BACKUP_DIR|MYPROXY_RUN_DIR|MYPROXY_SESSION_TTL|\
      MYPROXY_COOKIE_SECURE|MYPROXY_ALLOWED_HOSTS|MYPROXY_CORS_ORIGINS|\
      MYPROXY_USE_SUDO|MYPROXY_SYSTEMCTL|MYPROXY_SUDO|MYPROXY_SINGBOX_SERVICE|\
      MYPROXY_SINGBOX_VERSION|MYPROXY_BACKUP_LIMIT|MYPROXY_CERTIFICATE_PATH|\
      MYPROXY_PRIVATE_KEY_PATH|MYPROXY_SERVER_IP|MYPROXY_DOMAIN|\
      MYPROXY_SELF_SIGNED_MODE|SERVER_IP|DOMAIN|ACME_EMAIL|SELF_SIGNED_MODE)
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done <"$MYPROXY_PROJECT_ROOT/config/app.env"
else
  export MYPROXY_ENV=${MYPROXY_ENV:-development}
  export MYPROXY_HOME=${MYPROXY_HOME:-$MYPROXY_PROJECT_ROOT}
  export MYPROXY_DB=${MYPROXY_DB:-$MYPROXY_PROJECT_ROOT/data/myproxy.db}
  export MYPROXY_SINGBOX=${MYPROXY_SINGBOX:-$MYPROXY_PROJECT_ROOT/.runtime/sing-box/sing-box}
  export MYPROXY_SINGBOX_CONFIG=${MYPROXY_SINGBOX_CONFIG:-$MYPROXY_PROJECT_ROOT/config/sing-box.json}
  export MYPROXY_LOG_DIR=${MYPROXY_LOG_DIR:-$MYPROXY_PROJECT_ROOT/logs}
  export MYPROXY_BACKUP_DIR=${MYPROXY_BACKUP_DIR:-$MYPROXY_PROJECT_ROOT/backups}
  export MYPROXY_RUN_DIR=${MYPROXY_RUN_DIR:-$MYPROXY_PROJECT_ROOT/run}
fi

export UV_PYTHON_INSTALL_DIR="$MYPROXY_PROJECT_ROOT/.runtime/python"
export UV_CACHE_DIR="$MYPROXY_PROJECT_ROOT/.runtime/cache/uv"
export npm_config_cache="$MYPROXY_PROJECT_ROOT/.runtime/cache/npm"
export PATH="$MYPROXY_PROJECT_ROOT/.runtime/uv/bin:$MYPROXY_PROJECT_ROOT/.runtime/node/bin:$PATH"

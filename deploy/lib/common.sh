#!/usr/bin/env bash
set -Eeuo pipefail

readonly MYPROXY_ROOT="/opt/myproxy"
readonly MYPROXY_USER="myproxy"
readonly MYPROXY_GROUP="myproxy"
readonly MYPROXY_API_UNIT="myproxy-api.service"
readonly MYPROXY_SINGBOX_UNIT="myproxy-singbox.service"
readonly MYPROXY_NGINX_SITE="/etc/nginx/sites-available/myproxy.conf"
readonly MYPROXY_NGINX_LINK="/etc/nginx/sites-enabled/myproxy.conf"
readonly MYPROXY_SUDOERS="/etc/sudoers.d/myproxy"
readonly MYPROXY_LOGROTATE="/etc/logrotate.d/myproxy"
readonly MYPROXY_CERT_HOOK="/etc/letsencrypt/renewal-hooks/deploy/myproxy-cert-deploy"
readonly MYPROXY_ACME_ROOT="/var/lib/myproxy-acme"
readonly MYPROXY_ACCOUNT_MARKER="$MYPROXY_ROOT/.managed-service-account"

log() {
  printf '[myproxy] %s\n' "$*"
}

warn() {
  printf '[myproxy] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[myproxy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "run this command as root (for example: sudo bash $0)"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

acquire_deploy_lock() {
  require_command flock
  exec {MYPROXY_DEPLOY_LOCK_FD}>/run/lock/myproxy-deploy.lock
  flock --nonblock "$MYPROXY_DEPLOY_LOCK_FD" || \
    die "another MyProxy install, update or uninstall is already running"
}

acquire_certificate_lock() {
  require_command flock
  exec {MYPROXY_CERT_LOCK_FD}>/run/lock/myproxy-cert-deploy.lock
  flock "$MYPROXY_CERT_LOCK_FD"
}

release_certificate_lock() {
  [[ -n "${MYPROXY_CERT_LOCK_FD:-}" ]] || return 0
  flock --unlock "$MYPROXY_CERT_LOCK_FD"
  exec {MYPROXY_CERT_LOCK_FD}>&-
  unset MYPROXY_CERT_LOCK_FD
}

sanitize_git_checkout() {
  local git_dir="$MYPROXY_ROOT/.git"
  local hooks_dir="$git_dir/myproxy-disabled-hooks"
  local origin_url key section section_lower
  local -a dangerous_keys
  declare -A removed_sections=()

  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
    GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM \
    GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_EXEC_PATH GIT_SSH GIT_SSH_COMMAND \
    GIT_SSH_VARIANT GIT_PROXY_COMMAND GIT_TEMPLATE_DIR GIT_CEILING_DIRECTORIES \
    GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_PAGER

  [[ -d "$git_dir" && ! -L "$git_dir" && "$(readlink -f -- "$git_dir")" == "$git_dir" ]] || \
    die "$MYPROXY_ROOT must contain a real .git directory"
  origin_url=$(git -C "$MYPROXY_ROOT" config --local --no-includes --get remote.origin.url) || \
    die "Git origin is missing"
  [[ "$origin_url" =~ ^https://[A-Za-z0-9./:_-]+$ && "$origin_url" != *'@'* ]] || \
    die "Git origin must be a credential-free HTTPS URL"

  mapfile -t dangerous_keys < <(git -C "$MYPROXY_ROOT" config --local --no-includes \
    --name-only --get-regexp '^(include|includeIf|includeif|filter|url)\.' || true)
  for key in "${dangerous_keys[@]}"; do
    section=${key%.*}
    section_lower=${section,,}
    [[ "$section_lower" == include || "$section_lower" == includeif.* || \
      "$section_lower" == filter.* || "$section_lower" == url.* ]] || \
      die "could not classify unsafe Git configuration: $key"
    if [[ -z "${removed_sections[$section]:-}" ]]; then
      git -C "$MYPROXY_ROOT" config --local --remove-section "$section" 2>/dev/null || true
      removed_sections[$section]=1
    fi
  done
  if git -C "$MYPROXY_ROOT" config --local --no-includes --name-only \
    --get-regexp '^(include|includeIf|includeif|filter|url)\.' >/dev/null 2>&1; then
    die "unsafe local Git configuration could not be removed"
  fi
  git -C "$MYPROXY_ROOT" config --local --unset-all core.attributesFile 2>/dev/null || true
  git -C "$MYPROXY_ROOT" config --local --unset-all credential.helper 2>/dev/null || true
  git -C "$MYPROXY_ROOT" config --local core.fsmonitor false

  [[ "$hooks_dir" == "$MYPROXY_ROOT/.git/myproxy-disabled-hooks" ]] || die "Git hook path safety check failed"
  rm -rf -- "$hooks_dir"
  install -d -m 0700 -o root -g root "$hooks_dir"
  git -C "$MYPROXY_ROOT" config --local core.hooksPath "$hooks_dir"
}

assert_install_root() {
  local candidate=${1:-}
  [[ "$candidate" == "$MYPROXY_ROOT" ]] || die "refusing unexpected install root: $candidate"
  [[ "$candidate" == /opt/myproxy ]] || die "install root safety check failed"
  if [[ -e "$candidate" || -L "$candidate" ]]; then
    [[ -d "$candidate" && ! -L "$candidate" && "$(readlink -f -- "$candidate")" == "$candidate" ]] || \
      die "install root must be a real directory: $candidate"
  fi
}

assert_under_root() {
  local candidate=${1:-}
  [[ -n "$candidate" ]] || die "empty path rejected"
  case "$candidate" in
    "$MYPROXY_ROOT"/*) ;;
    *) die "path is outside $MYPROXY_ROOT: $candidate" ;;
  esac
}

assert_safe_tar_gz() {
  local archive=$1
  local listing
  [[ -f "$archive" && ! -L "$archive" ]] || die "archive must be a regular file: $archive"
  listing=$(tar -tzf "$archive") || die "could not read archive: $archive"
  if awk '/^\// || /(^|\/)\.\.($|\/)/ {bad=1} END {exit bad ? 0 : 1}' <<<"$listing"; then
    die "unsafe path found in archive: $archive"
  fi
}

assert_ubuntu_2004() {
  [[ -r /etc/os-release ]] || die "/etc/os-release is missing"
  local os_id os_version
  os_id=$(awk -F= '$1 == "ID" {gsub(/\"/, "", $2); print $2}' /etc/os-release)
  os_version=$(awk -F= '$1 == "VERSION_ID" {gsub(/\"/, "", $2); print $2}' /etc/os-release)
  [[ "$os_id" == ubuntu && "$os_version" == 20.04 ]] || \
    die "Ubuntu 20.04 is required; detected ${os_id:-unknown} ${os_version:-unknown}"
}

source_root_from_script() {
  local script_dir
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[1]}")" && pwd -P)
  cd -- "$script_dir/.." && pwd -P
}

read_pinned_version() {
  local key=$1
  local version_file=${2:-$MYPROXY_ROOT/.runtime-versions}
  [[ -f "$version_file" ]] || die "missing runtime version file: $version_file"

  local value count
  count=$(awk -F= -v key="$key" '$1 == key {count++} END {print count + 0}' "$version_file")
  [[ "$count" == 1 ]] || die "expected exactly one $key entry in $version_file"
  value=$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print}' "$version_file")
  [[ "$value" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]] || \
    die "invalid pinned version for $key: $value"
  printf '%s\n' "$value"
}

detect_architecture() {
  case "$(uname -m)" in
    x86_64) printf 'amd64\n' ;;
    aarch64|arm64) printf 'arm64\n' ;;
    *) die "unsupported architecture: $(uname -m)" ;;
  esac
}

atomic_install() {
  local source=$1 destination=$2 mode=$3 owner=${4:-root} group=${5:-root}
  local destination_dir temporary
  destination_dir=$(dirname -- "$destination")
  if [[ -e "$destination_dir" || -L "$destination_dir" ]]; then
    [[ -d "$destination_dir" && ! -L "$destination_dir" ]] || \
      die "destination parent must be a real directory: $destination_dir"
  else
    install -d -m 0755 -o root -g root "$destination_dir"
  fi
  temporary=$(mktemp "$destination_dir/.myproxy-install.XXXXXX")
  install -m "$mode" -o "$owner" -g "$group" "$source" "$temporary"
  mv -f -- "$temporary" "$destination"
}

run_as_myproxy() {
  runuser -u "$MYPROXY_USER" -- env -i PATH=/usr/bin:/bin "$@"
}

is_allowed_app_env_key() {
  case "$1" in
    MYPROXY_ENV|MYPROXY_HOME|MYPROXY_DB|MYPROXY_SINGBOX|MYPROXY_SINGBOX_CONFIG|\
    MYPROXY_LOG_DIR|MYPROXY_BACKUP_DIR|MYPROXY_RUN_DIR|MYPROXY_SESSION_TTL|\
    MYPROXY_COOKIE_SECURE|MYPROXY_ALLOWED_HOSTS|MYPROXY_CORS_ORIGINS|\
    MYPROXY_USE_SUDO|MYPROXY_SYSTEMCTL|MYPROXY_SUDO|MYPROXY_SINGBOX_SERVICE|\
    MYPROXY_SINGBOX_VERSION|MYPROXY_BACKUP_LIMIT|MYPROXY_CERTIFICATE_PATH|\
    MYPROXY_PRIVATE_KEY_PATH|MYPROXY_SERVER_IP|MYPROXY_DOMAIN|\
    MYPROXY_SELF_SIGNED_MODE|SERVER_IP|DOMAIN|ACME_EMAIL|SELF_SIGNED_MODE)
      return 0
      ;;
    *) return 1 ;;
  esac
}

normalize_app_env_file() {
  local env_file=${1:-$MYPROXY_ROOT/config/app.env}
  local env_dir line key value env_tmp error=''
  declare -A seen=()

  env_dir=$(dirname -- "$env_file")
  [[ -d "$env_dir" && ! -L "$env_dir" && "$(readlink -f -- "$env_dir")" == "$env_dir" ]] || \
    die "application configuration directory must be a real directory: $env_dir"
  [[ -f "$env_file" && ! -L "$env_file" ]] || \
    die "application environment must be a regular file: $env_file"
  env_tmp=$(mktemp "$(dirname -- "$env_file")/.app.env.normalized.XXXXXX")
  chmod 0600 "$env_tmp"

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" == \#* ]]; then
      printf '%s\n' "$line" >>"$env_tmp"
      continue
    fi
    if [[ "$line" == *$'\r'* || "$line" != *=* ]]; then
      error="malformed line in $env_file"
      break
    fi
    key=${line%%=*}
    value=${line#*=}
    if [[ ! "$key" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
      error="invalid key in $env_file"
      break
    fi
    if ! is_allowed_app_env_key "$key"; then
      warn "dropping unsupported environment key from $env_file: $key"
      continue
    fi
    if [[ ! "$value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]]; then
      error="unsafe value for $key in $env_file"
      break
    fi
    if [[ -n "${seen[$key]:-}" ]]; then
      error="duplicate $key in $env_file"
      break
    fi
    seen[$key]=1
    printf '%s=%s\n' "$key" "$value" >>"$env_tmp"
  done <"$env_file"

  if [[ -n "$error" ]]; then
    rm -f -- "$env_tmp"
    die "$error"
  fi
  chown root:"$MYPROXY_GROUP" "$env_tmp"
  chmod 0640 "$env_tmp"
  mv -f -- "$env_tmp" "$env_file"
}

chown_tree_nofollow() {
  local owner=$1 group=$2 tree
  shift 2
  for tree in "$@"; do
    if [[ ! -d "$tree" || -L "$tree" ]]; then
      warn "refusing recursive ownership change on unsafe directory: $tree"
      return 1
    fi
    find -P "$tree" -xdev -exec chown --no-dereference "$owner:$group" {} + || return 1
  done
}

read_app_env_value() {
  local key=$1
  local env_file=${2:-$MYPROXY_ROOT/config/app.env}
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "invalid environment key requested"
  [[ -f "$env_file" && ! -L "$env_file" && -r "$env_file" ]] || \
    die "missing or unsafe application environment: $env_file"

  local line parsed_key value found=0 result=''
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" && "$line" != \#* ]] || continue
    [[ "$line" != *$'\r'* && "$line" == *=* ]] || die "malformed line in $env_file"
    parsed_key=${line%%=*}
    value=${line#*=}
    [[ "$parsed_key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "invalid key in $env_file"
    is_allowed_app_env_key "$parsed_key" || continue
    [[ "$value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]] || \
      die "unsafe value for $parsed_key in $env_file"
    if [[ "$parsed_key" == "$key" ]]; then
      ((found += 1))
      result=$value
    fi
  done <"$env_file"
  ((found <= 1)) || die "duplicate $key in $env_file"
  [[ "$found" == 1 ]] || return 1
  printf '%s\n' "$result"
}

install_project_permissions() {
  local mutable_config tls_file
  assert_install_root "$MYPROXY_ROOT"
  chown_tree_nofollow root root "$MYPROXY_ROOT/backend" "$MYPROXY_ROOT/frontend" \
    "$MYPROXY_ROOT/deploy" "$MYPROXY_ROOT/scripts" || die "could not seal project source ownership"
  chown --no-dereference root:root "$MYPROXY_ROOT/myproxy"
  if [[ -d "$MYPROXY_ROOT/.git" ]]; then
    chown_tree_nofollow root root "$MYPROXY_ROOT/.git" || die "could not seal Git ownership"
    chmod -R go-rwx "$MYPROXY_ROOT/.git"
  fi
  if [[ -d "$MYPROXY_ROOT/backend/.venv" ]]; then
    chown_tree_nofollow root "$MYPROXY_GROUP" "$MYPROXY_ROOT/backend/.venv" || \
      die "could not seal backend environment ownership"
    chmod -R go-w "$MYPROXY_ROOT/backend/.venv"
    chmod 0750 "$MYPROXY_ROOT/backend/.venv"
  fi
  chown_tree_nofollow "$MYPROXY_USER" "$MYPROXY_GROUP" \
    "$MYPROXY_ROOT/data" "$MYPROXY_ROOT/logs" "$MYPROXY_ROOT/run" \
    "$MYPROXY_ROOT/backups" || die "could not set mutable directory ownership"
  [[ -d "$MYPROXY_ROOT/config" && ! -L "$MYPROXY_ROOT/config" && \
    "$(readlink -f -- "$MYPROXY_ROOT/config")" == "$MYPROXY_ROOT/config" ]] || \
    die "config directory must be a real directory"
  chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/config"
  chmod 1770 "$MYPROXY_ROOT/config"
  if [[ -d "$MYPROXY_ROOT/config/tls" ]]; then
    [[ ! -L "$MYPROXY_ROOT/config/tls" ]] || die "TLS directory must not be a symbolic link"
    chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/config/tls"
    chmod 0750 "$MYPROXY_ROOT/config/tls"
  fi
  if [[ -e "$MYPROXY_ROOT/config/app.env" || -L "$MYPROXY_ROOT/config/app.env" ]]; then
    [[ -f "$MYPROXY_ROOT/config/app.env" && ! -L "$MYPROXY_ROOT/config/app.env" ]] || \
      die "config/app.env must be a regular file"
    chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/config/app.env"
    chmod 0640 "$MYPROXY_ROOT/config/app.env"
  fi
  for mutable_config in sing-box.json; do
    if [[ -e "$MYPROXY_ROOT/config/$mutable_config" || -L "$MYPROXY_ROOT/config/$mutable_config" ]]; then
      [[ -f "$MYPROXY_ROOT/config/$mutable_config" && ! -L "$MYPROXY_ROOT/config/$mutable_config" ]] || \
        die "config/$mutable_config must be a regular file"
      chown "$MYPROXY_USER:$MYPROXY_GROUP" "$MYPROXY_ROOT/config/$mutable_config"
      chmod 0600 "$MYPROXY_ROOT/config/$mutable_config"
    fi
  done
  if [[ -d "$MYPROXY_ROOT/config/tls" ]]; then
    for tls_file in fullchain.pem privkey.pem; do
      if [[ -e "$MYPROXY_ROOT/config/tls/$tls_file" || -L "$MYPROXY_ROOT/config/tls/$tls_file" ]]; then
        [[ -f "$MYPROXY_ROOT/config/tls/$tls_file" && ! -L "$MYPROXY_ROOT/config/tls/$tls_file" ]] || \
          die "TLS file must be regular: $tls_file"
        chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/config/tls/$tls_file"
      fi
    done
    [[ ! -f "$MYPROXY_ROOT/config/tls/fullchain.pem" ]] || chmod 0640 "$MYPROXY_ROOT/config/tls/fullchain.pem"
    [[ ! -f "$MYPROXY_ROOT/config/tls/privkey.pem" ]] || chmod 0640 "$MYPROXY_ROOT/config/tls/privkey.pem"
  fi
  chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime"
  [[ ! -d "$MYPROXY_ROOT/.runtime/cache" ]] || \
    chown_tree_nofollow "$MYPROXY_USER" "$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime/cache" || \
      die "could not set runtime cache ownership"
  if [[ -d "$MYPROXY_ROOT/.build" ]]; then
    chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/.build"
    chmod 0710 "$MYPROXY_ROOT/.build"
  fi
  if [[ -d "$MYPROXY_ROOT/.protected-backups" ]]; then
    chown_tree_nofollow root root "$MYPROXY_ROOT/.protected-backups" || \
      die "could not protect deployment backups"
    chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/.protected-backups"
    chmod 0710 "$MYPROXY_ROOT/.protected-backups"
  fi
  chmod 0755 "$MYPROXY_ROOT"
  chmod 0750 "$MYPROXY_ROOT/data" "$MYPROXY_ROOT/logs" "$MYPROXY_ROOT/run" \
    "$MYPROXY_ROOT/backups" "$MYPROXY_ROOT/.runtime"
  [[ ! -f "$MYPROXY_ROOT/config/app.env" ]] || chmod 0640 "$MYPROXY_ROOT/config/app.env"
}

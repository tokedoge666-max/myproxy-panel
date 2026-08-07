#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_root
acquire_deploy_lock
acquire_certificate_lock
assert_ubuntu_2004
assert_install_root "$MYPROXY_ROOT"
[[ -d "$MYPROXY_ROOT/.git" ]] || die "$MYPROXY_ROOT is not a Git checkout"
[[ -x "$MYPROXY_ROOT/backend/.venv/bin/python" ]] || die "installed backend environment is missing"
sanitize_git_checkout
normalize_app_env_file "$MYPROXY_ROOT/config/app.env"

if ! git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" diff --quiet --ignore-submodules --; then
  die "tracked local changes exist in $MYPROXY_ROOT; commit or remove them before updating"
fi
if ! git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" diff --cached --quiet --ignore-submodules --; then
  die "staged local changes exist in $MYPROXY_ROOT; commit or remove them before updating"
fi

OLD_COMMIT=$(git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" rev-parse --verify HEAD)
UPDATE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
readonly OLD_COMMIT UPDATE_ID
readonly UPDATE_DIR="$MYPROXY_ROOT/.protected-backups/update/releases/$UPDATE_ID"
readonly RUNTIME_ARCHIVE="$UPDATE_DIR/runtimes.tar.gz"
readonly SOURCE_BUNDLE="$UPDATE_DIR/source.bundle"

assert_under_root "$UPDATE_DIR"
assert_under_root "$RUNTIME_ARCHIVE"

transaction_started=false
update_complete=false
api_recovery_needed=false
singbox_recovery_needed=false
state_archive=''

api_was_active=false
singbox_was_active=false
systemctl is-active --quiet "$MYPROXY_API_UNIT" && api_was_active=true
systemctl is-active --quiet "$MYPROXY_SINGBOX_UNIT" && singbox_was_active=true
[[ "$api_was_active" == true && "$singbox_was_active" == true ]] || \
  die "both MyProxy services must be active before an update"

escape_sed_replacement() {
  sed 's/[&|]/\\&/g' <<<"$1"
}

collect_app_environment() {
  APP_ENV_ARGS=()
  declare -A seen_keys=()
  local key value
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    [[ -n "$key" && "$key" != \#* ]] || continue
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "invalid key in config/app.env"
    [[ "$value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]] || die "unsafe value for $key in config/app.env"
    [[ -z "${seen_keys[$key]:-}" ]] || die "duplicate $key in config/app.env"
    seen_keys[$key]=1
    case "$key" in
      MYPROXY_ENV|MYPROXY_HOME|MYPROXY_DB|MYPROXY_SINGBOX|MYPROXY_SINGBOX_CONFIG|\
      MYPROXY_LOG_DIR|MYPROXY_BACKUP_DIR|MYPROXY_RUN_DIR|MYPROXY_SESSION_TTL|\
      MYPROXY_COOKIE_SECURE|MYPROXY_ALLOWED_HOSTS|MYPROXY_CORS_ORIGINS|\
      MYPROXY_USE_SUDO|MYPROXY_SYSTEMCTL|MYPROXY_SUDO|MYPROXY_SINGBOX_SERVICE|\
      MYPROXY_SINGBOX_VERSION|MYPROXY_BACKUP_LIMIT|MYPROXY_CERTIFICATE_PATH|\
      MYPROXY_PRIVATE_KEY_PATH|MYPROXY_SERVER_IP|MYPROXY_DOMAIN|\
      MYPROXY_SELF_SIGNED_MODE|SERVER_IP|DOMAIN|ACME_EMAIL|SELF_SIGNED_MODE)
        APP_ENV_ARGS+=("$key=$value")
        ;;
    esac
  done <"$MYPROXY_ROOT/config/app.env"
}

install_integrations() {
  local server_name self_signed nginx_tmp
  server_name=$(read_app_env_value DOMAIN "$MYPROXY_ROOT/config/app.env")
  [[ -n "$server_name" ]] || server_name=$(read_app_env_value SERVER_IP "$MYPROXY_ROOT/config/app.env")
  self_signed=$(read_app_env_value SELF_SIGNED_MODE "$MYPROXY_ROOT/config/app.env")
  [[ "$server_name" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || \
    die "unsafe server name in config/app.env"
  [[ "$self_signed" == true || "$self_signed" == false ]] || die "invalid SELF_SIGNED_MODE"

  atomic_install "$MYPROXY_ROOT/deploy/systemd/myproxy-api.service" \
    "/etc/systemd/system/$MYPROXY_API_UNIT" 0644 root root
  atomic_install "$MYPROXY_ROOT/deploy/systemd/myproxy-singbox.service" \
    "/etc/systemd/system/$MYPROXY_SINGBOX_UNIT" 0644 root root
  atomic_install "$MYPROXY_ROOT/deploy/sudoers/myproxy" "$MYPROXY_SUDOERS" 0440 root root
  visudo -cf "$MYPROXY_SUDOERS"
  atomic_install "$MYPROXY_ROOT/deploy/logrotate/myproxy" "$MYPROXY_LOGROTATE" 0644 root root
  logrotate --debug "$MYPROXY_LOGROTATE" >/dev/null

  if [[ "$self_signed" == false ]]; then
    atomic_install "$MYPROXY_ROOT/deploy/scripts/cert-deploy-hook.sh" \
      "$MYPROXY_CERT_HOOK" 0750 root root
  else
    rm -f -- "$MYPROXY_CERT_HOOK"
  fi

  nginx_tmp=$(mktemp /etc/nginx/sites-available/.myproxy-update.XXXXXX)
  sed "s|__SERVER_NAME__|$(escape_sed_replacement "$server_name")|g" \
    "$MYPROXY_ROOT/deploy/nginx/myproxy.conf.template" >"$nginx_tmp"
  chmod 0644 "$nginx_tmp"
  mv -f -- "$nginx_tmp" "$MYPROXY_NGINX_SITE"
  ln -sfn "$MYPROXY_NGINX_SITE" "$MYPROXY_NGINX_LINK"
  nginx -t
  systemctl daemon-reload
}

restore_state_archive() {
  local restore_stage="$UPDATE_DIR/restore-state"
  assert_under_root "$restore_stage"
  [[ -f "$state_archive" ]] || return 1
  assert_safe_tar_gz "$state_archive"
  rm -rf -- "$restore_stage"
  install -d -m 0700 "$restore_stage"
  tar -xzf "$state_archive" -C "$restore_stage"

  if [[ -d "$restore_stage/config" ]]; then
    rm -rf -- "$MYPROXY_ROOT/config"
    cp -a -- "$restore_stage/config" "$MYPROXY_ROOT/config"
  fi
  rm -f -- "$MYPROXY_ROOT/data/myproxy.db" "$MYPROXY_ROOT/data/myproxy.db-shm" \
    "$MYPROXY_ROOT/data/myproxy.db-wal"
  if [[ -f "$restore_stage/data/myproxy.db" ]]; then
    install -m 0600 -o "$MYPROXY_USER" -g "$MYPROXY_GROUP" \
      "$restore_stage/data/myproxy.db" "$MYPROXY_ROOT/data/myproxy.db"
  fi
  if [[ -d "$restore_stage/frontend/dist" ]]; then
    rm -rf -- "$MYPROXY_ROOT/frontend/dist"
    cp -a -- "$restore_stage/frontend/dist" "$MYPROXY_ROOT/frontend/dist"
  fi
  [[ ! -f "$restore_stage/.runtime-versions" ]] || \
    cp -a -- "$restore_stage/.runtime-versions" "$MYPROXY_ROOT/.runtime-versions"
  rm -rf -- "$restore_stage"
}

restore_runtimes() {
  [[ -f "$RUNTIME_ARCHIVE" ]] || return 1
  local component
  assert_safe_tar_gz "$RUNTIME_ARCHIVE"
  for component in uv node sing-box python; do
    assert_under_root "$MYPROXY_ROOT/.runtime/$component"
    rm -rf -- "$MYPROXY_ROOT/.runtime/$component"
  done
  rm -rf -- "$MYPROXY_ROOT/backend/.venv"
  tar -xzf "$RUNTIME_ARCHIVE" -C "$MYPROXY_ROOT"
  chown_tree_nofollow root root "$MYPROXY_ROOT/.runtime/uv" "$MYPROXY_ROOT/.runtime/node" \
    "$MYPROXY_ROOT/.runtime/sing-box" || return 1
  chown_tree_nofollow root "$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime/python" || return 1
  chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime"
  chown_tree_nofollow root "$MYPROXY_GROUP" "$MYPROXY_ROOT/backend/.venv" || return 1
  chmod -R go-w "$MYPROXY_ROOT/.runtime/python" "$MYPROXY_ROOT/backend/.venv"
  chmod 0750 "$MYPROXY_ROOT/backend/.venv"
  [[ ! -d "$MYPROXY_ROOT/.runtime/cache" ]] || \
    chown_tree_nofollow "$MYPROXY_USER" "$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime/cache"
}

rollback_update() {
  local original_status=$1
  local added_paths_file="$UPDATE_DIR/added-paths"
  local added_path added_target
  trap - EXIT ERR
  set +e
  warn "update failed; restoring commit $OLD_COMMIT and protected state"
  systemctl stop "$MYPROXY_API_UNIT" "$MYPROXY_SINGBOX_UNIT" >/dev/null 2>&1
  git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
    diff --name-only --diff-filter=A -z "$OLD_COMMIT"..HEAD >"$added_paths_file" 2>/dev/null
  if ! git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
    reset --hard "$OLD_COMMIT" >/dev/null 2>&1; then
    git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
      fetch "$SOURCE_BUNDLE" "$OLD_COMMIT" >/dev/null 2>&1
    git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
      reset --hard "$OLD_COMMIT" >/dev/null 2>&1
  fi
  if [[ -f "$added_paths_file" ]]; then
    while IFS= read -r -d '' added_path; do
      [[ -n "$added_path" && "$added_path" != /* && "$added_path" != ../* && \
        "$added_path" != */../* && "$added_path" != */.. ]] || continue
      added_target="$MYPROXY_ROOT/$added_path"
      case "$added_target" in "$MYPROXY_ROOT"/*) rm -f -- "$added_target" ;; esac
    done <"$added_paths_file"
  fi
  restore_state_archive
  restore_runtimes
  find "$MYPROXY_ROOT/deploy" "$MYPROXY_ROOT/scripts" -type f -name '*.sh' -exec chmod 0750 {} +
  chmod 0755 "$MYPROXY_ROOT/myproxy"
  install_project_permissions
  install_integrations
  systemctl restart "$MYPROXY_SINGBOX_UNIT" "$MYPROXY_API_UNIT" nginx.service
  if bash "$MYPROXY_ROOT/deploy/scripts/health-check.sh"; then
    warn "the previous release was restored successfully"
  else
    warn "rollback completed, but health checks still fail; inspect journalctl before retrying"
  fi
  exit "$original_status"
}

on_update_exit() {
  local status=$?
  if [[ "$transaction_started" == true && "$update_complete" != true ]]; then
    rollback_update "$status"
  fi
  if [[ "$api_recovery_needed" == true && "$api_was_active" == true ]]; then
    systemctl restart "$MYPROXY_API_UNIT" >/dev/null 2>&1 || \
      warn "could not restart the API after a pre-update failure"
  fi
  if [[ "$singbox_recovery_needed" == true && "$singbox_was_active" == true ]]; then
    systemctl restart "$MYPROXY_SINGBOX_UNIT" >/dev/null 2>&1 || \
      warn "could not restart sing-box after a pre-update failure"
  fi
  exit "$status"
}
trap on_update_exit EXIT

install -d -m 0710 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/.protected-backups"
install -d -m 0700 -o root -g root "$MYPROXY_ROOT/.protected-backups/update" \
  "$MYPROXY_ROOT/.protected-backups/update/releases" "$UPDATE_DIR"
printf '%s\n' "$OLD_COMMIT" >"$UPDATE_DIR/old-commit"
chmod 0600 "$UPDATE_DIR/old-commit"
api_recovery_needed=true
systemctl stop "$MYPROXY_API_UNIT"
log "backing up database, configuration and current frontend"
backup_output=$(bash "$MYPROXY_ROOT/deploy/scripts/backup-state.sh" update)
state_archive=$(tail -n 1 <<<"$backup_output")
case "$state_archive" in
  "$MYPROXY_ROOT"/.protected-backups/update/myproxy-*.tar.gz) ;;
  *) die "unexpected state-backup path: $state_archive" ;;
esac
[[ -f "$state_archive" ]] || die "state backup was not created"

log "backing up current project runtimes and source commit"
tar -C "$MYPROXY_ROOT" --exclude='.runtime/cache' -czf "$RUNTIME_ARCHIVE" \
  .runtime/uv .runtime/node .runtime/sing-box .runtime/python backend/.venv
chmod 0600 "$RUNTIME_ARCHIVE"
git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" bundle create "$SOURCE_BUNDLE" HEAD
chmod 0600 "$SOURCE_BUNDLE"
transaction_started=true

log "fetching the next release"
git -c safe.directory="$MYPROXY_ROOT" \
  -c core.hooksPath="$MYPROXY_ROOT/.git/myproxy-disabled-hooks" \
  -c credential.helper= -c submodule.recurse=false \
  -c protocol.ext.allow=never -c protocol.file.allow=never \
  -c protocol.ssh.allow=never -c protocol.git.allow=never \
  -c protocol.http.allow=never -c protocol.https.allow=always \
  -c http.sslVerify=true -C "$MYPROXY_ROOT" pull --ff-only
new_singbox_version=$(read_pinned_version SINGBOX_VERSION "$MYPROXY_ROOT/.runtime-versions")
[[ "$new_singbox_version" == 1.13.16 ]] || die "the fetched release does not pin sing-box 1.13.16"

find "$MYPROXY_ROOT/deploy" "$MYPROXY_ROOT/scripts" -type f -name '*.sh' -exec chmod 0750 {} +
chmod 0755 "$MYPROXY_ROOT/myproxy"
install_project_permissions

log "updating pinned runtimes and rebuilding sequentially"
bash "$MYPROXY_ROOT/deploy/scripts/download-runtime.sh" all
bash "$MYPROXY_ROOT/deploy/scripts/build-project.sh" all

collect_app_environment
(cd "$MYPROXY_ROOT/backend" && \
  runuser -u "$MYPROXY_USER" -- env -i PATH=/usr/bin:/bin "${APP_ENV_ARGS[@]}" \
    "$MYPROXY_ROOT/backend/.venv/bin/python" -m app.cli migrate)
(cd "$MYPROXY_ROOT/backend" && \
  runuser -u "$MYPROXY_USER" -- env -i PATH=/usr/bin:/bin "${APP_ENV_ARGS[@]}" \
    "$MYPROXY_ROOT/backend/.venv/bin/python" -m app.cli prepare-config)

run_as_myproxy "$MYPROXY_ROOT/.runtime/sing-box/sing-box" \
  check -c "$MYPROXY_ROOT/config/sing-box.json"
install_project_permissions
install_integrations
singbox_recovery_needed=true
systemctl stop "$MYPROXY_SINGBOX_UNIT"
systemctl restart "$MYPROXY_SINGBOX_UNIT"
systemctl restart "$MYPROXY_API_UNIT"
systemctl restart nginx.service
bash "$MYPROXY_ROOT/deploy/scripts/health-check.sh"

update_complete=true
trap - EXIT

mapfile -t old_release_dirs < <(find "$MYPROXY_ROOT/.protected-backups/update/releases" \
  -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{sub(/^[^ ]+ /, ""); print}')
if ((${#old_release_dirs[@]} > 2)); then
  for old_release in "${old_release_dirs[@]:2}"; do
    case "$old_release" in
      "$MYPROXY_ROOT"/.protected-backups/update/releases/[0-9]*T[0-9]*Z-[0-9]*) rm -rf -- "$old_release" ;;
      *) die "refusing to prune unexpected update backup: $old_release" ;;
    esac
  done
fi

log "update completed successfully"
log "rollback assets retained in $UPDATE_DIR"

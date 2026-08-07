#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_root
acquire_deploy_lock
assert_ubuntu_2004
assert_install_root "$MYPROXY_ROOT"

readonly ENV_FILE="$MYPROXY_ROOT/config/app.env"
readonly TEMP_NGINX_SITE="/etc/nginx/sites-available/myproxy-reconfigure-acme.conf"
readonly TEMP_NGINX_LINK="/etc/nginx/sites-enabled/myproxy-reconfigure-acme.conf"
[[ -r "$ENV_FILE" ]] || die "installed app.env is missing"
[[ -x "$MYPROXY_ROOT/backend/.venv/bin/python" ]] || die "installed backend environment is missing"
[[ -f "$MYPROXY_ROOT/data/myproxy.db" && ! -L "$MYPROXY_ROOT/data/myproxy.db" ]] || \
  die "existing database is required; reconfigure never initializes a new installation"
[[ -f "$MYPROXY_NGINX_SITE" && ! -L "$MYPROXY_NGINX_SITE" ]] || \
  die "installed nginx site is missing or unsafe"
normalize_app_env_file "$ENV_FILE"
singbox_version=$(read_pinned_version SINGBOX_VERSION "$MYPROXY_ROOT/.runtime-versions")
[[ "$singbox_version" == 1.13.16 ]] || die "installed release does not pin sing-box 1.13.16"

old_domain=$(read_app_env_value DOMAIN "$ENV_FILE")
old_server_ip=$(read_app_env_value SERVER_IP "$ENV_FILE")
old_email=$(read_app_env_value ACME_EMAIL "$ENV_FILE")
old_self_signed=$(read_app_env_value SELF_SIGNED_MODE "$ENV_FILE")
[[ "$old_self_signed" == true || "$old_self_signed" == false ]] || die "invalid current TLS mode"

domain=$old_domain
server_ip=$old_server_ip
acme_email=$old_email
self_signed=$old_self_signed
mode_selected=false

while (($#)); do
  case "$1" in
    --domain)
      (($# >= 2)) || die "--domain requires a value"
      domain=$2
      shift 2
      ;;
    --no-domain)
      domain=''
      shift
      ;;
    --server-ip)
      (($# >= 2)) || die "--server-ip requires a value"
      server_ip=$2
      shift 2
      ;;
    --email)
      (($# >= 2)) || die "--email requires a value"
      acme_email=$2
      shift 2
      ;;
    --self-signed)
      [[ "$mode_selected" == false ]] || die "choose only one TLS mode"
      self_signed=true
      mode_selected=true
      shift
      ;;
    --acme)
      [[ "$mode_selected" == false ]] || die "choose only one TLS mode"
      self_signed=false
      mode_selected=true
      shift
      ;;
    -h|--help)
      printf 'Usage: sudo bash deploy/reconfigure.sh [--domain NAME|--no-domain] [--server-ip IP] [--email ADDRESS] [--acme|--self-signed]\n'
      exit 0
      ;;
    *) die "unknown reconfigure option: $1" ;;
  esac
done

is_ipv4() {
  local address=$1 octet
  local -a octets
  IFS=. read -r -a octets <<<"$address"
  ((${#octets[@]} == 4)) || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
    ((10#$octet <= 255)) || return 1
  done
}

[[ -n "$server_ip" ]] && is_ipv4 "$server_ip" || die "SERVER_IP must be an IPv4 address"
if [[ -n "$domain" ]]; then
  [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ && "$domain" == *.* ]] || \
    die "invalid domain"
fi
if [[ "$self_signed" == false ]]; then
  [[ -n "$domain" ]] || die "ACME mode requires --domain"
  [[ "$acme_email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || \
    die "ACME mode requires a valid --email"
else
  [[ -n "$domain" || -n "$server_ip" ]] || die "self-signed mode requires a domain or server IP"
fi

server_name=${domain:-$server_ip}
domain_value=$domain
[[ "$server_name" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || \
  die "invalid resulting server name"

RECONFIGURE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
readonly RECONFIGURE_ID
readonly RECONFIGURE_DIR="$MYPROXY_ROOT/.protected-backups/reconfigure/releases/$RECONFIGURE_ID"
readonly NGINX_BACKUP="$RECONFIGURE_DIR/myproxy.conf"
assert_under_root "$RECONFIGURE_DIR"

transaction_started=false
reconfigure_complete=false
certificate_lock_held=false
services_recovery_needed=false
state_archive=''

api_was_active=false
singbox_was_active=false
systemctl is-active --quiet "$MYPROXY_API_UNIT" && api_was_active=true
systemctl is-active --quiet "$MYPROXY_SINGBOX_UNIT" && singbox_was_active=true
[[ "$api_was_active" == true && "$singbox_was_active" == true ]] || \
  die "both MyProxy services must be active before reconfiguration"

lock_certificate_state() {
  if [[ "$certificate_lock_held" != true ]]; then
    acquire_certificate_lock
    certificate_lock_held=true
  fi
}

unlock_certificate_state() {
  if [[ "$certificate_lock_held" == true ]]; then
    release_certificate_lock
    certificate_lock_held=false
  fi
}

escape_sed_replacement() {
  sed 's/[&|]/\\&/g' <<<"$1"
}

remove_temporary_nginx_site() {
  rm -f -- "$TEMP_NGINX_LINK" "$TEMP_NGINX_SITE"
}

write_deployment_env() {
  local env_tmp line key value replacement
  declare -A seen=()
  env_tmp=$(mktemp "$MYPROXY_ROOT/config/.app.env.reconfigure.XXXXXX")
  chmod 0600 "$env_tmp"

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" == \#* ]]; then
      printf '%s\n' "$line" >>"$env_tmp"
      continue
    fi
    [[ "$line" != *$'\r'* && "$line" == *=* ]] || die "malformed line in app.env"
    key=${line%%=*}
    value=${line#*=}
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "invalid key in app.env"
    [[ "$value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]] || die "unsafe value for $key in app.env"
    [[ -z "${seen[$key]:-}" ]] || die "duplicate $key in app.env"
    seen[$key]=1
    replacement=$value
    case "$key" in
      MYPROXY_ALLOWED_HOSTS) replacement="$server_name,localhost,127.0.0.1" ;;
      MYPROXY_CERTIFICATE_PATH) replacement="$MYPROXY_ROOT/config/tls/fullchain.pem" ;;
      MYPROXY_PRIVATE_KEY_PATH) replacement="$MYPROXY_ROOT/config/tls/privkey.pem" ;;
      MYPROXY_SERVER_IP|SERVER_IP) replacement=$server_ip ;;
      MYPROXY_DOMAIN|DOMAIN) replacement=$domain_value ;;
      MYPROXY_SELF_SIGNED_MODE|SELF_SIGNED_MODE) replacement=$self_signed ;;
      ACME_EMAIL) replacement=$acme_email ;;
    esac
    printf '%s=%s\n' "$key" "$replacement" >>"$env_tmp"
  done <"$ENV_FILE"

  for key in MYPROXY_ALLOWED_HOSTS MYPROXY_CERTIFICATE_PATH MYPROXY_PRIVATE_KEY_PATH \
    MYPROXY_SERVER_IP MYPROXY_DOMAIN MYPROXY_SELF_SIGNED_MODE \
    SERVER_IP DOMAIN ACME_EMAIL SELF_SIGNED_MODE; do
    [[ -n "${seen[$key]:-}" ]] && continue
    case "$key" in
      MYPROXY_ALLOWED_HOSTS) replacement="$server_name,localhost,127.0.0.1" ;;
      MYPROXY_CERTIFICATE_PATH) replacement="$MYPROXY_ROOT/config/tls/fullchain.pem" ;;
      MYPROXY_PRIVATE_KEY_PATH) replacement="$MYPROXY_ROOT/config/tls/privkey.pem" ;;
      MYPROXY_SERVER_IP|SERVER_IP) replacement=$server_ip ;;
      MYPROXY_DOMAIN|DOMAIN) replacement=$domain_value ;;
      MYPROXY_SELF_SIGNED_MODE|SELF_SIGNED_MODE) replacement=$self_signed ;;
      ACME_EMAIL) replacement=$acme_email ;;
    esac
    printf '%s=%s\n' "$key" "$replacement" >>"$env_tmp"
  done

  chown root:"$MYPROXY_GROUP" "$env_tmp"
  chmod 0640 "$env_tmp"
  mv -f -- "$env_tmp" "$ENV_FILE"
}

restore_state() {
  local restore_stage="$RECONFIGURE_DIR/restore-state"
  assert_under_root "$restore_stage"
  assert_safe_tar_gz "$state_archive"
  rm -rf -- "$restore_stage"
  install -d -m 0700 -o root -g root "$restore_stage"
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
  rm -rf -- "$restore_stage"
}

rollback_reconfigure() {
  local original_status=$1
  trap - EXIT ERR
  set +e
  warn "reconfiguration failed; restoring the previous deployment settings"
  lock_certificate_state
  systemctl stop "$MYPROXY_API_UNIT" "$MYPROXY_SINGBOX_UNIT" >/dev/null 2>&1
  remove_temporary_nginx_site
  restore_state
  atomic_install "$NGINX_BACKUP" "$MYPROXY_NGINX_SITE" 0644 root root
  ln -sfn "$MYPROXY_NGINX_SITE" "$MYPROXY_NGINX_LINK"
  if [[ "$old_self_signed" == false ]]; then
    atomic_install "$MYPROXY_ROOT/deploy/scripts/cert-deploy-hook.sh" \
      "$MYPROXY_CERT_HOOK" 0750 root root
  else
    rm -f -- "$MYPROXY_CERT_HOOK"
  fi
  install_project_permissions
  systemctl daemon-reload
  nginx -t
  systemctl restart "$MYPROXY_SINGBOX_UNIT" "$MYPROXY_API_UNIT" nginx.service
  if bash "$MYPROXY_ROOT/deploy/scripts/health-check.sh"; then
    warn "previous deployment settings restored successfully"
  else
    warn "rollback completed, but health checks still fail; inspect the service journal"
  fi
  unlock_certificate_state
  exit "$original_status"
}

on_reconfigure_exit() {
  local status=$?
  if [[ "$transaction_started" == true && "$reconfigure_complete" != true ]]; then
    rollback_reconfigure "$status"
  fi
  if [[ "$services_recovery_needed" == true ]]; then
    systemctl restart "$MYPROXY_SINGBOX_UNIT" "$MYPROXY_API_UNIT" >/dev/null 2>&1 || \
      warn "could not restart services after a pre-reconfigure failure"
  fi
  remove_temporary_nginx_site
  unlock_certificate_state
  exit "$status"
}
trap on_reconfigure_exit EXIT

remove_temporary_nginx_site
lock_certificate_state
install -d -m 0710 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/.protected-backups"
install -d -m 0700 -o root -g root \
  "$MYPROXY_ROOT/.protected-backups/reconfigure" \
  "$MYPROXY_ROOT/.protected-backups/reconfigure/releases" "$RECONFIGURE_DIR"
atomic_install "$MYPROXY_NGINX_SITE" "$NGINX_BACKUP" 0600 root root
services_recovery_needed=true
systemctl stop "$MYPROXY_API_UNIT"
systemctl stop "$MYPROXY_SINGBOX_UNIT"
backup_output=$(bash "$MYPROXY_ROOT/deploy/scripts/backup-state.sh" reconfigure)
state_archive=$(tail -n 1 <<<"$backup_output")
case "$state_archive" in
  "$MYPROXY_ROOT"/.protected-backups/reconfigure/myproxy-*.tar.gz) ;;
  *) die "unexpected state-backup path: $state_archive" ;;
esac
assert_safe_tar_gz "$state_archive"
transaction_started=true

write_deployment_env

if [[ "$self_signed" == false && "$server_name" != "$old_domain" ]]; then
  nginx_tmp=$(mktemp /etc/nginx/sites-available/.myproxy-reconfigure.XXXXXX)
  sed "s|__SERVER_NAME__|$(escape_sed_replacement "$server_name")|g" \
    "$MYPROXY_ROOT/deploy/nginx/myproxy-http.conf.template" >"$nginx_tmp"
  chmod 0644 "$nginx_tmp"
  mv -f -- "$nginx_tmp" "$TEMP_NGINX_SITE"
  ln -sfn "$TEMP_NGINX_SITE" "$TEMP_NGINX_LINK"
  nginx -t
  systemctl reload nginx.service
fi

if [[ "$self_signed" == false ]]; then
  atomic_install "$MYPROXY_ROOT/deploy/scripts/cert-deploy-hook.sh" \
    "$MYPROXY_CERT_HOOK" 0750 root root
  unlock_certificate_state
  bash "$MYPROXY_ROOT/deploy/scripts/provision-tls.sh" \
    --mode acme --server-name "$server_name" --server-ip "$server_ip" --email "$acme_email"
  lock_certificate_state
else
  bash "$MYPROXY_ROOT/deploy/scripts/provision-tls.sh" \
    --mode self-signed --server-name "$server_name" --server-ip "$server_ip"
fi

configure_domain_args=()
[[ -n "$domain" ]] || configure_domain_args+=(--clear-domain)
(cd "$MYPROXY_ROOT/backend" && \
  runuser -u "$MYPROXY_USER" -- env -i \
    PATH=/usr/bin:/bin \
    MYPROXY_ENV=production \
    MYPROXY_HOME="$MYPROXY_ROOT" \
    MYPROXY_DB="$MYPROXY_ROOT/data/myproxy.db" \
    MYPROXY_SINGBOX="$MYPROXY_ROOT/.runtime/sing-box/sing-box" \
    MYPROXY_SINGBOX_CONFIG="$MYPROXY_ROOT/config/sing-box.json" \
    MYPROXY_LOG_DIR="$MYPROXY_ROOT/logs" \
    MYPROXY_BACKUP_DIR="$MYPROXY_ROOT/backups" \
    MYPROXY_RUN_DIR="$MYPROXY_ROOT/run" \
    MYPROXY_USE_SUDO=true \
    MYPROXY_SYSTEMCTL=/bin/systemctl \
    MYPROXY_SUDO=/usr/bin/sudo \
    MYPROXY_SINGBOX_SERVICE=myproxy-singbox.service \
    MYPROXY_SINGBOX_VERSION="$singbox_version" \
    MYPROXY_CERTIFICATE_PATH="$MYPROXY_ROOT/config/tls/fullchain.pem" \
    MYPROXY_PRIVATE_KEY_PATH="$MYPROXY_ROOT/config/tls/privkey.pem" \
    MYPROXY_SERVER_IP="$server_ip" MYPROXY_DOMAIN="$domain_value" \
    MYPROXY_SELF_SIGNED_MODE="$self_signed" \
    MYPROXY_INTERNAL_RECONFIGURE=1 \
    SERVER_IP="$server_ip" DOMAIN="$domain_value" ACME_EMAIL="$acme_email" \
    SELF_SIGNED_MODE="$self_signed" \
    "$MYPROXY_ROOT/backend/.venv/bin/python" -m app.cli configure-deployment \
      "${configure_domain_args[@]}")

[[ -f "$MYPROXY_ROOT/config/sing-box.json" && ! -L "$MYPROXY_ROOT/config/sing-box.json" ]] || \
  die "configure-deployment did not generate a regular sing-box config"
run_as_myproxy "$MYPROXY_ROOT/.runtime/sing-box/sing-box" \
  check -c "$MYPROXY_ROOT/config/sing-box.json"

nginx_tmp=$(mktemp /etc/nginx/sites-available/.myproxy-reconfigure-final.XXXXXX)
sed "s|__SERVER_NAME__|$(escape_sed_replacement "$server_name")|g" \
  "$MYPROXY_ROOT/deploy/nginx/myproxy.conf.template" >"$nginx_tmp"
chmod 0644 "$nginx_tmp"
mv -f -- "$nginx_tmp" "$MYPROXY_NGINX_SITE"
ln -sfn "$MYPROXY_NGINX_SITE" "$MYPROXY_NGINX_LINK"
if [[ "$self_signed" == true ]]; then
  rm -f -- "$MYPROXY_CERT_HOOK"
fi
remove_temporary_nginx_site
install_project_permissions
nginx -t
systemctl daemon-reload
systemctl restart "$MYPROXY_SINGBOX_UNIT" "$MYPROXY_API_UNIT" nginx.service
bash "$MYPROXY_ROOT/deploy/scripts/health-check.sh"

reconfigure_complete=true
trap - EXIT
unlock_certificate_state
remove_temporary_nginx_site

mapfile -t old_reconfigure_dirs < <(find "$MYPROXY_ROOT/.protected-backups/reconfigure/releases" \
  -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{sub(/^[^ ]+ /, ""); print}')
if ((${#old_reconfigure_dirs[@]} > 5)); then
  for old_reconfigure in "${old_reconfigure_dirs[@]:5}"; do
    case "$old_reconfigure" in
      "$MYPROXY_ROOT"/.protected-backups/reconfigure/releases/[0-9]*T[0-9]*Z-[0-9]*) \
        rm -rf -- "$old_reconfigure" ;;
      *) die "refusing to prune unexpected reconfigure backup: $old_reconfigure" ;;
    esac
  done
fi

log "deployment settings reconfigured successfully"
log "panel: https://$server_name/"
log "protected rollback assets retained in $RECONFIGURE_DIR"

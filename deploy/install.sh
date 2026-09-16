#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_root
acquire_deploy_lock
assert_ubuntu_2004
assert_install_root "$MYPROXY_ROOT"

SOURCE_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
[[ -f "$SOURCE_ROOT/.runtime-versions" ]] || die "run install.sh from the MyProxy source tree"

domain=${DOMAIN:-}
server_ip=${SERVER_IP:-}
acme_email=${ACME_EMAIL:-}
self_signed=${SELF_SIGNED_MODE:-false}

dotenv_value() {
  local key=$1 file="$SOURCE_ROOT/.env"
  local count value
  [[ ! -e "$file" ]] && return 0
  [[ -f "$file" && ! -L "$file" ]] || die ".env must be a regular file, not a symbolic link"
  count=$(awk -F= -v key="$key" '$1 == key {count++} END {print count + 0}' "$file")
  ((count <= 1)) || die "duplicate $key in .env"
  [[ "$count" == 1 ]] || return 0
  value=$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print}' "$file")
  [[ "$value" != *$'\r'* && "$value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]] || \
    die "unsafe $key value in .env"
  printf '%s\n' "$value"
}

[[ -n "$domain" ]] || domain=$(dotenv_value DOMAIN)
[[ -n "$server_ip" ]] || server_ip=$(dotenv_value SERVER_IP)
[[ -n "$acme_email" ]] || acme_email=$(dotenv_value ACME_EMAIL)
if [[ "$self_signed" == false ]]; then
  dotenv_self_signed=$(dotenv_value SELF_SIGNED_MODE)
  [[ -z "$dotenv_self_signed" ]] || self_signed=$dotenv_self_signed
fi

while (($#)); do
  case "$1" in
    --domain) (($# >= 2)) || die "--domain requires a value"; domain=$2; shift 2 ;;
    --server-ip) (($# >= 2)) || die "--server-ip requires a value"; server_ip=$2; shift 2 ;;
    --email) (($# >= 2)) || die "--email requires a value"; acme_email=$2; shift 2 ;;
    --self-signed) self_signed=true; shift ;;
    -h|--help)
      printf 'Usage: sudo bash deploy/install.sh [--domain NAME --email ADDRESS] [--server-ip IP] [--self-signed]\n'
      exit 0
      ;;
    *) die "unknown install option: $1" ;;
  esac
done

case "${self_signed,,}" in
  1|true|yes|on) self_signed=true ;;
  0|false|no|off|'') self_signed=false ;;
  *) die "SELF_SIGNED_MODE must be true or false" ;;
esac

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

if [[ -z "$server_ip" ]]; then
  server_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
fi
[[ -z "$server_ip" ]] || is_ipv4 "$server_ip" || \
  die "SERVER_IP must be an IPv4 address"

if [[ "$self_signed" == true ]]; then
  server_name=${domain:-$server_ip}
  [[ -n "$server_name" ]] || die "self-signed mode needs --domain or --server-ip"
else
  [[ "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ && "$domain" == *.* ]] || \
    die "production ACME mode requires a valid --domain"
  [[ "$acme_email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || \
    die "production ACME mode requires a valid --email"
  server_name=$domain
fi
if [[ -n "$acme_email" ]]; then
  [[ "$acme_email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || \
    die "invalid ACME email"
fi
[[ "$server_name" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || \
  die "invalid server name"

singbox_version=$(read_pinned_version SINGBOX_VERSION "$SOURCE_ROOT/.runtime-versions")
[[ "$singbox_version" == 1.14.1 ]] || die "this deployment requires sing-box 1.14.1"

if [[ -e "$MYPROXY_ROOT" ]]; then
  die "$MYPROXY_ROOT already exists; use deploy/update.sh for an installed system"
fi

credentials_file=''
install_stage=''
service_account_created=false
service_group_created=false
udp_tuning_installed=false
on_install_exit() {
  local exit_code=$?
  local retain_managed_account=false
  trap - EXIT
  [[ -z "$credentials_file" ]] || rm -f -- "$credentials_file"
  if [[ "$udp_tuning_installed" == true ]]; then
    remove_udp_tuning || warn "could not fully revert UDP tuning after install failure"
  fi
  if [[ -d "$MYPROXY_ROOT" && ! -L "$MYPROXY_ROOT" ]]; then
    retain_managed_account=true
  fi
  if [[ -n "$install_stage" && "$install_stage" == /opt/.myproxy-install.* && \
    -d "$install_stage" && ! -L "$install_stage" ]]; then
    rm -rf -- "$install_stage"
  fi
  if [[ "$retain_managed_account" != true && "$service_account_created" == true ]] && \
    id "$MYPROXY_USER" >/dev/null 2>&1; then
    userdel "$MYPROXY_USER" >/dev/null 2>&1 || true
  fi
  if [[ "$retain_managed_account" != true && "$service_group_created" == true ]] && \
    getent group "$MYPROXY_GROUP" >/dev/null; then
    groupdel "$MYPROXY_GROUP" >/dev/null 2>&1 || true
  fi
  if [[ "$retain_managed_account" == true ]]; then
    warn "the installer-created service account was retained for a safe uninstall retry"
  fi
  warn "installation failed; partial files under $MYPROXY_ROOT were left for inspection"
  exit "$exit_code"
}
trap on_install_exit EXIT

log "installing required Ubuntu packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates certbot curl git iproute2 logrotate nginx openssl procps sudo tar xz-utils

if getent passwd "$MYPROXY_USER" >/dev/null || getent group "$MYPROXY_GROUP" >/dev/null; then
  die "a pre-existing myproxy user or group was found; refusing to adopt or later delete it"
fi
groupadd --system "$MYPROXY_GROUP"
service_group_created=true
useradd --system --gid "$MYPROXY_GROUP" --home-dir "$MYPROXY_ROOT" \
  --shell /usr/sbin/nologin "$MYPROXY_USER"
service_account_created=true
service_uid=$(id -u "$MYPROXY_USER")
service_gid=$(id -g "$MYPROXY_USER")
[[ "$service_uid" =~ ^[0-9]+$ && "$service_gid" =~ ^[0-9]+$ && \
  "$service_uid" != 0 && "$service_gid" != 0 ]] || \
  die "service account must have non-root numeric UID and GID"
[[ "$(getent passwd "$MYPROXY_USER" | awk -F: '{print $6}')" == "$MYPROXY_ROOT" ]] || \
  die "new service account has an unexpected home directory"
[[ "$(getent passwd "$MYPROXY_USER" | awk -F: '{print $7}')" == /usr/sbin/nologin ]] || \
  die "new service account must use /usr/sbin/nologin"
[[ "$(id -Gn "$MYPROXY_USER")" == "$MYPROXY_GROUP" ]] || \
  die "new service account has unexpected supplementary groups"

install_stage=$(mktemp -d /opt/.myproxy-install.XXXXXX)
[[ "$install_stage" == /opt/.myproxy-install.* && -d "$install_stage" && ! -L "$install_stage" ]] || \
  die "could not create a safe install staging directory"
chmod 0700 "$install_stage"
log "copying the application into $MYPROXY_ROOT"
tar -C "$SOURCE_ROOT" \
  --exclude='./.env' \
  --exclude='./.env.*' \
  --exclude='./.managed-service-account' \
  --exclude='./.runtime' \
  --exclude='./backend/.venv' \
  --exclude='./backend/.uv-cache' \
  --exclude='./backend/.ruff_cache' \
  --exclude='./backend/.pytest_cache' \
  --exclude='./.pytest_cache' \
  --exclude='./frontend/node_modules' \
  --exclude='./frontend/dist' \
  --exclude='./config/app.env' \
  --exclude='./config/sing-box.json' \
  --exclude='./config/tls' \
  --exclude='./data' --exclude='./logs' --exclude='./run' --exclude='./backups' \
  -cf - . | tar --no-overwrite-dir -C "$install_stage" -xf -
if [[ -f "$SOURCE_ROOT/.env.example" ]]; then
  [[ ! -L "$SOURCE_ROOT/.env.example" ]] || die ".env.example must not be a symbolic link"
  install -m 0644 "$SOURCE_ROOT/.env.example" "$install_stage/.env.example"
fi
chown_tree_nofollow root root "$install_stage" || die "could not seal installed source ownership"
for required_source in \
  .runtime-versions backend/pyproject.toml backend/uv.lock \
  frontend/package.json frontend/package-lock.json \
  deploy/update.sh deploy/bootstrap-update.sh deploy/uninstall.sh deploy/reconfigure.sh \
  deploy/lib/common.sh deploy/scripts/download-runtime.sh deploy/scripts/build-project.sh \
  deploy/scripts/provision-tls.sh deploy/scripts/health-check.sh deploy/scripts/cert-deploy-hook.sh \
  deploy/scripts/backup-state.sh \
  deploy/checksums/runtime-sha256.txt \
  deploy/systemd/myproxy-api.service deploy/systemd/myproxy-singbox.service \
  deploy/nginx/myproxy.conf.template deploy/nginx/myproxy-http.conf.template \
  deploy/sudoers/myproxy deploy/logrotate/myproxy \
  deploy/sysctl/90-myproxy-panel-udp.conf.template \
  config/app.env.template myproxy; do
  [[ -f "$install_stage/$required_source" && ! -L "$install_stage/$required_source" ]] || \
    die "required source must be a regular file: $required_source"
done
account_marker_tmp=$(mktemp "$install_stage/.managed-service-account.XXXXXX")
printf '%s:%s\n' "$service_uid" "$service_gid" >"$account_marker_tmp"
chown root:root "$account_marker_tmp"
chmod 0600 "$account_marker_tmp"
mv -f -- "$account_marker_tmp" "$install_stage/.managed-service-account"
[[ ! -e "$MYPROXY_ROOT" && ! -L "$MYPROXY_ROOT" ]] || \
  die "$MYPROXY_ROOT appeared while installation was being staged"
mv -T -- "$install_stage" "$MYPROXY_ROOT"
install_stage=''
chmod 0755 "$MYPROXY_ROOT"
assert_install_root "$MYPROXY_ROOT"
[[ -f "$MYPROXY_ACCOUNT_MARKER" && ! -L "$MYPROXY_ACCOUNT_MARKER" ]] || \
  die "managed service-account marker was not installed safely"
sanitize_git_checkout

install -d -m 0750 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime"
install -d -m 0750 -o "$MYPROXY_USER" -g "$MYPROXY_GROUP" \
  "$MYPROXY_ROOT/data" "$MYPROXY_ROOT/logs" \
  "$MYPROXY_ROOT/run" "$MYPROXY_ROOT/backups"
install -d -m 1770 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/config"
install -d -m 0750 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/config/tls"
find "$MYPROXY_ROOT/deploy" "$MYPROXY_ROOT/scripts" -type f -name '*.sh' -exec chmod 0750 {} +
chmod 0755 "$MYPROXY_ROOT/myproxy"

escape_sed_replacement() {
  sed 's/[&|]/\\&/g' <<<"$1"
}

domain_value=$domain
email_value=$acme_email
env_tmp=$(mktemp "$MYPROXY_ROOT/config/.app.env.XXXXXX")
sed \
  -e "s|__SERVER_NAME__|$(escape_sed_replacement "$server_name")|g" \
  -e "s|__SERVER_IP__|$(escape_sed_replacement "$server_ip")|g" \
  -e "s|__DOMAIN__|$(escape_sed_replacement "$domain_value")|g" \
  -e "s|__ACME_EMAIL__|$(escape_sed_replacement "$email_value")|g" \
  -e "s|__SELF_SIGNED_MODE__|$self_signed|g" \
  -e "s|__SINGBOX_VERSION__|$singbox_version|g" \
  "$MYPROXY_ROOT/config/app.env.template" >"$env_tmp"
chown root:"$MYPROXY_GROUP" "$env_tmp"
chmod 0640 "$env_tmp"
mv -f -- "$env_tmp" "$MYPROXY_ROOT/config/app.env"

log "downloading pinned project runtimes"
bash "$MYPROXY_ROOT/deploy/scripts/download-runtime.sh" all
log "building backend, then frontend, to limit peak memory use"
bash "$MYPROXY_ROOT/deploy/scripts/build-project.sh" all

install -d -m 0755 "$MYPROXY_ACME_ROOT/.well-known/acme-challenge"
if [[ "$self_signed" == false ]]; then
  install -d -m 0755 "$(dirname -- "$MYPROXY_CERT_HOOK")"
  atomic_install "$MYPROXY_ROOT/deploy/scripts/cert-deploy-hook.sh" \
    "$MYPROXY_CERT_HOOK" 0750 root root

  bootstrap_tmp=$(mktemp /etc/nginx/sites-available/.myproxy-http.XXXXXX)
  sed "s|__SERVER_NAME__|$(escape_sed_replacement "$server_name")|g" \
    "$MYPROXY_ROOT/deploy/nginx/myproxy-http.conf.template" >"$bootstrap_tmp"
  chmod 0644 "$bootstrap_tmp"
  mv -f -- "$bootstrap_tmp" "$MYPROXY_NGINX_SITE"
  ln -sfn "$MYPROXY_NGINX_SITE" "$MYPROXY_NGINX_LINK"
  nginx -t
  systemctl enable --now nginx.service
  systemctl reload nginx.service
  bash "$MYPROXY_ROOT/deploy/scripts/provision-tls.sh" \
    --mode acme --server-name "$server_name" --server-ip "$server_ip" --email "$acme_email"
else
  bash "$MYPROXY_ROOT/deploy/scripts/provision-tls.sh" \
    --mode self-signed --server-name "$server_name" --server-ip "$server_ip"
fi

install -d -m 0710 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/.build"
credentials_file=$(mktemp "$MYPROXY_ROOT/.build/.initial-credentials.XXXXXX")
chown root:"$MYPROXY_GROUP" "$credentials_file"
chmod 0640 "$credentials_file"
log "initializing the database and first sing-box configuration"
(cd "$MYPROXY_ROOT/backend" && \
  runuser -u "$MYPROXY_USER" -- env -i PATH=/usr/bin:/bin \
    MYPROXY_ENV=production \
    MYPROXY_HOME="$MYPROXY_ROOT" \
    MYPROXY_DB="$MYPROXY_ROOT/data/myproxy.db" \
    MYPROXY_SINGBOX="$MYPROXY_ROOT/.runtime/sing-box/sing-box" \
    MYPROXY_SINGBOX_CONFIG="$MYPROXY_ROOT/config/sing-box.json" \
    MYPROXY_LOG_DIR="$MYPROXY_ROOT/logs" \
    MYPROXY_BACKUP_DIR="$MYPROXY_ROOT/backups" \
    MYPROXY_RUN_DIR="$MYPROXY_ROOT/run" \
    MYPROXY_SINGBOX_VERSION="$singbox_version" \
    MYPROXY_USE_SUDO=true \
    MYPROXY_SYSTEMCTL=/bin/systemctl \
    MYPROXY_SUDO=/usr/bin/sudo \
    MYPROXY_SINGBOX_SERVICE=myproxy-singbox.service \
    MYPROXY_CERTIFICATE_PATH="$MYPROXY_ROOT/config/tls/fullchain.pem" \
    MYPROXY_PRIVATE_KEY_PATH="$MYPROXY_ROOT/config/tls/privkey.pem" \
    MYPROXY_SERVER_IP="$server_ip" MYPROXY_DOMAIN="$domain_value" \
    MYPROXY_SELF_SIGNED_MODE="$self_signed" \
    SERVER_IP="$server_ip" DOMAIN="$domain_value" ACME_EMAIL="$email_value" \
    SELF_SIGNED_MODE="$self_signed" \
    "$MYPROXY_ROOT/backend/.venv/bin/python" -m app.cli init --json) >"$credentials_file"
chown root:"$MYPROXY_GROUP" "$credentials_file"
chmod 0640 "$credentials_file"

[[ -s "$MYPROXY_ROOT/config/sing-box.json" ]] || die "initialization did not generate config/sing-box.json"
chown "$MYPROXY_USER:$MYPROXY_GROUP" "$MYPROXY_ROOT/config/sing-box.json"
chmod 0600 "$MYPROXY_ROOT/config/sing-box.json"
run_as_myproxy "$MYPROXY_ROOT/.runtime/sing-box/sing-box" \
  check -c "$MYPROXY_ROOT/config/sing-box.json"
install_project_permissions

atomic_install "$MYPROXY_ROOT/deploy/systemd/myproxy-api.service" \
  "/etc/systemd/system/$MYPROXY_API_UNIT" 0644 root root
atomic_install "$MYPROXY_ROOT/deploy/systemd/myproxy-singbox.service" \
  "/etc/systemd/system/$MYPROXY_SINGBOX_UNIT" 0644 root root
atomic_install "$MYPROXY_ROOT/deploy/sudoers/myproxy" "$MYPROXY_SUDOERS" 0440 root root
visudo -cf "$MYPROXY_SUDOERS"
atomic_install "$MYPROXY_ROOT/deploy/logrotate/myproxy" "$MYPROXY_LOGROTATE" 0644 root root
logrotate --debug "$MYPROXY_LOGROTATE" >/dev/null
udp_tuning_installed=true
install_udp_tuning

nginx_tmp=$(mktemp /etc/nginx/sites-available/.myproxy.XXXXXX)
sed "s|__SERVER_NAME__|$(escape_sed_replacement "$server_name")|g" \
  "$MYPROXY_ROOT/deploy/nginx/myproxy.conf.template" >"$nginx_tmp"
chmod 0644 "$nginx_tmp"
mv -f -- "$nginx_tmp" "$MYPROXY_NGINX_SITE"
ln -sfn "$MYPROXY_NGINX_SITE" "$MYPROXY_NGINX_LINK"
nginx -t

systemctl daemon-reload
systemctl enable "$MYPROXY_API_UNIT" "$MYPROXY_SINGBOX_UNIT" nginx.service
systemctl restart "$MYPROXY_SINGBOX_UNIT"
systemctl restart "$MYPROXY_API_UNIT"
systemctl restart nginx.service
bash "$MYPROXY_ROOT/deploy/scripts/health-check.sh"

mapfile -t initial_values < <(run_as_myproxy "$MYPROXY_ROOT/backend/.venv/bin/python" -c '
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    data = json.load(source)
for key in ("username", "password", "subscription_token"):
    value = data.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~@+-]{1,256}", value):
        raise SystemExit(f"initialization JSON contains an unsafe or missing {key}")
    print(value)
' "$credentials_file")
(( ${#initial_values[@]} == 3 )) || die "could not read initialization credentials"
rm -f -- "$credentials_file"
credentials_file=''

printf '\nMyProxy installation is ready. Save these one-time credentials now:\n'
printf '  Panel:        https://%s/\n' "$server_name"
printf '  Username:     %s\n' "${initial_values[0]}"
printf '  Password:     %s\n' "${initial_values[1]}"
printf '  Subscription: https://%s/sub/%s\n' "$server_name" "${initial_values[2]}"
if [[ "$self_signed" == true ]]; then
  printf '  Certificate:  self-signed; clients must explicitly trust it\n'
fi
printf '\nFirewall/security-group reminder (not changed automatically):\n'
printf '  Allow: 22/tcp, 80/tcp, 443/tcp, 8443/udp, 10443/udp, 8388/tcp, 8388/udp\n'
printf '  Never expose: 8000/tcp (the API listens on 127.0.0.1 only)\n'
trap - EXIT

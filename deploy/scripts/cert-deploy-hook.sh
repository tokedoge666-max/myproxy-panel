#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_ROOT="/opt/myproxy"
readonly TLS_DIR="$INSTALL_ROOT/config/tls"
readonly SERVICE_GROUP="myproxy"
readonly ENV_FILE="$INSTALL_ROOT/config/app.env"

die() {
  printf '[myproxy-cert] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ ${EUID} -eq 0 ]] || die "certificate deployment must run as root"
[[ "$INSTALL_ROOT" == /opt/myproxy ]] || die "install root safety check failed"
command -v flock >/dev/null 2>&1 || die "flock is required"
exec 8>/run/lock/myproxy-cert-deploy.lock
flock 8
[[ ! -L "$INSTALL_ROOT/config" && "$(readlink -f -- "$INSTALL_ROOT/config")" == "$INSTALL_ROOT/config" ]] || \
  die "config directory must not be a symbolic link"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || \
  die "app.env must be a regular file"
chown root:"$SERVICE_GROUP" "$INSTALL_ROOT/config"
chmod 1770 "$INSTALL_ROOT/config"
[[ -d "$TLS_DIR" && ! -L "$TLS_DIR" && "$(readlink -f -- "$TLS_DIR")" == "$TLS_DIR" ]] || \
  die "TLS destination must be a real directory: $TLS_DIR"
chown root:"$SERVICE_GROUP" "$TLS_DIR"
chmod 0750 "$TLS_DIR"

lineage=${RENEWED_LINEAGE:-${1:-}}
[[ -n "$lineage" ]] || die "RENEWED_LINEAGE was not provided"

expected_domain=''
domain_count=0
while IFS= read -r env_line || [[ -n "$env_line" ]]; do
  [[ -n "$env_line" && "$env_line" != \#* ]] || continue
  [[ "$env_line" != *$'\r'* && "$env_line" == *=* ]] || die "malformed app.env"
  env_key=${env_line%%=*}
  env_value=${env_line#*=}
  [[ "$env_key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "invalid key in app.env"
  [[ "$env_value" =~ ^[A-Za-z0-9_./,:@%+=-]*$ ]] || die "unsafe value in app.env"
  if [[ "$env_key" == DOMAIN ]]; then
    ((domain_count += 1))
    expected_domain=$env_value
  fi
done <"$ENV_FILE"
[[ "$domain_count" == 1 && "$expected_domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || \
  die "app.env does not contain one valid DOMAIN"
if [[ "$lineage" != "/etc/letsencrypt/live/$expected_domain" ]]; then
  printf '[myproxy-cert] ignoring unrelated lineage: %s\n' "$lineage"
  exit 0
fi
case "$lineage" in
  /etc/letsencrypt/live/*) ;;
  *) die "refusing certificate source outside /etc/letsencrypt/live" ;;
esac
[[ "$lineage" != *'/../'* && "$lineage" != */.. && "$lineage" != *$'\n'* ]] || \
  die "invalid certificate lineage"

source_cert=$(readlink -f -- "$lineage/fullchain.pem")
source_key=$(readlink -f -- "$lineage/privkey.pem")
case "$source_cert" in /etc/letsencrypt/archive/*/fullchain*.pem) ;; *) die "invalid fullchain target" ;; esac
case "$source_key" in /etc/letsencrypt/archive/*/privkey*.pem) ;; *) die "invalid private-key target" ;; esac
[[ -r "$source_cert" && -r "$source_key" ]] || die "certificate lineage is incomplete"

cert_tmp=$(mktemp "$TLS_DIR/.fullchain.XXXXXX")
key_tmp=$(mktemp "$TLS_DIR/.privkey.XXXXXX")
cleanup() {
  rm -f -- "$cert_tmp" "$key_tmp"
}
trap cleanup EXIT

install -m 0640 -o root -g "$SERVICE_GROUP" "$source_cert" "$cert_tmp"
install -m 0640 -o root -g "$SERVICE_GROUP" "$source_key" "$key_tmp"
openssl x509 -in "$cert_tmp" -noout >/dev/null 2>&1 || die "copied certificate is invalid"
openssl pkey -in "$key_tmp" -check -noout >/dev/null 2>&1 || die "copied private key is invalid"

cert_public=$(openssl x509 -in "$cert_tmp" -pubkey -noout | openssl sha256)
key_public=$(openssl pkey -in "$key_tmp" -pubout 2>/dev/null | openssl sha256)
[[ "$cert_public" == "$key_public" ]] || die "certificate and private key do not match"

mv -f -- "$cert_tmp" "$TLS_DIR/fullchain.pem"
mv -f -- "$key_tmp" "$TLS_DIR/privkey.pem"
trap - EXIT
chown root:"$SERVICE_GROUP" "$TLS_DIR/fullchain.pem" "$TLS_DIR/privkey.pem"
chmod 0640 "$TLS_DIR/fullchain.pem"
chmod 0640 "$TLS_DIR/privkey.pem"

if systemctl is-active --quiet myproxy-singbox.service; then
  systemctl restart myproxy-singbox.service
fi
if systemctl is-active --quiet nginx.service; then
  nginx -t
  systemctl restart nginx.service
fi

printf '[myproxy-cert] certificate copied atomically into %s\n' "$TLS_DIR"

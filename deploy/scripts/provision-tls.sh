#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

require_root
assert_install_root "$MYPROXY_ROOT"

mode=''
server_name=''
server_ip=''
acme_email=''
while (($#)); do
  case "$1" in
    --mode) (($# >= 2)) || die "--mode requires a value"; mode=$2; shift 2 ;;
    --server-name) (($# >= 2)) || die "--server-name requires a value"; server_name=$2; shift 2 ;;
    --server-ip) (($# >= 2)) || die "--server-ip requires a value"; server_ip=$2; shift 2 ;;
    --email) (($# >= 2)) || die "--email requires a value"; acme_email=$2; shift 2 ;;
    *) die "unknown TLS option: $1" ;;
  esac
done

case "$mode" in acme|self-signed) ;; *) die "--mode must be acme or self-signed" ;; esac
[[ "$server_name" =~ ^([A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]|[A-Za-z0-9])$ ]] || \
  die "invalid server name: $server_name"
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
if [[ -n "$server_ip" ]]; then
  is_ipv4 "$server_ip" || die "invalid server IPv4 address"
fi

readonly TLS_DIR="$MYPROXY_ROOT/config/tls"
[[ ! -L "$MYPROXY_ROOT/config" && ! -L "$TLS_DIR" ]] || die "TLS path must not be a symbolic link"
install -d -m 0750 -o root -g "$MYPROXY_GROUP" "$TLS_DIR"

if [[ "$mode" == acme ]]; then
  [[ "$server_name" == *.* ]] || die "ACME mode requires a fully qualified domain name"
  [[ "$acme_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || \
    die "ACME mode requires a valid email address"
  require_command certbot
  install -d -m 0755 "$MYPROXY_ACME_ROOT/.well-known/acme-challenge"
  log "requesting a Let's Encrypt certificate for $server_name"
  certbot certonly --non-interactive --agree-tos --no-eff-email \
    --webroot --webroot-path "$MYPROXY_ACME_ROOT" \
    --cert-name "$server_name" --email "$acme_email" -d "$server_name"
  RENEWED_LINEAGE="/etc/letsencrypt/live/$server_name" \
    "$MYPROXY_ROOT/deploy/scripts/cert-deploy-hook.sh"
else
  require_command openssl
  cert_tmp=$(mktemp "$TLS_DIR/.selfsigned-cert.XXXXXX")
  key_tmp=$(mktemp "$TLS_DIR/.selfsigned-key.XXXXXX")
  cleanup_tls() {
    rm -f -- "$cert_tmp" "$key_tmp"
  }
  trap cleanup_tls EXIT

  san="DNS:$server_name"
  if is_ipv4 "$server_name"; then
    san="IP:$server_name"
  fi
  if [[ -n "$server_ip" && "$san" != "IP:$server_ip" ]]; then
    san="$san,IP:$server_ip"
  fi
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 825 \
    -subj "/CN=$server_name" -addext "subjectAltName=$san" \
    -keyout "$key_tmp" -out "$cert_tmp" >/dev/null 2>&1
  openssl x509 -in "$cert_tmp" -noout >/dev/null 2>&1 || die "self-signed certificate generation failed"
  openssl pkey -in "$key_tmp" -check -noout >/dev/null 2>&1 || die "self-signed key generation failed"
  chown root:"$MYPROXY_GROUP" "$cert_tmp" "$key_tmp"
  chmod 0640 "$cert_tmp"
  chmod 0640 "$key_tmp"
  mv -f -- "$cert_tmp" "$TLS_DIR/fullchain.pem"
  mv -f -- "$key_tmp" "$TLS_DIR/privkey.pem"
  trap - EXIT
  log "created a self-signed certificate for $server_name"
fi

[[ -s "$TLS_DIR/fullchain.pem" && -s "$TLS_DIR/privkey.pem" ]] || die "TLS files were not installed"
chown root:"$MYPROXY_GROUP" "$TLS_DIR/fullchain.pem" "$TLS_DIR/privkey.pem"
chmod 0640 "$TLS_DIR/fullchain.pem"
chmod 0640 "$TLS_DIR/privkey.pem"

#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

assert_install_root "$MYPROXY_ROOT"
require_command curl
require_command ss

readonly ATTEMPTS=${MYPROXY_HEALTH_ATTEMPTS:-15}
readonly DELAY_SECONDS=${MYPROXY_HEALTH_DELAY:-2}
[[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ && "$ATTEMPTS" -le 60 ]] || die "invalid health attempt count"
[[ "$DELAY_SECONDS" =~ ^[0-9]+$ && "$DELAY_SECONDS" -le 10 ]] || die "invalid health delay"

systemctl is-active --quiet "$MYPROXY_API_UNIT" || die "$MYPROXY_API_UNIT is not active"
systemctl is-active --quiet "$MYPROXY_SINGBOX_UNIT" || die "$MYPROXY_SINGBOX_UNIT is not active"
nginx -t >/dev/null 2>&1 || die "nginx configuration check failed"

response=''
healthy=false
for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
  if response=$(curl --fail --silent --show-error --max-time 5 \
    http://127.0.0.1:8000/health 2>/dev/null); then
    if grep -Eq '^\{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"ok"[[:space:]]*\}$' <<<"$response"; then
      healthy=true
      break
    fi
  fi
  response=''
  ((attempt == ATTEMPTS)) || sleep "$DELAY_SECONDS"
done
[[ "$healthy" == true ]] || die "backend health endpoint did not return {\"status\":\"ok\"}"

server_name=$(read_app_env_value DOMAIN "$MYPROXY_ROOT/config/app.env")
[[ -n "$server_name" ]] || server_name=$(read_app_env_value SERVER_IP "$MYPROXY_ROOT/config/app.env")
self_signed=$(read_app_env_value SELF_SIGNED_MODE "$MYPROXY_ROOT/config/app.env")
[[ "$server_name" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$ ]] || \
  die "invalid server name in app.env"
https_args=(--fail --silent --show-error --max-time 5 --resolve "$server_name:443:127.0.0.1")
[[ "$self_signed" != true ]] || https_args+=(--insecure)
https_response=$(curl "${https_args[@]}" "https://$server_name/health")
grep -Eq '^\{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"ok"[[:space:]]*\}$' \
  <<<"$https_response" || die "nginx HTTPS health proxy returned an unexpected response"

[[ -x "$MYPROXY_ROOT/.runtime/sing-box/sing-box" ]] || die "sing-box binary is missing"
[[ -r "$MYPROXY_ROOT/config/sing-box.json" ]] || die "sing-box configuration is missing"
run_as_myproxy "$MYPROXY_ROOT/.runtime/sing-box/sing-box" check \
  -c "$MYPROXY_ROOT/config/sing-box.json" >/dev/null

mapfile -t expected_listeners < <(
  run_as_myproxy "$MYPROXY_ROOT/backend/.venv/bin/python" \
    - "$MYPROXY_ROOT/config/sing-box.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    config = json.load(source)

for inbound in config.get("inbounds", []):
    inbound_type = inbound.get("type")
    port = inbound.get("listen_port")
    if not isinstance(port, int):
        continue
    if inbound_type in {"hysteria2", "tuic"}:
        print(f"udp:{port}")
    elif inbound_type == "shadowsocks":
        network = inbound.get("network")
        if network in {None, "tcp"}:
            print(f"tcp:{port}")
        if network in {None, "udp"}:
            print(f"udp:{port}")
PY
)

for listener in "${expected_listeners[@]}"; do
  transport=${listener%%:*}
  port=${listener##*:}
  [[ "$transport" == tcp || "$transport" == udp ]] || die "invalid listener transport"
  [[ "$port" =~ ^[1-9][0-9]*$ && "$port" -le 65535 ]] || die "invalid listener port"
  listener_ready=false
  for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    if [[ "$transport" == tcp ]]; then
      [[ -n "$(ss -H -4 -ltn "sport = :$port")" ]] && listener_ready=true
    else
      [[ -n "$(ss -H -4 -lun "sport = :$port")" ]] && listener_ready=true
    fi
    [[ "$listener_ready" == true ]] && break
    ((attempt == ATTEMPTS)) || sleep "$DELAY_SECONDS"
  done
  [[ "$listener_ready" == true ]] || die "expected $transport listener is missing on port $port"
done

log "health check passed: API, sing-box, listeners and nginx are ready"

#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

require_root
acquire_deploy_lock
acquire_certificate_lock
assert_install_root "$MYPROXY_ROOT"

assume_yes=false
keep_recovery=true
while (($#)); do
  case "$1" in
    --yes) assume_yes=true; shift ;;
    --no-recovery-backup) keep_recovery=false; shift ;;
    -h|--help)
      printf 'Usage: sudo bash deploy/uninstall.sh [--yes] [--no-recovery-backup]\n'
      exit 0
      ;;
    *) die "unknown uninstall option: $1" ;;
  esac
done

resolved_root=$(readlink -m -- "$MYPROXY_ROOT")
[[ "$resolved_root" == /opt/myproxy ]] || die "refusing unexpected uninstall target: $resolved_root"
[[ -d "$MYPROXY_ROOT" ]] || die "$MYPROXY_ROOT is not installed"

remove_service_account=false
if [[ -f "$MYPROXY_ACCOUNT_MARKER" && ! -L "$MYPROXY_ACCOUNT_MARKER" && \
  "$(stat -c '%u:%g:%a' "$MYPROXY_ACCOUNT_MARKER")" == 0:0:600 ]]; then
  account_marker=$(<"$MYPROXY_ACCOUNT_MARKER")
  if [[ "$account_marker" =~ ^([0-9]+):([0-9]+)$ ]]; then
    marked_uid=${BASH_REMATCH[1]}
    marked_gid=${BASH_REMATCH[2]}
    if [[ "$marked_uid" != 0 && "$marked_gid" != 0 ]] && \
      getent passwd "$MYPROXY_USER" >/dev/null && getent group "$MYPROXY_GROUP" >/dev/null; then
      current_group_gid=$(getent group "$MYPROXY_GROUP" | awk -F: '{print $3}')
      if [[ "$(id -u "$MYPROXY_USER")" == "$marked_uid" && \
        "$(id -g "$MYPROXY_USER")" == "$marked_gid" && \
        "$current_group_gid" == "$marked_gid" ]]; then
        remove_service_account=true
      fi
    fi
  fi
fi
if [[ "$remove_service_account" != true ]]; then
  warn "service-account ownership marker is absent or mismatched; the myproxy user/group will be retained"
fi

if [[ "$assume_yes" != true ]]; then
  [[ -t 0 ]] || die "non-interactive uninstall requires --yes"
  printf 'This will remove MyProxy services and exactly %s. Continue? [y/N] ' "$MYPROXY_ROOT"
  read -r confirmation
  [[ "$confirmation" == y || "$confirmation" == Y ]] || die "uninstall cancelled"
fi

recovery_archive=''
uninstall_removal_started=false
restart_after_cancelled_uninstall() {
  local status=$?
  if [[ "$status" != 0 && "$uninstall_removal_started" != true ]]; then
    systemctl restart "$MYPROXY_SINGBOX_UNIT" "$MYPROXY_API_UNIT" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap restart_after_cancelled_uninstall EXIT

systemctl stop "$MYPROXY_API_UNIT" "$MYPROXY_SINGBOX_UNIT" >/dev/null 2>&1 || true
if [[ "$keep_recovery" == true && -x "$MYPROXY_ROOT/backend/.venv/bin/python" ]]; then
  log "creating an external recovery backup before removal"
  if ! backup_output=$(bash "$MYPROXY_ROOT/deploy/scripts/backup-state.sh" manual); then
    systemctl restart "$MYPROXY_SINGBOX_UNIT" "$MYPROXY_API_UNIT" >/dev/null 2>&1 || true
    die "recovery backup failed; uninstall was cancelled and services were restarted"
  fi
  internal_archive=$(tail -n 1 <<<"$backup_output")
  case "$internal_archive" in
    "$MYPROXY_ROOT"/.protected-backups/manual/myproxy-*.tar.gz) ;;
    *) die "unexpected backup path: $internal_archive" ;;
  esac
  recovery_dir=/var/backups/myproxy
  install -d -m 0700 "$recovery_dir"
  recovery_archive="$recovery_dir/uninstall-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  install -m 0600 -o root -g root "$internal_archive" "$recovery_archive"
fi

uninstall_removal_started=true
systemctl disable "$MYPROXY_API_UNIT" "$MYPROXY_SINGBOX_UNIT" >/dev/null 2>&1 || true

rm -f -- "/etc/systemd/system/$MYPROXY_API_UNIT"
rm -f -- "/etc/systemd/system/$MYPROXY_SINGBOX_UNIT"
rm -f -- "$MYPROXY_SUDOERS"
rm -f -- "$MYPROXY_LOGROTATE"
rm -f -- "$MYPROXY_CERT_HOOK"
rm -f -- "$MYPROXY_NGINX_LINK"
rm -f -- "$MYPROXY_NGINX_SITE"
rm -f -- "/etc/nginx/sites-enabled/myproxy-reconfigure-acme.conf"
rm -f -- "/etc/nginx/sites-available/myproxy-reconfigure-acme.conf"

systemctl daemon-reload
if nginx -t >/dev/null 2>&1 && systemctl is-active --quiet nginx.service; then
  systemctl reload nginx.service
fi

[[ "$MYPROXY_ACME_ROOT" == /var/lib/myproxy-acme ]] || die "ACME path safety check failed"
if [[ -d "$MYPROXY_ACME_ROOT" ]]; then
  rm -rf -- "$MYPROXY_ACME_ROOT"
fi

[[ "$MYPROXY_ROOT" == /opt/myproxy && "$(readlink -m -- "$MYPROXY_ROOT")" == /opt/myproxy ]] || \
  die "final install-root safety check failed"
rm -rf -- "$MYPROXY_ROOT"

if [[ "$remove_service_account" == true ]]; then
  if id "$MYPROXY_USER" >/dev/null 2>&1; then
    userdel "$MYPROXY_USER"
  fi
  if getent group "$MYPROXY_GROUP" >/dev/null; then
    groupdel "$MYPROXY_GROUP" >/dev/null 2>&1 || true
  fi
fi

log "MyProxy application, services, nginx site, log rotation, sudo rule and renewal hook were removed"
log "Let's Encrypt account/certificate history was intentionally left under /etc/letsencrypt"
if [[ -n "$recovery_archive" ]]; then
  log "recovery backup retained at $recovery_archive (root-only, mode 600)"
else
  warn "no external recovery backup was retained"
fi
trap - EXIT

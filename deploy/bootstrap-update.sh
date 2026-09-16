#!/usr/bin/env bash
set -Eeuo pipefail

# One-time bridge for releases whose already-running updater pins an older sing-box.
# The normal updater remains responsible for backups, checks and rollback.

readonly MYPROXY_ROOT="/opt/myproxy"

[[ ${EUID} -eq 0 ]] || {
  printf '[myproxy] ERROR: run this command as root\n' >&2
  exit 1
}
[[ -d "$MYPROXY_ROOT/.git" && ! -L "$MYPROXY_ROOT/.git" ]] || {
  printf '[myproxy] ERROR: %s is not a safe Git checkout\n' "$MYPROXY_ROOT" >&2
  exit 1
}

origin_url=$(git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
  config --local --no-includes --get remote.origin.url)
[[ "$origin_url" =~ ^https://github\.com/tokedoge666-max/myproxy-panel(\.git)?$ ]] || {
  printf '[myproxy] ERROR: unexpected Git origin: %s\n' "$origin_url" >&2
  exit 1
}

git -c safe.directory="$MYPROXY_ROOT" \
  -c core.hooksPath="$MYPROXY_ROOT/.git/myproxy-disabled-hooks" \
  -c credential.helper= -c submodule.recurse=false \
  -c protocol.ext.allow=never -c protocol.file.allow=never \
  -c protocol.ssh.allow=never -c protocol.git.allow=never \
  -c protocol.http.allow=never -c protocol.https.allow=always \
  -c http.sslVerify=true -C "$MYPROXY_ROOT" fetch origin main
target_commit=$(git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
  rev-parse --verify origin/main^{commit})
[[ "$target_commit" =~ ^[0-9a-f]{40}$ ]] || {
  printf '[myproxy] ERROR: could not resolve origin/main\n' >&2
  exit 1
}

stage=$(mktemp -d /tmp/myproxy-updater.XXXXXX)
cleanup() {
  local status=$?
  trap - EXIT
  [[ "$stage" == /tmp/myproxy-updater.* && -d "$stage" && ! -L "$stage" ]] && \
    rm -rf -- "$stage"
  exit "$status"
}
trap cleanup EXIT
install -d -m 0700 "$stage/lib"

git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
  show "$target_commit:deploy/update.sh" >"$stage/update.sh"
git -c safe.directory="$MYPROXY_ROOT" -C "$MYPROXY_ROOT" \
  show "$target_commit:deploy/lib/common.sh" >"$stage/lib/common.sh"
[[ -s "$stage/update.sh" && -s "$stage/lib/common.sh" ]] || {
  printf '[myproxy] ERROR: updater files are missing from target commit\n' >&2
  exit 1
}
chmod 0700 "$stage/update.sh" "$stage/lib/common.sh"

printf '[myproxy] bootstrapping the transactional updater from %s\n' "$target_commit"
MYPROXY_BOOTSTRAP_TARGET_COMMIT="$target_commit" bash "$stage/update.sh"

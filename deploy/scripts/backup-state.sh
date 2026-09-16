#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

require_root
assert_install_root "$MYPROXY_ROOT"

readonly BACKUP_KIND=${1:-manual}
case "$BACKUP_KIND" in
  manual|update|reconfigure) ;;
  *) die "usage: $0 [manual|update|reconfigure]" ;;
esac

readonly PROTECTED_BACKUP_ROOT="$MYPROXY_ROOT/.protected-backups"
readonly BACKUP_DIR="$PROTECTED_BACKUP_ROOT/$BACKUP_KIND"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
readonly TIMESTAMP
readonly STAGE="$PROTECTED_BACKUP_ROOT/.state-${BACKUP_KIND}-${TIMESTAMP}-$$"
readonly ARCHIVE="$BACKUP_DIR/myproxy-${TIMESTAMP}.tar.gz"
readonly ARCHIVE_TMP="$BACKUP_DIR/.myproxy-${TIMESTAMP}.tar.gz.tmp"

assert_under_root "$BACKUP_DIR"
assert_under_root "$STAGE"
assert_under_root "$ARCHIVE"

cleanup() {
  if [[ -d "$STAGE" ]]; then
    rm -rf -- "$STAGE"
  fi
  rm -f -- "$ARCHIVE_TMP"
}
trap cleanup EXIT

install -d -m 0710 -o root -g "$MYPROXY_GROUP" "$PROTECTED_BACKUP_ROOT"
install -d -m 0700 -o root -g root "$BACKUP_DIR" "$STAGE" "$STAGE/data"

if [[ -f "$MYPROXY_ROOT/data/myproxy.db" ]]; then
  python_bin="$MYPROXY_ROOT/backend/.venv/bin/python"
  [[ -x "$python_bin" ]] || die "project Python is unavailable for a consistent SQLite backup"
  : >"$STAGE/data/myproxy.db"
  chmod 0600 "$STAGE/data/myproxy.db"
  runuser -u "$MYPROXY_USER" -- env -i PATH=/usr/bin:/bin "$python_bin" - \
    "$MYPROXY_ROOT/data/myproxy.db" "$MYPROXY_ROOT/run" \
    >"$STAGE/data/myproxy.db" <<'PY'
import os
import shutil
import sqlite3
import sys
import tempfile

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
fd, temporary = tempfile.mkstemp(prefix=".myproxy-backup-", suffix=".db", dir=sys.argv[2])
os.close(fd)
destination = sqlite3.connect(temporary)
try:
    source.backup(destination)
    destination.close()
    with open(temporary, "rb") as completed:
        shutil.copyfileobj(completed, sys.stdout.buffer)
finally:
    try:
        destination.close()
    except Exception:
        pass
    source.close()
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
  [[ -s "$STAGE/data/myproxy.db" && ! -L "$STAGE/data/myproxy.db" ]] || \
    die "SQLite backup did not produce a regular file"
fi

if [[ -d "$MYPROXY_ROOT/config" ]]; then
  cp -a -- "$MYPROXY_ROOT/config" "$STAGE/config"
fi
if [[ -f "$MYPROXY_ROOT/.runtime-versions" ]]; then
  cp -a -- "$MYPROXY_ROOT/.runtime-versions" "$STAGE/.runtime-versions"
fi
if [[ -d "$MYPROXY_ROOT/frontend/dist" ]]; then
  install -d -m 0755 "$STAGE/frontend"
  cp -a -- "$MYPROXY_ROOT/frontend/dist" "$STAGE/frontend/dist"
fi

tar -C "$STAGE" -czf "$ARCHIVE_TMP" .
chmod 0600 "$ARCHIVE_TMP"
mv -f -- "$ARCHIVE_TMP" "$ARCHIVE"

mapfile -t archives < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'myproxy-*.tar.gz' \
  -printf '%T@ %p\n' | sort -nr | awk '{sub(/^[^ ]+ /, ""); print}')
if ((${#archives[@]} > 20)); then
  for old_archive in "${archives[@]:20}"; do
    case "$old_archive" in
      "$BACKUP_DIR"/myproxy-*.tar.gz) rm -f -- "$old_archive" ;;
      *) die "refusing to prune unexpected path: $old_archive" ;;
    esac
  done
fi

trap - EXIT
rm -rf -- "$STAGE"
log "created protected $BACKUP_KIND backup"
printf '%s\n' "$ARCHIVE"

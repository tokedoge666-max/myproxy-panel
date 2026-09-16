#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

require_root
assert_install_root "$MYPROXY_ROOT"

readonly COMPONENT=${1:-all}
case "$COMPONENT" in
  all|uv|node|sing-box) ;;
  *) die "usage: $0 [all|uv|node|sing-box]" ;;
esac

require_command curl
require_command readlink
require_command sha256sum
require_command tar

readonly VERSION_FILE="$MYPROXY_ROOT/.runtime-versions"
readonly CHECKSUM_FILE="$MYPROXY_ROOT/deploy/checksums/runtime-sha256.txt"

assert_root_owned_pin_file() {
  local pin_file=$1 mode
  [[ -f "$pin_file" && ! -L "$pin_file" ]] || \
    die "runtime pin must be a regular file: $pin_file"
  [[ "$(stat -c %u -- "$pin_file")" == 0 ]] || \
    die "runtime pin must be owned by root: $pin_file"
  mode=$(stat -c %a -- "$pin_file")
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || die "runtime pin has an invalid mode: $pin_file"
  (( (8#$mode & 0022) == 0 )) || \
    die "runtime pin must not be group/world writable: $pin_file"
}

assert_root_owned_pin_file "$VERSION_FILE"
assert_root_owned_pin_file "$CHECKSUM_FILE"
UV_VERSION=$(read_pinned_version UV_VERSION "$VERSION_FILE")
NODE_VERSION=$(read_pinned_version NODE_VERSION "$VERSION_FILE")
SINGBOX_VERSION=$(read_pinned_version SINGBOX_VERSION "$VERSION_FILE")
MACHINE_ARCH=$(detect_architecture)
DOWNLOAD_DIR=$(mktemp -d /tmp/myproxy-runtime.XXXXXX)
readonly UV_VERSION NODE_VERSION SINGBOX_VERSION MACHINE_ARCH DOWNLOAD_DIR
chown root:"$MYPROXY_GROUP" "$DOWNLOAD_DIR"
chmod 0750 "$DOWNLOAD_DIR"

cleanup() {
  [[ -n "${DOWNLOAD_DIR:-}" && "$DOWNLOAD_DIR" == /tmp/myproxy-runtime.* ]] || return 0
  [[ -d "$DOWNLOAD_DIR" && ! -L "$DOWNLOAD_DIR" ]] || return 0
  rm -rf -- "$DOWNLOAD_DIR"
}
trap cleanup EXIT

download() {
  local url=$1 destination=$2
  [[ "$destination" == "$DOWNLOAD_DIR"/* && ! -e "$destination" && ! -L "$destination" ]] || \
    die "unsafe or reused runtime download destination: $destination"
  curl --fail --location --silent --show-error --retry 3 \
    --proto '=https' --tlsv1.2 \
    --output "$destination" "$url"
  [[ -f "$destination" && ! -L "$destination" ]] || \
    die "download did not produce a regular file: $destination"
  chown root:"$MYPROXY_GROUP" "$destination"
  chmod 0640 "$destination"
}

manifest_hash() {
  local manifest=$1 asset_name=$2 label=$3 count expected_hash
  [[ -f "$manifest" && ! -L "$manifest" ]] || die "$label checksum manifest is unsafe"
  count=$(awk -v file="$asset_name" '$2 == file {count++} END {print count + 0}' "$manifest")
  [[ "$count" == 1 ]] || \
    die "$label checksum manifest did not contain exactly one $asset_name entry"
  expected_hash=$(awk -v file="$asset_name" '$2 == file {print $1}' "$manifest")
  [[ "$expected_hash" =~ ^[0-9a-fA-F]{64}$ ]] || \
    die "$label checksum manifest contains an invalid SHA256 for $asset_name"
  printf '%s\n' "${expected_hash,,}"
}

pinned_hash() {
  manifest_hash "$CHECKSUM_FILE" "$1" repository
}

verify_official_and_pinned_hash() {
  local archive=$1 official_hash=$2 asset_name=$3 repository_hash
  [[ "$official_hash" =~ ^[0-9a-fA-F]{64}$ ]] || \
    die "official SHA256 is invalid for $asset_name"
  official_hash=${official_hash,,}
  repository_hash=$(pinned_hash "$asset_name")
  [[ "$official_hash" == "$repository_hash" ]] || \
    die "official SHA256 does not match the repository pin for $asset_name"

  printf '%s  %s\n' "$repository_hash" "$(basename -- "$archive")" \
    >"$DOWNLOAD_DIR/target.sha256"
  chmod 0600 "$DOWNLOAD_DIR/target.sha256"
  (cd -- "$DOWNLOAD_DIR" && sha256sum --check --status target.sha256) || \
    die "SHA256 verification failed for $asset_name"
}

check_tar_members() {
  local archive=$1 compression=$2 listing verbose member_file metadata_file
  local type member target member_dir normalized
  local -a list_args verbose_args
  case "$compression" in
    gz)
      list_args=(-tzf "$archive")
      verbose_args=(-tvzf "$archive")
      ;;
    xz)
      list_args=(-tJf "$archive")
      verbose_args=(-tvJf "$archive")
      ;;
    *) die "unsupported archive format" ;;
  esac

  listing=$(run_as_myproxy env LC_ALL=C tar --quoting-style=escape "${list_args[@]}") || \
    die "could not list runtime archive as $MYPROXY_USER"
  [[ -n "$listing" ]] || die "runtime archive is empty: $(basename -- "$archive")"
  member_file=$(mktemp "$DOWNLOAD_DIR/members.XXXXXX")
  metadata_file=$(mktemp "$DOWNLOAD_DIR/metadata.XXXXXX")
  chmod 0600 "$member_file" "$metadata_file"

  if ! LC_ALL=C awk '
    {
      name=$0
      if (name ~ /^\// || name ~ /(^|\/)\.\.($|\/)/ ||
          name ~ /(^|\/)\.($|\/)/ || name ~ /\/\// ||
          name ~ /[[:space:]\\]/ || name ~ /(^|\/)-/) {
        bad=1
      }
      sub(/\/$/, "", name)
      if (name == "" || seen[name]++) {
        bad=1
      }
      print name
    }
    END {exit bad ? 1 : 0}
  ' <<<"$listing" >"$member_file"; then
    die "unsafe, duplicate or ambiguous path found in $(basename -- "$archive")"
  fi

  verbose=$(run_as_myproxy env LC_ALL=C tar --numeric-owner --quoting-style=escape \
    "${verbose_args[@]}") || die "could not inspect runtime archive metadata as $MYPROXY_USER"
  if ! LC_ALL=C awk '
    {
      mode=$1
      type=substr(mode, 1, 1)
      if (type != "-" && type != "d" && type != "l") {
        bad=1
        next
      }
      if (mode ~ /[sS]/) {
        bad=1
      }
      if (type == "l") {
        if (NF < 8 || $(NF - 1) != "->") {
          bad=1
          next
        }
        name=$(NF - 2)
        target=$NF
      } else {
        name=$NF
        target="-"
      }
      sub(/\/$/, "", name)
      printf "%s\t%s\t%s\n", type, name, target
    }
    END {exit bad ? 1 : 0}
  ' <<<"$verbose" >"$metadata_file"; then
    die "archive contains a special, setuid/setgid or ambiguous member: $(basename -- "$archive")"
  fi

  if ! LC_ALL=C awk -F '\t' '
    NR == FNR {
      allowed[$1]=1
      allowed_count++
      next
    }
    {
      type=$1
      name=$2
      if (!(name in allowed) || seen[name]++) {
        bad=1
      }
      types[name]=type
      names[++member_count]=name
    }
    END {
      if (allowed_count != member_count) {
        bad=1
      }
      for (i=1; i<=member_count; i++) {
        component_count=split(names[i], components, "/")
        prefix=""
        for (j=1; j<component_count; j++) {
          prefix=(prefix == "" ? components[j] : prefix "/" components[j])
          if (types[prefix] == "l") {
            bad=1
          }
        }
      }
      exit bad ? 1 : 0
    }
  ' "$member_file" "$metadata_file"; then
    die "archive metadata and paths disagree or traverse an archive symlink"
  fi

  while IFS=$'\t' read -r type member target; do
    [[ "$type" == l ]] || continue
    [[ "$target" =~ ^[A-Za-z0-9._/+@%:=~-]+$ && "$target" != /* ]] || \
      die "unsafe symbolic-link target in $(basename -- "$archive"): $member"
    member_dir=${member%/*}
    [[ "$member_dir" != "$member" ]] || member_dir=.
    normalized=$(readlink -m -- "/archive-root/$member_dir/$target")
    case "$normalized" in
      /archive-root/*) ;;
      *) die "symbolic link escapes the archive root: $member" ;;
    esac
  done <"$metadata_file"
}

extract_archive_as_myproxy() {
  local archive=$1 compression=$2 destination=$3 strip_components=$4
  local -a extract_args
  [[ "$destination" == "$DOWNLOAD_DIR"/* && ! -e "$destination" && ! -L "$destination" ]] || \
    die "unsafe or reused runtime extraction destination: $destination"
  install -d -m 0750 -o "$MYPROXY_USER" -g "$MYPROXY_GROUP" "$destination"
  case "$compression" in
    gz) extract_args=(-xzf "$archive") ;;
    xz) extract_args=(-xJf "$archive") ;;
    *) die "unsupported archive format" ;;
  esac
  run_as_myproxy env LC_ALL=C tar --no-same-owner --no-same-permissions \
    --strip-components="$strip_components" "${extract_args[@]}" -C "$destination" || \
    die "could not extract runtime archive as $MYPROXY_USER"
}

validate_extracted_tree() {
  local stage=$1 link resolved
  [[ -d "$stage" && ! -L "$stage" && "$(readlink -f -- "$stage")" == "$stage" ]] || \
    die "runtime stage is not a real directory: $stage"
  if find -P "$stage" -xdev \( -type b -o -type c -o -type p -o -type s \) \
    -print -quit | grep -q .; then
    die "runtime stage contains a special file: $stage"
  fi
  if find -P "$stage" -xdev -type f -perm /6000 -print -quit | grep -q .; then
    die "runtime stage contains a setuid or setgid file: $stage"
  fi
  if find -P "$stage" -xdev -type f -links +1 -print -quit | grep -q .; then
    die "runtime stage contains an unexpected hard link: $stage"
  fi
  while IFS= read -r -d '' link; do
    resolved=$(readlink -f -- "$link") || die "runtime stage contains a dangling link: $link"
    case "$resolved" in
      "$stage"/*) ;;
      *) die "runtime stage contains an escaping link: $link" ;;
    esac
  done < <(find -P "$stage" -xdev -type l -print0)
}

seal_stage_for_probe() {
  local stage=$1
  validate_extracted_tree "$stage"
  chown_tree_nofollow root "$MYPROXY_GROUP" "$stage" || \
    die "could not seal runtime staging ownership"
  find -P "$stage" -xdev -type d -exec chmod 0750 {} +
  find -P "$stage" -xdev -type f -perm /111 -exec chmod 0750 {} +
  find -P "$stage" -xdev -type f ! -perm /111 -exec chmod 0640 {} +
  validate_extracted_tree "$stage"
}

replace_runtime_dir() {
  local stage=$1 destination=$2
  local previous="${destination}.previous.$$.tmp"
  local local_stage="${destination}.install.$$.tmp"
  assert_under_root "$destination"
  assert_under_root "$previous"
  assert_under_root "$local_stage"
  [[ -d "$stage" && ! -L "$stage" ]] || die "runtime staging directory is missing: $stage"

  install -d -m 0750 -o root -g "$MYPROXY_GROUP" "$(dirname -- "$destination")"
  [[ ! -e "$local_stage" && ! -L "$local_stage" ]] || \
    die "runtime staging collision: $local_stage"
  [[ ! -e "$previous" && ! -L "$previous" ]] || \
    die "stale runtime rollback path exists: $previous"
  if ! cp -a -- "$stage" "$local_stage"; then
    rm -rf -- "$local_stage"
    die "failed to stage runtime on the destination filesystem"
  fi

  if [[ -e "$destination" || -L "$destination" ]]; then
    mv -T -- "$destination" "$previous"
  fi
  if ! mv -T -- "$local_stage" "$destination"; then
    [[ ! -e "$previous" && ! -L "$previous" ]] || mv -T -- "$previous" "$destination"
    rm -rf -- "$local_stage"
    die "failed to install runtime at $destination"
  fi
  if [[ -e "$previous" || -L "$previous" ]]; then
    rm -rf -- "$previous"
  fi
}

install_uv() {
  local triple archive checksum_manifest official_hash extracted stage destination asset_name
  local uv_source uvx_source installed_version reported_version
  destination="$MYPROXY_ROOT/.runtime/uv"
  # Release builds append platform/build metadata to `uv --version`; use the
  # machine-readable short form so the pinned semantic version is exact.
  if [[ -x "$destination/bin/uv" && -f "$destination/bin/uv" && \
    ! -L "$destination/bin/uv" && -f "$destination/VERSION" && ! -L "$destination/VERSION" ]] && \
    [[ "$(<"$destination/VERSION")" == "$UV_VERSION" ]]; then
    installed_version=$(run_as_myproxy "$destination/bin/uv" self version --short 2>/dev/null || true)
    if [[ "$installed_version" == "$UV_VERSION" ]]; then
      log "uv $UV_VERSION is already installed"
      return
    fi
  fi

  case "$MACHINE_ARCH" in
    amd64) triple=x86_64-unknown-linux-gnu ;;
    arm64) triple=aarch64-unknown-linux-gnu ;;
  esac
  asset_name="uv-${triple}.tar.gz"
  archive="$DOWNLOAD_DIR/$asset_name"
  checksum_manifest="$DOWNLOAD_DIR/uv.sha256"
  download "https://releases.astral.sh/github/uv/releases/download/${UV_VERSION}/${asset_name}" "$archive"
  download "https://releases.astral.sh/github/uv/releases/download/${UV_VERSION}/${asset_name}.sha256" \
    "$checksum_manifest"
  official_hash=$(manifest_hash "$checksum_manifest" "$asset_name" official-uv)
  verify_official_and_pinned_hash "$archive" "$official_hash" "$asset_name"
  check_tar_members "$archive" gz

  extracted="$DOWNLOAD_DIR/uv-extracted"
  stage="$DOWNLOAD_DIR/uv-stage"
  extract_archive_as_myproxy "$archive" gz "$extracted" 0
  install -d -m 0750 -o root -g "$MYPROXY_GROUP" "$stage" "$stage/bin"
  uv_source=$(find -P "$extracted" -xdev -type f -name uv -print -quit)
  uvx_source=$(find -P "$extracted" -xdev -type f -name uvx -print -quit)
  [[ -n "$uv_source" && -n "$uvx_source" ]] || die "uv archive is missing uv or uvx"
  install -m 0750 -o root -g "$MYPROXY_GROUP" "$uv_source" "$stage/bin/uv"
  install -m 0750 -o root -g "$MYPROXY_GROUP" "$uvx_source" "$stage/bin/uvx"
  printf '%s\n' "$UV_VERSION" >"$stage/VERSION"
  chown root:"$MYPROXY_GROUP" "$stage/VERSION"
  chmod 0640 "$stage/VERSION"
  seal_stage_for_probe "$stage"
  if ! reported_version=$(run_as_myproxy "$stage/bin/uv" self version --short); then
    die "downloaded uv could not execute"
  fi
  [[ "$reported_version" == "$UV_VERSION" ]] || \
    die "downloaded uv version mismatch: expected $UV_VERSION, got ${reported_version:-empty}"
  replace_runtime_dir "$stage" "$destination"
  log "installed uv $UV_VERSION (official and repository SHA256 verified)"
}

install_node() {
  local node_arch archive official_manifest official_hash stage destination asset_name node_version
  destination="$MYPROXY_ROOT/.runtime/node"
  if [[ -x "$destination/bin/node" && -f "$destination/bin/node" && \
    ! -L "$destination/bin/node" && -f "$destination/VERSION" && ! -L "$destination/VERSION" ]] && \
    [[ "$(<"$destination/VERSION")" == "$NODE_VERSION" ]]; then
    node_version=$(run_as_myproxy "$destination/bin/node" --version 2>/dev/null || true)
    if [[ "$node_version" == "v$NODE_VERSION" ]]; then
      log "Node.js $NODE_VERSION is already installed"
      return
    fi
  fi

  case "$MACHINE_ARCH" in
    amd64) node_arch=x64 ;;
    arm64) node_arch=arm64 ;;
  esac
  asset_name="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
  archive="$DOWNLOAD_DIR/$asset_name"
  official_manifest="$DOWNLOAD_DIR/SHASUMS256.txt"
  download "https://nodejs.org/dist/v${NODE_VERSION}/${asset_name}" "$archive"
  download "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" "$official_manifest"
  official_hash=$(manifest_hash "$official_manifest" "$asset_name" official-node)
  verify_official_and_pinned_hash "$archive" "$official_hash" "$asset_name"
  check_tar_members "$archive" xz

  stage="$DOWNLOAD_DIR/node-stage"
  extract_archive_as_myproxy "$archive" xz "$stage" 1
  printf '%s\n' "$NODE_VERSION" >"$stage/VERSION"
  chown "$MYPROXY_USER:$MYPROXY_GROUP" "$stage/VERSION"
  chmod 0640 "$stage/VERSION"
  seal_stage_for_probe "$stage"
  node_version=$(run_as_myproxy "$stage/bin/node" --version 2>/dev/null || true)
  [[ "$node_version" == "v$NODE_VERSION" ]] || die "downloaded Node.js version mismatch"
  replace_runtime_dir "$stage" "$destination"
  log "installed Node.js $NODE_VERSION (official and repository SHA256 verified)"
}

github_asset_digest() {
  local asset_name=$1 release_json=$2 digest
  digest=$(awk -v target="\"name\": \"${asset_name}\"" '
    index($0, target) {found=1; next}
    found && /"digest": "sha256:/ {
      line=$0
      sub(/^.*"digest": "sha256:/, "", line)
      sub(/".*$/, "", line)
      print line
      exit
    }
    found && /"name": / {exit}
  ' "$release_json")
  [[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] || \
    die "official GitHub release metadata did not provide a SHA256 digest for $asset_name"
  printf '%s\n' "${digest,,}"
}

install_sing_box() {
  local archive release_json official_hash stage destination asset_name installed_version
  destination="$MYPROXY_ROOT/.runtime/sing-box"
  if [[ -x "$destination/sing-box" && -f "$destination/sing-box" && \
    ! -L "$destination/sing-box" && -f "$destination/VERSION" && ! -L "$destination/VERSION" ]] && \
    [[ "$(<"$destination/VERSION")" == "$SINGBOX_VERSION" ]]; then
    installed_version=$(run_as_myproxy "$destination/sing-box" version 2>/dev/null | head -n 1 || true)
    if [[ "$installed_version" == *"$SINGBOX_VERSION"* ]]; then
      log "sing-box $SINGBOX_VERSION is already installed"
      return
    fi
  fi

  asset_name="sing-box-${SINGBOX_VERSION}-linux-${MACHINE_ARCH}.tar.gz"
  archive="$DOWNLOAD_DIR/$asset_name"
  release_json="$DOWNLOAD_DIR/sing-box-release.json"
  download "https://api.github.com/repos/SagerNet/sing-box/releases/tags/v${SINGBOX_VERSION}" \
    "$release_json"
  official_hash=$(github_asset_digest "$asset_name" "$release_json")
  download "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/${asset_name}" \
    "$archive"
  verify_official_and_pinned_hash "$archive" "$official_hash" "$asset_name"
  check_tar_members "$archive" gz

  stage="$DOWNLOAD_DIR/sing-box-stage"
  extract_archive_as_myproxy "$archive" gz "$stage" 1
  [[ -f "$stage/sing-box" && ! -L "$stage/sing-box" ]] || \
    die "sing-box archive is missing its executable"
  chmod 0750 "$stage/sing-box"
  printf '%s\n' "$SINGBOX_VERSION" >"$stage/VERSION"
  chown "$MYPROXY_USER:$MYPROXY_GROUP" "$stage/VERSION"
  chmod 0640 "$stage/VERSION"
  seal_stage_for_probe "$stage"
  installed_version=$(run_as_myproxy "$stage/sing-box" version 2>/dev/null | head -n 1 || true)
  [[ "$installed_version" == *"$SINGBOX_VERSION"* ]] || \
    die "downloaded sing-box version mismatch"
  replace_runtime_dir "$stage" "$destination"
  log "installed sing-box $SINGBOX_VERSION (official and repository SHA256 verified)"
}

install -d -m 0750 -o root -g "$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime"
case "$COMPONENT" in
  all)
    install_uv
    install_node
    install_sing_box
    ;;
  uv) install_uv ;;
  node) install_node ;;
  sing-box) install_sing_box ;;
esac

for runtime_dir in uv node sing-box; do
  [[ ! -d "$MYPROXY_ROOT/.runtime/$runtime_dir" ]] || \
    chown_tree_nofollow root "$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime/$runtime_dir" || \
      die "could not seal downloaded runtime ownership"
done
chown root:"$MYPROXY_GROUP" "$MYPROXY_ROOT/.runtime"
chmod 0750 "$MYPROXY_ROOT/.runtime"

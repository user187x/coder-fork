#!/usr/bin/env bash
# File Browser in the workspace: FileBrowser Quantum, the workspace image's. Installed as ~/.local/bin/coder-filebrowser
# by startup.sh and by the Cloud Drive's script (scripts/cloud-drive.sh: a platform section, so templates made in the
# template builder get the current one at every build), whichever is newer (HELPER_VERSION). Its settings beside its
# database (~/.config/filebrowser-quantum/env: APPS, START_DIR, CLOUD_DRIVE_LINK; without it - a template's own older
# startup.sh started File Browser - they are read from the config.yaml that one wrote). It serves under the app's path
# (Coder proxies path apps with their /@user/workspace.agent/apps/<slug> prefix intact, so its assets would 404
# elsewhere: an endless spinner), on 127.0.0.1 only, with no sign-in of its own (Coder's app proxy lets only the owner
# in) and no update check.
#   bash filebrowser.sh install installed, unless the one there is newer
#   coder-filebrowser start     started, unless it runs
#   coder-filebrowser restart   started again when what it should show changed (the Cloud Drive's watcher calls it as
#                               the owner connects or disconnects this workspace's drive); nothing if it does not run
#   coder-filebrowser check     the same, skipped while another call works (the watcher's, every 10 seconds: it also
#                               takes over a File Browser an older startup.sh started with this config)
# Its sources, each a link in its sidebar: the Start folder, and every Cloud Drive here - the owner's (~/cloud-drive)
# and each one shared with them (~/cloud-drive-shared/<owner>: read only unless shared read and write), and each folder
# of one shared with them (~/cloud-drive-shared/<owner>/<folder>: "<owner>'s <folder>"; a shared file cannot be a
# source of its own, and is in the Cloud Drive window - and in the workspace at its link). Those are links
# to volumes mounted out of the way (/mnt/...), and Quantum opens no link that leads out of its source's folder (it
# answers 403: "permission denied", or an empty folder). The workspace image's own build (a version ending -coder.N:
# FileBrowser Quantum with symlinkTargets) opens them where they are: the Start folder may follow links into the
# drives' folders (and the owner's drive's source folder), so they open in place - the links in the Start folder, and
# `cloud-drive mount` ones. Older builds (an image from before it) refuse that setting, so there the links are hidden
# in the Start folder instead; the drives' own sources are in the sidebar either way.
set -eu
HELPER_VERSION=4
dir="$HOME/.config/filebrowser-quantum"
config="$dir/config.yaml"
# per container (a PID kept in the home would name another process after a restart)
pidfile=/tmp/coder-filebrowser.pid
port=13339
shared="$HOME/cloud-drive-shared"
# a drive whose storage does not answer is left out, not waited on
probe() { timeout 10 test "$@"; }
# File Browser runs as PID $1 (one that exited and is not reaped yet, a zombie, does not)
alive() { [ "$(cat "/proc/$1/comm" 2>/dev/null)" = filebrowser ] && ! grep -q '^State:[[:space:]]*Z' "/proc/$1/status"; }

# Its settings: the env file startup.sh writes, else the config.yaml File Browser runs with (the app's path and the
# Start folder). CLOUD_DRIVE_LINK from the caller (the Cloud Drive's script) comes first.
settings_in() {
 local link="${CLOUD_DRIVE_LINK:-}"
 if [ -f "$dir/env" ]; then
  # shellcheck source=/dev/null
  . "$dir/env"
 elif [ -s "$config" ] && jq -e . "$config" >/dev/null 2>&1; then
  APPS="$(jq -r '.http.baseURL | sub("/filebrowser/$"; "")' "$config")"
  START_DIR="$(jq -r '.server.sources[0].path' "$config")"
 elif [ -s "$config" ]; then
  # the YAML an older startup.sh wrote
  APPS="$(sed -n 's|^  baseURL: "\(.*\)/filebrowser/"$|\1|p' "$config" | head -1)"
  START_DIR="$(sed -n 's|^    - path: "\(.*\)"$|\1|p' "$config" | head -1)"
 fi
 CLOUD_DRIVE_LINK="${link:-${CLOUD_DRIVE_LINK:-}}"
 [ -n "${APPS:-}" ] && [ -n "${START_DIR:-}" ]
}

# The PID of File Browser run with this config: the one this started, or one an older startup.sh started (taken over).
ours() {
 local pid
 if [ -s "$pidfile" ]; then
  pid="$(cat "$pidfile")"
  if alive "$pid"; then
   echo "$pid"
   return 0
  fi
 fi
 for pid in $(pgrep -x -u "$(id -u)" filebrowser); do
  if alive "$pid" && tr '\0' '\n' <"/proc/$pid/cmdline" 2>/dev/null | grep -qxF "$config"; then
   echo "$pid" >"$pidfile"
   echo "$pid"
   return 0
  fi
 done
 return 1
}

# Another File Browser runs (one this did not start, with another config): left as it is.
others() {
 local pid
 for pid in $(pgrep -x -u "$(id -u)" filebrowser); do
  if alive "$pid"; then
   return 0
  fi
 done
 return 1
}

# The Cloud Drives here, a JSON object a line: {name, path, readOnly}.
drives() {
 local link item owner
 if [ -n "${CLOUD_DRIVE_LINK:-}" ] && [ -L "$CLOUD_DRIVE_LINK" ]; then
  source_of "$CLOUD_DRIVE_LINK" "Cloud Drive" false
 fi
 for link in "$shared"/*; do
  if [ -L "$link" ]; then
   source_of "$link" "$(basename "$link")'s Cloud Drive"
  elif [ -d "$link" ]; then
   # folders and files of an owner's drive shared: each folder a source
   owner="$(basename "$link")"
   owner="${owner% (shared items)}"
   for item in "$link"/*; do
    if [ -L "$item" ]; then
     source_of "$item" "$owner's $(basename "$item")"
    fi
   done
  fi
 done
}

# source_of LINK NAME [READONLY]: the folder LINK leads to as a source (nothing for a file, or one that does not
# answer); read only as given, else unless it is writable.
source_of() {
 local target ro="${3:-}"
 target="$(timeout 10 readlink -f "$1")" && probe -d "$target" || return 0
 if [ -z "$ro" ]; then
  ro=true
  if probe -w "$target"; then ro=false; fi
 fi
 jq -nc --arg name "$2" --arg path "$target" --argjson ro "$ro" '{name: $name, path: $path, readOnly: $ro}'
}

# Whether this File Browser opens links that lead out of a source into the folders it is told (symlinkTargets: the
# workspace image's own build, its version ending -coder.N).
follows_links() {
 filebrowser version 2>/dev/null | grep -q 'Version.*v[0-9][0-9.]*-coder\.'
}

# Where links in the Start folder may lead (a JSON array): the drives' folders, and the folder the owner's drive is
# served from when it is one of the workspace's (not one holding the Start folder itself).
link_targets() {
 local start target
 start="$(readlink -f "$START_DIR")"
 {
  printf '%s\n' /mnt/cloud-drive /mnt/cloud-drive-peers /mnt/cloud-drive-items
  if [ -n "${CLOUD_DRIVE_LINK:-}" ] && [ -L "$CLOUD_DRIVE_LINK" ] &&
   target="$(timeout 10 readlink -f "$CLOUD_DRIVE_LINK")"; then
   case "$start/" in "$target"/*) ;; *) echo "$target" ;; esac
  fi
 } | jq -Rsc 'split("\n") | map(select(. != "")) | unique'
}

# The Start folder's rules: the drives' links in it hidden (they cannot open there), a JSON object a line - the
# owner's drive's link (Quantum lists a link as a file), and the shared drives' folder (it holds only links).
hidden() {
 local start
 start="$(readlink -f "$START_DIR")"
 if [ -n "${CLOUD_DRIVE_LINK:-}" ] && [ -L "$CLOUD_DRIVE_LINK" ]; then
  rule filePath "$start" "$CLOUD_DRIVE_LINK"
 fi
 if [ -d "$shared" ]; then
  rule folderPath "$start" "$shared"
 fi
}

# rule KIND START PATH: {KIND: PATH within the Start folder}, when it is in there.
rule() {
 local parent
 parent="$(readlink -f "$(dirname "$3")")"
 case "$parent/" in "$2"/*) ;; *) return 0 ;; esac
 jq -nc --arg kind "$1" --arg p "${parent#"$2"}/$(basename "$3")" '{($kind): $p}'
}

# The drives (a JSON array), each named apart from the others and the Start folder: "<name> (2)" for a second one.
unique_names() {
 drives | jq -sc --arg home "$(basename "$START_DIR")" 'reduce .[] as $d ([]; . as $acc
	| ([$home] + ($acc | map(.name))) as $taken
	| ([$d.name] + [range(2; 99) | "\($d.name) (\(.))"] | map(select(. as $n | ($taken | index($n)) == null))
		| first) as $name
	| $acc + [$d + {name: $name}])'
}

# Its settings (JSON, which YAML reads as it is): the Start folder (following its links into the drives, or hiding
# them), then the drives.
settings() {
 local start_config
 if follows_links; then
  start_config="$(jq -nc --argjson targets "$(link_targets)" '{symlinkTargets: $targets, rules: []}')"
 else
  start_config="$(hidden | jq -sc '{rules: .}')"
 fi
 jq -n --arg base "$APPS/filebrowser/" --argjson port "$port" --arg db "$dir/filebrowser.sqlite" --arg start "$START_DIR" \
  --arg name "$(basename "$START_DIR")" --argjson start_config "$start_config" \
  --argjson drives "$(unique_names)" '
	def all: {view: true, download: true, modify: true, create: true, delete: true};
	def look: {view: true, download: true, modify: false, create: false, delete: false};
	{http: {listen: "127.0.0.1", port: $port, baseURL: $base, trustProxyHeaders: true},
	 server: {disableUpdateCheck: true, database: {path: $db}, cacheDir: "/tmp/filebrowser-cache",
	  sources: ([{path: $start, name: $name, config: ({defaultEnabled: true, defaultPermissions: all} + $start_config)}]
	   + [$drives[] | {path, name, config: {defaultEnabled: true, readOnly,
	      defaultPermissions: (if .readOnly then look else all end)}}])},
	 auth: {methods: {noauth: true}},
	 userDefaults: {listing: {showHidden: true}, account: {disableUpdateNotifications: true}}}'
}

# A link in the sidebar for each source (Quantum gives a new user one for its first source only), the rest as it was.
sidebar() {
 local url="http://127.0.0.1:$port$APPS/filebrowser/" user body
 for _ in $(seq 300); do
  curl -fsS -o /dev/null "${url}health" 2>/dev/null && break
  sleep 1
 done
 user="$(curl -fsS "${url}api/users?username=self")" || return 0
 body="$(jq -c --argjson user "$user" '[.server.sources[].name] as $names | ($user.sidebarLinks // []) as $links
		| [$names[] as $n | ([$links[] | select(.category == "source" and .sourceName == $n)] | first)
			// {name: "", category: "source", target: "/", icon: "", sourceName: $n}]
			+ [$links[] | select(.category != "source")]
		| if (map(select(.category == "source") | .sourceName)) == ($links | map(select(.category == "source") | .sourceName))
		  then empty else {which: ["sidebarLinks"], data: {sidebarLinks: .}} end' "$config")"
 if [ -n "$body" ]; then
  curl -fsS -o /dev/null -X PATCH -H 'Content-Type: application/json' --data "$body" "${url}api/users" || true
 fi
}

launch() {
 local what
 settings >"$config.new" && mv "$config.new" "$config"
 # the whole subshell redirected (exec: it becomes File Browser), so nothing keeps the caller's output open; nor
 # the lock (9)
 (cd "$dir" && exec filebrowser -c "$config" --no-input) </dev/null >/tmp/filebrowser.log 2>&1 9>&- &
 echo $! >"$pidfile"
 sidebar </dev/null >>/tmp/filebrowser.log 2>&1 9>&- &
 what="$(jq -r '[.server.sources[1:][] | .name + (if .config.readOnly then " (read only)" else "" end)]
		| if length > 0 then ", with " + join(", ") else "" end' "$config")"
 echo "File Browser (Quantum): started in $START_DIR$what (log: /tmp/filebrowser.log)"
}

stop() {
 local pid="$1"
 kill "$pid" 2>/dev/null || true
 for _ in $(seq 50); do
  alive "$pid" || return 0
  sleep 0.2
 done
 kill -9 "$pid" 2>/dev/null || true
}

case "${1:-}" in
start | restart | check) ;;
install)
 # bash <this file> install: this one as ~/.local/bin/coder-filebrowser, unless the one there is newer
 dst="$HOME/.local/bin/coder-filebrowser"
 have="$(sed -n 's/^HELPER_VERSION=\([0-9]*\)$/\1/p' "$dst" 2>/dev/null | head -1)"
 if [ "${have:-0}" -le "$HELPER_VERSION" ]; then
  mkdir -p "$(dirname "$dst")" && cp "$0" "$dst.new.$$" && chmod 0755 "$dst.new.$$" && mv "$dst.new.$$" "$dst"
 fi
 exit 0
 ;;
*)
 sed -n '2,/^set /p' "$0" | sed '$d'
 exit 2
 ;;
esac
mkdir -p "$dir"
rm -f "$dir/pid" # where helper version 1 kept it
# one at a time: startup.sh and the Cloud Drive's watcher may both call at a workspace's start; never stuck behind a
# call that hangs (a drive's storage not answering)
exec 9>"$dir/lock"
case "$1" in
check) flock -n 9 || exit 0 ;;
*) flock -w 120 9 || {
 echo "File Browser: busy, left as it is"
 exit 0
} ;;
esac
case "$1" in
start)
 if ours >/dev/null || others; then
  echo "File Browser: running already"
 elif settings_in; then
  launch
 else
  echo "File Browser: no settings ($dir/env)"
 fi
 ;;
restart | check)
 pid="$(ours)" || exit 0
 settings_in || exit 0
 [ "$(settings)" != "$(cat "$config" 2>/dev/null)" ] || exit 0
 stop "$pid"
 launch
 ;;
esac

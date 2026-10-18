#!/usr/bin/env bash
# File Browser in the workspace: FileBrowser Quantum, the workspace image's. Installed by startup.sh as
# ~/.local/bin/coder-filebrowser, its settings beside its database (~/.config/filebrowser-quantum/env: APPS,
# START_DIR, CLOUD_DRIVE_LINK). It serves under the app's path (Coder proxies path apps with their
# /@user/workspace.agent/apps/<slug> prefix intact, so its assets would 404 elsewhere: an endless spinner), on
# 127.0.0.1 only, with no sign-in of its own (Coder's app proxy lets only the owner in) and no update check.
#   coder-filebrowser start     started, unless it runs
#   coder-filebrowser restart   started again when what it should show changed (the Cloud Drive's watcher calls it as
#                               the owner connects or disconnects this workspace's drive); nothing if it does not run
# Its sources, each a link in its sidebar: the Start folder, and every Cloud Drive here - the owner's (~/cloud-drive)
# and each one shared with them (~/cloud-drive-shared/<owner>: read only unless shared read and write). Those are links
# to volumes mounted out of the way (/mnt/...), and Quantum opens no link that leads out of its source's folder (it
# answers 403: "permission denied", or an empty folder), so each drive is a source of its own and its link is hidden in
# the Start folder.
set -eu
dir="$HOME/.config/filebrowser-quantum"
# shellcheck source=/dev/null
. "$dir/env"
config="$dir/config.yaml"
pidfile="$dir/pid"
port=13339
base="$APPS/filebrowser/"
shared="$HOME/cloud-drive-shared"

running() { [ -s "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; }

# The Cloud Drives here, a JSON object a line: {name, path, readOnly}.
drives() {
 local link target ro
 if [ -n "${CLOUD_DRIVE_LINK:-}" ] && [ -L "$CLOUD_DRIVE_LINK" ] && target="$(readlink -f "$CLOUD_DRIVE_LINK")" &&
  [ -d "$target" ]; then
  jq -nc --arg path "$target" '{name: "Cloud Drive", path: $path, readOnly: false}'
 fi
 for link in "$shared"/*; do
  [ -L "$link" ] && target="$(readlink -f "$link")" && [ -d "$target" ] || continue
  ro=true
  if [ -w "$target" ]; then ro=false; fi
  jq -nc --arg name "$(basename "$link")'s Cloud Drive" --arg path "$target" --argjson ro "$ro" \
   '{name: $name, path: $path, readOnly: $ro}'
 done
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

# Its settings (JSON, which YAML reads as it is): the Start folder, then the drives.
settings() {
 jq -n --arg base "$base" --argjson port "$port" --arg db "$dir/filebrowser.sqlite" --arg start "$START_DIR" \
  --arg name "$(basename "$START_DIR")" --argjson rules "$(hidden | jq -sc .)" \
  --argjson drives "$(drives | jq -sc .)" '
	def all: {view: true, download: true, modify: true, create: true, delete: true};
	def look: {view: true, download: true, modify: false, create: false, delete: false};
	{http: {listen: "127.0.0.1", port: $port, baseURL: $base, trustProxyHeaders: true},
	 server: {disableUpdateCheck: true, database: {path: $db}, cacheDir: "/tmp/filebrowser-cache",
	  sources: ([{path: $start, name: $name, config: {defaultEnabled: true, defaultPermissions: all, rules: $rules}}]
	   + [$drives[] | {path, name, config: {defaultEnabled: true, readOnly,
	      defaultPermissions: (if .readOnly then look else all end)}}])},
	 auth: {methods: {noauth: true}},
	 userDefaults: {listing: {showHidden: true}, account: {disableUpdateNotifications: true}}}'
}

# A link in the sidebar for each source (Quantum gives a new user one for its first source only), the rest as it was.
sidebar() {
 local url="http://127.0.0.1:$port$base" user body
 for _ in $(seq 60); do
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
 local pid
 pid="$(cat "$pidfile")"
 kill "$pid" 2>/dev/null || true
 for _ in $(seq 50); do
  kill -0 "$pid" 2>/dev/null || return 0
  sleep 0.2
 done
 kill -9 "$pid" 2>/dev/null || true
}

mkdir -p "$dir"
# one at a time: startup.sh and the Cloud Drive's watcher may both call at a workspace's start
exec 9>"$dir/lock"
flock 9
case "${1:-}" in
start)
 if running || pgrep -x filebrowser >/dev/null; then
  echo "File Browser: running already"
 else
  launch
 fi
 ;;
restart)
 running || exit 0
 [ "$(settings)" != "$(cat "$config" 2>/dev/null)" ] || exit 0
 stop
 launch
 ;;
*)
 sed -n '2,/^set /p' "$0" | sed '$d'
 exit 2
 ;;
esac

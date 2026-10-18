#!/usr/bin/env bash
# The owner's Cloud Drive in this workspace (coder_script.cloud_drive in ../main.tf): ~/cloud-drive links to it while
# the pod's annotation platform.coder/cloud-drive says so. The platform service sets the annotation, the pod sees it at
# /etc/platform/cloud-drive (the downward API) within a second, and the watcher started here follows it: no restart.
#   on              its volume, mounted out of the way (MOUNT), when this workspace started with one
#   off             none: disconnected by the owner (dragging its cable away), or shared with others (the Cloud
#                   Drive is the owner's alone)
#   source:<folder> a folder of this workspace is the owner's Cloud Drive (the platform service serves it from here
#                   to their other workspaces): linked as it is
# How it started (mode, from the platform service then): "mount" with the volume; "source"; "shared" or "offline"
# (the owner's source workspace was not running) without one - the drive comes with the next start.
# It also installs the `cloud-drive` command (~/.local/bin/cloud-drive): the Cloud Drive mounted into folders of the
# workspace (`cloud-drive mount ~/project/data`), files of the same name resolved as the owner chooses; the watcher
# brings those mounts up to date every 10 seconds (`cloud-drive sync`).
# The Cloud Drives other users shared with the owner (Cloud Drive > Sharing) come mounted under PEERS, one folder per
# owner (read only unless shared read and write): linked at ~/cloud-drive-shared/<owner>, at every start.
# main.tf renders this with templatefile(): link, mode, mount and cli are the template's values (no shell braces here).
LINK='${link}'
MODE='${mode}'
MOUNT='${mount}'
STATE=/etc/platform/cloud-drive
PEERS=/mnt/cloud-drive-peers
SHARED="$HOME/cloud-drive-shared"
PIDFILE=/tmp/cloud-drive-watch.pid
CLI="$HOME/.local/bin/cloud-drive"

mkdir -p "$HOME/.local/bin" && echo '${cli}' | base64 -d >"$CLI.new" && chmod 0755 "$CLI.new" && mv "$CLI.new" "$CLI"

case "$MODE" in
shared) echo "Cloud Drive: none here - this workspace is shared with others, and the Cloud Drive is its owner's alone" ;;
offline) echo "Cloud Drive: its source workspace was not running when this one started - restart this one once it runs" ;;
esac

sync_link() {
 local state target
 state="$(cat "$STATE" 2>/dev/null)"
 case "$state" in
 off) target="" ;;
 source:/*) target="$${state#source:}" ;;
 *) target="$MOUNT" ;;
 esac
 if [ -z "$target" ]; then
  if [ -L "$LINK" ]; then
   rm -f "$LINK" && echo "Cloud Drive: not here ($LINK removed)"
  fi
  return 0
 fi
 if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$target" ]; then
  return 0
 fi
 mkdir -p "$target" 2>/dev/null || true
 if [ -L "$LINK" ]; then
  rm -f "$LINK"
 elif [ -d "$LINK" ] && ! rmdir "$LINK" 2>/dev/null; then
  # a folder made there while the drive was disconnected: kept beside it
  aside="$LINK.local"
  [ -e "$aside" ] && aside="$LINK.local-$(date +%Y%m%d-%H%M%S)"
  mv "$LINK" "$aside" && echo "Cloud Drive: the folder $LINK had files: moved to $aside"
 fi
 ln -s "$target" "$LINK" && echo "Cloud Drive: $LINK -> $target"
}

sync_link

# The shared Cloud Drives: a link for each one mounted now; links to ones no longer shared go.
if [ -d "$SHARED" ]; then
 find "$SHARED" -mindepth 1 -maxdepth 1 -type l -lname "$PEERS/*" -delete
fi
for d in "$PEERS"/*/; do
 [ -d "$d" ] || continue
 name="$(basename "$d")"
 mkdir -p "$SHARED" && ln -sfn "$PEERS/$name" "$SHARED/$name"
 if [ -w "$PEERS/$name" ]; then access="read and write"; else access="read only"; fi
 echo "Cloud Drive: $name's, shared with you ($access): $SHARED/$name"
done
if [ -d "$SHARED" ] && [ -z "$(ls -A "$SHARED")" ]; then
 rmdir "$SHARED"
fi

# A watcher from an earlier start (the agent restarted) gives way to this one.
if [ -f "$PIDFILE" ]; then
 kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
LINK="$LINK" MOUNT="$MOUNT" STATE="$STATE" CLI="$CLI" setsid bash -c "$(declare -f sync_link)"'
 n=0
 while sleep 1; do
  sync_link
  n=$((n + 1))
  if [ $((n % 10)) -eq 0 ] && [ -s "$HOME/.config/cloud-drive/mounts.json" ]; then
   "$CLI" sync >/dev/null || true
  fi
 done' </dev/null >>/tmp/cloud-drive.log 2>&1 &
echo $! >"$PIDFILE"

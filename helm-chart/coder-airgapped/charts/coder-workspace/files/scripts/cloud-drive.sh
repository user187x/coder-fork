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
# owner (read only unless shared read and write): linked at ~/cloud-drive-shared/<owner>, at every start. A folder or
# file of one shared with them comes mounted under ITEMS (/mnt/cloud-drive-items/<share id>; ITEMS_LIST names them):
# linked at ~/cloud-drive-shared/<owner>/<its name> - or ~/cloud-drive-shared/<owner> (shared items)/<its name> when
# that owner's entire drive is linked at ~/cloud-drive-shared/<owner> too.
# File Browser shows each of them as a source of its own: this installs its helper (scripts/filebrowser.sh, kept when the
# template's own startup.sh installed a newer one), which starts it again as they change and, every 10 seconds, takes
# over a File Browser an older startup.sh started (a template made in the template builder keeps its own startup.sh,
# while this section is the base template's current one at every build).
# main.tf renders this with templatefile(): link, mode, mount, cli, filebrowser and items are the template's values (no
# shell braces here).
LINK='${link}'
MODE='${mode}'
MOUNT='${mount}'
STATE=/etc/platform/cloud-drive
PEERS=/mnt/cloud-drive-peers
ITEMS=/mnt/cloud-drive-items
ITEMS_LIST='${items}'
SHARED="$HOME/cloud-drive-shared"
PIDFILE=/tmp/cloud-drive-watch.pid
CLI="$HOME/.local/bin/cloud-drive"
FB="$HOME/.local/bin/coder-filebrowser"

mkdir -p "$HOME/.local/bin" && echo '${cli}' | base64 -d >"$CLI.new" && chmod 0755 "$CLI.new" && mv "$CLI.new" "$CLI"
fb_helper="$(mktemp)" && echo '${filebrowser}' | base64 -d >"$fb_helper" && bash "$fb_helper" install
rm -f "$fb_helper"

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
   files_again
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
 files_again
}

# File Browser shows each Cloud Drive here as a source of its own (scripts/filebrowser.sh): started again when they
# change (nothing if it does not run, or nothing changed for it).
files_again() {
 if [ -x "$FB" ]; then
  CLOUD_DRIVE_LINK="$LINK" "$FB" restart || true
 fi
}

# The same, in the background and skipped while another call works (the watcher's, every 10 seconds).
files_check() {
 if [ -x "$FB" ]; then
  CLOUD_DRIVE_LINK="$LINK" "$FB" check </dev/null &
 fi
}

sync_link

# The shared Cloud Drives: a link for each one mounted now; links to ones no longer shared go (and the folders that
# held only links to shared folders and files).
if [ -d "$SHARED" ]; then
 find "$SHARED" -mindepth 1 -maxdepth 1 -type l -lname "$PEERS/*" -delete
 find "$SHARED" -mindepth 2 -maxdepth 2 -type l -lname "$ITEMS/*" -delete
 find "$SHARED" -mindepth 1 -maxdepth 1 -type d -empty -delete
fi
for d in "$PEERS"/*/; do
 [ -d "$d" ] || continue
 name="$(basename "$d")"
 mkdir -p "$SHARED" && ln -sfn "$PEERS/$name" "$SHARED/$name"
 if [ -w "$PEERS/$name" ]; then access="read and write"; else access="read only"; fi
 echo "Cloud Drive: $name's, shared with you ($access): $SHARED/$name"
done
# The shared folders and files: a link for each one mounted now, by its name (another number when two are named alike).
echo "$ITEMS_LIST" | base64 -d 2>/dev/null | jq -r '.[]? | [.id, .owner, .item] | @tsv' 2>/dev/null |
 while IFS=$'\t' read -r id owner item; do
  [ -e "$ITEMS/$id" ] || continue
  dir="$SHARED/$owner"
  if [ -L "$dir" ]; then dir="$SHARED/$owner (shared items)"; fi
  mkdir -p "$dir" || continue
  name="$item"
  n=2
  while [ -e "$dir/$name" ] || [ -L "$dir/$name" ]; do
   name="$item ($n)"
   n=$((n + 1))
  done
  ln -s "$ITEMS/$id" "$dir/$name"
  if [ -w "$ITEMS/$id" ]; then access="read and write"; else access="read only"; fi
  if [ -d "$ITEMS/$id" ]; then what="folder"; else what="file"; fi
  echo "Cloud Drive: $owner's $what $item, shared with you ($access): $dir/$name"
 done
if [ -d "$SHARED" ] && [ -z "$(ls -A "$SHARED")" ]; then
 rmdir "$SHARED"
fi
files_again

# A watcher from an earlier start (the agent restarted) gives way to this one.
if [ -f "$PIDFILE" ]; then
 kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
LINK="$LINK" MOUNT="$MOUNT" STATE="$STATE" CLI="$CLI" FB="$FB" setsid bash -c "$(declare -f sync_link files_again files_check)"'
 n=0
 while sleep 1; do
  sync_link
  n=$((n + 1))
  if [ $((n % 10)) -eq 0 ]; then
   if [ -s "$HOME/.config/cloud-drive/mounts.json" ]; then
    "$CLI" sync >/dev/null || true
   fi
   files_check
  fi
 done' </dev/null >>/tmp/cloud-drive.log 2>&1 &
echo $! >"$PIDFILE"

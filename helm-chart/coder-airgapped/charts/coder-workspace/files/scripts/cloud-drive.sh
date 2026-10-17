#!/usr/bin/env bash
# The owner's Cloud Drive in this workspace (coder_script.cloud_drive in ../main.tf): ~/cloud-drive links to its
# volume, mounted out of the way, while the pod's annotation platform.coder/cloud-drive says "on". The owner may
# disconnect the workspace on the Workspaces page (dragging its cable away) and connect it again; the platform service
# sets the annotation, the pod sees it at /etc/platform/cloud-drive (the downward API) within a second, and the watcher
# started here follows it: no restart.
# main.tf renders this with templatefile(): link and mount are the template's values (no shell braces here).
LINK='${link}'
MOUNT='${mount}'
STATE=/etc/platform/cloud-drive
PIDFILE=/tmp/cloud-drive-watch.pid

sync_link() {
 if [ "$(cat "$STATE" 2>/dev/null)" = off ]; then
  if [ -L "$LINK" ]; then
   rm -f "$LINK" && echo "Cloud Drive: disconnected ($LINK removed)"
  fi
  return 0
 fi
 if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$MOUNT" ]; then
  return 0
 fi
 if [ -L "$LINK" ]; then
  rm -f "$LINK"
 elif [ -d "$LINK" ] && ! rmdir "$LINK" 2>/dev/null; then
  # a folder made there while the drive was disconnected: kept beside it
  aside="$LINK.local"
  [ -e "$aside" ] && aside="$LINK.local-$(date +%Y%m%d-%H%M%S)"
  mv "$LINK" "$aside" && echo "Cloud Drive: the folder $LINK had files: moved to $aside"
 fi
 ln -s "$MOUNT" "$LINK" && echo "Cloud Drive: connected at $LINK"
}

sync_link
# A watcher from an earlier start (the agent restarted) gives way to this one.
if [ -f "$PIDFILE" ]; then
 kill "$(cat "$PIDFILE")" 2>/dev/null || true
fi
LINK="$LINK" MOUNT="$MOUNT" STATE="$STATE" setsid bash -c "$(declare -f sync_link)"'
 while sleep 1; do sync_link; done' </dev/null >>/tmp/cloud-drive.log 2>&1 &
echo $! >"$PIDFILE"

#!/bin/sh
# Lock Down: jails a workspace, as an admin set it (Workspace page > Lock Down; the platform service's lockdown.py).
#   lockdown.sh MODES CODER_HOST [ROOT]
#
# MODES     a comma list of network and sudo, or off; or file:PATH, to read it from that file (missing: off). It sets
#           exactly MODES: what is not in it is lifted, so running it again changes nothing.
# CODER_HOST  the host the workspace's agent reaches Coder on (the access URL's): Network keeps it, so the agent stays
#           connected and admins can still open the workspace's terminal and apps, and inspect what runs there.
# ROOT      where the workspace container's / is, for Sudo (/proc/1/root from a container sharing its processes);
#           empty: sudo is left alone.
#
# Run as root in the workspace pod's network namespace with NET_ADMIN: the template's init container (at start) or an
# ephemeral container the platform service adds to the running pod (no restart: the workspace's processes keep
# running, for the admins to look at). The workspace container has no NET_ADMIN, so not even root in it can undo
# Network; Sudo is a last sudoers rule that denies every command, so nobody in it can become root any more (a
# process that is root already stays root).
#
# Network: everything in or out is refused but loopback, DNS (the pod's name servers) and Coder (CODER_HOST's
# addresses), IPv6 entirely. Connections that are open already, to anything else, stop at once.
set -eu
modes=$1
host=$2
root=${3-}

case $modes in file:*) modes=$(cat "${modes#file:}" 2>/dev/null || echo off) ;; esac
net=
sudo_=
case ",$modes," in *,network,*) net=1 ;; esac
case ",$modes," in *,sudo,*) sudo_=1 ;; esac

out=CODER-LOCKDOWN
in=CODER-LOCKDOWN-IN

# Network: the rules go first in OUTPUT and INPUT, in chains of their own (lifted by removing them).
for t in iptables ip6tables; do
 while $t -D OUTPUT -j "$out" 2>/dev/null; do :; done
 while $t -D INPUT -j "$in" 2>/dev/null; do :; done
 for c in "$out" "$in"; do
  $t -F "$c" 2>/dev/null || true
  $t -X "$c" 2>/dev/null || true
 done
done
if [ -n "$net" ]; then
 servers=$(awk '/^nameserver/ { print $2 }' /etc/resolv.conf | grep -v ':' || true)
 coder=
 for i in 1 2 3 4 5 6 7 8 9 10; do
  coder=$(getent ahostsv4 "$host" 2>/dev/null | awk '{ print $1 }' | sort -u | tr '\n' ' ')
  [ -n "$coder" ] && break
  sleep 1
 done
 [ -n "$coder" ] || echo "lockdown: $host does not resolve: the workspace is cut off from Coder too" >&2
 iptables -N "$out"
 iptables -N "$in"
 iptables -A "$out" -o lo -j ACCEPT
 iptables -A "$in" -i lo -j ACCEPT
 for s in $servers; do
  for p in udp tcp; do
   iptables -A "$out" -d "$s" -p "$p" --dport 53 -j ACCEPT
   iptables -A "$in" -s "$s" -p "$p" --sport 53 -j ACCEPT
  done
 done
 for c in $coder; do
  iptables -A "$out" -d "$c" -p tcp -j ACCEPT
  iptables -A "$in" -s "$c" -p tcp -j ACCEPT
 done
 iptables -A "$out" -p tcp -j REJECT --reject-with tcp-reset
 iptables -A "$out" -j REJECT
 iptables -A "$in" -j DROP
 iptables -I OUTPUT 1 -j "$out"
 iptables -I INPUT 1 -j "$in"
 ip6tables -N "$out"
 ip6tables -N "$in"
 ip6tables -A "$out" -o lo -j ACCEPT
 ip6tables -A "$in" -i lo -j ACCEPT
 ip6tables -A "$out" -j REJECT
 ip6tables -A "$in" -j DROP
 ip6tables -I OUTPUT 1 -j "$out"
 ip6tables -I INPUT 1 -j "$in"
fi

# Sudo: a last rule in sudoers.d that denies every command to everyone (sudo uses the last rule that matches).
if [ -n "$root" ]; then
 f="$root/etc/sudoers.d/zz-coder-lockdown"
 if [ -n "$sudo_" ]; then
  printf '%s\n' "# Lock Down: an admin took sudo away in this workspace (Workspace page > Lock Down)." \
   'ALL ALL=(ALL:ALL) !ALL' >"$f.new"
  chown 0:0 "$f.new"
  chmod 0440 "$f.new"
  mv -f "$f.new" "$f"
 else
  rm -f "$f"
 fi
fi

state() { if [ -n "$1" ]; then echo on; else echo off; fi; }
if [ -n "$root" ]; then
 echo "lockdown: network $(state "$net"), sudo $(state "$sudo_")"
else
 echo "lockdown: network $(state "$net") (sudo is set by the workspace's start script)"
fi

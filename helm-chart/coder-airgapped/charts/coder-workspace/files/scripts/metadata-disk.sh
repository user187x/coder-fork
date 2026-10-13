#!/usr/bin/env bash
# The home volume (metadata "3_home_disk" in ../main.tf).
vol=$(echo "${CODER_HOME_DISK_SIZE:-}" | awk '{ n = $0 + 0; u = $0; sub(/^[0-9.]+/, "", u); m = 1; if (u == "Ki") m = 1024; else if (u == "Mi") m = 1024^2; else if (u == "Gi") m = 1024^3; else if (u == "Ti") m = 1024^4; else if (u == "k" || u == "K") m = 1e3; else if (u == "M") m = 1e6; else if (u == "G") m = 1e9; else if (u == "T") m = 1e12; printf "%.0f", n * m }')
set -- $(df -B1 --output=size,used "$HOME" | tail -1)
total=$1
used=$2
if [ -n "$vol" ] && [ "$vol" -gt 0 ] && awk -v t="$total" -v v="$vol" 'BEGIN { exit !(t > v * 1.1) }'; then
 used=$(nice -n 19 du -sxB1 "$HOME" 2>/dev/null | awk '{ print $1 }')
 total=$vol
fi
awk -v u="$used" -v t="$total" 'BEGIN { printf "%.1f/%.1f GB (%.0f%%)", u / 1e9, t / 1e9, (t > 0 ? u * 100 / t : 0) }'

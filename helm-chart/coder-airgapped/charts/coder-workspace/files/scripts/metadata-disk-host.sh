#!/usr/bin/env bash
# The node's disk (metadata "6_disk_host" in ../main.tf).
set -- $(df -B1 --output=size,used / | tail -1)
awk -v t="$1" -v u="$2" 'BEGIN { printf "%.1f/%.1f GB (%.0f%%)", u / 1e9, t / 1e9, (t > 0 ? u * 100 / t : 0) }'

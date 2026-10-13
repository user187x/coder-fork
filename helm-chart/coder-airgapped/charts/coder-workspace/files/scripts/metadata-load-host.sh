#!/usr/bin/env bash
# The node's load (metadata "7_load_host" in ../main.tf).
awk -v n="$(getconf _NPROCESSORS_ONLN 2>/dev/null || grep -c ^processor /proc/cpuinfo)" '{ printf "%.2f / %d cores (%.0f%%)", $1, n, $1 * 100 / n }' /proc/loadavg

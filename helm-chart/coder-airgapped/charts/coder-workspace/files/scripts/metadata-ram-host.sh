#!/usr/bin/env bash
# The node's RAM (metadata "5_mem_usage_host" in ../main.tf).
coder stat mem --host -o json | tr -d " \n" | awk '{ u = $0; sub(/.*"used":/, "", u); t = $0; sub(/.*"total":/, "", t); u += 0; t += 0; if (t > 0) printf "%.2f/%.2f GB (%.0f%%)", u / 1e9, t / 1e9, u * 100 / t; else printf "%.2f GB", u / 1e9 }'

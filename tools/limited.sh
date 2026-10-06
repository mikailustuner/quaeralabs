#!/usr/bin/env bash
# Runs heavy jobs at low priority inside a memory- and CPU-limited systemd scope.
# If the limit is exceeded only this scope is killed; the rest of the system is unaffected.
# Usage: tools/limited.sh <memory, e.g. 6G> <cpu quota, e.g. 300%> command [args...]
set -euo pipefail
MEM=${1:?memory limit required}; CPU=${2:?cpu quota required}; shift 2
exec systemd-run --user --scope --quiet -p MemoryMax="$MEM" -p MemorySwapMax=0 -p CPUQuota="$CPU" -- nice -n 10 "$@"

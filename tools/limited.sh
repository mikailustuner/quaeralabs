#!/usr/bin/env bash
# Ağır işleri bellek ve CPU sınırlı bir systemd kapsamında, düşük öncelikle çalıştırır.
# Sınır aşılırsa yalnızca bu kapsam öldürülür; sistemin geri kalanı etkilenmez.
# Kullanım: tools/limited.sh <bellek, ör. 6G> <cpu kotası, ör. 300%> komut [argümanlar...]
set -euo pipefail
MEM=${1:?bellek sınırı gerekli}; CPU=${2:?cpu kotası gerekli}; shift 2
exec systemd-run --user --scope --quiet -p MemoryMax="$MEM" -p MemorySwapMax=0 -p CPUQuota="$CPU" -- nice -n 10 "$@"

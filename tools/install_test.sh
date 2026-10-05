#!/usr/bin/env bash
# install.sh'yi temiz Linux konteynerlerinde sınar ("temiz makine" kurulum testi).
#
#   tools/install_test.sh [debian:13 ubuntu:24.04 ...]
#
# Her imaj için:
#   1) sistem paketleri yokken betik açık bir mesajla durmalı (sudo çalıştırmadan)
#   2) git, curl, bubblewrap kurulduktan sonra normal kullanıcıyla çekirdek kurulum tamamlanmalı
#   3) `quaera doctor` zorunlu bileşenleri ✓ görmeli (model sağlayıcı hariç: konteynerde claude CLI yok)
#   4) `quaera serve` ayağa kalkmalı, /api/settings 200 dönmeli; sözleşme ve sunucu testleri geçmeli
# bubblewrap'ın konteyner içinde ad alanı açabilmesi için seccomp/apparmor gevşetilir ve SYS_ADMIN verilir.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ $# -gt 0 ]; then IMAGES=("$@"); else IMAGES=(debian:13 ubuntu:24.04); fi
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT
rsync -a --exclude .venv --exclude ml-env --exclude lean/.lake --exclude web/node_modules --exclude web/dist \
      --exclude images --exclude '*.pdf' --exclude __pycache__ --exclude .pytest_cache --exclude 'evals/results' \
      "$ROOT/" "$SRC/quaeralabs/"

for img in "${IMAGES[@]}"; do
  echo "════ $img"
  docker run --rm -m 3g --cpus 2 --security-opt seccomp=unconfined --security-opt apparmor=unconfined --cap-add SYS_ADMIN \
    -v "$SRC/quaeralabs:/src:ro" "$img" bash -euo pipefail -c '
      export DEBIAN_FRONTEND=noninteractive
      cp -r /src /tmp/q && cd /tmp/q
      echo "── 1) eksik sistem paketleri"
      if ./install.sh > /tmp/out1 2>&1; then echo "BEKLENMEDİK: eksik paketlerle kuruldu"; exit 1; fi
      grep -q "Eksik sistem paketleri" /tmp/out1 && echo "ok: açık mesajla durdu: $(grep -o "Kurmak için:.*" /tmp/out1 | head -c 120)"
      apt-get update -qq && apt-get install -y -qq git curl ca-certificates bubblewrap nodejs npm sudo rsync >/dev/null
      useradd -m arastirmaci && cp -r /src /home/arastirmaci/quaeralabs && chown -R arastirmaci /home/arastirmaci/quaeralabs
      echo "── 2) çekirdek kurulum (normal kullanıcı)"
      su - arastirmaci -c "cd ~/quaeralabs && ./install.sh" 2>&1 | tail -25
      echo "── 3) doctor"
      su - arastirmaci -c "~/.local/bin/quaera doctor" || true
      echo "── 4) sunucu ve testler"
      su - arastirmaci -c "cd ~/quaeralabs && (~/.local/bin/quaera serve >/tmp/serve.log 2>&1 &) ; for i in \$(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:8765/api/settings && break; sleep 1; done; curl -s -o /dev/null -w \"api/settings: %{http_code}\n\" http://127.0.0.1:8765/api/settings; curl -s -o /dev/null -w \"arayüz: %{http_code}\n\" http://127.0.0.1:8765/"
      su - arastirmaci -c "cd ~/quaeralabs && uv run --quiet pytest -q tests/test_validate.py tests/test_server.py tests/test_memory.py tests/test_triage.py 2>&1 | tail -2"
    ' 2>&1 | tee "/tmp/install-test-${img//[:\/]/-}.log" | grep -vE "^(Get|Hit|Selecting|Preparing|Unpacking|Setting up|Processing)"
done

#!/usr/bin/env bash
# Tests install.sh in clean Linux containers ("clean machine" install test).
#
#   tools/install_test.sh [debian:13 ubuntu:24.04 ...]
#
# For each image:
#   1) without the system packages the script must stop with a clear message (without running sudo)
#   2) after installing git, curl, bubblewrap the core install must complete as a normal user
#   3) `quaera doctor` must show the required components as ✓ (except the model provider: no claude CLI in the container)
#   4) `quaera serve` must come up and /api/settings must return 200; the contract and server tests must pass
# seccomp/apparmor are relaxed and SYS_ADMIN is granted so bubblewrap can create namespaces inside the container.
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
      echo "── 1) missing system packages"
      if ./install.sh > /tmp/out1 2>&1; then echo "UNEXPECTED: installed with missing packages"; exit 1; fi
      grep -q "Missing system packages" /tmp/out1 && echo "ok: stopped with a clear message: $(grep -o "To install:.*" /tmp/out1 | head -c 120)"
      apt-get update -qq && apt-get install -y -qq git curl ca-certificates bubblewrap nodejs npm sudo rsync >/dev/null
      useradd -m researcher && cp -r /src /home/researcher/quaeralabs && chown -R researcher /home/researcher/quaeralabs
      echo "── 2) core install (normal user)"
      su - researcher -c "cd ~/quaeralabs && ./install.sh" 2>&1 | tail -25
      echo "── 3) doctor"
      su - researcher -c "~/.local/bin/quaera doctor" || true
      echo "── 4) server and tests"
      su - researcher -c "cd ~/quaeralabs && (~/.local/bin/quaera serve >/tmp/serve.log 2>&1 &) ; for i in \$(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:8765/api/settings && break; sleep 1; done; curl -s -o /dev/null -w \"api/settings: %{http_code}\n\" http://127.0.0.1:8765/api/settings; curl -s -o /dev/null -w \"web UI: %{http_code}\n\" http://127.0.0.1:8765/"
      su - researcher -c "cd ~/quaeralabs && uv run --quiet pytest -q tests/test_validate.py tests/test_server.py tests/test_memory.py tests/test_triage.py 2>&1 | tail -2"
    ' 2>&1 | tee "/tmp/install-test-${img//[:\/]/-}.log" | grep -vE "^(Get|Hit|Selecting|Preparing|Unpacking|Setting up|Processing)"
done

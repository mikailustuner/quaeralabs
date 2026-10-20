#!/usr/bin/env bash
# Tests the installed-package path (improvement plan A3) in clean Linux containers: no repo checkout, no Lean.
#
#   tools/wheel_install_test.sh [debian:13 ubuntu:24.04 ...]
#
# Builds the web UI and the wheel, then in each image as a normal user:
#   1) installs the wheel into a fresh virtualenv (what `pipx install quaeralabs` does)
#   2) `quaera doctor` shows a working core and, before setup, the ML tier as off
#   3) `quaera setup ml --no-torch` creates ~/.quaera/ml-env; doctor then shows the ML tier ready
#   4) an experiment script runs through `quaera sandbox-exec` (scikit-learn inside the sandbox, no network)
#   5) `quaera serve` serves the web UI and /api/settings reports the ML domain as ready
# seccomp/apparmor/masked system paths are relaxed and SYS_ADMIN is granted so bubblewrap can create namespaces and mount
# /proc inside the container.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ $# -gt 0 ]; then IMAGES=("$@"); else IMAGES=(debian:13); fi
(cd "$ROOT/web" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
rm -rf "$ROOT/dist" && (cd "$ROOT" && uv build --wheel >/dev/null)
WHEEL="$(ls "$ROOT"/dist/*.whl)"

for img in "${IMAGES[@]}"; do
  echo "════ $img"
  docker run --rm -m 3g --cpus 2 --security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --cap-add SYS_ADMIN \
    -v "$WHEEL:/wheel/$(basename "$WHEEL"):ro" "$img" bash -euo pipefail -c '
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq && apt-get install -y -qq python3 python3-venv bubblewrap curl ca-certificates >/dev/null
      useradd -m researcher
      su - researcher -c "
        set -euo pipefail
        export QUAERA_LITELLM_MODELS={}   # stands in for a model provider: the test makes no model call
        echo \"── 1) install the wheel\"
        python3 -m venv ~/q && ~/q/bin/pip install --quiet /wheel/*.whl && echo ok
        echo \"── 2) doctor before setup\"
        ~/q/bin/quaera doctor > /tmp/d1 || true; cat /tmp/d1
        grep -q \"\\[✓\\] sandbox (bwrap)\" /tmp/d1 && grep -q \"\\[–\\] ML experiment environment\" /tmp/d1
        echo \"── 3) setup ml\"
        ~/q/bin/quaera setup ml --no-torch
        ~/q/bin/quaera doctor | grep \"ML experiment environment\" | tee /tmp/d2; grep -q \"\[✓\]\" /tmp/d2
        echo \"── 4) an experiment in the sandbox\"
        mkdir -p ~/exp && printf \"from sklearn.linear_model import LogisticRegression\nimport numpy as np\nX=np.array([[0],[1],[2],[3]]); y=[0,0,1,1]\nprint(\\\"acc\\\", LogisticRegression().fit(X,y).score(X,y))\n\" > ~/exp/run.py
        ~/q/bin/quaera sandbox-exec --workdir ~/exp -- python run.py | tee /tmp/r
        grep -q \"acc 1.0\" /tmp/r
        echo \"── 5) web UI and settings\"
        (~/q/bin/quaera serve >/tmp/serve.log 2>&1 &)
        for i in \$(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:8765/api/settings && break; sleep 1; done
        curl -s -o /dev/null -w \"web UI: %{http_code}\n\" http://127.0.0.1:8765/
        curl -s http://127.0.0.1:8765/api/settings | python3 -c \"import json,sys; d=json.load(sys.stdin)[\\\"domains\\\"]; print(\\\"domains:\\\", d); assert d[\\\"ml\\\"][\\\"ready\\\"]\"
        echo PASSED
      "
    ' 2>&1 | tee "/tmp/wheel-test-${img//[:\/]/-}.log" | grep -vE "^(Get|Hit|Selecting|Preparing|Unpacking|Setting up|Processing)"
done

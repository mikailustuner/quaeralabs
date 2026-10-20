# Lean 4 + Mathlib + REPL image (improvement plan A2, ADR 0018).
# Build context: the repo's lean/ directory (its .dockerignore leaves out .lake):
#   docker build -f docker/lean.Dockerfile -t quaera-lean lean
# QuaeraLabs runs it through the container sandbox: no network, read-only root, the host user's UID. It never runs lake;
# it calls lean and the REPL directly, at the paths listed in /etc/quaera-image.json.
FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*
ENV ELAN_HOME=/opt/elan PATH=/opt/elan/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

COPY . /lean
WORKDIR /lean
# Permissions are set in the same layer that writes the files: a later `chmod -R` would copy them into a new layer.
RUN curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
      | sh -s -- -y --no-modify-path --default-toolchain "$(cat lean-toolchain)" \
    && lean --version && chmod -R a+rX /opt/elan
# The Mathlib build cache (downloaded, not compiled), then the REPL. Git histories and the generated C files are not
# needed to check proofs.
RUN lake exe cache get && lake build REPL/repl \
    && find .lake/packages -maxdepth 2 -name .git -prune -exec rm -rf {} + \
    && rm -rf .lake/packages/*/.lake/build/ir \
    && chmod -R a+rX /lean

# Where everything is, for LeanChecker (src/quaera/lean.py, image mode). Same order as the host's LEAN_PATH.
RUN set -eu; \
    tc_name="$(cat lean-toolchain)"; \
    tc="$ELAN_HOME/toolchains/$(printf '%s' "$tc_name" | sed 's|/|--|g; s|:|---|g')"; \
    test -x "$tc/bin/lean"; test -x /lean/.lake/packages/REPL/.lake/build/bin/repl; \
    test -f /lean/.lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean; \
    paths=""; for d in /lean/.lake/packages/*/.lake/build/lib/lean; do paths="$paths\"$d\", "; done; \
    rev="$(sed -n 's/^rev = "\(.*\)"/\1/p' lakefile.toml | head -1)"; \
    printf '{"kind": "lean", "workspace": "/lean", "toolchain": "%s", "toolchainName": "%s", "lean": "%s/bin/lean", "repl": "%s", "leanPath": [%s"%s/lib/lean"], "mathlibRev": "%s"}\n' \
      "$tc" "$tc_name" "$tc" /lean/.lake/packages/REPL/.lake/build/bin/repl "$paths" "$tc" "$rev" > /etc/quaera-image.json; \
    cat /etc/quaera-image.json

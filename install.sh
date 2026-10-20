#!/usr/bin/env bash
# QuaeraLabs one-command install (Phase 4).
#
#   ./install.sh                     # core: Python environment, web UI, `quaera` command
#   ./install.sh --with-ml           # + ML experiment environment (numpy, scikit-learn, torch; CUDA if NVIDIA is present)
#   ./install.sh --with-lean         # + Lean 4 + Mathlib + REPL (~8 GB download/build)
#   ./install.sh --all               # everything
#   ./install.sh --lean-image        # Lean as a prebuilt container image instead (Docker or Podman; no build)
#   ./install.sh --ml-image          # the ML environment as a container image (CPU only)
#
# Run outside the repo, it clones from QUAERA_REPO (default below) into the --dir directory.
# Supported: Linux (x86_64, arm64), WSL2 on Windows, and macOS with Docker Desktop, OrbStack or Podman (Lean and ML
# always come as images there). The script never runs sudo; if a system package is needed it prints the command and stops.
set -euo pipefail

REPO_URL="${QUAERA_REPO:-https://github.com/mikailustuner/quaeralabs.git}"
DIR="${QUAERA_DIR:-$HOME/quaeralabs}"
WITH_ML=0 WITH_LEAN=0 ML_IMAGE=0 LEAN_IMAGE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-ml) WITH_ML=1 ;;
    --with-lean) WITH_LEAN=1 ;;
    --all) WITH_ML=1; WITH_LEAN=1 ;;
    --ml-image) WITH_ML=1; ML_IMAGE=1 ;;
    --lean-image) WITH_LEAN=1; LEAN_IMAGE=1 ;;
    --dir) DIR="$2"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1;33m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;35m!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# 1) Platform -----------------------------------------------------------------------------------
case "$(uname -s)" in
  Linux)
    if grep -qi microsoft /proc/version 2>/dev/null; then PLATFORM="WSL2"; else PLATFORM="Linux"; fi ;;
  Darwin)
    PLATFORM="macOS"
    # No bubblewrap on macOS: every sandboxed command runs in a container, and Lean and the ML environment are images.
    [ "$WITH_LEAN" = 1 ] && LEAN_IMAGE=1
    [ "$WITH_ML" = 1 ] && ML_IMAGE=1 ;;
  *)
    die "Unsupported system: $(uname -s). On Windows, run it inside WSL2 (docs/installation.md)." ;;
esac
ok "Platform: $PLATFORM ($(uname -m))"

# 2) System tools (the ones needing sudo are only shown) -----------------------------------------
pm_install() {
  if command -v apt-get >/dev/null; then echo "sudo apt-get install -y $*"
  elif command -v dnf >/dev/null; then echo "sudo dnf install -y $*"
  elif command -v pacman >/dev/null; then echo "sudo pacman -S --needed $*"
  elif command -v zypper >/dev/null; then echo "sudo zypper install $*"
  else echo "install with your package manager: $*"; fi
}
ENGINE=""
for e in podman docker; do
  if command -v "$e" >/dev/null && "$e" info >/dev/null 2>&1; then ENGINE="$e"; break; fi
done
missing=()
for tool in git curl; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if [ "$PLATFORM" = "macOS" ]; then
  [ ${#missing[@]} -eq 0 ] || die "Missing tools: ${missing[*]}. Install the Xcode command line tools: xcode-select --install"
  [ -n "$ENGINE" ] || die "macOS needs a running container engine for the sandbox: Docker Desktop, OrbStack or Podman (podman machine start). Details: docs/installation.md#macos"
  ok "Sandbox: containers ($ENGINE)"
else
  [ ${#missing[@]} -eq 0 ] || die "Missing system packages: ${missing[*]}. To install: $(pm_install ${missing[*]}) — then run the script again."
  if command -v bwrap >/dev/null && bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev true 2>/dev/null; then
    ok "bubblewrap works (sandbox)"
  elif [ -n "$ENGINE" ]; then
    warn "bubblewrap is missing or cannot create namespaces here; the sandbox uses containers ($ENGINE) instead (docs/installation.md#bubblewrap)"
  elif command -v bwrap >/dev/null; then
    die "bubblewrap is installed but unprivileged namespaces are disabled, and no container engine (podman, docker) is running. Details and fix: docs/installation.md#bubblewrap"
  else
    die "No sandbox: install bubblewrap ($(pm_install bubblewrap)) or a container engine (podman or docker), then run the script again."
  fi
  if command -v systemd-run >/dev/null && systemd-run --user --scope --quiet true 2>/dev/null; then
    ok "systemd user session: memory/CPU limits can be applied"
  else
    warn "No systemd user session: memory/CPU limits cannot be applied to experiments (ADR 0010). On WSL2, set [boot] systemd=true in /etc/wsl.conf."
  fi
fi
if { [ "$LEAN_IMAGE" = 1 ] || [ "$ML_IMAGE" = 1 ]; } && [ -z "$ENGINE" ]; then
  die "--lean-image and --ml-image need a running container engine (podman or docker)."
fi

# 3) Source code ------------------------------------------------------------------------------
if [ -f pyproject.toml ] && grep -q 'name = "quaeralabs"' pyproject.toml 2>/dev/null; then
  DIR="$(pwd)"
  ok "Repo: $DIR"
elif [ -d "$DIR/.git" ]; then
  say "Updating existing repo: $DIR"; git -C "$DIR" pull --ff-only
else
  say "Cloning: $REPO_URL → $DIR"; git clone --depth 1 "$REPO_URL" "$DIR"
fi
cd "$DIR"

# 4) uv and Python environment -------------------------------------------------------------------
if ! command -v uv >/dev/null; then
  say "Installing uv (official Astral script, ~/.local/bin)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version | cut -d' ' -f2)"
say "Installing Python dependencies"
uv sync --quiet --extra memory
ok "Python environment ready"

# 5) Web UI -------------------------------------------------------------------------------------
# Without a system Node 20+ (e.g. the Ubuntu 24.04 repo ships 18) a portable Node is downloaded from the official
# nodejs.org archive, checked against SHASUMS256 and used only to build the UI (no sudo, not installed system-wide).
node_ok() { command -v node >/dev/null && command -v npm >/dev/null && [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 20 ]; }
if [ -f web/dist/index.html ]; then
  ok "Web UI already built (web/dist)"
else
  if ! node_ok; then
    case "$(uname -m)" in x86_64) NODE_ARCH=x64 ;; aarch64|arm64) NODE_ARCH=arm64 ;; *) NODE_ARCH="" ;; esac
    NODE_OS=linux; [ "$PLATFORM" = "macOS" ] && NODE_OS=darwin
    if command -v sha256sum >/dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
    NODE_HOME="$HOME/.cache/quaeralabs/node"
    if [ -n "$NODE_ARCH" ] && [ ! -x "$NODE_HOME/bin/node" ]; then
      say "Node.js 20+ not found; downloading a portable Node 22 to build the UI (~30 MB, ~/.cache/quaeralabs)"
      base="https://nodejs.org/dist/latest-v22.x"
      sums="$(curl -fsSL "$base/SHASUMS256.txt")"
      line="$(printf '%s\n' "$sums" | grep -E " node-v[0-9.]+-${NODE_OS}-${NODE_ARCH}\.tar\.gz$" | head -1)"
      file="${line##* }"; sum="${line%% *}"
      tmp="$(mktemp -d)"
      curl -fsSL "$base/$file" -o "$tmp/$file"
      echo "$sum  $tmp/$file" | $SHA -c --quiet - || die "Node archive checksum mismatch; the download is corrupt or was tampered with."
      mkdir -p "$NODE_HOME" && tar -xzf "$tmp/$file" -C "$NODE_HOME" --strip-components=1 && rm -rf "$tmp"
    fi
    [ -x "$NODE_HOME/bin/node" ] && export PATH="$NODE_HOME/bin:$PATH"
  fi
  if node_ok; then
    say "Building the web UI (Node $(node --version))"
    (cd web && npm ci --no-audit --no-fund --silent && npm run build --silent)
    ok "Web UI built"
  else
    warn "Could not build the web UI (no Node 20+ and the download failed). The command line works; install Node and run 'cd web && npm ci && npm run build'."
  fi
fi

# 6) ML experiment environment (optional) ---------------------------------------------------------
image_of() { uv run --quiet python -c "from quaera import images; print(images.$1())"; }
if [ "$ML_IMAGE" = 1 ]; then
  img="$(image_of ml_image)"
  say "Pulling the ML image (CPU; ~2 GB): $img"
  "$ENGINE" pull "$img"
  ok "ML image ready"
elif [ "$WITH_ML" = 1 ]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu126"; kind="CUDA"
  else
    TORCH_INDEX="https://download.pytorch.org/whl/cpu"; kind="CPU"
  fi
  say "Installing the ML environment ($kind): ml-env/"
  uv venv --quiet --python 3.12 ml-env
  uv pip install --quiet --python ml-env/bin/python numpy scipy scikit-learn
  uv pip install --quiet --python ml-env/bin/python torch --index-url "$TORCH_INDEX"
  ok "ML environment ready"
fi

# 7) Lean 4 + Mathlib (optional) -------------------------------------------------------------------
if [ "$LEAN_IMAGE" = 1 ]; then
  img="$(image_of lean_image)"
  say "Pulling the Lean image (Lean + Mathlib + REPL; several GB): $img"
  "$ENGINE" pull "$img"
  ok "Lean image ready"
elif [ "$WITH_LEAN" = 1 ]; then
  if ! command -v lake >/dev/null && [ ! -x "$HOME/.elan/bin/lake" ]; then
    say "Installing elan (Lean version manager)"
    curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
  fi
  export PATH="$HOME/.elan/bin:$PATH"
  say "Downloading the Mathlib cache (several GB) and building the REPL"
  (cd lean && lake exe cache get && lake build REPL/repl)
  ok "Lean + Mathlib + REPL ready"
fi

# 8) Model provider ----------------------------------------------------------------------------
if command -v claude >/dev/null; then
  ok "claude CLI found (model provider; your key never leaves this machine)"
else
  warn "No model provider: install Claude Code and log in with 'claude', or configure another provider with QUAERA_LITELLM_MODELS (docs/installation.md)."
fi

# 9) `quaera` command ---------------------------------------------------------------------------
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/quaera" <<EOF
#!/bin/sh
exec uv run --quiet --project "$DIR" quaera "\$@"
EOF
chmod +x "$HOME/.local/bin/quaera"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "~/.local/bin is not on PATH; add it to your shell configuration." ;; esac
ok "'quaera' command: ~/.local/bin/quaera"

echo
say "Environment check (quaera doctor):"
if uv run --quiet quaera doctor; then
  echo; ok "Install complete. To start:  quaera serve   → http://127.0.0.1:8765"
else
  echo; warn "Install finished, but research cannot start until the ✗ lines above are fixed (usually: log in with 'claude')."
fi

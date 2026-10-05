#!/usr/bin/env bash
# QuaeraLabs tek komutla kurulum (Faz 4).
#
#   ./install.sh                     # çekirdek: Python ortamı, arayüz, `quaera` komutu
#   ./install.sh --with-ml           # + ML deney ortamı (numpy, scikit-learn, torch; NVIDIA varsa CUDA)
#   ./install.sh --with-lean         # + Lean 4 + Mathlib + REPL (~8 GB indirme/derleme)
#   ./install.sh --all               # hepsi
#
# Depo dışında çalıştırılırsa QUAERA_REPO adresinden (varsayılan aşağıda) --dir dizinine klonlar.
# Desteklenen: Linux (x86_64, arm64) ve Windows'ta WSL2. macOS henüz desteklenmiyor (bkz. docs/kurulum.md).
# Betik sudo çalıştırmaz; sistem paketi gerekiyorsa komutu gösterir ve durur.
set -euo pipefail

REPO_URL="${QUAERA_REPO:-https://github.com/quaeralabs/quaeralabs.git}"
DIR="${QUAERA_DIR:-$HOME/quaeralabs}"
WITH_ML=0 WITH_LEAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-ml) WITH_ML=1 ;;
    --with-lean) WITH_LEAN=1 ;;
    --all) WITH_ML=1; WITH_LEAN=1 ;;
    --dir) DIR="$2"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "bilinmeyen seçenek: $1" >&2; exit 2 ;;
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
    die "macOS henüz desteklenmiyor: ajanın yazdığı kodu yalıtan sandbox Linux'a özgü (bubblewrap). macOS arka ucu Faz 5'te; ayrıntı: docs/kurulum.md" ;;
  *)
    die "Desteklenmeyen sistem: $(uname -s). Windows'ta WSL2 içinde çalıştırın (docs/kurulum.md)." ;;
esac
ok "Platform: $PLATFORM ($(uname -m))"

# 2) Sistem araçları (sudo gerektirenler yalnızca gösterilir) --------------------------------------
pm_install() {
  if command -v apt-get >/dev/null; then echo "sudo apt-get install -y $*"
  elif command -v dnf >/dev/null; then echo "sudo dnf install -y $*"
  elif command -v pacman >/dev/null; then echo "sudo pacman -S --needed $*"
  elif command -v zypper >/dev/null; then echo "sudo zypper install $*"
  else echo "paket yöneticinizle kurun: $*"; fi
}
missing=()
for tool in git curl bwrap; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if [ ${#missing[@]} -gt 0 ]; then
  pkgs="${missing[*]}"; pkgs="${pkgs/bwrap/bubblewrap}"
  die "Eksik sistem paketleri: ${missing[*]}. Kurmak için: $(pm_install $pkgs) — sonra betiği yeniden çalıştırın."
fi
if ! bwrap --unshare-all --ro-bind / / true 2>/dev/null; then
  die "bubblewrap kurulu ama ayrıcalıksız ad alanları kapalı (kullanıcı ad alanı izni yok). Ayrıntı ve çözüm: docs/kurulum.md#bubblewrap"
fi
ok "bubblewrap çalışıyor (sandbox)"
if command -v systemd-run >/dev/null && systemd-run --user --scope --quiet true 2>/dev/null; then
  ok "systemd kullanıcı oturumu: bellek/CPU sınırları uygulanabilir"
else
  warn "systemd kullanıcı oturumu yok: deneylere bellek/CPU sınırı uygulanamaz (ADR 0010). WSL2'de /etc/wsl.conf içinde [boot] systemd=true önerilir."
fi

# 3) Kaynak kod -------------------------------------------------------------------------------
if [ -f pyproject.toml ] && grep -q 'name = "quaeralabs"' pyproject.toml 2>/dev/null; then
  DIR="$(pwd)"
  ok "Depo: $DIR"
elif [ -d "$DIR/.git" ]; then
  say "Mevcut depo güncelleniyor: $DIR"; git -C "$DIR" pull --ff-only
else
  say "Klonlanıyor: $REPO_URL → $DIR"; git clone --depth 1 "$REPO_URL" "$DIR"
fi
cd "$DIR"

# 4) uv ve Python ortamı -----------------------------------------------------------------------
if ! command -v uv >/dev/null; then
  say "uv kuruluyor (Astral resmi betiği, ~/.local/bin)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version | cut -d' ' -f2)"
say "Python bağımlılıkları kuruluyor"
uv sync --quiet
ok "Python ortamı hazır"

# 5) Arayüz -------------------------------------------------------------------------------------
# Sistemde Node 20+ yoksa (ör. Ubuntu 24.04 deposu 18 verir) resmi nodejs.org arşivinden taşınabilir bir Node
# indirilir, SHASUMS256 ile doğrulanır ve yalnızca arayüzü derlemek için kullanılır (sudo yok, sisteme kurulmaz).
node_ok() { command -v node >/dev/null && command -v npm >/dev/null && [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 20 ]; }
if [ -f web/dist/index.html ]; then
  ok "Arayüz derlenmiş (web/dist)"
else
  if ! node_ok; then
    case "$(uname -m)" in x86_64) NODE_ARCH=x64 ;; aarch64|arm64) NODE_ARCH=arm64 ;; *) NODE_ARCH="" ;; esac
    NODE_HOME="$HOME/.cache/quaeralabs/node"
    if [ -n "$NODE_ARCH" ] && [ ! -x "$NODE_HOME/bin/node" ]; then
      say "Node.js 20+ bulunamadı; arayüzü derlemek için taşınabilir Node 22 indiriliyor (~30 MB, ~/.cache/quaeralabs)"
      base="https://nodejs.org/dist/latest-v22.x"
      sums="$(curl -fsSL "$base/SHASUMS256.txt")"
      line="$(printf '%s\n' "$sums" | grep -E " node-v[0-9.]+-linux-${NODE_ARCH}\.tar\.gz$" | head -1)"
      file="${line##* }"; sum="${line%% *}"
      tmp="$(mktemp -d)"
      curl -fsSL "$base/$file" -o "$tmp/$file"
      echo "$sum  $tmp/$file" | sha256sum -c --quiet - || die "Node arşivinin sağlama toplamı tutmadı; indirme bozuk ya da değiştirilmiş."
      mkdir -p "$NODE_HOME" && tar -xzf "$tmp/$file" -C "$NODE_HOME" --strip-components=1 && rm -rf "$tmp"
    fi
    [ -x "$NODE_HOME/bin/node" ] && export PATH="$NODE_HOME/bin:$PATH"
  fi
  if node_ok; then
    say "Arayüz derleniyor (Node $(node --version))"
    (cd web && npm ci --no-audit --no-fund --silent && npm run build --silent)
    ok "Arayüz derlendi"
  else
    warn "Arayüz derlenemedi (Node 20+ yok ve indirilemedi). Komut satırı çalışır; Node kurup 'cd web && npm ci && npm run build' çalıştırın."
  fi
fi

# 6) ML deney ortamı (isteğe bağlı) ---------------------------------------------------------------
if [ "$WITH_ML" = 1 ]; then
  if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu126"; kind="CUDA"
  else
    TORCH_INDEX="https://download.pytorch.org/whl/cpu"; kind="CPU"
  fi
  say "ML ortamı kuruluyor ($kind): ml-env/"
  uv venv --quiet --python 3.12 ml-env
  uv pip install --quiet --python ml-env/bin/python numpy scipy scikit-learn
  uv pip install --quiet --python ml-env/bin/python torch --index-url "$TORCH_INDEX"
  ok "ML ortamı hazır"
fi

# 7) Lean 4 + Mathlib (isteğe bağlı) ---------------------------------------------------------------
if [ "$WITH_LEAN" = 1 ]; then
  if ! command -v lake >/dev/null && [ ! -x "$HOME/.elan/bin/lake" ]; then
    say "elan (Lean sürüm yöneticisi) kuruluyor"
    curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y --default-toolchain none
  fi
  export PATH="$HOME/.elan/bin:$PATH"
  say "Mathlib önbelleği indiriliyor (birkaç GB) ve REPL derleniyor"
  (cd lean && lake exe cache get && lake build REPL/repl)
  ok "Lean + Mathlib + REPL hazır"
fi

# 8) Model sağlayıcı ---------------------------------------------------------------------------
if command -v claude >/dev/null; then
  ok "claude CLI bulundu (model sağlayıcı; anahtarınız bu makineden çıkmaz)"
else
  warn "Model sağlayıcı yok: Claude Code'u kurup 'claude' ile giriş yapın ya da QUAERA_LITELLM_MODELS ile başka bir sağlayıcı tanımlayın (docs/kurulum.md)."
fi

# 9) `quaera` komutu ---------------------------------------------------------------------------
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/quaera" <<EOF
#!/bin/sh
exec uv run --quiet --project "$DIR" quaera "\$@"
EOF
chmod +x "$HOME/.local/bin/quaera"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "~/.local/bin PATH'te değil; kabuk yapılandırmanıza ekleyin." ;; esac
ok "'quaera' komutu: ~/.local/bin/quaera"

echo
say "Ortam denetimi (quaera doctor):"
if uv run --quiet quaera doctor; then
  echo; ok "Kurulum tamam. Başlamak için:  quaera serve   → http://127.0.0.1:8765"
else
  echo; warn "Kurulum bitti ama yukarıdaki ✗ satırları giderilmeden araştırma başlatılamaz (çoğunlukla: 'claude' ile giriş yapın)."
fi

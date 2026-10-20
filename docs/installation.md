# Installation

QuaeraLabs runs entirely on your own computer. Model calls are made with your account; your key never leaves your machine.

## One command

```bash
git clone https://github.com/mikailustuner/quaeralabs.git && cd quaeralabs
./install.sh            # core: Python environment, web UI, `quaera` command
./install.sh --all      # + ML experiment environment + Lean 4/Mathlib (~15 GB disk in total)
quaera serve            # → http://127.0.0.1:8765
```

The script does not run sudo. If a system package is missing, it prints the install command and stops.

| Option | What it installs | Disk | When you need it |
| --- | --- | --- | --- |
| (none) | uv, Python dependencies, web UI, `~/.local/bin/quaera`. If the system has no Node 20+, a portable Node 22 is downloaded from nodejs.org to build the UI (verified with SHASUMS256, `~/.cache/quaeralabs/node`) | ~400 MB | always |
| `--with-ml` | `ml-env/`: numpy, scipy, scikit-learn, torch (CUDA if an NVIDIA driver is present, otherwise CPU) | 1–6 GB | AI/ML research |
| `--with-lean` | elan, Lean 4 v4.34.1, Mathlib cache, Lean REPL | ~8 GB | mathematics research |
| `--lean-image` | the same, as a prebuilt container image (pulled, nothing is built) | ~8 GB | mathematics research without building Lean; always on macOS |
| `--ml-image` | the ML environment as a container image (CPU torch) | ~2 GB | AI/ML research without a host environment; always on macOS |

After installation, `quaera doctor` shows what is ready, in tiers: core (required, ✓/✗), AI/ML research, mathematics
research (Lean) and extras (✓/–). A missing research tier is not an error; that domain is simply off, and the web UI greys
it out with the reason.

## Without the repo: an installed package (AI/ML, Critic, audit)

For AI/ML research you do not need the repo or Lean. The package ships the rule files and the built web UI.

```bash
pipx install quaeralabs        # once published to PyPI (release workflow: .github/workflows/release.yml)
# until then, from a checkout:  cd web && npm ci && npm run build && cd .. && uv build --wheel && pipx install dist/*.whl
quaera setup ml                # creates ~/.quaera/ml-env (numpy, scipy, scikit-learn, torch; --no-torch to skip PyTorch)
quaera doctor
quaera serve
```

`tools/wheel_install_test.sh` checks this path in a clean container. It installs the wheel, runs doctor, runs `setup ml`,
runs an experiment inside the sandbox, and opens the web UI. For mathematics without a checkout, pull the Lean image
(`docker pull ghcr.io/mikailustuner/quaera-lean:v4.34.1`, see [Lean and ML images](#lean-and-ml-images)).

## Platforms

| Platform | Status | Note |
| --- | --- | --- |
| Linux x86_64 (Debian 12+, Ubuntu 22.04+, Fedora 39+) | Supported | Tested in clean Debian 13 and Ubuntu 24.04 containers (`tools/install_test.sh`). The container test runs with AppArmor disabled; the AppArmor namespace restriction on Ubuntu desktop is not measured by this test (see [bubblewrap](#bubblewrap)) |
| Windows 11 + WSL2 | Supported (untested) | WSL2 runs a real Linux kernel, so bubblewrap works. For memory limits, add `systemd=true` under `[boot]` in `/etc/wsl.conf` |
| Linux arm64 | Very likely works (untested) | Lean and torch arm64 packages are available |
| macOS (Apple Silicon, Intel) | Supported through containers (**not yet tested on a Mac**) | See [macOS](#macos) |

### macOS
Agent-written code runs in a sandbox with the network off and the home directory invisible (ADR 0002, 0009, 0018).
bubblewrap is Linux-only, so on macOS every sandboxed command runs in a Linux container, and Lean and the ML environment
come as [images](#lean-and-ml-images). You need a running container engine: Docker Desktop, OrbStack or Podman
(`podman machine init && podman machine start`). Then:

```bash
git clone https://github.com/mikailustuner/quaeralabs.git && cd quaeralabs
./install.sh --all      # on macOS this pulls the Lean and ML images instead of building them
```

The container engine shares your home and temporary directories with its Linux VM by default; the sandbox binds only a
fresh working directory into each container. This path has been built and tested on Linux with the same images and the
same container backend, not yet on a Mac; GPU experiments are not available on macOS.

## Sandbox backends

Agent-written code always runs in a sandbox. Two backends follow the same contract (`tests/test_sandbox_contract.py`,
ADR 0018): network off, home directory invisible, only the working directory writable, and limits on time, CPU, processes and memory.

| `QUAERA_SANDBOX` | Backend | When |
| --- | --- | --- |
| `auto` (default) | bubblewrap if it can create namespaces here, otherwise Docker/Podman | always the safe choice |
| `bwrap` | bubblewrap | Linux, WSL2 |
| `container` | Podman (preferred) or Docker (`QUAERA_CONTAINER_ENGINE`); base image `QUAERA_SANDBOX_IMAGE` (default `debian:13`) | Ubuntu with the AppArmor namespace restriction, or no bubblewrap |

There is no mode without a sandbox. If neither backend works, `quaera doctor` says so and research does not start.

## Lean on a slow disk

Lean maps most of the Mathlib build files (~6 GB) into memory when it loads `import Mathlib`. On a rotating disk (HDD)
with 16 GB of RAM this did not finish in 25 minutes in our measurement (improvement plan A4): random reads are slow and
pages are dropped and read again under memory pressure. A research run then stops with "Lean could not load
`import Mathlib`". `quaera doctor` shows how long the last load took and whether it failed. Fixes, cheapest first:
put `lean/.lake` (or the Docker data directory, for the Lean image) on an SSD, or run Lean on another machine.

One research process runs one Lean REPL for all its branches (`QUAERA_SHARED_LEAN=0` turns this off), and one-shot
builds run one at a time (`QUAERA_LEAN_SLOTS`, default 1), so parallel branches do not multiply the memory Lean needs.

## Lean and ML images

Two prebuilt images carry their own tools, so the host needs nothing but Docker or Podman (improvement plan A2):

| Image | Contents | Size | Replaces |
| --- | --- | --- | --- |
| `ghcr.io/mikailustuner/quaera-lean:v4.34.1` | Lean 4 v4.34.1, the Mathlib build cache, the Lean REPL (`docker/lean.Dockerfile`) | ~8 GB | `./install.sh --with-lean` |
| `ghcr.io/mikailustuner/quaera-ml:0.1` | Python 3.12, numpy, scipy, scikit-learn, CPU torch (`docker/ml.Dockerfile`) | ~2 GB | `ml-env` / `quaera setup ml` |

They run through the container sandbox like everything else: no network, read-only root, the host user's ID, and no
host directory except a fresh working directory (and the data directory for ML). Choose with `QUAERA_LEAN` and `QUAERA_ML`:
`auto` (default) uses the host environment on Linux when it is installed, otherwise the image if it has been pulled;
`host` and `image` force one. Other image names: `QUAERA_LEAN_IMAGE`, `QUAERA_ML_IMAGE`. GPU experiments need the host
environment or a remote runner; the ML image is CPU only. `quaera prove` (your own Lean project) needs Lean on the host.

The images are built and published by `.github/workflows/images.yml` for linux/amd64 and linux/arm64. To build one yourself:
`docker build -f docker/lean.Dockerfile -t quaera-lean lean` and `QUAERA_LEAN_IMAGE=quaera-lean`.

## bubblewrap

If `bwrap --unshare-all --ro-bind / / --proc /proc --dev /dev true` fails, your system does not allow unprivileged user
namespaces. Instead of the fixes below you can install Podman or Docker; `auto` then uses the container backend.
- Ubuntu 24.04+: AppArmor restriction. `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` is a temporary fix; the permanent fix is to add an AppArmor profile for bwrap.
- Some hardened kernels: `sudo sysctl -w kernel.unprivileged_userns_clone=1`.
- Inside a Docker container: the container must be allowed to create namespaces (e.g. `--security-opt seccomp=unconfined --cap-add SYS_ADMIN`). This setup is not recommended.

## Model provider
Any of these works on its own; several together give the cross-model checks (Critic, Verifier, parallel lanes, Discovery ideation).

- **CLIs** (detected automatically): [Claude Code](https://claude.com/claude-code) (`claude`, log in once), Codex CLI, OpenCode CLI, Antigravity CLI.
- **API keys** (Anthropic, OpenAI, Google Gemini, OpenRouter) and **local models** (any OpenAI-compatible server: Ollama, vLLM, LM Studio),
  through the provider registry. Add them in the web UI (Settings → API-key providers) or on the command line:

```bash
uv sync --extra providers                                  # LiteLLM, used for every API provider
quaera providers add openrouter --kind openrouter --model cheap=openai/gpt-4o-mini --model best=anthropic/claude-…
quaera providers key openrouter                            # asks for the key (hidden); stored in ~/.quaera/secrets.env, mode 600
quaera providers add local --kind openai-compatible --api-base http://127.0.0.1:11434/v1 --family qwen --model cheap=qwen3:8b --free
quaera providers test local
quaera route critic openrouter                             # a role on a specific provider (the cross-model rule still applies)
quaera route engineer --ladder local@cheap anthropic@balanced anthropic@best   # start cheap, climb on failure
```

The registry lives in `~/.quaera/providers.json`; keys come from environment variables or `~/.quaera/secrets.env` and are never written
into a project, an event, a report or an API response. Every call stays under the budget cap: a model with no known price is refused
unless you give its price (`--price model=in,out` USD per million tokens) or mark a local model `--free`. Subscription CLIs and local
models report no cost, so bound them with `--max-calls` / `--max-hours` on `quaera ask` (or the matching fields in the web UI).
`QUAERA_LITELLM_MODELS='{"cheap": "openai/…", …}'` still works for a single API provider.

## Literature sources
The literature step searches arXiv and OpenAlex (titles and abstracts) without any key. Semantic Scholar is searched too
when `S2_API_KEY` is set (a free key from semanticscholar.org; put it in `~/.quaera/secrets.env`); without a key its shared
rate limit refuses most requests, so it is skipped. Every cited source is still verified against arXiv, Crossref or
DataCite before it reaches a report. Search results are cached for 7 days in `~/.quaera/cache/literature.db`
(`QUAERA_CACHE=off` turns this off); verification is never cached.

## Uninstall
```bash
rm -rf ~/quaeralabs ~/.local/bin/quaera   # code and command
rm -rf ~/.quaera                          # projects, memory, signing key (careful: deletes your research)
```

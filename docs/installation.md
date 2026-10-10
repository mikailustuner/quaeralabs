# Installation

QuaeraLabs runs entirely on your own computer. Model calls are made with your account; your key never leaves your machine.

## One command

```bash
git clone https://github.com/quaeralabs/quaeralabs.git && cd quaeralabs
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

After installation, `quaera doctor` shows what is ready. Required components are marked ✓/✗, optional ones ✓/–.

## Platforms

| Platform | Status | Note |
| --- | --- | --- |
| Linux x86_64 (Debian 12+, Ubuntu 22.04+, Fedora 39+) | Supported | Tested in clean Debian 13 and Ubuntu 24.04 containers (`tools/install_test.sh`). The container test runs with AppArmor disabled; the AppArmor namespace restriction on Ubuntu desktop is not measured by this test (see [bubblewrap](#bubblewrap)) |
| Windows 11 + WSL2 | Supported (untested) | WSL2 runs a real Linux kernel, so bubblewrap works. For memory limits, add `systemd=true` under `[boot]` in `/etc/wsl.conf` |
| Linux arm64 | Very likely works (untested) | Lean and torch arm64 packages are available |
| macOS | **Not supported yet** | See below |

### Why no macOS yet?
Experiment code and Lean files written by agents run in a sandbox with the network off and the home directory invisible (ADR 0002, 0009). This sandbox relies on bubblewrap, which uses Linux kernel namespaces and has no macOS equivalent. There are two candidate backends for macOS: `sandbox-exec` profiles and a lightweight Linux virtual machine/container. Neither will be released until it has been developed and tested on a Mac. Running without a sandbox is not an option: a script written by an agent could access files on your computer. The target is Phase 5. Until then, macOS users can use a Linux virtual machine (e.g. Ubuntu on UTM or OrbStack).

## bubblewrap

If `bwrap --unshare-all --ro-bind / / true` fails, your system does not allow unprivileged user namespaces.
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

## Uninstall
```bash
rm -rf ~/quaeralabs ~/.local/bin/quaera   # code and command
rm -rf ~/.quaera                          # projects, memory, signing key (careful: deletes your research)
```

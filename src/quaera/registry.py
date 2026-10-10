"""Provider registry (capacity plan P1, P2, S1): API-key providers, role routing and escalation ladders.

Lives in `~/.quaera/providers.json` (never in the repo):

    {"providers": [
        {"id": "openrouter", "kind": "openrouter", "models": {"cheap": "…", "balanced": "…", "best": "…"},
         "price": {"<model>": [in, out]}, "limits": {"concurrent": 4}, "enabled": true},
        {"id": "local", "kind": "openai-compatible", "apiBase": "http://127.0.0.1:11434/v1", "family": "qwen",
         "models": {"cheap": "qwen3:8b"}, "free": true}],
     "routing": {"critic": "openrouter", "literature": "local"},
     "ladders": {"engineer": ["local@cheap", "anthropic@balanced", "anthropic@best"]}}

Kinds: anthropic, openai, google, openrouter, openai-compatible (vLLM, Ollama, LM Studio). Every kind goes through
LiteLLM. API keys come from an environment variable (`apiKeyEnv`, a default per kind) or from `~/.quaera/secrets.env`
(mode 600) written by the settings page. Keys are never written to events, blobs, reports or API responses.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

KINDS = {
    #  kind: (LiteLLM prefix, default key env, default family)
    "anthropic": ("anthropic/", "ANTHROPIC_API_KEY", "anthropic"),
    "openai": ("openai/", "OPENAI_API_KEY", "openai"),
    "google": ("gemini/", "GEMINI_API_KEY", "google"),
    "openrouter": ("openrouter/", "OPENROUTER_API_KEY", None),
    "openai-compatible": ("openai/", None, "local"),
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED_IDS = {"anthropic", "openai", "opencode", "google", "scripted", "claude", "codex", "agy"}
PROFILES = ("cheap", "balanced", "best")


def quaera_home() -> Path:
    return Path(os.environ.get("QUAERA_HOME", Path.home() / ".quaera"))


def registry_path() -> Path:
    return quaera_home() / "providers.json"


def secrets_path() -> Path:
    return quaera_home() / "secrets.env"


def load() -> dict:
    p = registry_path()
    if not p.exists():
        return {"providers": [], "routing": {}, "ladders": {}}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {"providers": data.get("providers") or [], "routing": data.get("routing") or {}, "ladders": data.get("ladders") or {}}


def save(data: dict) -> None:
    errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def key_env(entry: dict) -> str | None:
    return entry.get("apiKeyEnv") or KINDS.get(entry.get("kind", ""), (None, None, None))[1] or \
        (f"QUAERA_KEY_{entry['id'].upper().replace('-', '_')}" if entry.get("kind") == "openai-compatible" and entry.get("needsKey") else None)


def validate(data: dict) -> list[str]:
    errors, ids = [], set()
    for e in data.get("providers", []):
        where = f"provider {e.get('id', '?')}"
        if not ID_RE.match(str(e.get("id", ""))):
            errors.append(f"{where}: id must be lowercase letters, digits and dashes (≤32)")
        elif e["id"] in RESERVED_IDS:
            errors.append(f"{where}: id is reserved for a built-in provider")
        elif e["id"] in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(e.get("id"))
        if e.get("kind") not in KINDS:
            errors.append(f"{where}: kind must be one of {', '.join(KINDS)}")
        models = e.get("models")
        if not isinstance(models, dict) or not models or not all(k in PROFILES and isinstance(v, str) and v.strip() for k, v in models.items()):
            errors.append(f"{where}: models must map cheap/balanced/best to model names")
        if e.get("kind") == "openai-compatible" and not str(e.get("apiBase", "")).startswith(("http://", "https://")):
            errors.append(f"{where}: an OpenAI-compatible server needs apiBase (http://…)")
        for m, pr in (e.get("price") or {}).items():
            if not (isinstance(pr, list) and len(pr) == 2 and all(isinstance(x, (int, float)) and x >= 0 for x in pr)):
                errors.append(f"{where}: price for {m} must be [input, output] USD per million tokens")
        conc = (e.get("limits") or {}).get("concurrent")
        if conc is not None and not (isinstance(conc, int) and 1 <= conc <= 64):
            errors.append(f"{where}: limits.concurrent must be 1–64")
    for role, route in (data.get("routing") or {}).items():
        if not isinstance(route, str):
            errors.append(f"routing {role}: route must be a provider id")
    for role, ladder in (data.get("ladders") or {}).items():
        if not (isinstance(ladder, list) and all(isinstance(x, str) and x for x in ladder)):
            errors.append(f"ladders {role}: a list of 'provider@profile' strings")
        elif any(x.partition("@")[2] not in ("", *PROFILES) for x in ladder):
            errors.append(f"ladders {role}: profile must be cheap, balanced or best")
    return errors


# --- secrets -----------------------------------------------------------------------------------

def load_secrets() -> None:
    """Puts keys from ~/.quaera/secrets.env into the environment (an existing environment variable wins)."""
    p = secrets_path()
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2)


def set_secret(name: str, value: str) -> None:
    if not re.match(r"^[A-Z][A-Z0-9_]*$", name):
        raise ValueError("key variable name must be UPPER_CASE")
    p = secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [l for l in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []) if not l.startswith(f"{name}=")]
    if value:
        lines.append(f"{name}={value.strip()}")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.chmod(p, 0o600)
    if value:
        os.environ[name] = value.strip()
    else:
        os.environ.pop(name, None)


def has_key(entry: dict) -> bool:
    env = key_env(entry)
    return env is None or bool(os.environ.get(env))


# --- building providers ---------------------------------------------------------------------------

def family_of(entry: dict) -> str:
    if entry.get("family"):
        return str(entry["family"])
    prefix, _env, fam = KINDS[entry["kind"]]
    if fam:
        return fam
    first = next(iter(entry["models"].values()))       # openrouter: "anthropic/claude-…" → anthropic
    return first.split("/", 1)[0] if "/" in first else "openrouter"


def litellm_name(entry: dict, model: str) -> str:
    prefix = KINDS[entry["kind"]][0]
    return model if model.startswith(prefix) else prefix + model


def build(entry: dict, mock_response: str | None = None):
    from .gateway import LiteLLMProvider
    models = {p: litellm_name(entry, m) for p, m in entry["models"].items()}
    prices = {litellm_name(entry, m): v for m, v in (entry.get("price") or {}).items()}
    return LiteLLMProvider(models, mock_response=mock_response, family=family_of(entry), api_key_env=key_env(entry),
                           api_base=entry.get("apiBase"), prices=prices, free=bool(entry.get("free")),
                           concurrency=(entry.get("limits") or {}).get("concurrent"),
                           billing="local" if entry.get("free") else "metered", id=entry["id"])


def describe(entry: dict) -> dict:
    """Entry as shown on the settings page; the key itself is never returned, only whether it is set."""
    env = key_env(entry)
    ready = bool(entry.get("enabled", True)) and has_key(entry)
    return {"id": entry["id"], "name": entry.get("name") or entry["id"], "kind": entry["kind"], "family": family_of(entry),
            "models": entry["models"], "apiBase": entry.get("apiBase"), "keyEnv": env, "keySet": has_key(entry),
            "price": entry.get("price") or {}, "free": bool(entry.get("free")), "limits": entry.get("limits") or {},
            "enabled": bool(entry.get("enabled", True)), "ready": ready, "registry": True,
            "billing": "local (free)" if entry.get("free") else "metered (budget-capped, API price)",
            "model": entry["models"].get("balanced") or next(iter(entry["models"].values())),
            "note": "" if ready else (f"set {env}" if not has_key(entry) else "disabled")}

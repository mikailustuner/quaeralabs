"""Model gateway (ADR 0003).

All model calls go through here. The gateway:
- picks a model per role (costProfile in the agent definition),
- enforces the cross-model rule (mustDifferFrom),
- computes the worst-case cost BEFORE every call; it does not make a call that would exceed the cap,
- writes the actual cost to the event log; emits a warning at 80%.

Budget guarantee: estimate = (input token upper bound × input price) + (output token limit × output price).
The output token limit is passed to the provider, so the actual cost cannot exceed the estimate.

Capacity plan (docs/capacity-plan.md) additions:
- providers are keyed by a route id (e.g. "anthropic", "openrouter", "local-qwen"); the cross-model rule compares the
  provider's *family*, so two providers of the same family may serve different roles (P2),
- routing (role -> route) and ladders (role -> [route@profile, ...], escalated on failure) (P2, S1),
- the budget has four dimensions: USD, calls, tokens and wall-clock; subscription CLIs report $0, so calls and time
  are what bound them (P4),
- API providers carry their own accounting constants (no CLI scaffolding overhead, exact output limit) (P3),
- an optional completion cache: an identical call is never paid twice (C1),
- per-provider concurrency limits (S4) and a `sample` index for best-of-N sampling (K5).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

# USD / million tokens (input, output). Source: Anthropic price table (claude-api reference, 2026-09-25):
# Opus 5.5 4/20, Sonnet 5.5 2/10, Haiku 4.5 1/5. A cache write costs ~1.25x the input price.
# Used for the budget estimate; the provider reports the actual cost and that is what goes into the event log.
PRICES = {
    "haiku": (1.0, 5.0),
    "sonnet": (2.0, 10.0),
    "opus": (4.0, 20.0),
    "scripted": (0.0, 0.0),
}
CACHE_WRITE = 1.25
# The `claude` CLI adds its own scaffolding to every call and writes it to the cache (~5k tokens measured in Phase 2).
PROVIDER_OVERHEAD_TOKENS = 8000
# CLAUDE_CODE_MAX_OUTPUT_TOKENS is not a hard limit (2500 requested, 2797 produced was seen): assume 2x for output.
OUTPUT_SAFETY = 2.0

# Cost profile -> provider-specific model alias
PROFILE_MODELS = {
    "anthropic": {"cheap": "haiku", "balanced": "sonnet", "best": "opus"},
    "scripted": {"cheap": "scripted", "balanced": "scripted", "best": "scripted"},
}


class BudgetExceeded(Exception):
    pass


class ModelError(Exception):
    def __init__(self, message: str, cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = cost_usd


@dataclass
class Completion:
    text: str
    model: str
    family: str
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False


class Provider(Protocol):
    family: str

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float) -> Completion: ...


def estimate_input_tokens(*texts: str) -> int:
    # Conservative upper bound: ~2.5 characters/token for Turkish text and code.
    return int(sum(len(t) for t in texts) / 2.5)


def worst_case_cost(model: str, system: str, prompt: str, max_output_tokens: int,
                    price: tuple[float, float] | None = None, overhead: int = PROVIDER_OVERHEAD_TOKENS,
                    safety: float = OUTPUT_SAFETY, cache_write: float = CACHE_WRITE) -> float:
    """`overhead`, `safety`, `cache_write`: the provider's accounting constants (CLI defaults; API providers are exact)."""
    price_in, price_out = price or PRICES[model]
    tokens_in = (estimate_input_tokens(system, prompt) + overhead) * cache_write
    return (tokens_in * price_in + max_output_tokens * safety * price_out) / 1_000_000


# Extended thinking is counted against the CLI's output-token limit. Without headroom a high-effort answer is cut off,
# the CLI continues it in a new turn and `result` then holds only the last fragment (seen as "invalid JSON").
THINKING_HEADROOM = {"low": 2000, "medium": 4000, "high": 8000, "xhigh": 16000, "max": 32000}


class ClaudeCLIProvider:
    """Anthropic models through the user's logged-in `claude` CLI.

    Tools are off, the session is not saved, user settings and MCP servers are not loaded.
    Output tokens are capped with CLAUDE_CODE_MAX_OUTPUT_TOKENS, spending with --max-budget-usd.
    """

    family = "anthropic"

    def __init__(self, binary: str = "claude", timeout_s: int = 900):
        self.binary = binary
        self.timeout_s = timeout_s

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        limit = max_output_tokens + THINKING_HEADROOM.get(effort or "", 0)
        env = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(limit))
        cmd = [
            self.binary, "-p", "--output-format", "json", "--model", model,
            "--tools", "", "--system-prompt", system, "--no-session-persistence",
            "--setting-sources", "", "--strict-mcp-config", "--max-budget-usd", f"{budget_usd:.4f}",
        ] + (["--effort", effort] if effort else [])
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout_s, env=env)
        except subprocess.TimeoutExpired as exc:
            raise ModelError(f"model call did not finish within {self.timeout_s} s") from exc
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ModelError(f"claude CLI returned invalid output: {proc.stdout[:300]} {proc.stderr[:300]}") from exc
        if data.get("is_error") or data.get("subtype", "success") != "success":
            raise ModelError(f"claude CLI error: {data.get('result') or data.get('subtype')}",
                             float(data.get("total_cost_usd", 0.0)))
        if int(data.get("num_turns") or 1) > 1:       # no tools, so more than one turn means the answer was continued
            raise ModelError(f"claude CLI answer was cut off at the {limit}-token output limit",
                             float(data.get("total_cost_usd", 0.0)))
        usage = data.get("usage", {})
        resolved = next(iter(data.get("modelUsage", {})), model)
        return Completion(
            text=data.get("result", ""),
            model=f"anthropic/{resolved}",
            family=self.family,
            cost_usd=float(data.get("total_cost_usd", 0.0)),
            input_tokens=int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )


class LiteLLMProvider:
    """LiteLLM adapter for API-key providers (Anthropic, OpenAI, Google, OpenRouter, local OpenAI-compatible servers).

    `models`: cost profile -> LiteLLM model name, e.g. {"cheap": "openai/<model>", "balanced": ..., "best": ...}.
    The family is the model name's prefix (openai/…, gemini/…) unless given. Prices come from `prices` (USD per million
    tokens, set in the provider registry) or LiteLLM's cost table; a model with neither cannot be estimated, so the call
    is refused (budget guarantee). A local server marked free has price (0, 0).
    API calls are exact: no CLI scaffolding overhead and the output limit is enforced by the API (P3).
    `mock_response` is for tests only: no network call is made.
    """

    overhead_tokens = 0
    output_safety = 1.0
    supports_temperature = True
    supports_json_mode = True
    RETRYABLE = ("RateLimitError", "APIConnectionError", "ServiceUnavailableError", "InternalServerError", "Timeout",
                 "APIError", "BadGatewayError")

    def __init__(self, models: dict[str, str], mock_response: str | None = None, timeout_s: int = 600, *,
                 family: str | None = None, api_key_env: str | None = None, api_base: str | None = None,
                 prices: dict[str, list[float]] | None = None, free: bool = False, concurrency: int | None = None,
                 retries: int = 4, billing: str = "metered", id: str | None = None):
        import litellm  # optional dependency: uv sync --extra providers
        self.litellm = litellm
        self.models = models
        first = next(iter(models.values()))
        self.family = family or ({"gemini": "google", "openrouter": first.split("/")[1] if first.count("/") >= 2 else "openrouter"}
                                 .get(first.split("/", 1)[0], first.split("/", 1)[0]))
        self.mock_response = mock_response
        self.timeout_s = timeout_s
        self.api_key_env, self.api_base = api_key_env, api_base
        self.prices = {k: tuple(v) for k, v in (prices or {}).items()}
        self.free, self.concurrency, self.retries, self.billing, self.id = free, concurrency, retries, billing, id
        # Anthropic charges a cache write at ~1.25x; other providers cache prefixes for free (or not at all).
        self.cache_write = CACHE_WRITE if first.startswith("anthropic/") else 1.0

    def model_for(self, profile: str) -> str:
        return self.models.get(profile) or next(iter(self.models.values()))

    def _supports(self, fn: str, model: str) -> bool:
        try:
            return bool(getattr(self.litellm, fn)(model=model))
        except Exception:   # unknown model in LiteLLM's table: treat the feature as unsupported
            return False

    def _supports_reasoning(self, model: str) -> bool:
        return self._supports("supports_reasoning", model)

    def price(self, model: str) -> tuple[float, float]:
        if model in self.prices:
            return self.prices[model]
        if self.free:
            return (0.0, 0.0)
        info = self.litellm.model_cost.get(model) or self.litellm.model_cost.get(model.split("/", 1)[-1])
        if not info or "input_cost_per_token" not in info:
            raise BudgetExceeded(f"price unknown for {model}; call not made because the budget cap cannot be guaranteed "
                                 "(set a price for it in the provider registry)")
        return info["input_cost_per_token"] * 1e6, info["output_cost_per_token"] * 1e6

    def _messages(self, model: str, system: str, prompt: str) -> list[dict]:
        # Prompt caching (P3): the system prompt is the stable prefix; Anthropic needs an explicit cache marker,
        # OpenAI/Gemini cache an identical prefix automatically.
        if model.startswith("anthropic/") and len(system) > 4000:
            sys_msg = {"role": "system", "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
        else:
            sys_msg = {"role": "system", "content": system}
        return [sys_msg, {"role": "user", "content": prompt}]

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None, json_mode: bool = False, temperature: float | None = None) -> Completion:
        import random
        import time as _time
        extra: dict = {"mock_response": self.mock_response} if self.mock_response else {}
        if effort and self._supports_reasoning(model):   # non-reasoning models reject this parameter
            extra["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(effort, effort)   # LiteLLM: low/medium/high
        if json_mode and self._supports("supports_response_schema", model):
            extra["response_format"] = {"type": "json_object"}
        if temperature is not None and "reasoning_effort" not in extra:
            extra["temperature"] = temperature
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise ModelError(f"{self.api_key_env} is not set; add the key in Settings → Providers")
            extra["api_key"] = key
        if self.api_base:
            extra["api_base"] = self.api_base
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.litellm.completion(model=model, messages=self._messages(model, system, prompt),
                                               max_tokens=max_output_tokens, timeout=self.timeout_s, **extra)
                break
            except Exception as exc:  # provider errors are turned into a single ModelError type
                last = exc
                if type(exc).__name__ not in self.RETRYABLE or attempt == self.retries:
                    raise ModelError(f"{model}: {str(exc)[:300]}") from exc
                _time.sleep(min(30.0, 2 ** attempt) * (0.5 + random.random() / 2))   # exponential backoff with jitter
        else:  # pragma: no cover - the loop either breaks or raises
            raise ModelError(f"{model}: {last}")
        usage = resp.usage
        p_in, p_out = self.price(model)
        cost = (usage.prompt_tokens * p_in + usage.completion_tokens * p_out) / 1e6
        return Completion(text=resp.choices[0].message.content or "", model=model, family=self.family, cost_usd=cost,
                          input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)


class ScriptedProvider:
    """For tests: returns prewritten answers in order or from a function."""

    def __init__(self, responder: Callable[[str, str], str], family: str = "scripted", cost_per_call: float = 0.0):
        self.responder = responder
        self.family = family
        self.cost_per_call = cost_per_call
        self.calls: list[dict] = []

    def price(self, model: str) -> tuple[float, float]:
        return PRICES.get(model, (0.0, 0.0))     # a test family without a price table costs nothing to estimate

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        self.calls.append({"system": system, "prompt": prompt, "model": model, "effort": effort})
        return Completion(text=self.responder(system, prompt), model=f"{self.family}/{model}", family=self.family,
                          cost_usd=min(self.cost_per_call, budget_usd))


EFFORT_FACTOR = {"low": 1.0, "medium": 1.25, "high": 1.5, "xhigh": 2.0, "max": 2.5}
SAMPLE_TEMPERATURE = 0.8      # best-of-N samples (K5) on providers that accept a temperature


@dataclass
class Limits:
    """Budget dimensions besides USD (P4). None = unlimited. `deadline` is a Unix timestamp."""
    calls: int | None = None
    tokens: int | None = None
    deadline: float | None = None


@dataclass
class Gateway:
    providers: dict[str, Provider]
    cap_usd: float
    agent_specs: dict[str, dict]
    record: Callable[[str, dict], None] = lambda kind, payload: None
    spent_usd: float = 0.0
    warned: bool = False
    role_families: dict[str, str] = field(default_factory=dict)   # role -> family it first ran on (cross-model rule)
    profile_overrides: dict[str, str] = field(default_factory=dict)
    effort_overrides: dict[str, str] | None = None
    calibration: dict[str, float] = field(default_factory=dict)   # model -> highest observed actual/estimate ratio (if >1)
    # Parallel calls (e.g. two Hypothesis agents at once): each call reserves its own upper bound before starting; only
    # that bound is given to the provider. The reserved bounds cannot sum past the remaining budget, so the cap holds in parallel too.
    reserved_usd: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    routing: dict[str, str] = field(default_factory=dict)          # role -> route id (P2)
    ladders: dict[str, list[str]] = field(default_factory=dict)    # role -> ["route@profile", ...] (S1)
    limits: Limits = field(default_factory=Limits)                 # calls / tokens / wall-clock (P4)
    calls: int = 0
    tokens: int = 0
    reserved_calls: int = 0
    cache: object | None = None                                    # cache.CompletionCache (C1)
    semaphores: dict[str, threading.Semaphore] = field(default_factory=dict, repr=False)   # per route (S4)

    def __post_init__(self):
        for key, prov in self.providers.items():
            n = getattr(prov, "concurrency", None)
            if n:
                self.semaphores[key] = threading.Semaphore(int(n))

    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def family_of(self, key: str | None) -> str | None:
        """The model family of a route (a route id equals its family for the built-in CLI providers)."""
        if key is None or key not in self.providers:
            return key
        return getattr(self.providers[key], "family", key) or key

    def families(self) -> list[str]:
        return list(dict.fromkeys(self.family_of(k) for k in self.providers))

    def exhausted(self) -> str | None:
        """The budget dimension that is used up, or None."""
        import time as _time
        lim = self.limits
        if self.spent_usd >= self.cap_usd - 1e-9:
            return f"USD cap ${self.cap_usd:.2f} reached"
        if lim.calls is not None and self.calls + self.reserved_calls >= lim.calls:
            return f"call budget ({lim.calls} model calls) reached"
        if lim.tokens is not None and self.tokens >= lim.tokens:
            return f"token budget ({lim.tokens} tokens) reached"
        if lim.deadline is not None and _time.time() >= lim.deadline:
            return "time budget reached"
        return None

    def share_cap(self, share: float):
        """A stage's budget share as a predicate: True while the stage may still spend (USD and calls)."""
        usd_cap = self.spent_usd + share * self.remaining()
        call_cap = None if self.limits.calls is None else self.calls + share * max(0, self.limits.calls - self.calls)

        def ok() -> bool:
            return (self.spent_usd < usd_cap - 1e-12 and (call_cap is None or self.calls < call_cap)
                    and self.exhausted() is None)
        ok.usd_cap = usd_cap   # type: ignore[attr-defined]
        return ok

    def _avoid(self, role: str) -> set[str]:
        avoid = {self.role_families.get(r) for r in self.agent_specs[role]["model"].get("mustDifferFrom", [])}
        avoid.discard(None)
        return avoid

    def _family_for(self, role: str) -> tuple[str, bool]:
        """Picks a route for the role. Returns: (route id, whether cross-model was achieved)."""
        avoid = self._avoid(role)
        keys = list(self.providers)
        preferred = self.routing.get(role)
        if preferred in self.providers and self.family_of(preferred) not in avoid:
            return preferred, bool(avoid)
        for key in keys:
            if self.family_of(key) not in avoid:
                return key, bool(avoid)
        return keys[0], False

    def alternative(self, role: str, failed: str | None) -> str | None:
        """Another route to retry a role on after `failed` kept failing; prefers another family and keeps mustDifferFrom."""
        avoid = self._avoid(role)
        failed_family = self.family_of(failed) if failed in self.providers else failed
        others = [k for k in self.providers if k != failed]
        return (next((k for k in others if self.family_of(k) != failed_family and self.family_of(k) not in avoid), None)
                or next((k for k in others if self.family_of(k) not in avoid), None) or (others[0] if others else None))

    def ladder_step(self, role: str, rung: int) -> tuple[str, str | None] | None:
        """(route, profile) for rung `rung` of the role's ladder (S1); the top rung repeats. None without a ladder."""
        ladder = [x for x in self.ladders.get(role, []) if x.partition("@")[0] in self.providers]
        if not ladder:
            return None
        key, _, profile = ladder[min(max(rung, 0), len(ladder) - 1)].partition("@")
        return key, profile or None

    def call(self, role: str, system: str, prompt: str, max_output_tokens: int = 8000,
             family: str | None = None, *, rung: int | None = None, profile: str | None = None,
             json_mode: bool = False, sample: int | None = None) -> tuple[Completion, bool]:
        """`family`: a specific route (so a second parallel instance of the same role runs on a different model).
        `rung`: climb the role's ladder (S1). `profile`: override the cost profile (e.g. a cheap JSON repair).
        `json_mode`: ask API providers for a JSON object. `sample`: best-of-N index (cache key + temperature)."""
        import time as _time
        step = self.ladder_step(role, rung) if rung is not None and family is None else None
        if step:
            key, cross = step[0], bool(self._avoid(role))
            profile = profile or step[1]
        else:
            key, cross = (family, False) if family in self.providers else self._family_for(role)
        provider = self.providers[key]
        fam = self.family_of(key)
        profile = profile or self.profile_overrides.get(role) or self.agent_specs[role]["model"]["costProfile"]
        model = provider.model_for(profile) if hasattr(provider, "model_for") else PROFILE_MODELS.get(fam, {}).get(profile, profile)
        price = provider.price(model) if hasattr(provider, "price") else None
        effort = (self.effort_overrides or {}).get(role) or self.agent_specs[role]["model"].get("effort")
        effort = None if effort == "default" else effort        # "default": provider default (baseline for measurement)
        cache_key = None
        if self.cache is not None:
            cache_key = self.cache.key(key, model, effort, system, prompt, max_output_tokens, sample)
            hit = self.cache.get(cache_key)
            if hit is not None:
                completion = Completion(**{**hit, "cost_usd": 0.0, "cached": True})
                self.role_families.setdefault(role, fam)
                self.record("model.call", {"role": role, "model": completion.model, "family": fam, "provider": key,
                                           "costUsd": 0.0, "estimateUsd": 0.0, "spentUsd": round(self.spent_usd, 6),
                                           "capUsd": self.cap_usd, "inputTokens": completion.input_tokens,
                                           "outputTokens": completion.output_tokens, "effort": effort, "cached": True,
                                           "billing": getattr(provider, "billing", "metered")})
                return completion, cross
        # Thinking tokens are billed as output: at high effort the worst-case estimate is scaled up.
        # (The hard cap is still the remaining budget given to the provider; this factor only makes early stopping right.)
        estimate = (worst_case_cost(model, system, prompt, max_output_tokens, price,
                                    getattr(provider, "overhead_tokens", PROVIDER_OVERHEAD_TOKENS),
                                    getattr(provider, "output_safety", OUTPUT_SAFETY),
                                    getattr(provider, "cache_write", CACHE_WRITE))
                    * EFFORT_FACTOR.get(effort, 1.0) * self.calibration.get(model, 1.0))
        with self.lock:
            available = self.cap_usd - self.spent_usd - self.reserved_usd
            dim = None
            if self.limits.calls is not None and self.calls + self.reserved_calls >= self.limits.calls:
                dim = f"call budget ({self.limits.calls} model calls) reached"
            elif self.limits.tokens is not None and self.tokens >= self.limits.tokens:
                dim = f"token budget ({self.limits.tokens} tokens) reached"
            elif self.limits.deadline is not None and _time.time() >= self.limits.deadline:
                dim = "time budget reached"
            if dim or estimate > available:
                self.record("budget.blocked", {"role": role, "model": model, "estimateUsd": round(estimate, 4),
                                               "spentUsd": round(self.spent_usd, 4), "capUsd": self.cap_usd,
                                               **({"dimension": dim} if dim else {})})
                raise BudgetExceeded(dim or
                    f"{role} call may cost up to ${estimate:.3f} in the worst case; remaining budget ${max(0.0, available):.3f}"
                )
            # Estimate + half the remaining margin: even if the estimate is wrong the provider cannot exceed this; the other half stays for parallel calls.
            limit = estimate + (available - estimate) / 2
            self.reserved_usd += limit
            self.reserved_calls += 1
        kwargs: dict = {}
        if json_mode and getattr(provider, "supports_json_mode", False):
            kwargs["json_mode"] = True
        if sample and getattr(provider, "supports_temperature", False):
            kwargs["temperature"] = SAMPLE_TEMPERATURE
        sem = self.semaphores.get(key)
        try:
            if sem:
                sem.acquire()
            try:
                completion = provider.complete(model, system, prompt, max_output_tokens, budget_usd=limit, effort=effort, **kwargs)
            finally:
                if sem:
                    sem.release()
        except ModelError as exc:
            with self.lock:
                self.reserved_usd -= limit
                self.reserved_calls -= 1
                self.spent_usd += exc.cost_usd
                self.calls += 1
            self.record("model.error", {"role": role, "model": model, "provider": key, "error": str(exc)[:300],
                                        "costUsd": round(exc.cost_usd, 6), "spentUsd": round(self.spent_usd, 6)})
            raise
        except BaseException:
            with self.lock:
                self.reserved_usd -= limit
                self.reserved_calls -= 1
            raise
        with self.lock:
            self.reserved_usd -= limit
            self.reserved_calls -= 1
            self.spent_usd += completion.cost_usd
            self.calls += 1
            self.tokens += completion.input_tokens + completion.output_tokens
        self.role_families.setdefault(role, fam)
        if estimate > 0 and completion.cost_usd > estimate:
            # Estimate exceeded: scale later estimates for this model by the observed ratio (+10%) and record it.
            ratio = completion.cost_usd / estimate * 1.1 * self.calibration.get(model, 1.0)
            self.calibration[model] = max(self.calibration.get(model, 1.0), ratio)
            self.record("budget.estimate_exceeded", {"role": role, "model": model, "costUsd": round(completion.cost_usd, 6),
                                                     "estimateUsd": round(estimate, 6), "newFactor": round(self.calibration[model], 3)})
        if cache_key is not None and completion.text:
            self.cache.put(cache_key, completion)
        self.record("model.call", {
            "role": role, "model": completion.model, "family": fam, "provider": key, "costUsd": round(completion.cost_usd, 6),
            "estimateUsd": round(estimate, 6), "spentUsd": round(self.spent_usd, 6), "capUsd": self.cap_usd,
            "inputTokens": completion.input_tokens, "outputTokens": completion.output_tokens, "effort": effort,
            "billing": getattr(provider, "billing", "metered"),
            **({"rung": rung} if step else {}), **({"sample": sample} if sample else {}),
        })
        if not self.warned and self.spent_usd >= 0.8 * self.cap_usd:
            self.warned = True
            self.record("budget.warning", {"spentUsd": round(self.spent_usd, 4), "capUsd": self.cap_usd})
        return completion, cross


def parse_json(text: str):
    """Extracts JSON from model output (inside a code block or plain).

    The model may write plain text before the JSON, and that text may contain square brackets ("t ∈ [0, 1]"). So the
    code block is tried first, then the first valid JSON *object* in the text, and the list last; trailing extra text is ignored.
    """
    dec = json.JSONDecoder()
    blocks = [m.group(1).strip() for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S)]
    for cand in blocks + [text]:
        try:                                   # if the whole text is JSON (object or list), take it as is
            value = json.loads(cand.strip())
            if isinstance(value, (dict, list)):
                return value
        except json.JSONDecodeError:
            pass
        for opener in ("{", "["):
            for m in re.finditer(re.escape(opener), cand):
                try:
                    value, _ = dec.raw_decode(cand[m.start():])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, (dict, list)):
                    return value
    raise ModelError(f"model did not return valid JSON: {text[:300]}")

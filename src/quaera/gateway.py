"""Model gateway (ADR 0003).

All model calls go through here. The gateway:
- picks a model per role (costProfile in the agent definition),
- enforces the cross-model rule (mustDifferFrom),
- computes the worst-case cost BEFORE every call; it does not make a call that would exceed the cap,
- writes the actual cost to the event log; emits a warning at 80%.

Budget guarantee: estimate = (input token upper bound × input price) + (output token limit × output price).
The output token limit is passed to the provider, so the actual cost cannot exceed the estimate.
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


class Provider(Protocol):
    family: str

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float) -> Completion: ...


def estimate_input_tokens(*texts: str) -> int:
    # Conservative upper bound: ~2.5 characters/token for Turkish text and code.
    return int(sum(len(t) for t in texts) / 2.5)


def worst_case_cost(model: str, system: str, prompt: str, max_output_tokens: int,
                    price: tuple[float, float] | None = None) -> float:
    price_in, price_out = price or PRICES[model]
    tokens_in = (estimate_input_tokens(system, prompt) + PROVIDER_OVERHEAD_TOKENS) * CACHE_WRITE
    return (tokens_in * price_in + max_output_tokens * OUTPUT_SAFETY * price_out) / 1_000_000


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
    """LiteLLM adapter for API-key providers (OpenAI, Google, Mistral, local models…).

    `models`: cost profile -> LiteLLM model name, e.g. {"cheap": "openai/<model>", "balanced": ..., "best": ...}.
    The family is the model name's prefix (openai/…, gemini/…). Prices are read from LiteLLM's cost table; a model
    missing from the table cannot be estimated, so the call is refused (budget guarantee).
    `mock_response` is for tests only: no network call is made.
    """

    def __init__(self, models: dict[str, str], mock_response: str | None = None, timeout_s: int = 600):
        import litellm  # optional dependency: uv sync --extra providers
        self.litellm = litellm
        self.models = models
        self.family = next(iter(models.values())).split("/", 1)[0]
        self.mock_response = mock_response
        self.timeout_s = timeout_s

    def model_for(self, profile: str) -> str:
        return self.models[profile]

    def _supports_reasoning(self, model: str) -> bool:
        try:
            return bool(self.litellm.supports_reasoning(model=model))
        except Exception:
            return False

    def price(self, model: str) -> tuple[float, float]:
        info = self.litellm.model_cost.get(model) or self.litellm.model_cost.get(model.split("/", 1)[-1])
        if not info or "input_cost_per_token" not in info:
            raise BudgetExceeded(f"price unknown for {model}; call not made because the budget cap cannot be guaranteed")
        return info["input_cost_per_token"] * 1e6, info["output_cost_per_token"] * 1e6

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        extra = {"mock_response": self.mock_response} if self.mock_response else {}
        if effort and self._supports_reasoning(model):   # non-reasoning models reject this parameter
            extra["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(effort, effort)   # LiteLLM: low/medium/high
        try:
            resp = self.litellm.completion(model=model, messages=[{"role": "system", "content": system},
                                                                  {"role": "user", "content": prompt}],
                                           max_tokens=max_output_tokens, timeout=self.timeout_s, **extra)
        except Exception as exc:  # provider errors are turned into a single ModelError type
            raise ModelError(f"{model}: {exc}") from exc
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

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        self.calls.append({"system": system, "prompt": prompt, "model": model, "effort": effort})
        return Completion(text=self.responder(system, prompt), model=f"{self.family}/{model}", family=self.family,
                          cost_usd=min(self.cost_per_call, budget_usd))


EFFORT_FACTOR = {"low": 1.0, "medium": 1.25, "high": 1.5, "xhigh": 2.0, "max": 2.5}


@dataclass
class Gateway:
    providers: dict[str, Provider]
    cap_usd: float
    agent_specs: dict[str, dict]
    record: Callable[[str, dict], None] = lambda kind, payload: None
    spent_usd: float = 0.0
    warned: bool = False
    role_families: dict[str, str] = field(default_factory=dict)
    profile_overrides: dict[str, str] = field(default_factory=dict)
    effort_overrides: dict[str, str] | None = None
    calibration: dict[str, float] = field(default_factory=dict)   # model -> highest observed actual/estimate ratio (if >1)
    # Parallel calls (e.g. two Hypothesis agents at once): each call reserves its own upper bound before starting; only
    # that bound is given to the provider. The reserved bounds cannot sum past the remaining budget, so the cap holds in parallel too.
    reserved_usd: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def _family_for(self, role: str) -> tuple[str, bool]:
        """Picks a provider family for the role. Returns: (family, whether cross-model was achieved)."""
        avoid = {self.role_families.get(r) for r in self.agent_specs[role]["model"].get("mustDifferFrom", [])}
        avoid.discard(None)
        families = list(self.providers)
        for fam in families:
            if fam not in avoid:
                return fam, bool(avoid)
        return families[0], False

    def alternative(self, role: str, failed: str | None) -> str | None:
        """Another family to retry a role on after `failed` kept failing; keeps the role's mustDifferFrom rule if it can."""
        avoid = {self.role_families.get(r) for r in self.agent_specs[role]["model"].get("mustDifferFrom", [])}
        others = [f for f in self.providers if f != failed]
        return next((f for f in others if f not in avoid), others[0] if others else None)

    def call(self, role: str, system: str, prompt: str, max_output_tokens: int = 8000,
             family: str | None = None) -> tuple[Completion, bool]:
        """`family`: a specific provider family (so a second parallel instance of the same role runs on a different model)."""
        profile = self.profile_overrides.get(role) or self.agent_specs[role]["model"]["costProfile"]
        family, cross = (family, False) if family in self.providers else self._family_for(role)
        provider = self.providers[family]
        model = provider.model_for(profile) if hasattr(provider, "model_for") else PROFILE_MODELS.get(family, {}).get(profile, profile)
        price = provider.price(model) if hasattr(provider, "price") else None
        effort = (self.effort_overrides or {}).get(role) or self.agent_specs[role]["model"].get("effort")
        effort = None if effort == "default" else effort        # "default": provider default (baseline for measurement)
        # Thinking tokens are billed as output: at high effort the worst-case estimate is scaled up.
        # (The hard cap is still the remaining budget given to the provider; this factor only makes early stopping right.)
        estimate = (worst_case_cost(model, system, prompt, max_output_tokens, price) * EFFORT_FACTOR.get(effort, 1.0)
                    * self.calibration.get(model, 1.0))
        with self.lock:
            available = self.cap_usd - self.spent_usd - self.reserved_usd
            if estimate > available:
                self.record("budget.blocked", {"role": role, "model": model, "estimateUsd": round(estimate, 4),
                                               "spentUsd": round(self.spent_usd, 4), "capUsd": self.cap_usd})
                raise BudgetExceeded(
                    f"{role} call may cost up to ${estimate:.3f} in the worst case; remaining budget ${max(0.0, available):.3f}"
                )
            # Estimate + half the remaining margin: even if the estimate is wrong the provider cannot exceed this; the other half stays for parallel calls.
            limit = estimate + (available - estimate) / 2
            self.reserved_usd += limit
        try:
            completion = self.providers[family].complete(model, system, prompt, max_output_tokens, budget_usd=limit,
                                                         effort=effort)
        except ModelError as exc:
            with self.lock:
                self.reserved_usd -= limit
                self.spent_usd += exc.cost_usd
            self.record("model.error", {"role": role, "model": model, "error": str(exc)[:300],
                                        "costUsd": round(exc.cost_usd, 6), "spentUsd": round(self.spent_usd, 6)})
            raise
        except BaseException:
            with self.lock:
                self.reserved_usd -= limit
            raise
        with self.lock:
            self.reserved_usd -= limit
            self.spent_usd += completion.cost_usd
        self.role_families.setdefault(role, family)
        if estimate > 0 and completion.cost_usd > estimate:
            # Estimate exceeded: scale later estimates for this model by the observed ratio (+10%) and record it.
            ratio = completion.cost_usd / estimate * 1.1 * self.calibration.get(model, 1.0)
            self.calibration[model] = max(self.calibration.get(model, 1.0), ratio)
            self.record("budget.estimate_exceeded", {"role": role, "model": model, "costUsd": round(completion.cost_usd, 6),
                                                     "estimateUsd": round(estimate, 6), "newFactor": round(self.calibration[model], 3)})
        self.record("model.call", {
            "role": role, "model": completion.model, "family": family, "costUsd": round(completion.cost_usd, 6),
            "estimateUsd": round(estimate, 6), "spentUsd": round(self.spent_usd, 6), "capUsd": self.cap_usd,
            "inputTokens": completion.input_tokens, "outputTokens": completion.output_tokens, "effort": effort,
            "billing": getattr(provider, "billing", "metered"),
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

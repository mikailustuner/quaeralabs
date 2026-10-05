"""Model gateway (ADR 0003).

Tüm model çağrıları buradan geçer. Gateway:
- rol başına model seçer (ajan tanımındaki costProfile),
- çapraz model kuralını uygular (mustDifferFrom),
- her çağrıdan ÖNCE en kötü durum maliyetini hesaplar; tavanı aşacak çağrıyı yapmaz,
- gerçek maliyeti olay kaydına yazar; %80'de uyarı üretir.

Bütçe garantisi: tahmin = (girdi token üst sınırı × girdi fiyatı) + (çıktı token sınırı × çıktı fiyatı).
Çıktı token sınırı sağlayıcıya iletilir, böylece gerçek maliyet tahmini aşamaz.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

# USD / milyon token (girdi, çıktı). Kaynak: Anthropic fiyat tablosu (claude-api referansı, 2026-09-25):
# Opus 5.5 4/20, Sonnet 5.5 2/10, Haiku 4.5 1/5. Önbelleğe yazma girdinin ~1,25 katı.
# Bütçe tahmini için kullanılır; sağlayıcı gerçek maliyeti raporlar ve olay kaydına o yazılır.
PRICES = {
    "haiku": (1.0, 5.0),
    "sonnet": (2.0, 10.0),
    "opus": (4.0, 20.0),
    "scripted": (0.0, 0.0),
}
CACHE_WRITE = 1.25
# `claude` CLI her çağrıya kendi iskeletini ekler ve önbelleğe yazar (Faz 2'de ~5 bin token ölçüldü).
PROVIDER_OVERHEAD_TOKENS = 8000
# CLAUDE_CODE_MAX_OUTPUT_TOKENS kesin bir sınır değil (2500 istenip 2797 üretildiği görüldü): çıktı için 2 kat varsayılır.
OUTPUT_SAFETY = 2.0

# Maliyet profili -> sağlayıcıya özgü model takma adı
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
    # Muhafazakâr üst sınır: Türkçe ve kod için ~2.5 karakter/token.
    return int(sum(len(t) for t in texts) / 2.5)


def worst_case_cost(model: str, system: str, prompt: str, max_output_tokens: int,
                    price: tuple[float, float] | None = None) -> float:
    price_in, price_out = price or PRICES[model]
    tokens_in = (estimate_input_tokens(system, prompt) + PROVIDER_OVERHEAD_TOKENS) * CACHE_WRITE
    return (tokens_in * price_in + max_output_tokens * OUTPUT_SAFETY * price_out) / 1_000_000


class ClaudeCLIProvider:
    """Kullanıcının giriş yapmış `claude` CLI'si üzerinden Anthropic modelleri.

    Araçlar kapalı, oturum kaydedilmez, kullanıcı ayarları ve MCP sunucuları yüklenmez.
    Çıktı token sınırı CLAUDE_CODE_MAX_OUTPUT_TOKENS ile, harcama --max-budget-usd ile sınırlanır.
    """

    family = "anthropic"

    def __init__(self, binary: str = "claude", timeout_s: int = 900):
        self.binary = binary
        self.timeout_s = timeout_s

    def complete(self, model: str, system: str, prompt: str, max_output_tokens: int, budget_usd: float,
                 effort: str | None = None) -> Completion:
        env = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(max_output_tokens))
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
    """API anahtarıyla çalışan sağlayıcılar (OpenAI, Google, Mistral, yerel modeller…) için LiteLLM adaptörü.

    `models`: maliyet profili -> LiteLLM model adı, ör. {"cheap": "openai/<model>", "balanced": ..., "best": ...}.
    Aile, model adının önekidir (openai/…, gemini/…). Fiyat LiteLLM'in maliyet tablosundan okunur; tabloda olmayan
    bir model için tahmin yapılamayacağından çağrı reddedilir (bütçe garantisi).
    `mock_response` yalnızca testler içindir: ağ çağrısı yapılmaz.
    """

    def __init__(self, models: dict[str, str], mock_response: str | None = None, timeout_s: int = 600):
        import litellm  # isteğe bağlı bağımlılık: uv sync --extra providers
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
        if effort and self._supports_reasoning(model):   # akıl yürütmeyen modeller bu parametreyi reddeder
            extra["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(effort, effort)   # LiteLLM: low/medium/high
        try:
            resp = self.litellm.completion(model=model, messages=[{"role": "system", "content": system},
                                                                  {"role": "user", "content": prompt}],
                                           max_tokens=max_output_tokens, timeout=self.timeout_s, **extra)
        except Exception as exc:  # sağlayıcı hataları tek tip ModelError'a çevrilir
            raise ModelError(f"{model}: {exc}") from exc
        usage = resp.usage
        p_in, p_out = self.price(model)
        cost = (usage.prompt_tokens * p_in + usage.completion_tokens * p_out) / 1e6
        return Completion(text=resp.choices[0].message.content or "", model=model, family=self.family, cost_usd=cost,
                          input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)


class ScriptedProvider:
    """Testler için: önceden yazılmış cevapları sırayla ya da bir fonksiyonla döndürür."""

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
    calibration: dict[str, float] = field(default_factory=dict)   # model -> gözlenen en yüksek gerçek/tahmin oranı (>1 ise)
    # Paralel çağrılar (ör. iki Hipotez ajanı aynı anda): her çağrı başlamadan kendi üst sınırını ayırır; sağlayıcıya
    # yalnızca bu sınır verilir. Ayrılmış sınırların toplamı kalan bütçeyi geçemediği için tavan paralelde de aşılamaz.
    reserved_usd: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def _family_for(self, role: str) -> tuple[str, bool]:
        """Rol için sağlayıcı ailesi seçer. Dönüş: (aile, çapraz model sağlandı mı)."""
        avoid = {self.role_families.get(r) for r in self.agent_specs[role]["model"].get("mustDifferFrom", [])}
        avoid.discard(None)
        families = list(self.providers)
        for fam in families:
            if fam not in avoid:
                return fam, bool(avoid)
        return families[0], False

    def call(self, role: str, system: str, prompt: str, max_output_tokens: int = 8000,
             family: str | None = None) -> tuple[Completion, bool]:
        """`family`: belirli bir sağlayıcı ailesi (paralel çalışan aynı rolün ikinci örneği farklı modelle çalışsın diye)."""
        profile = self.profile_overrides.get(role) or self.agent_specs[role]["model"]["costProfile"]
        family, cross = (family, False) if family in self.providers else self._family_for(role)
        provider = self.providers[family]
        model = provider.model_for(profile) if hasattr(provider, "model_for") else PROFILE_MODELS.get(family, {}).get(profile, profile)
        price = provider.price(model) if hasattr(provider, "price") else None
        effort = (self.effort_overrides or {}).get(role) or self.agent_specs[role]["model"].get("effort")
        effort = None if effort == "default" else effort        # "default": sağlayıcı varsayılanı (ölçümde taban için)
        # Düşünme token'ları çıktı olarak faturalanır: yüksek eforda en kötü durum tahmini büyütülür.
        # (Kesin tavan yine sağlayıcıya verilen kalan bütçedir; bu çarpan yalnızca erken durdurmayı doğru yapar.)
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
            # Tahmin + kalan payın yarısı: tahmin yanılsa da sağlayıcı bu sınırı aşamaz; yarısı paralel çağrılara kalır.
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
            # Tahmin aşıldı: bu model için sonraki tahminleri gözlenen oranla (+%10) büyüt ve kayda geçir.
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
    """Model çıktısından JSON çıkarır (kod bloğu içinde ya da düz).

    Model JSON'dan önce düz metin yazabilir ve o metinde köşeli parantez geçebilir ("t ∈ [0, 1]"). Bu yüzden önce
    kod bloğu, sonra metindeki ilk geçerli JSON *nesnesi*, en son liste denenir; sondaki fazlalık yok sayılır.
    """
    dec = json.JSONDecoder()
    blocks = [m.group(1).strip() for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S)]
    for cand in blocks + [text]:
        try:                                   # metnin tamamı JSON ise (nesne ya da liste) olduğu gibi
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

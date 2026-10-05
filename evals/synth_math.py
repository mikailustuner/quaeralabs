"""QuaeraLabs-Synth-Math: şablonlardan rastgele örneklenen, doğruluğu Python'la hesaplanan Lean 4 problemleri.

Amaç miniF2F'teki kirlilik sorununu azaltmak: problemler bu betiğin seed'iyle üretilir, hiçbir yerde
yayımlanmış değildir. Şablonlar tanıdık türdendir; ölçülen şey yeni örneklerde ispat yazabilme becerisidir,
yeni matematik keşfi değil.

Kullanım:
  uv run python evals/synth_math.py generate --seed 2026 --per-template 3   # evals/data/synth-math-<seed>.jsonl
  uv run python evals/synth_math.py run --seed 2026 --attempts 2 --budget 3 --profile balanced
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from fractions import Fraction
from math import gcd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = "import Mathlib\n\nset_option maxHeartbeats 400000\n\nopen BigOperators Real Nat\n\n"


def q(fr: Fraction) -> str:
    """Kesirli sayıyı Lean ℝ ifadesine çevirir."""
    return f"({fr.numerator} : ℝ)" if fr.denominator == 1 else f"(({fr.numerator} : ℝ) / {fr.denominator})"


def t_linear(rng, name):
    while True:
        a, b, d, e = (rng.randint(-9, 9) for _ in range(4))
        if a * e - b * d != 0:
            break
    x, y = Fraction(rng.randint(-12, 12), rng.choice([1, 1, 2, 3])), Fraction(rng.randint(-12, 12), rng.choice([1, 2]))
    c, f = a * x + b * y, d * x + e * y
    return (f"theorem {name} (x y : ℝ) (h₀ : {a} * x + {b} * y = {q(c)}) (h₁ : {d} * x + {e} * y = {q(f)}) :\n"
            f"    x = {q(x)} ∧ y = {q(y)} := by\n")


def t_powmod(rng, name):
    a, n, m = rng.randint(2, 19), rng.randint(20, 90), rng.choice([7, 9, 11, 13, 17, 19, 23, 25, 27])
    return f"theorem {name} : ({a} ^ {n}) % {m} = {pow(a, n, m)} := by\n"


def t_quad_pos(rng, name):
    while True:
        a, b, c = rng.randint(1, 9), rng.randint(-15, 15), rng.randint(1, 30)
        if b * b < 4 * a * c:
            break
    return f"theorem {name} (x : ℝ) : 0 < {a} * x ^ 2 + {b} * x + {c} := by\n"


def t_sum(rng, name):
    a, b = rng.randint(1, 9), rng.randint(0, 9)
    # 2 * Σ_{i<n} (a*i + b) = n * (a*(n-1) + 2b)  → ℕ'de çıkarma olmaması için n*(a*n + 2b) = 2Σ + a*n
    return (f"theorem {name} (n : ℕ) :\n"
            f"    2 * ∑ i ∈ Finset.range n, ({a} * i + {b}) + {a} * n = n * ({a} * n + {2 * b}) := by\n")


def t_recurrence(rng, name):
    k, c, d, m = rng.randint(4, 7), rng.randint(0, 5), rng.randint(0, 4), rng.randint(2, 3)
    u = d
    for _ in range(k):
        u = m * u + c
    return (f"theorem {name} (u : ℕ → ℕ) (h₀ : u 0 = {d}) (h₁ : ∀ n, u (n + 1) = {m} * u n + {c}) :\n"
            f"    u {k} = {u} := by\n")


def t_count(rng, name):
    N, k = rng.randint(60, 150), rng.randint(3, 9)
    r = rng.randint(0, k - 1)
    val = sum(1 for n in range(N) if n % k == r)
    return f"theorem {name} : ((Finset.range {N}).filter (fun n => n % {k} = {r})).card = {val} := by\n"


def t_amgm(rng, name):
    s = rng.randint(2, 30)
    return f"theorem {name} (x y : ℝ) (h₀ : x + y = {s}) : x * y ≤ {q(Fraction(s * s, 4))} := by\n"


def t_gcd(rng, name):
    g = rng.randint(2, 60)
    a, b = g * rng.randint(100, 900), g * rng.randint(100, 900)
    return f"theorem {name} : Nat.gcd {a} {b} = {gcd(a, b)} := by\n"


def t_divisible(rng, name):
    k = rng.choice([2, 3, 6])
    shift = rng.randint(0, 5)
    # Ardışık k sayının çarpımı k! ile, dolayısıyla k ile bölünür.
    terms = " * ".join(f"(n + {shift + i})" for i in range(k))
    return f"theorem {name} (n : ℕ) : {k} ∣ {terms} := by\n"


def t_compose(rng, name):
    a, b, c, d, x0 = rng.randint(-6, 6), rng.randint(-9, 9), rng.randint(-6, 6), rng.randint(-9, 9), rng.randint(-5, 5)
    val = a * (c * x0 + d) + b
    return (f"theorem {name} (f g : ℝ → ℝ) (hf : ∀ x, f x = {a} * x + {b}) (hg : ∀ x, g x = {c} * x + {d}) :\n"
            f"    f (g ({x0})) = {val} := by\n")


TEMPLATES = [t_linear, t_powmod, t_quad_pos, t_sum, t_recurrence, t_count, t_amgm, t_gcd, t_divisible, t_compose]

# --- v0.2: zor şablonlar (çok adımlı akıl yürütme; doğruluk inşa yoluyla garanti) --------------------


def h_square_residues(rng, name):
    m = rng.choice([3, 4, 5, 7, 8, 9, 11])
    residues = sorted({(n * n) % m for n in range(m)})
    disj = " ∨ ".join(f"(n ^ 2) % {m} = {r}" for r in residues)
    return f"theorem {name} (n : ℕ) : {disj} := by\n"


def h_no_root_mod(rng, name):
    while True:
        m = rng.choice([3, 4, 5, 7, 8, 11])
        c = rng.randint(1, 30)
        if all((n * n + c) % m != 0 for n in range(m)):
            return f"theorem {name} (n : ℕ) : ¬ ({m} ∣ n ^ 2 + {c}) := by\n"


def h_pow_div(rng, name):
    while True:
        b, d = rng.randint(2, 9), rng.choice([3, 5, 7, 9, 11, 13, 31])
        if gcd(b, d) != 1:
            continue
        k = next(k for k in range(1, d) if pow(b, k, d) == 1)
        if k >= 2:
            return f"theorem {name} (n : ℕ) : {d} ∣ {b} ^ ({k} * n) - 1 := by\n"


def h_sum_squares(rng, name):
    a, b = rng.randint(1, 6), rng.randint(0, 6)
    # 6 Σ_{i≤n} (a i² + b i) = a n(n+1)(2n+1) + 3 b n(n+1)
    return (f"theorem {name} (n : ℕ) :\n"
            f"    6 * ∑ i ∈ Finset.range (n + 1), ({a} * i ^ 2 + {b} * i) = {a} * n * (n + 1) * (2 * n + 1) + {3 * b} * n * (n + 1) := by\n")


def h_recurrence_closed(rng, name):
    m, c, d = rng.randint(2, 4), rng.randint(1, 6), rng.randint(0, 5)
    # u(n+1) = m u(n) + c  ⇒  (m-1) u(n) + c = ((m-1) d + c) m^n
    return (f"theorem {name} (u : ℕ → ℕ) (h₀ : u 0 = {d}) (h₁ : ∀ n, u (n + 1) = {m} * u n + {c}) (n : ℕ) :\n"
            f"    {m - 1} * u n + {c} = {(m - 1) * d + c} * {m} ^ n := by\n")


def h_cauchy(rng, name):
    c, k = rng.randint(-7, 7), rng.randint(2, 6)
    return (f"theorem {name} (f : ℝ → ℝ) (hf : ∀ x y, f (x + y) = f x + f y) (h₁ : f 1 = {c}) :\n"
            f"    f {k} = {k * c} := by\n")


def h_vertex(rng, name):
    a, b = rng.randint(1, 6), rng.randint(-12, 12)
    c = rng.randint(-10, 10)
    bound = Fraction(c) - Fraction(b * b, 4 * a)
    return f"theorem {name} (x : ℝ) : {q(bound)} ≤ {a} * x ^ 2 + {b} * x + {c} := by\n"


def h_bezout(rng, name):
    while True:
        a, b = rng.randint(20, 300), rng.randint(20, 300)
        if gcd(a, b) > 1 and a != b:
            return f"theorem {name} : ∃ x y : ℤ, {a} * x + {b} * y = {gcd(a, b)} := by\n"


def h_no_dioph(rng, name):
    while True:
        g = rng.randint(2, 9)
        a, b = g * rng.randint(2, 15), g * rng.randint(2, 15)
        c = rng.randint(1, 200)
        if c % gcd(a, b) != 0:
            return f"theorem {name} : ¬ ∃ x y : ℤ, {a} * x + {b} * y = {c} := by\n"


def h_sym_ineq(rng, name):
    k = rng.randint(1, 5)
    return (f"theorem {name} (a b c : ℝ) :\n"
            f"    {k} * (a * b + b * c + c * a) ≤ {k} * (a ^ 2 + b ^ 2 + c ^ 2) := by\n")


HARD_TEMPLATES = [h_square_residues, h_no_root_mod, h_pow_div, h_sum_squares, h_recurrence_closed,
                  h_cauchy, h_vertex, h_bezout, h_no_dioph, h_sym_ineq]


# --- olimpiyat düzeyi (Faz 2 sonu): hard seti de 30/30 çözüldüğü için ekibin sınırını ölçmek amacıyla eklendi ---
# Her şablon ifadenin doğruluğunu üretirken Python'la sınar (assert); yanlış ifade sete giremez.

def _fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def o_ind_div(rng, name):
    """d ∣ a^(2n+1) + b^(n+2) — klasik tümevarım (7 ∣ 3^(2n+1) + 2^(n+2) ailesi)."""
    while True:
        d, a, b = rng.randint(5, 61), rng.randint(2, 15), rng.randint(2, 15)
        if a % d and b % d and (a * a - b) % d == 0 and (a + b * b) % d == 0:
            assert all((a ** (2 * n + 1) + b ** (n + 2)) % d == 0 for n in range(40))
            return f"theorem {name} (n : ℕ) : {d} ∣ {a} ^ (2 * n + 1) + {b} ^ (n + 2) := by\n"


def o_fermat_poly(rng, name):
    """D ∣ n^k − n; D, (p−1) ∣ (k−1) olan asalların çarpımı (ör. 2730 ∣ n^13 − n)."""
    k = rng.choice([7, 9, 11, 13, 17, 19])
    D = 1
    for p in (2, 3, 5, 7, 11, 13, 17, 19):
        if (k - 1) % (p - 1) == 0:
            D *= p
    assert all((n ** k - n) % D == 0 for n in range(-60, 60))
    return f"theorem {name} (n : ℤ) : {D} ∣ n ^ {k} - n := by\n"


def o_mod_powers(rng, name):
    """Kuvvet toplamlarının kalan engelleri (üç kare 8k+7 olamaz, iki dördüncü kuvvet …)."""
    while True:
        e, m, vars_ = rng.choice([(2, 4, 2), (2, 8, 3), (4, 16, 2), (3, 9, 2), (3, 7, 2), (4, 16, 3)])
        res = {sum(t) % m for t in __import__("itertools").product([pow(x, e, m) for x in range(m)], repeat=vars_)}
        bad = [r for r in range(m) if r not in res]
        if bad:
            r = rng.choice(bad)
            names = "x y z w"[: 2 * vars_ - 1]
            terms = " + ".join(f"{v} ^ {e}" for v in names.split())
            return f"theorem {name} ({names} : ℤ) : ({terms}) % {m} ≠ {r} := by\n"


INEQ3 = [
    "(a + b + c) * (1 / a + 1 / b + 1 / c) ≥ 9",
    "a / b + b / c + c / a ≥ 3",
    "a ^ 3 + b ^ 3 + c ^ 3 ≥ 3 * a * b * c",
    "(a + b) * (b + c) * (c + a) ≥ 8 * a * b * c",
    "a ^ 2 / b + b ^ 2 / c + c ^ 2 / a ≥ a + b + c",
    "a / (b + c) + b / (c + a) + c / (a + b) ≥ 3 / 2",
]


def o_ineq3(rng, name):
    """Üç değişkenli klasik eşitsizlikler (AM-GM, Nesbitt …), pozitif gerçel sayılarda."""
    return f"theorem {name} (a b c : ℝ) (ha : 0 < a) (hb : 0 < b) (hc : 0 < c) :\n    {rng.choice(INEQ3)} := by\n"


FIB = [
    ("(Nat.fib (n + 2) : ℤ) * Nat.fib n - (Nat.fib (n + 1) : ℤ) ^ 2 = (-1) ^ (n + 1)",
     lambda n: _fib(n + 2) * _fib(n) - _fib(n + 1) ** 2 == (-1) ** (n + 1)),
    ("∑ i ∈ Finset.range n, Nat.fib i + 1 = Nat.fib (n + 1)", lambda n: sum(_fib(i) for i in range(n)) + 1 == _fib(n + 1)),
    ("∑ i ∈ Finset.range (n + 1), Nat.fib i ^ 2 = Nat.fib n * Nat.fib (n + 1)",
     lambda n: sum(_fib(i) ** 2 for i in range(n + 1)) == _fib(n) * _fib(n + 1)),
    ("Nat.fib (2 * n + 1) = Nat.fib n ^ 2 + Nat.fib (n + 1) ^ 2", lambda n: _fib(2 * n + 1) == _fib(n) ** 2 + _fib(n + 1) ** 2),
]


def o_fib_identity(rng, name):
    stmt, check = rng.choice(FIB)
    assert all(check(n) for n in range(40))
    return f"theorem {name} (n : ℕ) :\n    {stmt} := by\n"


def o_gcd_linear(rng, name):
    """gcd(a n + b, c n + d) = 1, |ad − bc| = 1 (IMO 1959/1: 21n+4, 14n+3 ailesi)."""
    while True:
        a, b, c = rng.randint(2, 40), rng.randint(1, 30), rng.randint(2, 40)
        for d in range(1, 60):
            if abs(a * d - b * c) == 1 and (a, b) != (c, d):
                assert all(gcd(a * n + b, c * n + d) == 1 for n in range(300))
                return f"theorem {name} (n : ℕ) : Nat.gcd ({a} * n + {b}) ({c} * n + {d}) = 1 := by\n"


def o_no_rational_root(rng, name):
    """q^k = p'nin rasyonel çözümü yok (p tam k. kuvvet değil)."""
    while True:
        k, p = rng.choice([2, 3]), rng.randint(2, 60)
        if round(p ** (1 / k)) ** k != p:
            return f"theorem {name} : ¬ ∃ q : ℚ, q ^ {k} = {p} := by\n"


OLYMPIAD_TEMPLATES = [o_ind_div, o_fermat_poly, o_mod_powers, o_ineq3, o_fib_identity, o_gcd_linear, o_no_rational_root]


LEVEL_TAG = {"v1": "", "hard": "-hard", "olympiad": "-olympiad"}


def generate(seed: int, per: int, level: str = "v1") -> Path:
    rng = random.Random(seed)
    rows = []
    for t in {"hard": HARD_TEMPLATES, "olympiad": OLYMPIAD_TEMPLATES}.get(level, TEMPLATES):
        seen: set[str] = set()
        for i in range(per):
            name = f"qsm_{t.__name__[2:]}_{i}"   # h_/o_/t_ önekini at
            for _ in range(200):   # aynı ifadeyi iki kez üretme (adı dışında)
                stmt = t(rng, name)
                body = stmt.split(" ", 2)[2]
                if body not in seen:
                    break
            seen.add(body)
            rows.append({"name": name, "template": t.__name__[2:], "formal_statement": stmt})
    out = ROOT / "evals" / "data" / f"synth-math{LEVEL_TAG[level]}-{seed}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return out


def run(seed: int, attempts: int, budget: float, profile: str, level: str = "v1") -> int:
    from quaera import prompts
    from quaera.gateway import BudgetExceeded, ClaudeCLIProvider, Gateway, ModelError
    from quaera.lean import LeanChecker, statement_of
    from quaera.orchestrator import extract_lean, feedback_text
    from quaera.permissions import Permissions

    tag = LEVEL_TAG[level]
    rows = [json.loads(l) for l in (ROOT / "evals/data" / f"synth-math{tag}-{seed}.jsonl").read_text().splitlines()]
    perms = Permissions.load()
    events: list[dict] = []
    gw = Gateway({"anthropic": ClaudeCLIProvider()}, budget, perms.agents, lambda k, p: events.append({"kind": k, **p}),
                 profile_overrides={"engineer": profile})
    lean = LeanChecker()
    out = []
    for i, r in enumerate(rows, 1):
        name, stmt = r["name"], r["formal_statement"].rstrip()
        pilot_src = HEADER + stmt + " sorry\n"
        pilot = lean.check(pilot_src, name)
        row = {"name": name, "template": r["template"], "pilot_ok": pilot.compiled, "solved": False, "attempts": 0}
        if not pilot.compiled:
            row["pilot_errors"] = pilot.errors[:2]
            out.append(row)
            print(f"[{i}/{len(rows)}] {name}: ifade derlenmiyor (üretici hatası)", flush=True)
            continue
        approved = statement_of(pilot_src, name)
        system = prompts.PROVE.replace("`quaera_main`", f"`{name}`")
        prompt = f"Prove this theorem. Keep the theorem name `{name}` and its statement exactly.\n```lean\n{pilot_src}```"
        try:
            for attempt in range(attempts):
                c, _ = gw.call("engineer", system, prompt, 8000)
                src = extract_lean(c.text)
                rep = lean.check(src, name, approved)
                row["attempts"] += 1
                if rep.verified:
                    clean = lean.check(src, name, approved, clean=True)
                    row["solved"], row["clean_verified"], row["proof"] = clean.verified, clean.verified, src
                    break
                prompt += f"\n\nAttempt {attempt + 1} failed:\n```lean\n{src}\n```\n{feedback_text(rep.__dict__)}\nFix it."
        except BudgetExceeded as exc:
            row["budget_stop"] = str(exc)
            out.append(row)
            break
        except ModelError as exc:
            row["model_error"] = str(exc)[:200]
        out.append(row)
        print(f"[{i}/{len(rows)}] {name}: {'ÇÖZÜLDÜ' if row['solved'] else 'çözülemedi'} ({row['attempts']}) · ${gw.spent_usd:.3f}", flush=True)
    lean.close()
    attempted = [r for r in out if r["attempts"] > 0]
    by_t: dict[str, list[bool]] = {}
    for r in attempted:
        by_t.setdefault(r["template"], []).append(r["solved"])
    summary = {"createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": seed,
               "problems": len(rows), "statement_compile_ok": sum(r["pilot_ok"] for r in out), "attempted": len(attempted),
               "solved": sum(r["solved"] for r in attempted),
               f"pass@{attempts}": round(sum(r["solved"] for r in attempted) / len(attempted), 3) if attempted else None,
               "by_template": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_t.items())},
               "models": sorted({e["model"] for e in events if e["kind"] == "model.call"}),
               "spent_usd": round(gw.spent_usd, 4), "budget_usd": budget, "rows": out}
    summary["level"] = level
    path = ROOT / "evals/results" / f"synth-math{tag}-{seed}-{summary['createdAt'][:10]}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, required=True)
    g.add_argument("--per-template", type=int, default=3)
    g.add_argument("--level", choices=["v1", "hard", "olympiad"], default="v1")
    r = sub.add_parser("run")
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--attempts", type=int, default=2)
    r.add_argument("--budget", type=float, required=True)
    r.add_argument("--profile", choices=["cheap", "balanced", "best"], default="balanced")
    r.add_argument("--level", choices=["v1", "hard", "olympiad"], default="v1")
    a = ap.parse_args()
    if a.cmd == "generate":
        print(generate(a.seed, a.per_template, a.level))
        return 0
    return run(a.seed, a.attempts, a.budget, a.profile, a.level)


if __name__ == "__main__":
    sys.exit(main())

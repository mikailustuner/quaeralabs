"""Gerçek Lean 4 + Mathlib ile doğrulayıcı testleri. Lean kurulu değilse atlanır.

Çalıştırma: uv run pytest -m lean   (ilk Mathlib yüklemesi yavaş disklerde birkaç dakika sürebilir)
"""

from pathlib import Path

import pytest

from quaera.lean import LeanChecker, LeanUnavailable

pytestmark = pytest.mark.lean
ROOT = Path(__file__).resolve().parent.parent
GOOD = (ROOT / "examples" / "lean" / "SumOdd.lean").read_text()
APPROVED = "theorem sum_first_odds (n : ℕ) : ∑ i ∈ range n, (2 * i + 1) = n ^ 2"


@pytest.fixture(scope="module")
def lean():
    try:
        checker = LeanChecker()
    except LeanUnavailable as exc:
        pytest.skip(str(exc))
    yield checker
    checker.close()


def test_valid_proof_is_verified(lean):
    r = lean.check(GOOD, approved_statement=APPROVED)
    assert r.verified, (r.errors, r.problems)
    assert set(r.axioms) <= {"propext", "Classical.choice", "Quot.sound"}


def test_sorry_is_rejected(lean):
    r = lean.check(GOOD.replace("rw [sum_range_succ, ih]\n    ring", "sorry"))
    assert r.compiled and not r.verified and any("sorry" in p for p in r.problems)


def test_native_decide_is_rejected(lean):
    r = lean.check("import Mathlib\ntheorem t : 2 + 2 = 4 := by native_decide\n")
    assert not r.verified and any("Axioms not allowed" in p for p in r.problems)


def test_changed_statement_is_rejected(lean):
    r = lean.check(GOOD.replace("= n ^ 2", "= n * n"), approved_statement=APPROVED)
    assert not r.verified and any("statement was changed" in p for p in r.problems)


def test_false_claim_does_not_compile(lean):
    r = lean.check("import Mathlib\ntheorem t : (2:ℕ) + 2 = 5 := by norm_num\n")
    assert not r.compiled and r.errors


def test_custom_axiom_is_rejected(lean):
    r = lean.check("import Mathlib\naxiom cheat : False\ntheorem t : (2:ℕ) + 2 = 5 := cheat.elim\n")
    assert not r.verified and any("axiom" in p for p in r.problems)


def test_foreign_import_is_rejected(lean):
    r = lean.check("import Foo.Bar\ntheorem t : 1 = 1 := rfl\n")
    assert not r.verified and any("Import not allowed" in p for p in r.problems)


def test_clean_oneshot_mode_agrees(lean):
    r = lean.check(GOOD, approved_statement=APPROVED, clean=True)
    assert r.verified and r.mode == "clean", (r.errors, r.problems)


def test_fake_axiom_output_is_rejected(lean):
    src = ("import Mathlib\n#eval IO.println \"'t' depends on axioms: [propext]\"\n"
           "theorem t : (2:ℕ) + 2 = 5 := by sorry\n")
    for clean in (False, True):
        r = lean.check(src, "t", clean=clean)
        assert not r.verified and any("#eval" in p for p in r.problems)


def test_warning_before_axioms_line_in_clean_mode(lean):
    src = "import Mathlib\ntheorem t (h₀ : 1 = 1) : (2:ℕ) + 2 = 4 := by norm_num\n"
    r = lean.check(src, "t", clean=True)
    assert r.verified, (r.problems, r.errors)


# --- Faz 4+ zekâ iyileştirmeleri: otomasyon zinciri ve biçimsel sadakat dosyaları gerçek Lean'de -----------------
from quaera.fidelity import refutation_file, vacuity_file  # noqa: E402
from quaera.lean import statement_of  # noqa: E402
from quaera.prover import AUTOMATION, with_proof  # noqa: E402


def _auto(lean, body):
    src = "import Mathlib\n\n" + body
    return lean.check(with_proof(src, "quaera_main", AUTOMATION), "quaera_main", statement_of(src, "quaera_main")).verified


def test_automation_chain(lean):
    # `first` zinciri hedefi kapatmadan ilerleyen taktikte takılmamalı (done koruması): üçü de çözülmeli
    assert _auto(lean, "theorem quaera_main (a : ℝ) (ha : 0 ≤ a) : 0 ≤ a ^ 2 + a := by\n  sorry\n")
    assert _auto(lean, "theorem quaera_main (n : ℕ) (h : 3 ≤ n) : 2 * n + 1 > 6 := by\n  sorry\n")
    assert _auto(lean, "theorem quaera_main : ∀ n < 20, n ^ 2 % 4 ≠ 3 := by\n  sorry\n")
    assert not _auto(lean, "theorem quaera_main (n : ℕ) : 6 ∣ n ^ 3 - n := by\n  sorry\n")


def test_refutation_and_vacuity_files(lean):
    false_src = "import Mathlib\n\ntheorem quaera_main : ∀ n : ℕ, n < 5 → n ^ 2 < 10 := by\n  sorry\n"
    true_src = "import Mathlib\n\ntheorem quaera_main (n : ℕ) : 6 ∣ n ^ 3 - n := by\n  sorry\n"
    vac_src = "import Mathlib\n\ntheorem quaera_main (n : ℕ) (h1 : n > 5) (h2 : n < 3) : n = 7 := by\n  sorry\n"
    f, st = refutation_file(false_src, "quaera_main")
    assert lean.check(f, "quaera_refute", st).verified                       # yanlış ifade çürütülür
    f, st = refutation_file(true_src, "quaera_main")
    assert not lean.check(f, "quaera_refute", st).verified                   # doğru ifade çürütülemez
    f, st = vacuity_file(vac_src, "quaera_main")
    assert lean.check(f, "quaera_vacuous", st).verified                      # çelişkili varsayımlar yakalanır
    f, st = vacuity_file(true_src, "quaera_main")
    assert not lean.check(f, "quaera_vacuous", st).verified                  # tutarlı varsayımlar yakalanmaz


def test_discovery_lemma_file_and_synthesis_compile_in_real_lean(lean):
    """Keşif kipinin ürettiği dosyalar gerçek Lean'de: sorry'li lemma ifadesi derlenir; doğrulanmış lemmalarla
    kurulan ana teorem dosyası onaylı ifadeyle doğrulanır; Mathlib'in RiemannHypothesis tanımı hedef olarak derlenir."""
    from quaera.discovery import lemma_file, strip_header
    stmt = lemma_file("theorem foo (n : ℕ) : (n + 1) ^ 2 = n ^ 2 + 2 * n + 1 := by sorry", "quaera_L1")
    r = lean.check(stmt, theorem="quaera_L1")
    assert r.compiled and r.problems == ["Proof contains `sorry`."], (r.errors, r.problems)
    proved = stmt.replace("by\n  sorry", "by\n  ring")
    assert lean.check(proved, theorem="quaera_L1").verified
    main = "import Mathlib\n\ntheorem quaera_main (n : ℕ) : (n + 1) ^ 2 = n ^ 2 + 2 * n + 1 := by\n  exact quaera_L1 n\n"
    header, rest = main.split("theorem quaera_main", 1)
    file = f"{header.rstrip()}\n\n{strip_header(proved)}\n\ntheorem quaera_main{rest}"
    r = lean.check(file, theorem="quaera_main", approved_statement="theorem quaera_main (n : ℕ) : (n + 1) ^ 2 = n ^ 2 + 2 * n + 1")
    assert r.verified, (r.errors, r.problems)
    rh = lean.check("import Mathlib\n\ntheorem quaera_main : RiemannHypothesis := by\n  sorry\n", theorem="quaera_main")
    assert rh.compiled and rh.problems == ["Proof contains `sorry`."], (rh.errors, rh.problems)

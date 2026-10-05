"""Biçimselleştirme sadakati (#6): ispata para harcamadan önce Lean ifadesinin kendisini sınar.

  boşluk     varsayımlar çelişkiliyse ifade "boşuna doğru"dur (ör. `n > 5 → n < 3 → …`); `False` türetilmeye çalışılır
  çürütme    ifadenin olumsuzu otomatik taktiklerle (gerekirse bir model denemesiyle) ispatlanmaya çalışılır;
             başarılı olursa hipotez Lean'de doğrulanmış bir karşı örnekle ÇÜRÜTÜLMÜŞ olur
  geri çeviri  yalnızca Lean dosyasını gören bir model ifadeyi Türkçeye çevirir; Eleştirmen hipotezle karşılaştırır

Dosyalar ajanın yazdığı başlığı (import/open/yardımcı tanımlar) korur; yalnızca teorem değişir.
"""

from __future__ import annotations

import re

# `done` koruması: hedefi kapatmadan ilerleyen taktik `first` zincirini durdurmasın (gerçek Lean'de sınandı).
AUTOMATION = ("first | decide | omega | (norm_num; done) | (simp; done) | (push_neg; decide) | (push_neg; norm_num; done)"
              " | (push_neg; simp; done) | aesop")
VACUITY_AUTOMATION = "first | omega | linarith | nlinarith | (simp_all; done) | aesop | (norm_num at *; done) | decide"
OPEN = "([{⦃"
CLOSE = {"(": ")", "[": "]", "{": "}", "⦃": "⦄"}


def split_theorem(source: str, name: str) -> tuple[str, str, str] | None:
    """Dosyayı (başlık, bağlayıcılar, sonuç) olarak ayırır. `theorem name (x : ℕ) (h : P x) : Q x := …`
    → ("import …\n", "(x : ℕ) (h : P x)", "Q x"). Ayrıştırılamazsa None."""
    m = re.search(rf"(?:theorem|lemma)\s+{re.escape(name)}\b", source)
    if not m:
        return None
    header, rest = source[:m.start()], source[m.end():]
    i, binders = 0, []
    while i < len(rest):
        c = rest[i]
        if c.isspace():
            i += 1
            continue
        if c in OPEN:
            depth, j = 0, i
            while j < len(rest):
                if rest[j] in OPEN:
                    depth += 1
                elif rest[j] in CLOSE.values():
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth != 0:
                return None
            binders.append(rest[i:j + 1])
            i = j + 1
            continue
        if c == ":" and not rest.startswith(":=", i):
            end = rest.find(":=", i)
            if end < 0:
                return None
            return header, " ".join(binders), rest[i + 1:end].strip()
        return None
    return None


def as_prop(binders: str, concl: str) -> str:
    """Bağlayıcıları ∀ ile sarar: Lean 4 `∀ (x : ℕ) (h : P x), Q x` biçimini kabul eder.
    Örtük/örnek bağlayıcılar ({α}, [inst]) açık bağlayıcıya çevrilir."""
    if not binders:
        return concl
    explicit = re.sub(r"[{⦃\[]([^{}⦃⦄\[\]]*)[}⦄\]]", lambda m: f"({m.group(1)})" if ":" in m.group(1) else f"(_inst : {m.group(1)})", binders)
    return f"∀ {explicit}, {concl}"


def refutation_file(source: str, name: str, proof: str = f"by\n  {AUTOMATION}") -> tuple[str, str] | None:
    """(dosya, onaylanacak ifade) döner: `theorem quaera_refute : ¬ (∀ …, …)`."""
    parts = split_theorem(source, name)
    if not parts:
        return None
    header, binders, concl = parts
    stmt = f"theorem quaera_refute : ¬ ({as_prop(binders, concl)})"
    return f"{header}{stmt} := {proof}\n", stmt


def vacuity_file(source: str, name: str) -> tuple[str, str] | None:
    """Varsayımlardan `False` türetmeyi dener. Bağlayıcı yoksa boşluk söz konusu değildir: None."""
    parts = split_theorem(source, name)
    if not parts or not parts[1]:
        return None
    header, binders, _ = parts
    stmt = f"theorem quaera_vacuous {binders} : False"
    return f"{header}{stmt} := by\n  {VACUITY_AUTOMATION}\n", stmt

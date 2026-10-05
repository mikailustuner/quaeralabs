"""#4 çeşitli hipotez üretimi: iki bakış açısı, tekrar ayıklama, Eleştirmen puanı ve onay kartı."""

import json

from quaera import prompts
from quaera.orchestrator import similar

from test_orchestrator import Script, make


def test_two_lenses_dedupe_and_critic_ranking_order(tmp_path):
    script, lens_prompts = Script(critic_objects_first=False), []

    def spy(system, prompt):
        if system == prompts.HYPOTHESIS:
            lens_prompts.append(prompt)
            if "contrarian" in prompt:   # ikinci bakış açısı (şerit B, paralel): bir tekrar + yeni adaylar
                return json.dumps({"hypotheses": [
                    {"statement": "For every n, the sum of the first n odd numbers is n².", "falsifiabilityNote": "An n that breaks the equality refutes the hypothesis."},
                    {"statement": "The sum of the first n odd numbers equals n times n, and this holds for every n.", "falsifiabilityNote": "y"},          # sözleşmeye uymayan (çok kısa) not: atlanmalı
                    {"statement": "For every n, the sum of the first n odd numbers is a perfect square whose root is n.", "falsifiabilityNote": "A sum that is not a square refutes it."}]})
        if system == prompts.RANK_HYPOTHESES:   # Eleştirmen ikinci adayı en üste koysun
            return json.dumps({"ranking": [
                {"index": 0, "testability": 5, "plausibility": 5, "novelty": 5, "scope": 5, "cost": 5, "reason": "orta"},
                {"index": 1, "testability": 1, "plausibility": 1, "novelty": 1, "scope": 1, "cost": 1, "reason": "zayıf"},
                {"index": 2, "testability": 10, "plausibility": 10, "novelty": 8, "scope": 10, "cost": 9, "reason": "en iyi"},
                {"index": 3, "testability": 7, "plausibility": 7, "novelty": 6, "scope": 9, "cost": 8, "reason": "iyi"}]})
        return script(system, prompt)
    orch = make(tmp_path, spy)
    orch.run()
    assert len(lens_prompts) == 2 and all("Lens for this round" in p for p in lens_prompts)
    assert sum("contrarian" in p for p in lens_prompts) == 1
    ranked = orch.store.events("hypotheses.ranked")[0]["payload"]["candidates"]
    assert len(ranked) == 4 and ranked[0]["reason"] == "en iyi" and ranked[0]["score"] > ranked[-1]["score"]
    # en yüksek puanlı aday sözleşmeye uymadığı için atlandı ve kayda geçti; sıradaki en iyi aday ilk taslak oldu
    assert orch.store.events("hypothesis.invalid")[0]["payload"]["statement"].startswith("The sum of the first n odd numbers equals n times n")
    drafts = sorted(orch.store.latest("hypothesis"), key=lambda h: h["id"])
    assert drafts[0]["statement"].startswith("For every n, the sum of the first n odd numbers is a perfect square") and len(drafts) == 3
    approval = next(m for m in orch.store.latest("message") if m.get("approvalRequest", {}).get("action") == "accept_hypothesis")
    assert approval["approvalRequest"]["decision"]["decision"] == "approved"


def test_similarity_threshold():
    assert similar("Her n için toplam n²'dir.", "her n için toplam n²'dir.")
    assert not similar("Her n için toplam n²'dir.", "Toplam her zaman tektir.")

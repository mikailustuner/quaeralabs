"""#4 diverse hypothesis generation: two lenses, deduplication, Critic score and the approval card."""

import json

from quaera import prompts
from quaera.orchestrator import similar

from test_orchestrator import Script, make


def test_two_lenses_dedupe_and_critic_ranking_order(tmp_path):
    script, lens_prompts = Script(critic_objects_first=False), []

    def spy(system, prompt):
        if system == prompts.HYPOTHESIS:
            lens_prompts.append(prompt)
            if "contrarian" in prompt:   # second lens (lane B, parallel): one duplicate + new candidates
                return json.dumps({"hypotheses": [
                    {"statement": "For every n, the sum of the first n odd numbers is n².", "falsifiabilityNote": "An n that breaks the equality refutes the hypothesis."},
                    {"statement": "The sum of the first n odd numbers equals n times n, and this holds for every n.", "falsifiabilityNote": "y"},          # note breaks the contract (too short): must be skipped
                    {"statement": "For every n, the sum of the first n odd numbers is a perfect square whose root is n.", "falsifiabilityNote": "A sum that is not a square refutes it."}]})
        if system == prompts.RANK_HYPOTHESES:   # the Critic puts the second candidate on top
            return json.dumps({"ranking": [
                {"index": 0, "testability": 5, "plausibility": 5, "novelty": 5, "scope": 5, "cost": 5, "reason": "medium"},
                {"index": 1, "testability": 1, "plausibility": 1, "novelty": 1, "scope": 1, "cost": 1, "reason": "weak"},
                {"index": 2, "testability": 10, "plausibility": 10, "novelty": 8, "scope": 10, "cost": 9, "reason": "best"},
                {"index": 3, "testability": 7, "plausibility": 7, "novelty": 6, "scope": 9, "cost": 8, "reason": "good"}]})
        return script(system, prompt)
    orch = make(tmp_path, spy)
    orch.run()
    assert len(lens_prompts) == 2 and all("Lens for this round" in p for p in lens_prompts)
    assert sum("contrarian" in p for p in lens_prompts) == 1
    ranked = orch.store.events("hypotheses.ranked")[0]["payload"]["candidates"]
    assert len(ranked) == 4 and ranked[0]["reason"] == "best" and ranked[0]["score"] > ranked[-1]["score"]
    # the top-scored candidate broke the contract, so it was skipped and recorded; the next best became the first draft
    assert orch.store.events("hypothesis.invalid")[0]["payload"]["statement"].startswith("The sum of the first n odd numbers equals n times n")
    drafts = sorted(orch.store.latest("hypothesis"), key=lambda h: h["id"])
    assert drafts[0]["statement"].startswith("For every n, the sum of the first n odd numbers is a perfect square") and len(drafts) == 3
    approval = next(m for m in orch.store.latest("message") if m.get("approvalRequest", {}).get("action") == "accept_hypothesis")
    assert approval["approvalRequest"]["decision"]["decision"] == "approved"


def test_similarity_threshold():
    assert similar("For every n the sum is n².", "for every n the sum is n².")
    assert not similar("For every n the sum is n².", "The sum is always odd.")

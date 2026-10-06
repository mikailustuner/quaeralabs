"""ML research loop (Phase 2).

Differences from the mathematics loop:
- The Experiment designer, Analyst and Writer are LLM agents; the Critic reviews the plan and the result separately.
- The Engineer writes a Python script; it runs in the sandbox with no network and read-only data.
- Statistics (mean, standard deviation, 95% confidence interval) are computed in code; the Analyst interprets, never produces numbers.
- The Verifier checks out the recorded commit into a clean directory, reruns it with the same seed and compares the metrics.
- Every sentence by the Writer must be tied to a research object's id; untied sentences are dropped from the report.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from statistics import mean, stdev

from . import prompts
from .contracts import prereg_hash
from .gateway import parse_json
from .orchestrator import Orchestrator, StopResearch
from .store import now

PARALLEL_RUNS = max(1, int(os.environ.get("QUAERA_PARALLEL_RUNS", "2")))

ML_STAGES = [
    "literature", "data_profile", "hypothesis", "hypothesis_approval", "design", "plan_review", "preregistration", "experiment_approval",
    "pilot", "run", "analysis", "critique", "verification", "conclude", "report",
]
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}
METRICS_RE = re.compile(r"^QUAERA_METRICS\s+(\{.*\})\s*$", re.M)
ID_RE = re.compile(r"\[((?:[A-Z]{1,3}-\d{4})(?:\s*,\s*[A-Z]{1,3}-\d{4})*)\]\s*\.?\s*$")


def stats_table(runs: list[dict]) -> dict:
    """For each metric: n, mean, standard deviation and 95% confidence interval (t distribution)."""
    names = sorted({k for r in runs for k in r.get("metrics", {})})
    table = {}
    for name in names:
        xs = [r["metrics"][name] for r in runs if name in r.get("metrics", {})]
        m = mean(xs)
        sd = stdev(xs) if len(xs) > 1 else 0.0
        half = T95.get(len(xs) - 1, 1.96) * sd / math.sqrt(len(xs)) if len(xs) > 1 else float("nan")
        table[name] = {"n": len(xs), "mean": round(m, 6), "std": round(sd, 6),
                       "ci95": [round(m - half, 6), round(m + half, 6)] if len(xs) > 1 else None}
    return table


def compare(a: dict, b: dict) -> tuple[str, list[str]]:
    """Verification: compares the metrics of two runs with the same seed."""
    diffs, worst = [], 0.0
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            diffs.append(f"{k}: missing in one run")
            worst = math.inf
            continue
        d = abs(a[k] - b[k])
        rel = d / max(abs(a[k]), 1e-12)
        worst = max(worst, rel if d > 1e-9 else 0.0)
        if d > 1e-9:
            diffs.append(f"{k}: {a[k]} ↔ {b[k]}")
    if worst == 0.0:
        return "yes", diffs
    return ("partial" if worst <= 0.01 else "no"), diffs


class MLOrchestrator(Orchestrator):
    stages = ML_STAGES

    # helpers -------------------------------------------------------------
    @property
    def workdir(self) -> str:
        return str(self.store.path.parent / "work")

    @property
    def data_dir(self) -> str:
        return self.store.meta("dataDir")

    def stage_data_profile(self) -> None:
        """#3 (ML): a deterministic data profile before the hypothesis — shapes, class balance, basic statistics, relation to the target."""
        actor = self.det("engineer")
        script = (Path(__file__).parent / "scripts" / "data_profile.py").read_text(encoding="utf-8")
        try:
            self.tools.call_json("engineer", "sandbox.write", workdir=self.workdir, path="data_profile.py", content=script,
                                 message="data profile")
            res = self._exec("engineer", ["data_profile.py"], timeout_s=180)
        except Exception as exc:   # if profiling fails, the research continues without a profile
            self.store.append("tool.error", actor, {"tool": "sandbox.exec", "error": str(exc)[:300]})
            return
        found = re.search(r"QUAERA_PROFILE\s+(\{.*\})", res.get("stdout", ""))
        if res.get("returncode") != 0 or not found:
            self.store.append("tool.error", actor, {"tool": "data_profile", "error": res.get("stderr", "")[-300:]})
            return
        profile = json.loads(found.group(1))
        self.set_state("data_profile", profile)
        self.store.append("data.profiled", actor, {"files": [f.get("file") for f in profile.get("files", [])]})

    def plan_text(self, plan: dict) -> str:
        return json.dumps(plan, ensure_ascii=False, indent=1)

    def _design(self, feedback: str = "") -> tuple[dict, dict]:
        h, q = self.selected_hypothesis(), self.question()
        plan, actor = self.ask_json("experiment_designer", prompts.DESIGN_ML,
                                   f"Question: {q['title']}\nData: {q['scope']}\nHypothesis: {h['statement']}\n{feedback}", 3000)
        plan["seeds"] = max(3, min(int(plan.get("seeds", 3)), 5))
        return plan, actor

    def _put_experiment(self, plan: dict, actor: dict, prev: dict | None = None) -> dict:
        h, lit = self.selected_hypothesis(), self.state("literature")
        obj = {**(prev or {}), "type": "experiment", "createdBy": actor, "hypothesisIds": [h["id"]], "domain": "ml",
               "method": plan["method"], "baselines": plan.get("baselines", []),
               "metrics": [m for m in plan.get("metrics", []) if m.get("direction") in ("higher_is_better", "lower_is_better", "binary")],
               "environment": {"runtime": "python3.12 · numpy · scikit-learn · torch (quaera ml-env)"},
               "budget": {"estimatedUsd": round(self.gateway.remaining(), 2)},
               "noveltyCheck": {"verdict": lit["verdict"], "basis": lit.get("basis", "search"),
                                "summary": lit["summary"] or "No literature summary.",
                                "priorWork": [self.store.get(p)["uri"] for p in lit["papers"]]},
               "status": "draft"}
        return self.store.put(obj)

    # stages ---------------------------------------------------------------
    def stage_design(self) -> None:
        plan, actor = self._design()
        exp = self._put_experiment(plan, actor)
        self.set_state("plan", plan)
        self.set_state("experiment_id", exp["id"])

    def stage_plan_review(self) -> None:
        q = self.question()
        rounds = self.permissions.spec("critic")["limits"].get("maxRounds", 3)
        for round_ in range(1, rounds + 1):
            plan = self.state("plan")
            review, critic = self.ask_json("critic", prompts.CRITIC_EXPERIMENT,
                                        f"This is a PLAN (no results yet). Question: {q['title']}\nData: {q['scope']}\n"
                                        f"Plan:\n{self.plan_text(plan)}", 3000)
            serious = review.get("flawed") and review.get("severity") in ("high", "blocking")
            if not serious:
                self.store.append("plan.approved", critic, {"round": round_, "summary": review.get("summary", "")})
                return
            exp = self.store.get(self.state("experiment_id"))
            cr = self.store.put({"type": "critique", "createdBy": critic, "targetId": exp["id"],
                                 "category": (review.get("categories") or ["methodology"])[0], "severity": review["severity"],
                                 "body": f"{review.get('summary', '')} Evidence: {review.get('evidence', '')}".strip(),
                                 "blindReview": False, "status": "open"})
            obj = self.message(critic, "objection", "experiment_designer", cr["id"], cr["body"], round_=round_)
            plan, designer = self._design(f"\nThe Critic objected to your previous plan:\n{cr['body']}\nPrevious plan:\n{self.plan_text(plan)}\nRevise it.")
            exp = self._put_experiment(plan, designer, exp)
            self.set_state("plan", plan)
            self.message(designer, "response", "critic", cr["id"], "Plan revised to address the objection.", reply_to=obj["id"], round_=round_)
            again, critic = self.ask_json("critic", prompts.CRITIC_EXPERIMENT,
                                        f"This is a revised PLAN (no results yet). Question: {q['title']}\nData: {q['scope']}\n"
                                        f"Objection was: {cr['body']}\nRevised plan:\n{self.plan_text(plan)}", 3000)
            if not (again.get("flawed") and again.get("severity") in ("high", "blocking")):
                self.store.put({**cr, "createdBy": critic, "status": "resolved",
                                "resolution": {"by": critic, "at": now(), "text": again.get("summary") or "The revised plan resolves the objection."}})
                self.store.append("plan.approved", critic, {"round": round_, "summary": again.get("summary", "")})
                return
            self.store.put({**cr, "createdBy": critic, "status": "rejected_with_reason",
                            "resolution": {"by": critic, "at": now(), "text": f"Revision insufficient: {again.get('summary', '')}"}})
        decision = self.ask_human(self.det("director"), "approve_experiment", self.state("experiment_id"),
                                  f"The Critic and the Designer did not agree in {rounds} rounds. Final plan:\n{self.plan_text(self.state('plan'))}", 0.0)
        if not decision.approved:
            raise StopResearch("no agreement was reached on the experiment plan")

    def stage_preregistration(self) -> None:
        plan, h = self.state("plan"), self.selected_hypothesis()
        designer = self.store.get(self.state("experiment_id"))["createdBy"]
        pre = {"type": "preregistration", "createdBy": designer, "hypothesisId": h["id"],
               "primaryMetric": plan["primaryMetric"], "successCriterion": plan["successCriterion"],
               "analysisPlan": plan["analysisPlan"], "seeds": plan["seeds"], "lockedAt": now()}
        pre["contentHash"] = prereg_hash(pre)
        pre = self.store.put(pre)
        exp = self.store.get(self.state("experiment_id"))
        self.store.put({**exp, "preregistrationId": pre["id"], "status": "awaiting_approval"}, by=exp["createdBy"])

    def stage_experiment_approval(self) -> None:
        exp = self.store.get(self.state("experiment_id"))
        # The cost is the model budget left at approval time (calls made since design are subtracted).
        exp = {**exp, "budget": {**exp["budget"], "estimatedUsd": math.floor(self.gateway.remaining() * 10000) / 10000}}
        pre = self.store.get(exp["preregistrationId"])
        verdict = exp["noveltyCheck"]["verdict"]
        options = ["Proceed", "Replicate the known result", "Change direction (stop the research)"] if verdict not in ("novel", "unknown") else None
        summary = (f"{exp['id']}: {exp['method']}\nPreregistration {pre['id']}: {pre['primaryMetric']} · {pre['successCriterion']} · {pre['seeds']} seed\n"
                   f"Novelty: {verdict} ({exp['noveltyCheck'].get('basis')}) · Model budget: ${exp['budget']['estimatedUsd']:.2f} · Compute: local")
        decision = self.ask_human(exp["createdBy"], "approve_experiment", exp["id"], summary, exp["budget"]["estimatedUsd"], options)
        if not decision.approved or (options and decision.choice == 2):
            raise StopResearch("the human did not approve the experiment")
        choice = ["proceed", "replicate", "change_direction"][decision.choice] if options else "proceed"
        approval = {"by": self.human(), "at": now(), "decision": "approved", "autonomyLevel": decision.autonomy}
        self.store.put({**exp, "status": "approved", "approval": approval,
                        "noveltyCheck": {**exp["noveltyCheck"], "userChoice": choice},
                        "budget": {**exp["budget"], "approvedUsd": exp["budget"]["estimatedUsd"]}}, by=self.human())

    def _exec(self, role: str, args: list[str], workdir: str | None = None, timeout_s: int = 600) -> dict:
        return self.tools.call_json(role, "sandbox.exec", workdir=workdir or self.workdir, command=["python", *args],
                                    data_dir=self.data_dir, timeout_s=timeout_s)

    def _run_obj(self, actor: dict, kind: str, seed: int, ok: bool, started: str, metrics: dict, commit: str, log: str) -> dict:
        digest = self.store.put_blob(log.encode())
        art = self.store.put({"type": "artifact", "createdBy": actor, "kind": "log", "uri": f"cas:{digest}",
                              "provider": "local", "sha256": digest, "mediaType": "text/plain"})
        return self.store.put({"type": "run", "createdBy": actor, "experimentId": self.state("experiment_id"), "kind": kind,
                               "status": "succeeded" if ok else "failed", "seed": seed,
                               "config": {"commit": commit, "pilot": kind == "pilot"}, "hardware": "local CPU",
                               "startedAt": started, "endedAt": now(), "metrics": metrics, "logs": art["id"]})

    def stage_pilot(self) -> None:
        plan = self.state("plan")
        q = self.question()
        limit = self.permissions.spec("engineer")["limits"].get("maxFixAttempts", 3)
        prompt = f"Question: {q['title']}\nData: {q['scope']}\nApproved plan:\n{self.plan_text(plan)}"
        wanted = {m["name"] for m in plan.get("metrics", [])}
        for attempt in range(limit + 1):
            started = now()
            text, actor = self.guarded(self.llm, "engineer", prompts.ENGINEER_ML, prompt, 6000)
            m = re.search(r"```python\s*\n(.*?)```", text, re.S)
            code = (m.group(1) if m else text).strip() + "\n"
            commit = self.tools.call_json("engineer", "sandbox.write", workdir=self.workdir, path="experiment.py",
                                          content=code, message=f"pilot attempt {attempt}")["commit"]
            self.activity("engineer", f"Running pilot (attempt {attempt + 1})", "start")
            res = self._exec("engineer", ["experiment.py", "--seed", "0", "--pilot"], timeout_s=180)
            self.activity("engineer", f"Running pilot (attempt {attempt + 1})", "done" if res.get("returncode") == 0 else "fail",
                          res.get("stderr", "")[-200:] if res.get("returncode") else "")
            found = METRICS_RE.search(res["stdout"])
            metrics = json.loads(found.group(1)) if found else {}
            missing = wanted - set(metrics)
            ok = res["returncode"] == 0 and found is not None and not missing
            self._run_obj(actor, "pilot", 0, ok, started, {k: float(v) for k, v in metrics.items()}, commit,
                          res["stdout"][-3000:] + "\n" + res["stderr"][-3000:])
            if ok:
                self.set_state("code", {"commit": commit})
                return
            problem = (f"exit code {res['returncode']}, timed_out={res['timed_out']}\nstderr:\n{res['stderr'][-1500:]}\n"
                       f"stdout tail:\n{res['stdout'][-800:]}\nmissing metrics: {sorted(missing) if found else 'QUAERA_METRICS line not found'}")
            prompt += f"\n\nAttempt {attempt + 1} failed:\n```python\n{code}```\n{problem}\nFix the script."
        raise StopResearch("the Engineer could not write a working pilot script")

    def stage_run(self) -> None:
        code, seeds = self.state("code"), self.store.latest("preregistration")[-1]["seeds"]
        engineer = self.actors.get("engineer") or next(r["createdBy"] for r in reversed(self.store.latest("run")) if r["kind"] == "pilot")
        def one(seed: int) -> str:
            lane = f"seed {seed}"
            self.activity("engineer", f"Running experiment: seed {seed + 1}/{seeds}", "start", lane=lane)
            started = now()
            res = self._exec("engineer", ["experiment.py", "--seed", str(seed)])
            found = METRICS_RE.search(res["stdout"])
            metrics = {k: float(v) for k, v in json.loads(found.group(1)).items()} if found else {}
            ok = res["returncode"] == 0 and bool(found)
            run = self._run_obj(engineer, "full", seed, ok, started, metrics,
                                code["commit"], res["stdout"][-3000:] + "\n" + res["stderr"][-3000:])
            self.activity("engineer", f"Running experiment: seed {seed + 1}/{seeds}", "done" if ok else "fail",
                          ", ".join(f"{k}={v:.4g}" for k, v in list(metrics.items())[:3]), lane=lane)
            return run["id"]

        # Seeds run concurrently in PARALLEL_RUNS lanes (each sandbox process within its own memory/CPU limit; sequential on GPU).
        width = 1 if self.store.meta("gpu") else PARALLEL_RUNS
        ids = []
        for i in range(0, seeds, width):
            ids += self.parallel(*[(lambda seed=seed: one(seed)) for seed in range(i, min(seeds, i + width))])
        self.set_state("full_runs", ids)

    def _analysis_input(self) -> tuple[dict, dict, list[dict]]:
        runs = [self.store.get(i) for i in self.state("full_runs")]
        ok = [r for r in runs if r["status"] == "succeeded"]
        pre = self.store.latest("preregistration")[-1]
        return pre, stats_table(ok), ok

    def stage_analysis(self) -> None:
        pre, table, ok = self._analysis_input()
        if not ok:
            raise StopResearch("none of the full runs succeeded")
        out, analyst = self.ask_json("analyst", prompts.ANALYST,
                                     f"Pre-registration:\n{json.dumps({k: pre[k] for k in ('primaryMetric', 'successCriterion', 'analysisPlan', 'seeds')}, ensure_ascii=False)}\n"
                                     f"Statistics table (computed by code, {len(ok)} successful seeds):\n{json.dumps(table, ensure_ascii=False)}", 2000)
        self._write_result(analyst, out, ok, table)

    def _write_result(self, analyst: dict, out: dict, ok: list[dict], table: dict, prev: dict | None = None) -> dict:
        relation = out.get("relation") if out.get("relation") in ("supports", "contradicts", "inconclusive") else "inconclusive"
        res = self.store.put({**(prev or {}), "type": "result", "createdBy": analyst, "experimentId": self.state("experiment_id"),
                              "runIds": [r["id"] for r in ok], "summary": out.get("summary") or "No summary.",
                              "metrics": {f"{k}_mean": v["mean"] for k, v in table.items()},
                              "limitations": out.get("limitations") or ["No limitations stated."],
                              "negative": bool(out.get("negative", relation != "supports"))})
        link = next((l for l in self.store.latest("evidence_link") if l["resultId"] == res["id"]), None)
        self.store.put({**(link or {}), "type": "evidence_link", "createdBy": analyst, "resultId": res["id"],
                        "hypothesisId": self.selected_hypothesis()["id"], "relation": relation, "independence": "L1",
                        "context": f"{len(ok)} seeds, same code and data"})
        self.set_state("result_id", res["id"])
        self.set_state("analysis", {"relation": relation, "table": table})
        return res

    def stage_critique(self) -> None:
        q = self.question()
        rounds = self.permissions.spec("critic")["limits"].get("maxRounds", 3)
        for round_ in range(1, rounds + 1):
            pre, table, ok = self._analysis_input()
            res = self.store.get(self.state("result_id"))
            # Blind review order: raw numbers and config first, then the preregistration, the interpretation last.
            report = (f"Question: {q['title']}\nData: {q['scope']}\n\n1) Raw statistics ({len(ok)} seeds): {json.dumps(table, ensure_ascii=False)}\n"
                      f"Run configs: {json.dumps([{'seed': r['seed'], **r['config'], 'metrics': r['metrics']} for r in ok], ensure_ascii=False)}\n"
                      f"Plan: {self.plan_text(self.state('plan'))}\n\n2) Pre-registration: {pre['primaryMetric']} · {pre['successCriterion']} · locked {pre['lockedAt']}\n\n"
                      f"3) Analyst interpretation: {res['summary']} → relation {self.state('analysis')['relation']}. Limitations: {res['limitations']}")
            review, critic = self.ask_json("critic", prompts.CRITIC_EXPERIMENT, f"Experiment report:\n{report}", 3000)
            # Every flaw found in the interpretation (medium included) is opened as an objection and awaits the Analyst's reply.
            # Previously only high/blocking objections were opened; in live fault injection the Critic rated an
            # overgeneralization "medium", the finding was silently dropped and reached the report (evals/critic_live_check.py).
            if not review.get("flawed"):
                self.store.append("result.reviewed", critic, {"round": round_, "summary": review.get("summary", ""),
                                                             "flawed": bool(review.get("flawed")), "severity": review.get("severity")})
                return
            cr = self.store.put({"type": "critique", "createdBy": critic, "targetId": res["id"],
                                 "category": (review.get("categories") or ["methodology"])[0],
                                 "severity": review.get("severity") if review.get("severity") in ("low", "medium", "high", "blocking") else "medium",
                                 "body": f"{review.get('summary', '')} Evidence: {review.get('evidence', '')}".strip(),
                                 "blindReview": True, "status": "open"})
            obj = self.message(critic, "objection", "analyst", cr["id"], cr["body"], round_=round_)
            answer, analyst = self.ask_json("analyst", prompts.ANALYST_RESPONSE,
                                         f"Objection: {cr['body']}\nYour analysis: {res['summary']} (relation {self.state('analysis')['relation']})\n"
                                         f"Statistics table: {json.dumps(table, ensure_ascii=False)}\nPre-registration: {pre['successCriterion']}", 2000)
            self.message(analyst, "response", "critic", cr["id"], answer.get("response") or "Response.", reply_to=obj["id"], round_=round_)
            if answer.get("accept"):
                self._write_result(analyst, answer, ok, table, res)
                self.store.put({**cr, "createdBy": critic, "status": "accepted",
                                "resolution": {"by": critic, "at": now(), "text": f"The Analyst accepted the objection: {answer.get('response', '')}"}})
            # next round: the Critic re-reviews the updated result
        open_crit = [c for c in self.store.latest("critique") if c["status"] == "open" and c["targetId"] == self.state("result_id")]
        self.store.append("critique.unresolved", self.det("director"), {"open": [c["id"] for c in open_crit]})

    def stage_verification(self) -> None:
        runs = [self.store.get(i) for i in self.state("full_runs")]
        ref = next((r for r in runs if r["status"] == "succeeded"), None)
        if not ref:
            return
        verifier = self.det("verifier")
        clean = self.tools.call_json("verifier", "sandbox.create_clean", workdir=self.workdir, commit=ref["config"]["commit"])["workdir"]
        started = now()
        self.activity("verifier", f"Re-running in a clean environment: seed {ref['seed']}", "start")
        res = self._exec("verifier", ["experiment.py", "--seed", str(ref["seed"])], workdir=clean)
        self.activity("verifier", f"Re-running in a clean environment: seed {ref['seed']}", "done" if res.get("returncode") == 0 else "fail")
        found = METRICS_RE.search(res["stdout"])
        metrics = {k: float(v) for k, v in json.loads(found.group(1)).items()} if found else {}
        run = self._run_obj(verifier, "verification", ref["seed"], res["returncode"] == 0 and bool(found), started, metrics,
                            ref["config"]["commit"], res["stdout"][-3000:] + "\n" + res["stderr"][-3000:])
        reproduced, diffs = compare(ref["metrics"], metrics) if metrics else ("no", ["the verification run produced no metrics"])
        self.store.put({"type": "verification", "createdBy": verifier, "resultId": self.state("result_id"),
                        "verificationRunIds": [run["id"]], "reproduced": reproduced, "crossModel": False,
                        "tolerance": "exact (|diff| ≤ 1e-9); partial up to 1%", "differences": diffs[:10]})

    def stage_conclude(self) -> None:
        h = self.state("hypothesis_id") and self.selected_hypothesis()
        if not h:
            return
        analysis = self.state("analysis") or {}
        open_crit = [c for c in self.store.latest("critique") if c["status"] == "open"]
        verified = any(v["reproduced"] == "yes" for v in self.store.latest("verification"))
        relation = analysis.get("relation")
        if open_crit:
            status = "under_critique"
        elif verified and relation == "supports":
            status = "supported"
        elif verified and relation == "contradicts":
            status = "refuted"
        else:
            status = "inconclusive"
        if h["status"] != "rejected":
            self.store.put({**h, "status": status}, by=self.det("hypothesis"))
        replied = {m.get("inReplyTo") for m in self.store.latest("message")}
        for m in self.store.latest("message"):
            if m.get("requiresResponse") and m["id"] not in replied:
                self.message(self.det("director"), "response", m["createdBy"]["role"], m["subjectId"],
                             "Objection unresolved within the round limit; shown in the report as an open objection.", reply_to=m["id"])

    def stage_report(self) -> None:
        q = self.question()
        objs = self.store.latest()
        ids = {o["id"] for o in objs}
        facts = [{k: o.get(k) for k in ("id", "type", "status", "statement", "summary", "relation", "reproduced", "body",
                                        "successCriterion", "primaryMetric", "limitations", "negative") if o.get(k) is not None}
                 for o in objs if o["type"] in ("hypothesis", "preregistration", "result", "evidence_link", "critique", "verification")]
        try:
            out, writer = self.ask_json("writer", prompts.WRITER,
                                        f"Original question: {q['title']}\nFacts:\n{json.dumps(facts, ensure_ascii=False)}", 2500)
        except StopResearch:
            out, writer = {"discussion": [], "answer": "unclear", "answerReason": ""}, self.det("writer")
        kept, dropped = [], []
        for sentence in out.get("discussion", []):
            m = ID_RE.search(sentence.strip())
            cited = [x.strip() for x in m.group(1).split(",")] if m else []
            (kept if cited and all(c in ids for c in cited) else dropped).append(sentence)
        answer = out.get("answer") if out.get("answer") in ("yes", "no", "unclear") else "unclear"
        reason = out.get("answerReason", "")
        if not ID_RE.search(reason.strip()):
            answer, reason = "unclear", f"(Answer counted as 'unclear' because the Writer's reasoning was not tied to a source) {reason}"
        self.store.append("writer.output", writer, {"kept": kept, "dropped": dropped, "answer": answer, "answerReason": reason})
        super().stage_report()

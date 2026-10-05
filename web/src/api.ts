// Sunucu (src/quaera/server.py) ile konuşan ince katman ve olay kaydından durum türetme.
// Canlı laboratuvar ve tekrar oynatma aynı `derive` fonksiyonunu kullanır; tek doğruluk kaynağı olay kaydıdır.
import { useEffect, useRef, useState } from "react";

export type Actor = { kind: "human" | "agent"; role?: string; userId?: string; model?: string };
export type QEvent = { seq: number; at: string; kind: string; actor: Actor; payload: any };
export type QObject = { id: string; type: string; createdBy: Actor; [k: string]: any };

export type Summary = {
  id: string; title: string; shortTitle: string; managerUsd: number; domain: "math" | "ml"; mode?: "verify" | "discover"; families?: string[] | null; stages: string[]; done: string[]; current: string | null;
  running: boolean; error: string | null; stopped: any; spentUsd: number; capUsd: number | null; calls: number;
  hypothesis: { id: string; status: string; statement: string } | null; branchOf: string | null; branchAt: number | null; createdAt: string | null;
};
export type Pending = { id: string; action: string; summary: string; cost_usd: number; options: string[] | null };
export type Detail = Summary & { objects: QObject[]; states: Record<string, any>; pending: Pending[]; report: string | null };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new Error(data?.error || `${r.status} ${r.statusText}`);
  return data as T;
}

export const api = {
  projects: () => req<Summary[]>("/api/projects"),
  project: (id: string) => req<Detail>(`/api/projects/${encodeURIComponent(id)}`),
  events: (id: string) => req<QEvent[]>(`/api/projects/${encodeURIComponent(id)}/events`),
  graph: (id: string) => req<{ nodes: GNode[]; edges: GEdge[] }>(`/api/projects/${encodeURIComponent(id)}/graph`),
  settings: () => req<any>("/api/settings"),
  testProvider: (id: string) => req<{ ok: boolean; answer?: string; model?: string; family?: string; error?: string; seconds: number }>(
    `/api/providers/${encodeURIComponent(id)}/test`, { method: "POST", body: "{}" }),
  tree: (id: string) => req<TreeData>(`/api/projects/${encodeURIComponent(id)}/tree`),
  branchWith: (id: string, body: object) =>
    req<{ id: string }>(`/api/projects/${encodeURIComponent(id)}/branch`, { method: "POST", body: JSON.stringify(body) }),
  iterate: (id: string, body: object) =>
    req(`/api/projects/${encodeURIComponent(id)}/iterate`, { method: "POST", body: JSON.stringify(body) }),
  triage: (id: string, note: string, expect: Record<string, unknown>) =>
    req<{ case: string }>(`/api/projects/${encodeURIComponent(id)}/triage`, { method: "POST", body: JSON.stringify({ note, expect }) }),
  memory: (q: string) => req<MemoryHit[]>(`/api/memory?q=${encodeURIComponent(q)}`),
  learnings: (opts: { limit?: number; q?: string; project?: string } = {}) =>
    req<Learning[]>(`/api/memory/learnings?${new URLSearchParams(Object.entries(opts).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]))}`),
  manager: (id: string) => req<{ history: ChatTurn[]; usage: { spentUsd: number; capUsd: number } }>(`/api/projects/${encodeURIComponent(id)}/manager`),
  ask: (id: string, text: string) =>
    req<{ reply: ChatTurn; usage: { spentUsd: number; capUsd: number } }>(`/api/projects/${encodeURIComponent(id)}/manager`, { method: "POST", body: JSON.stringify({ text }) }),
  rename: (id: string, shortTitle: string) =>
    req<{ shortTitle: string }>(`/api/projects/${encodeURIComponent(id)}/rename`, { method: "POST", body: JSON.stringify({ shortTitle }) }),
  create: (body: object) => req<{ id: string }>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  approve: (id: string, aid: string, approved: boolean, choice = 0, note = "") =>
    req(`/api/projects/${encodeURIComponent(id)}/approvals/${aid}`, { method: "POST", body: JSON.stringify({ approved, choice, note }) }),
  message: (id: string, to: string, text: string) =>
    req(`/api/projects/${encodeURIComponent(id)}/messages`, { method: "POST", body: JSON.stringify({ to, text }) }),
  run: (id: string, autonomy: string, autoLimit: number) =>
    req(`/api/projects/${encodeURIComponent(id)}/run`, { method: "POST", body: JSON.stringify({ autonomy, autoLimit }) }),
  branch: (id: string, at: number) =>
    req<{ id: string }>(`/api/projects/${encodeURIComponent(id)}/branch`, { method: "POST", body: JSON.stringify({ at }) }),
};
export type DiffOp = ["=" | "-" | "+", string];
export type TreeNode = {
  id: string; title: string; domain: string; parent: string | null; running?: boolean;
  branch: { parent: string; atStage: string; kind: "hypothesis" | "approach" | "note"; reason: string; hypothesis: string | null;
            note: string | null; by: Actor; createdAt: string } | null;
  hypothesis: { id: string; statement: string; status: string; scope: string | null } | null;
  design: { primaryMetric: string; successCriterion: string; seeds: number } | null;
  method: string | null; formal: string | null;
  outcome: "running" | "supported" | "refuted" | "inconclusive" | "stopped"; answer: string | null; proofVerified: boolean;
  reproduced: string | null; metric: { name: string; mean: number; ci95?: number[] } | null; stopped: string | null;
  stagesDone: number; costUsd: number; metricDelta: number | null;
  diff: { hypothesis: DiffOp[] | null; method: DiffOp[] | null; formal: DiffOp[] | null; successCriterion: DiffOp[] | null } | null;
};
export type TreeData = { root: string; nodes: TreeNode[]; stats: {
  branches: number; finished: number; supported: number; refuted: number; inconclusive: number; stopped: number;
  successRate: number | null; conclusiveRate: number | null; totalCostUsd: number; costPerConclusive: number | null;
  bestMetric: { project: string; name: string; mean: number } | null } };
export type Learning = { project: string; domain: string; title: string; kind: string; text: string; at: string };
export type ChatTurn = { seq: number; at: string; from: "human" | "manager"; text: string; forward?: string | null; costUsd?: number; model?: string; error?: string };
export type MemoryHit = { project: string; title: string; domain: string; outcome: string; answer: string | null; reproduced: string | null };
export type GNode = { id: string; type: string; status: string | null; label: string };
export type GEdge = { from: string; to: string; label: string };

/** Olay kaydını SSE ile canlı izler; bağlantı koparsa tarayıcı EventSource kendisi yeniden bağlanır. */
export function useEventLog(pid: string | null) {
  const [events, setEvents] = useState<QEvent[]>([]);
  const [status, setStatus] = useState<"loading" | "live" | "offline" | "error">("loading");
  const last = useRef(0);
  useEffect(() => {
    if (!pid) return;
    setEvents([]); last.current = 0; setStatus("loading");
    const es = new EventSource(`/api/projects/${encodeURIComponent(pid)}/stream?after=0`);
    let buf: QEvent[] = [];
    let timer: number | undefined;
    const flush = () => { const b = buf; buf = []; timer = undefined; setEvents((prev) => [...prev, ...b]); };
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "error" : "offline");
    es.onmessage = (m) => {
      const e = JSON.parse(m.data) as QEvent;
      if (e.seq <= last.current) return;
      last.current = e.seq; buf.push(e);
      if (timer === undefined) timer = window.setTimeout(flush, 120);   // toplu güncelleme: ilk yüklemede yüzlerce olay
    };
    return () => { es.close(); if (timer) clearTimeout(timer); };
  }, [pid]);
  return { events, status };
}

// --- ajanlar ve aşamalar ------------------------------------------------------------------

export const ROLES = ["director", "literature", "hypothesis", "experiment_designer", "engineer", "analyst", "critic", "verifier", "writer"] as const;
export type Role = (typeof ROLES)[number];
export const ROLE_NAME: Record<string, string> = {
  director: "Director", literature: "Literature", hypothesis: "Hypothesis", experiment_designer: "Experiment designer",
  engineer: "Engineer", analyst: "Analyst", critic: "Critic", verifier: "Verifier", writer: "Writer", manager: "Project manager", human: "You",
};
export const ROLE_JOB: Record<string, string> = {
  director: "Runs the plan and the budget", literature: "Surveys sources, checks novelty", hypothesis: "Proposes testable hypotheses",
  experiment_designer: "Designs the experiment and preregistration", engineer: "Writes and runs code and proofs", analyst: "Interprets results statistically",
  critic: "Reviews blind, raises objections", verifier: "Reproduces in a clean environment", writer: "Writes the cited report",
  manager: "Watches the project and answers you without interrupting the team",
};
export const STAGE_NAME: Record<string, string> = {
  literature: "Literature review", data_profile: "Data profile", explore: "Exploration (small cases)", hypothesis: "Hypotheses", hypothesis_approval: "Hypothesis approval", design: "Experiment design",
  plan_review: "Plan review", preregistration: "Preregistration", experiment_approval: "Experiment approval", pilot: "Pilot", run: "Experiment runs",
  formalize: "Formalization", statement_review: "Statement review", prove: "Proof", analysis: "Analysis",
  critique: "Critique", result_review: "Result review", verification: "Verification", conclude: "Conclusion", report: "Report",
  landscape: "Research landscape", target: "Target claim", ideation: "Multi-model ideation", cross_review: "Cross-review",
  strategy_approval: "Strategy choice", program: "Lemma program", attack: "Attack rounds", synthesis: "Synthesis",
};
export const STAGE_ROLE: Record<string, Role> = {
  literature: "literature", data_profile: "engineer", explore: "engineer", hypothesis: "hypothesis", hypothesis_approval: "director", design: "experiment_designer",
  plan_review: "critic", preregistration: "experiment_designer", experiment_approval: "director", pilot: "engineer", run: "engineer",
  formalize: "engineer", statement_review: "critic", prove: "engineer", analysis: "analyst", critique: "critic",
  result_review: "critic", verification: "verifier", conclude: "director", report: "writer",
  landscape: "literature", target: "hypothesis", ideation: "hypothesis", cross_review: "critic", strategy_approval: "director",
  program: "engineer", attack: "engineer", synthesis: "engineer",
};
export const TOOL_NAME: Record<string, string> = {
  "lean.compile": "Compiling Lean", "sandbox.exec": "Running in the sandbox", "sandbox.write": "Writing code",
  "sandbox.create_clean": "Preparing a clean environment", "arxiv.search": "Searching arXiv", "openalex.search": "Searching OpenAlex",
  "mathlib.search": "Searching Mathlib", "arxiv.lookup": "Verifying a source", "crossref.lookup": "Verifying a DOI",
};
export const PURPOSE_NAME: Record<string, string> = {
  LITERATURE_PLAN: "Literature search plan", LITERATURE_SUMMARY: "Literature summary and novelty verdict", HYPOTHESIS: "Hypothesis proposals",
  HYPOTHESIS_ML: "Hypothesis proposals (ML)", RANK_HYPOTHESES: "Scoring the hypothesis candidates", FORMALIZE: "Writing the Lean statement",
  CRITIC_STATEMENT: "Reviewing the formal statement", BACKTRANSLATE: "Independent back-translation of the Lean statement", PROVE: "Writing a proof",
  SKETCH: "Proof sketch (splitting into lemmas)", REFUTE: "Refutation attempt (counterexample)", EXPLORE_MATH: "Small-case exploration script",
  CRITIC_RESULT: "Reviewing the proof", DESIGN_ML: "Experiment design", CRITIC_EXPERIMENT: "Blind review of the experiment", ENGINEER_ML: "Experiment code",
  ANALYST: "Statistical analysis", ANALYST_RESPONSE: "Answering an objection", WRITER: "Report discussion", OTHER: "Model call",
  LANDSCAPE: "Mapping approaches and barriers", TARGET: "Stating the target claim", IDEATE: "Proposing attack strategies",
  CROSS_REVIEW: "Cross-reviewing a strategy", PROGRAM: "Turning the strategy into lemmas", REPAIR: "Repairing a refuted lemma",
};
export const ACTION_NAME: Record<string, string> = {
  accept_hypothesis: "choose a hypothesis", approve_experiment: "approve the experiment", raise_budget_cap: "raise the budget cap", publish: "publish",
  extra_spend: "approve extra spending",
};

export type AgentState = {
  role: Role; status: "working" | "done" | "idle" | "objecting"; lastAt: string | null; calls: number; costUsd: number; model: string | null;
  said: number; current: Step[];             // şu an süren adımlar (paralel şeritler dahil)
};
export type Step = { role: string; lane: string | null; step: string; status: "start" | "done" | "fail"; detail: string; at: string; endAt?: string; seq: number };
export type LiveTool = { role: string; tool: string; code?: string; args: Record<string, unknown>; at: string; seq: number; open: boolean };

export type Derived = {
  objects: Map<string, QObject>; done: string[]; current: string | null; spentUsd: number; capUsd: number | null;
  agents: AgentState[]; pending: { id: string; action: string; summary: string; costUsd: number; options: string[] | null; seq: number }[];
  stageSeq: Record<string, number>; crashed: string | null; finished: boolean;
  steps: Step[];             // tüm adımlar (başlangıç ve bitiş birleştirilmiş), zamana göre
  active: Step[];            // şu an süren adımlar
  liveTool: LiveTool | null; // en son başlayan araç (Lean derlemesi, sandbox'ta deney) ve kodu
  said: QEvent[];            // agent.said olayları
};

/** Olay kaydından ekranın ihtiyaç duyduğu her şeyi türetir (saf fonksiyon; tekrar oynatmada `events.slice(0, n)` verilir). */
export function derive(events: QEvent[], stages: string[], capFallback: number | null = null): Derived {
  const objects = new Map<string, QObject>();
  const done: string[] = [];
  const stageSeq: Record<string, number> = {};
  const per: Record<string, AgentState> = {};
  for (const r of ROLES) per[r] = { role: r, status: "idle", lastAt: null, calls: 0, costUsd: 0, model: null, said: 0, current: [] };
  let spent = 0, cap = capFallback, crashed: string | null = null, liveTool: LiveTool | null = null;
  const pending = new Map<string, Derived["pending"][number]>();
  const open = new Map<string, Step>();
  const steps: Step[] = [];
  const said: QEvent[] = [];
  for (const e of events) {
    const p = e.payload || {};
    const role = (e.actor.role && per[e.actor.role]) ? e.actor.role : null;
    if (role && e.actor.model !== "quaera/deterministic") per[role].lastAt = e.at;
    switch (e.kind) {
      case "object.put": objects.set(p.object.id, p.object); break;
      case "stage.done": done.push(p.stage); stageSeq[p.stage] = e.seq; break;
      case "model.call":
        spent += p.costUsd || 0; if (p.capUsd != null) cap = p.capUsd;
        if (per[p.role]) { per[p.role].calls++; per[p.role].costUsd += p.costUsd || 0; per[p.role].model = p.model; per[p.role].lastAt = e.at; }
        break;
      case "model.error": spent += p.costUsd || 0; break;
      case "approval.pending": pending.set(p.id, { id: p.id, action: p.action, summary: p.summary, costUsd: p.costUsd, options: p.options, seq: e.seq }); break;
      case "approval.resolved": pending.delete(p.id); break;
      case "run.crashed": crashed = p.error; break;
      case "agent.said": said.push(e); if (per[p.role]) per[p.role].said++; break;
      case "activity": {
        const key = `${p.role}|${p.lane ?? ""}|${p.step}`;
        if (p.status === "start") {
          const st: Step = { role: p.role, lane: p.lane ?? null, step: p.step, status: "start", detail: p.detail || "", at: e.at, seq: e.seq };
          open.set(key, st); steps.push(st);
        } else {
          const st = open.get(key);
          if (st) { st.status = p.status; st.endAt = e.at; if (p.detail) st.detail = p.detail; open.delete(key); }
          else steps.push({ role: p.role, lane: p.lane ?? null, step: p.step, status: p.status, detail: p.detail || "", at: e.at, endAt: e.at, seq: e.seq });
        }
        break;
      }
      case "tool.started": liveTool = { role: p.role, tool: p.tool, code: p.code, args: p.args || {}, at: e.at, seq: e.seq, open: true }; break;
      case "tool.call": {
        const lt = liveTool as LiveTool | null;
        if (lt && lt.tool === p.tool && lt.role === p.role) liveTool = { ...lt, open: false };
        break;
      }
    }
  }
  // Rapor yazıldıysa araştırma bitmiştir (eski projelerde sonradan eklenen aşamalar eksik görünmesin).
  const reported = events.some((e) => e.kind === "report.written");
  const current = reported ? null : stages.find((s) => !done.includes(s)) ?? null;
  if (reported || crashed) for (const st of open.values()) { st.status = crashed ? "fail" : "done"; st.endAt = st.endAt || st.at; }
  const active = reported || crashed ? [] : [...open.values()];
  // Süren araç çağrısı (Lean derlemesi, sandbox'ta deney) da aktif iştir: model çağrısı bitmiş olsa bile ajan çalışıyor.
  if (!reported && !crashed && liveTool && (liveTool as LiveTool).open) {
    const lt = liveTool as LiveTool;
    active.push({ role: lt.role, lane: null, step: TOOL_NAME[lt.tool] || lt.tool, status: "start", detail: "", at: lt.at, seq: lt.seq });
  }
  for (const st of steps) st.step = PURPOSE_NAME[st.step] || st.step;     // model çağrısı adımları amaç kodu taşır
  const openObjection = [...objects.values()].some((o) => o.type === "critique" && o.status === "open" && ["high", "blocking"].includes(o.severity));
  for (const st of done) if (STAGE_ROLE[st]) per[STAGE_ROLE[st]].lastAt ||= "1";   // deterministik roller (Direktör, Doğrulayıcı) de iş yapmış sayılır
  for (const a of Object.values(per)) {
    if (a.lastAt || a.calls) a.status = "done";
    a.current = active.filter((s) => s.role === a.role);
    if (a.current.length) a.status = "working";
  }
  if (current && !crashed && !active.length) per[STAGE_ROLE[current]].status = "working";
  if (openObjection) per.critic.status = "objecting";
  return { objects, done, current, spentUsd: spent, capUsd: cap, agents: ROLES.map((r) => per[r]), pending: [...pending.values()],
           stageSeq, crashed, finished: current === null, steps, active, liveTool: reported ? null : liveTool, said };
}

/** Para biçimi: "$1.17", "$0.062" (küçük tutarlarda üç ondalık). */
export const money = (x: number | null | undefined) =>
  x == null ? "—" : `$${x.toLocaleString("en-US", { minimumFractionDigits: x < 1 ? 3 : 2, maximumFractionDigits: x < 1 ? 3 : 2 })}`;
export const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const date = (iso: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—");

/** Her olayı tek satırlık açıklamaya çevirir. null dönenler zaman çizelgesinde gösterilmez. */
export type Described = { who: string; text: string; tone?: "warn" | "bad" | "good" | "info"; open?: "ranking" | "proof" | "agent" | "strategies" | "program" };

export function describe(e: QEvent): Described | null {
  const p = e.payload || {};
  const who = e.actor.kind === "human" ? "human" : e.actor.role || "director";
  switch (e.kind) {
    case "object.put": {
      const o = p.object as QObject;
      const r = o.createdBy?.kind === "human" ? "human" : o.createdBy?.role || who;
      const t: Record<string, string> = {
        question: `Research question: ${o.title}`, hypothesis: `Hypothesis ${o.id} (${o.status}): ${o.statement}`,
        experiment: `Experiment ${o.id} designed: ${o.method || ""}`, preregistration: `Preregistration ${o.id}: ${o.successCriterion || ""}`,
        result: `Result ${o.id}: ${o.summary}`, verification: `Verification ${o.id}: reproduced = ${o.reproduced}`,
        critique: `${o.status === "open" ? "Objection" : "Critique"} ${o.id} [${o.severity}] ${o.body}`,
        evidence_link: `Evidence link: ${o.resultId} → ${o.hypothesisId} (${o.relation})`,
        run: `Run ${o.id}: ${o.status || ""}`,
      };
      if (o.type === "message") {
        if (o.createdBy?.kind === "human") return { who: "human", text: `@${ROLE_NAME[o.to] || o.to}: ${o.body}`, tone: "info" };
        if (o.kind === "approval_request") return null;
        return { who: r, text: `${o.kind === "objection" ? "Objection" : o.kind === "response" ? "Reply" : "Message"}: ${o.body}`, tone: o.kind === "objection" ? "warn" : undefined };
      }
      if (o.type === "artifact") return null;
      const tone = o.type === "critique" && o.status === "open" ? "warn" : o.type === "verification" ? (o.reproduced === "yes" ? "good" : "bad") : undefined;
      return t[o.type] ? { who: r, text: t[o.type], tone } : null;
    }
    case "stage.done": return { who: STAGE_ROLE[p.stage] || who, text: `${STAGE_NAME[p.stage] || p.stage} finished`, tone: "good" };
    case "model.call": return null;
    case "tool.call": return { who: p.role || who, text: `Tool: ${p.tool}${p.args?.query ? ` — “${p.args.query}”` : ""}` };
    case "tool.error": return { who, text: `Tool error (${p.tool}): ${p.error}. Research continues.`, tone: "warn" };
    case "approval.pending": return { who: "director", text: `Waiting for you to ${ACTION_NAME[p.action] || p.action} (${money(p.costUsd)})`, tone: "info" };
    case "approval.resolved": return { who: "human", text: `${p.approved ? "Approved" : "Rejected"} (${p.id})`, tone: p.approved ? "good" : "bad" };
    case "plan.approved": return { who: "critic", text: `Plan approved: ${p.summary}`, tone: "good" };
    case "result.reviewed": return { who: "critic", text: `Result reviewed: ${p.summary}` };
    case "statement.approved": return { who: "critic", text: "Formal statement approved", tone: "good" };
    case "lean.output": return { who, text: "Lean compiler output saved" };
    case "budget.estimate_exceeded": return { who: "director", text: "A call exceeded its estimate; estimates were recalibrated", tone: "warn" };
    case "model.error": return { who: p.role || who, text: `Model error: ${p.error || ""}`, tone: "bad" };
    case "run.crashed": return { who: "director", text: `Run stopped: ${p.error}`, tone: "bad" };
    case "budget.blocked": return { who: "director", text: `Call blocked by the budget cap (estimate ${money(p.estimateUsd)})`, tone: "bad" };
    case "budget.warning": return { who: "director", text: `Most of the budget is spent: ${money(p.spentUsd)} of ${money(p.capUsd)}`, tone: "warn" };
    case "critique.unresolved": return { who: "critic", text: "Objection left unresolved; it will be stated in the report", tone: "warn" };
    case "citation.rejected": return { who: "writer", text: "A sentence without a valid source was rejected", tone: "warn" };
    case "scope.review": return { who: "critic", text: `Scope review: ${p.relation || p.scopeRelation || (p.weakerThanQuestion ? "weaker than the question" : "matches the question")}` };
    case "model.invalid_json": return { who: p.role || who, text: "Invalid reply; asked again", tone: "warn" };
    case "branch.created": return null;
    case "exploration.done": return { who: "engineer", text: p.counterexample ? `Exploration: counterexample candidate found (${JSON.stringify(p.counterexample.values ?? p.counterexample)}) — checked: ${p.checked}` :
      `Exploration: no counterexample for ${p.checked}${(p.observations || []).length ? ` · ${p.observations[0]}` : ""}`, tone: p.counterexample ? "warn" : "info" };
    case "data.profiled": return { who: "engineer", text: `Data profiled: ${(p.files || []).join(", ") || "no files"}` };
    case "statement.backtranslated": return { who: "verifier", text: `Independent back-translation: ${p.translation}`, tone: (p.oddities || []).length ? "warn" : undefined };
    case "fidelity.check": return { who: "engineer", text: p.check === "vacuity" ? (p.verified ? "Assumptions are contradictory: the statement is vacuously true!" : "Vacuity check: assumptions are consistent")
      : (p.verified ? "Refutation: the negation was proved in Lean" : "Refutation attempt: no counterexample found"), tone: p.verified ? (p.check === "vacuity" ? "bad" : "warn") : undefined };
    case "branch.change": return { who: e.actor.kind === "human" ? "human" : "director",
      text: `This branch was opened (${({ hypothesis: "hypothesis changed", approach: "approach changed", note: "note added" } as any)[p.kind] || p.kind}): ${p.reason}`, tone: "info" };
    case "branch.spawned": return { who: e.actor.kind === "human" ? "human" : "director", text: `New branch opened: ${p.child} — ${p.reason}`, tone: "info" };
    case "branch.proposed": return { who: "director", text: p.decision === "stop" ? `No new branch proposed: ${p.reason}` : `Revision proposal (${p.decision}): ${p.reason}`, tone: "info" };
    case "branch.revision_failed": return { who: "director", text: `Could not get a revision proposal: ${p.error}`, tone: "warn" };
    case "report.written": return { who: "writer", text: "Report written", tone: "good" };
    case "writer.output": return { who: "writer", text: `${(p.kept || []).length} cited sentences kept, ${(p.dropped || []).length} uncited sentences dropped` };
    case "fault.injected": return { who: "director", text: `Evaluation: a deliberate fault was injected (${p.kind})`, tone: "warn" };
    case "message.delivered": return null;
    case "agent.said": return null;
    case "activity": return null;
    case "tool.started": return null;
    case "manager.chat": case "manager.call": case "manager.error": case "manager.blocked": case "memory.error": return null;
    case "strategy.proposed": return { who: "hypothesis", text: `Strategy ${p.id} (${p.family}, lens: ${p.lensName}): ${p.title}`, tone: "info", open: "strategies" };
    case "strategy.reviewed": return { who: "critic", text: `Cross-review of ${p.id} by ${p.reviewerFamily}: ${p.score}/10${p.fatalFlaw ? ` — fatal flaw: ${p.fatalFlaw}` : ""}`, tone: p.fatalFlaw ? "warn" : undefined, open: "strategies" };
    case "strategy.chosen": return { who: "human", text: `Strategy ${p.id} chosen${(p.fallbacks || []).length ? ` (fallbacks: ${p.fallbacks.join(", ")})` : ""}`, tone: "info" };
    case "strategy.dead": return { who: "director", text: `Strategy ${p.id} abandoned: ${p.reason}`, tone: "warn" };
    case "ideation.lane_failed": return { who: "hypothesis", text: `Ideation lane ${p.lane} (${p.family}) failed: ${p.error}`, tone: "warn" };
    case "program.lemma": return { who: "engineer", text: `Lemma ${p.id}${p.repairOf ? ` (repair of ${p.repairOf})` : ""}: ${p.statement}${p.status === "unformalized" ? " — could not be stated in Lean" : ""}`, open: "program" };
    case "lemma.status": return p.status === "numeric" ? { who: "engineer", text: `${p.id}: numerical check — ${p.detail}` }
      : { who: "engineer", text: `${p.id} round ${p.round} via ${p.family}: ${({ verified: "verified in Lean", refuted: "refuted in Lean", failed: "no proof this round" } as any)[p.status] || p.status}`,
          tone: p.status === "verified" ? "good" : p.status === "refuted" ? "bad" : undefined, open: "program" };
    case "lemma.reverified": return { who: "verifier", text: `${p.id} recompiled in a clean process: ${p.verified ? "verified" : "FAILED"}`, tone: p.verified ? "good" : "bad" };
    case "attack.round": return { who: "director", text: `Attack round ${p.round}: open lemmas ${(p.open || []).join(", ")}` };
    case "attack.done": return { who: "director", text: `Attack finished: ${p.verified} verified, ${p.refuted} refuted, ${p.open} open${p.stopped ? ` (${p.stopped})` : ""}` };
    case "synthesis.done": return { who: "engineer", text: p.solved ? "Main theorem assembled from the verified lemmas in Lean" : "Synthesis of the main theorem did not succeed", tone: p.solved ? "good" : "warn" };
    case "synthesis.skipped": return { who: "director", text: `Synthesis skipped: ${p.reason}` };
    case "hypotheses.ranked": {
      const best = (p.candidates || [])[0];
      return { who: "critic", text: `Scored ${(p.candidates || []).length} hypothesis candidates on testability, plausibility, novelty, scope and cost. Top: ${best ? `${best.score}/10 — ${String(best.statement).slice(0, 90)}` : "—"}`, tone: "info", open: "ranking" };
    }
    case "proof.search": return { who: "engineer", text: p.solved ? `Proof found (${p.modelCalls} model calls)` : `No proof found${p.stopped ? `: ${p.stopped}` : ""} (${p.modelCalls ?? 0} model calls)`, tone: p.solved ? "good" : "warn", open: "proof" };
    case "hypothesis.invalid": return { who: "hypothesis", text: `Skipped a candidate that broke the contract: ${String(p.statement).slice(0, 80)}`, tone: "warn" };
    case "memory.recalled": return { who: "director", text: `${(p.items || []).length} similar past projects from lab memory were shown to the Hypothesis agent for orientation (not evidence)` };
    case "package.exported": return { who: "human", text: "Evidence package exported" };
    case "state": return p.key === "stopped" && p.value ? { who: "director", text: `Research stopped: ${p.value}`, tone: "bad" } : null;
    default: return { who, text: e.kind };
  }
}

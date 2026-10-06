import { FormEvent, KeyboardEvent, useRef, useState } from "react";
import { ACTION_NAME, Derived, Pending, QEvent, ROLES, ROLE_NAME, STAGE_NAME, Summary, api, describe, money, time } from "../api";
import { ActiveAgents, AgentDrawer, TeamList } from "../agents";
import { Avatar } from "../components";
import { DiscoveryView } from "../discovery";
import { MLExperiment, MathExperiment, statesOf } from "../experiment";
import { HypothesisRanking } from "../ranking";
import { ManagerDrawer, ManagerState, ManagerThread, useManager } from "../manager";
import { Code, InlineMath } from "../rich";

type Props = {
  summary: Pick<Summary, "id" | "title" | "domain" | "stages" | "running" | "error" | "stopped"> & { mode?: string };
  events: QEvent[];
  d: Derived;
  pending: Pending[];          // live: pending approvals on the server; empty in replay
  live: boolean;
  onChanged?: () => void;
  onToast?: (m: string) => void;
  onBranched?: (id: string) => void;
};

// Main stages (the top bar in the mockup): detailed stages are grouped here.
const PHASES: Record<string, [string, string[]][]> = {
  math: [["Literature", ["literature"]], ["Hypothesis", ["hypothesis", "hypothesis_approval"]],
         ["Exploration & statement", ["explore", "design", "experiment_approval", "formalize", "statement_review"]], ["Proof", ["prove"]],
         ["Verification", ["analysis", "result_review", "verification"]], ["Report", ["conclude", "report"]]],
  ml: [["Literature & data", ["literature", "data_profile"]], ["Hypothesis", ["hypothesis", "hypothesis_approval"]],
       ["Experiment design", ["design", "plan_review", "preregistration", "experiment_approval"]], ["Experiment", ["pilot", "run"]],
       ["Analysis", ["analysis", "critique", "verification"]], ["Report", ["conclude", "report"]]],
  discover: [["Landscape", ["literature", "landscape"]], ["Target", ["target", "design"]],
             ["Ideation", ["ideation", "cross_review", "strategy_approval"]], ["Lemma program", ["formalize", "statement_review", "program"]],
             ["Attack", ["attack", "synthesis"]], ["Verification", ["analysis", "result_review", "verification"]], ["Report", ["conclude", "report"]]],
};

export function PhaseBar({ domain, d, mode }: { domain: string; d: Derived; mode?: string }) {
  const phases = (mode === "discover" ? PHASES.discover : PHASES[domain]) || PHASES.math;
  return (
    <ol className="phases" aria-label="Research phases">
      {phases.map(([label, stages], i) => {
        const done = d.finished || stages.every((s) => d.done.includes(s));
        const cur = !done && stages.includes(d.current || "");
        return (
          <li key={label} style={{ display: "contents" }}>
            {i > 0 && <span className={`phase-line ${done || cur ? "done" : ""}`} aria-hidden="true" />}
            <span className={`phase ${done ? "done" : cur ? "cur" : ""}`} aria-current={cur ? "step" : undefined}>
              <span className="pi" aria-hidden="true">{done ? "✓" : ""}</span>{label}
              <span className="sr-only">{done ? " (done)" : cur ? " (current)" : " (upcoming)"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function LabView({ summary, events, d, pending, live, onChanged, onToast, onBranched }: Props) {
  const [agent, setAgent] = useState<string | null>(null);
  const [showRanking, setShowRanking] = useState(false);
  const objs = [...d.objects.values()];
  const st = statesOf(events);
  const h = objs.find((o) => o.id === st.hypothesis_id) || objs.filter((o) => o.type === "hypothesis").pop();
  const pre = objs.filter((o) => o.type === "preregistration").pop();
  const ranked = [...events].reverse().find((e) => e.kind === "hypotheses.ranked");
  const result = objs.filter((o) => o.type === "result").pop();
  const pid = live ? summary.id : null;
  const m = useManager(pid);
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <div className="workspace">
      <div className="stack-lg" style={{ minWidth: 0 }}>
        <RunBar summary={summary} d={d} live={live} onChanged={onChanged} onToast={onToast} />
        {live && pending.map((p) => <DecisionCard key={p.id} pid={summary.id} p={p} d={d} events={events} domain={summary.domain} mode={summary.mode} onDone={onChanged} onToast={onToast} />)}
        {!live && d.pending.map((p) => (
          <div key={p.id} className="decision"><span className="kicker">A human decision was required here</span><h2>{ACTION_NAME[p.action] || p.action}</h2></div>
        ))}

        <section aria-labelledby="sum-h" className="stack">
          <h2 id="sum-h" className="section-title">Evidence summary</h2>
          <div className="summary-grid">
            <div className="card stack" style={{ gap: 8, gridColumn: (h?.statement || "").length > 240 ? "1 / -1" : undefined }}>
              <div className="row"><h3>Hypothesis</h3><span className="spacer" />{h && <StatusBadge s={h.status} />}</div>
              {h ? <InlineMath text={h.statement} /> : <p className="muted">Not chosen yet.</p>}
              {h?.scopeRelation?.relation === "restricted" && <p className="faint">Restricted scope: {h.scopeRelation.note}</p>}
              {ranked && <button className="btn plain sm" style={{ justifySelf: "start" }} onClick={() => setShowRanking((x) => !x)} aria-expanded={showRanking}>
                {showRanking ? "Hide scoring" : `How was it chosen? (${ranked.payload.candidates.length} candidates scored)`}</button>}
            </div>
            <div className="card stack" style={{ gap: 8 }}>
              <div className="row"><h3>Success criterion</h3><span className="spacer" />{pre?.lockedAt && <span className="lock">🔒 locked</span>}</div>
              {pre ? <><InlineMath text={pre.successCriterion} /><p className="faint">Written before the experiment; the result is judged against it.</p></>
                : <p className="muted">No preregistration yet.</p>}
            </div>
            <StatusCard stages={summary.stages} d={d} live={live} pid={summary.id} onToast={onToast} onBranched={onBranched} />
          </div>
          {showRanking && ranked && <section className="card"><div className="card-head"><h2>Hypothesis scoring</h2></div><HypothesisRanking ev={ranked} hypotheses={objs.filter((o) => o.type === "hypothesis")} /></section>}
        </section>

        <section aria-labelledby="exp-title" className="stack">
          <h2 id="exp-title" className="section-title">{summary.domain === "ml" ? "Experiment" : summary.mode === "discover" ? "Discovery" : "Proof"}</h2>
          {summary.mode === "discover" && <DiscoveryView events={events} pid={pid} />}
          {summary.domain === "ml" ? <MLExperiment d={d} events={events} pid={pid} /> : <MathExperiment d={d} events={events} pid={pid} />}
        </section>

        <section className="card" aria-labelledby="feed-h">
          <div className="card-head"><h2 id="feed-h">Recent activity</h2><span className="faint">{live ? "live" : "replay"}</span></div>
          <Feed events={events} onAgent={setAgent} onRanking={() => setShowRanking(true)} />
        </section>
        {live && <Composer pid={summary.id} m={m} onToast={onToast} finished={d.finished} onManager={() => { if (window.matchMedia("(max-width: 1100px)").matches) setChatOpen(true); }} />}
      </div>

      <aside className="rail" aria-label="Manager, budget and team">
        {live && pid && <ManagerCard pid={pid} m={m} onExpand={() => setChatOpen(true)} onToast={onToast} />}
        <Budget d={d} />
        <ActiveAgents d={d} running={!!summary.running} onOpen={setAgent} />
        <TeamList agents={d.agents} onOpen={setAgent} />
        <Limits items={[...(h?.scopeRelation?.relation === "restricted" ? [h.scopeRelation.note] : []), ...(result?.limitations || [])]} />
      </aside>
      <AgentDrawer role={agent} d={d} pid={pid} onClose={() => setAgent(null)} />
      {live && pid && <ManagerDrawer open={chatOpen} pid={pid} m={m} onClose={() => setChatOpen(false)} onToast={onToast} />}
    </div>
  );
}

function Limits({ items }: { items: string[] }) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 2);
  return (
    <div className="callout" role="note"><span className="ci" aria-hidden="true">i</span>
      <div className="stack" style={{ gap: 6 }}><strong>Limits of this result</strong>
        {shown.map((l, i) => <span key={i} className="muted" style={{ display: "-webkit-box", WebkitLineClamp: all ? "unset" : 4, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{l}</span>)}
        {(items.length > 2 || items.some((l) => l.length > 220)) && <button className="more" onClick={() => setAll((a) => !a)} aria-expanded={all}>{all ? "Show less" : `All (${items.length})`}</button>}
      </div></div>
  );
}

function StatusCard({ stages, d, live, pid, onToast, onBranched }: { stages: string[]; d: Derived; live: boolean; pid: string; onToast?: (m: string) => void; onBranched?: (id: string) => void }) {
  const [all, setAll] = useState(false);
  const reached = stages.filter((s) => d.done.includes(s) || d.current === s);
  const shown = all ? reached : reached.slice(-4);
  return (
    <div className="card stack" style={{ gap: 6 }}>
      <div className="row"><h3>Status</h3><span className="spacer" /><span className="faint">{d.done.length}/{stages.length} stages</span></div>
      <div className="meterbar" aria-hidden="true"><span style={{ width: `${(d.finished ? 1 : d.done.length / stages.length) * 100}%` }} /></div>
      <ol className="checklist" aria-label="Stages">
        {shown.map((s) => {
          const done = d.done.includes(s);
          return (
            <li key={s}><span className={`ck ${done ? "done" : "run"}`} aria-hidden="true">{done ? "✓" : ""}</span>
              <span>{STAGE_NAME[s] || s}</span>
              {done && live && onBranched ? <BranchButton pid={pid} seq={d.stageSeq[s]} stage={s} onToast={onToast} onBranched={onBranched} /> : <span />}</li>
          );
        })}
      </ol>
      {reached.length > 4 && <button className="btn plain sm" style={{ justifySelf: "start" }} onClick={() => setAll((a) => !a)} aria-expanded={all}>{all ? "Show less" : `All stages (${reached.length})`}</button>}
    </div>
  );
}

function StatusBadge({ s }: { s: string }) {
  const map: Record<string, [string, string]> = {
    supported: ["good", "supported"], refuted: ["bad", "refuted"], inconclusive: ["warn", "inconclusive"], accepted: ["info", "being tested"],
    testing: ["info", "being tested"], under_critique: ["warn", "under objection"], draft: ["", "draft"], rejected: ["", "rejected"],
  };
  const [c, t] = map[s] || ["", s];
  return <span className={`badge ${c}`}>{t}</span>;
}

function Budget({ d }: { d: Derived }) {
  const frac = d.capUsd ? Math.min(1, d.spentUsd / d.capUsd) : 0;
  return (
    <section className="card stack" aria-labelledby="bud-h" style={{ gap: 8 }}>
      <div className="row"><h2 id="bud-h">Budget</h2><span className="spacer" /><strong>{money(d.spentUsd)}</strong><span className="faint">/ {money(d.capUsd)}</span></div>
      <div className={`meterbar ${frac > 0.9 ? "bad" : frac > 0.7 ? "warn" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(frac * 100)} aria-label="Budget used"><span style={{ width: `${frac * 100}%` }} /></div>
      <div className="row faint"><span>{Math.round(frac * 100)}% used</span><span className="spacer" /><span>{money(d.capUsd == null ? null : Math.max(0, d.capUsd - d.spentUsd))} left</span></div>
    </section>
  );
}

function BranchButton({ pid, seq, stage, onToast, onBranched }: { pid: string; seq: number; stage: string; onToast?: (m: string) => void; onBranched: (id: string) => void }) {
  const go = async () => {
    if (!confirm(`Re-run everything after “${STAGE_NAME[stage] || stage}” in a new branch? This project stays unchanged.`)) return;
    try { const r = await api.branch(pid, seq); onToast?.(`Branch created: ${r.id}`); onBranched(r.id); }
    catch (e: any) { onToast?.(`Could not create a branch: ${e.message}`); }
  };
  return <button className="icon-btn" onClick={go} aria-label={`Branch after ${STAGE_NAME[stage]}`} title="Branch from here">⑂</button>;
}

function Feed({ events, onAgent, onRanking }: { events: QEvent[]; onAgent: (r: string) => void; onRanking: () => void }) {
  const [all, setAll] = useState(false);
  const items = events.map((e) => ({ e, d: describe(e) })).filter((x) => x.d).reverse();
  const shown = all ? items : items.slice(0, 12);
  if (!items.length) return <p className="muted">No activity yet.</p>;
  return (
    <>
      <ol className="feed" aria-live="polite" aria-relevant="additions">
        {shown.map(({ e, d }) => (
          <li key={e.seq} className={d!.tone || ""}>
            <Avatar role={d!.who} size="sm" />
            <p className="ev">
              {(ROLES as readonly string[]).includes(d!.who)
                ? <button className="who" style={{ all: "unset", cursor: "pointer", fontWeight: 600, marginRight: 6 }} onClick={() => onAgent(d!.who)}>{ROLE_NAME[d!.who]}</button>
                : <span className="who">{ROLE_NAME[d!.who] || d!.who}</span>}
              <InlineMath text={d!.text} />
              {d!.open === "ranking" && <button className="open" onClick={onRanking}>See scoring</button>}
              {d!.open === "proof" && <a className="open" href="#proof-h" style={{ marginLeft: 6 }}>See the proof process</a>}
              {d!.open === "strategies" && <a className="open" href="#str-h" style={{ marginLeft: 6 }}>See strategies</a>}
              {d!.open === "program" && <a className="open" href="#prog-h" style={{ marginLeft: 6 }}>See the program</a>}
            </p>
            <time className="when" dateTime={e.at}>{time(e.at)}</time>
          </li>
        ))}
      </ol>
      {items.length > 12 && <button className="btn plain sm" onClick={() => setAll((a) => !a)} aria-expanded={all}>{all ? "Show less" : `All activity (${items.length})`}</button>}
    </>
  );
}

function RunBar({ summary, d, live, onChanged, onToast }: { summary: Props["summary"]; d: Derived; live: boolean; onChanged?: () => void; onToast?: (m: string) => void }) {
  const [autonomy, setAutonomy] = useState("manual");
  const [limit, setLimit] = useState(0.5);
  const [busy, setBusy] = useState(false);
  const crashed = d.crashed || summary.error;
  const paused = live && !summary.running && !d.finished;
  if (!crashed && !paused) return null;
  const resume = async () => {
    setBusy(true);
    try { await api.run(summary.id, autonomy, limit); onToast?.("Research resumed"); onChanged?.(); }
    catch (e: any) { onToast?.(`Could not start: ${e.message}`); }
    finally { setBusy(false); }
  };
  return (
    <section className="card stack" aria-label="Run status" style={{ gap: 10 }}>
      <div className="row"><span className={`badge ${crashed ? "bad" : "warn"}`}>{crashed ? "The run stopped with an error" : summary.stopped ? "Research stopped" : "Paused"}</span>
        {d.current && <span className="muted">Next stage: <strong style={{ color: "var(--text)" }}>{STAGE_NAME[d.current] || d.current}</strong></span>}</div>
      {crashed && <p className="err">{crashed}</p>}
      {paused && (
        <div className="row">
          <label className="row" style={{ fontWeight: 500, gap: 6 }}><span className="faint">Autonomy</span>
            <select value={autonomy} onChange={(e) => setAutonomy(e.target.value)} style={{ width: "auto" }}>
              <option value="manual">Ask me at every step</option><option value="under">Auto below a limit</option><option value="cap">Auto up to budget cap</option>
            </select></label>
          {autonomy === "under" && <label className="row" style={{ fontWeight: 500, gap: 6 }}><span className="faint">Limit $</span>
            <input type="number" min={0} step={0.1} value={limit} onChange={(e) => setLimit(Number(e.target.value))} style={{ width: 90 }} /></label>}
          <button className="btn primary" onClick={resume} disabled={busy}>{busy ? "Starting…" : "Resume"}</button>
        </div>
      )}
    </section>
  );
}

/** Decision card: shows in structured form what you are approving (data, method, seeds, cost, statement). */
function DecisionCard({ pid, p, d, events, domain, mode, onDone, onToast }: {
  pid: string; p: Pending; d: Derived; events: QEvent[]; domain: string; mode?: string; onDone?: () => void; onToast?: (m: string) => void;
}) {
  const [choice, setChoice] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const objs = [...d.objects.values()];
  const st = statesOf(events);
  const pre = objs.filter((o) => o.type === "preregistration").pop();
  const exp = objs.filter((o) => o.type === "experiment").pop();
  const ranked = [...events].reverse().find((e) => e.kind === "hypotheses.ranked");
  const scores: Record<string, any> = Object.fromEntries((ranked?.payload.candidates || []).map((c: any) => [c.statement, c]));
  const drafts = objs.filter((o) => o.type === "hypothesis" && o.status === "draft").sort((a, b) => a.id.localeCompare(b.id));
  const send = async (approved: boolean) => {
    setBusy(true);
    try { await api.approve(pid, p.id, approved, choice, note); onToast?.(approved ? "Approved" : "Rejected"); onDone?.(); }
    catch (e: any) { onToast?.(`Could not send: ${e.message}`); setBusy(false); }
  };
  const discover = mode === "discover";
  const title = p.action === "accept_hypothesis" ? (discover ? "Confirm the target claim (not narrowed)" : "Which hypothesis should be tested?")
    : p.action === "approve_experiment" ? (discover ? "Choose the strategy to pursue" : domain === "ml" ? "Approve the experiment plan" : "Approve the proof plan")
    : `Please ${ACTION_NAME[p.action] || p.action}`;
  return (
    <section className="decision" aria-labelledby={`ap-${p.id}`}>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="stack" style={{ gap: 4, flex: 1, minWidth: 0 }}>
          <span className="kicker">⏱ Your decision is needed</span>
          <h2 id={`ap-${p.id}`}>{title}</h2>
        </div>
        <span className="badge warn">{p.cost_usd > 0 ? `at most ${money(p.cost_usd)} (estimate)` : "no extra model cost"}</span>
      </div>
      {p.action === "approve_experiment" && (
        <div className="facts">
          {domain === "ml" && st.data_profile?.files?.[0] && <div className="fact"><span className="k">Dataset</span><span className="v">{st.data_profile.files.map((f: any) => f.file).join(", ")}</span>
            <span className="s">{(st.data_profile.files.find((f: any) => f.arrays)?.arrays || []).map((a: any) => `${a.name}: ${a.shape.join("×")}`).join(" · ")}</span></div>}
          {exp?.method && <div className="fact"><span className="k">Method</span><span className="v" style={{ fontWeight: 500 }}>{String(exp.method).slice(0, 140)}{String(exp.method).length > 140 ? "…" : ""}</span></div>}
          {pre && <div className="fact"><span className="k">Primary metric</span><span className="v">{pre.primaryMetric}</span>{pre.seeds && <span className="s">{pre.seeds} seeds</span>}</div>}
          <div className="fact"><span className="k">Cost (estimate)</span><span className="v">{money(p.cost_usd)}</span><span className="s">worst case; cannot exceed the cap</span></div>
          {pre && <div className="fact" style={{ gridColumn: "1 / -1" }}><span className="k">Preregistered success criterion 🔒</span><span className="v" style={{ fontWeight: 500 }}><InlineMath text={pre.successCriterion} /></span></div>}
          {domain === "math" && st.exploration && <div className="fact" style={{ gridColumn: "1 / -1" }}><span className="k">Exploration</span>
            <span className="v" style={{ fontWeight: 500 }}>{st.exploration.checked}{st.exploration.counterexample ? " — counterexample candidate found!" : " — no counterexample"}</span></div>}
        </div>
      )}
      {p.action === "accept_hypothesis" && drafts.length > 0 ? (
        <fieldset style={{ border: 0, padding: 0 }}>
          <legend className="sr-only">Hypotheses</legend>
          <div className="stack">
            {drafts.map((h, i) => {
              const sc = scores[h.statement];
              return (
                <label key={h.id} className="option">
                  <input type="radio" name={`c-${p.id}`} checked={choice === i} onChange={() => setChoice(i)} />
                  <span className="stack" style={{ gap: 4 }}><InlineMath text={h.statement} />
                    {sc && <span className="faint">Critic: {sc.reason}</span>}
                    {h.scopeRelation?.relation && <span className="faint">Scope: {({ full: "answers the whole question", restricted: "restricted", related: "related" } as any)[h.scopeRelation.relation]}</span>}</span>
                  {sc ? <span className="stack" style={{ gap: 0, textAlign: "right" }}><span className="score">{sc.score}</span><span className="faint">/10</span></span> : <span />}
                </label>
              );
            })}
          </div>
        </fieldset>
      ) : p.options && p.options.length > 0 ? (
        <fieldset><legend>Options</legend>{p.options.map((o, i) => (
          <label key={i} className="choice"><input type="radio" name={`c-${p.id}`} checked={choice === i} onChange={() => setChoice(i)} /><span><InlineMath text={o} /></span></label>))}</fieldset>
      ) : p.action !== "approve_experiment" && <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{p.summary}</pre>}
      {p.action === "approve_experiment" && p.summary && <details><summary className="faint">Full plan text</summary><div style={{ marginTop: 6 }}><Code code={p.summary} /></div></details>}
      <div className="decision-actions">
        <label>Note <span className="help">(optional, recorded)</span>
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a short note to your decision…" /></label>
        <div className="row">
          <button className="btn primary" onClick={() => send(true)} disabled={busy}>Approve</button>
          <button className="btn danger" onClick={() => send(false)} disabled={busy}>Reject</button>
        </div>
      </div>
    </section>
  );
}

/** ManagerCard: the latest exchange with the manager in the right panel (input via the box below). */
function ManagerCard({ pid, m, onExpand, onToast }: { pid: string; m: ManagerState; onExpand: () => void; onToast?: (m: string) => void }) {
  return (
    <section className="card pm-card" aria-labelledby="pmc-h">
      <div className="card-head" style={{ marginBottom: 8 }}>
        <h2 id="pmc-h"><Avatar role="manager" size="sm" />Project manager</h2>
        <button className="btn plain sm" onClick={onExpand}>Open chat</button>
      </div>
      <ManagerThread pid={pid} m={m} onToast={onToast} input={false} onPick={(s) => { m.ask(s).catch(() => undefined); }} />
    </section>
  );
}

/** Bottom composer: the default recipient is the Project manager (does not interrupt the team); optionally a note to the team. */
function Composer({ pid, m, onToast, finished, onManager }: { pid: string; m: ManagerState; onToast?: (m: string) => void; finished: boolean; onManager: () => void }) {
  const [to, setTo] = useState("manager");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const v = text.trim();
    if (!v) { setError("Write a message first."); return; }
    setError(null);
    if (to === "manager") {
      setText(""); onManager();
      try { await m.ask(v); } catch { setText(v); }
      return;
    }
    try { await api.message(pid, to, v); setText(""); onToast?.(`Note queued for ${to === "all" ? "the whole team" : ROLE_NAME[to]}; it is added to their next call`); }
    catch (err: any) { setError(err.message); }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  const toTeam = to !== "manager";
  return (
    <form className="composer" onSubmit={send} aria-label="Message the manager or the team">
      <label className="sr-only" htmlFor="msg-text">Message</label>
      <textarea id="msg-text" ref={ta} rows={1} value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey} aria-invalid={!!error} maxLength={4000}
        placeholder={toTeam ? (finished ? "Research finished; your note is recorded" : `Note for ${to === "all" ? "the whole team" : ROLE_NAME[to]} (added to their next call)…`)
          : "Ask the project manager about progress, decisions or results…"} />
      <div className="composer-bar">
        <div className="composer-controls">
        <label className="chip-field"><span>To</span>
          <select id="msg-to" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Recipient">
            <option value="manager">Project manager (doesn't interrupt the team)</option>
            <option value="director">@Director (note to the team)</option>
            <option value="all">@everyone</option>
            {ROLES.filter((r) => r !== "director").map((r) => <option key={r} value={r}>@{ROLE_NAME[r]}</option>)}
          </select>
        </label>
        <span className="faint composer-hint">{toTeam ? "Interrupts: the agent reads it on its next call." : "Answers from the project record; the team keeps working."}</span>
        </div>
        <button className="send" type="submit" disabled={m.waiting && !toTeam} aria-label={toTeam ? "Send note to the team" : "Ask the project manager"}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
        </button>
      </div>
      {error && <p className="err" role="alert">{error}</p>}
    </form>
  );
}

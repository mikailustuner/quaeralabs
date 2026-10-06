import { useEffect, useRef, useState } from "react";
import { AgentState, Derived, PURPOSE_NAME, QEvent, ROLES, ROLE_JOB, ROLE_NAME, Step, TOOL_NAME, money, time } from "./api";
import { Avatar } from "./components";
import { Code, RichText } from "./rich";

const STATUS_NAME: Record<string, string> = { working: "Working", done: "Contributed", idle: "Waiting", objecting: "Objection open" };

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}
const elapsed = (from: string, now: number) => {
  const s = Math.max(0, Math.round((now - new Date(from).getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};

function StepIcon({ st }: { st: Step["status"] }) {
  return <span className={`ck ${st === "done" ? "done" : st === "fail" ? "fail" : "run"}`} aria-hidden="true">{st === "done" ? "✓" : st === "fail" ? "!" : ""}</span>;
}

/** Right panel: the agent(s) currently working, live steps and the code being compiled/run right now. */
export function ActiveAgents({ d, running, onOpen }: { d: Derived; running: boolean; onOpen: (role: string) => void }) {
  const now = useNow(d.active.length > 0);
  const workers = d.agents.filter((a) => a.current.length);
  const lead = workers[0]?.role || (d.current ? d.agents.find((a) => a.status === "working")?.role : null);
  const recent = d.steps.filter((s) => !lead || s.role === lead).slice(-6);
  return (
    <section className="card stack" aria-labelledby="aa-h" style={{ gap: 12 }}>
      <div className="card-head" style={{ marginBottom: 0 }}>
        <h2 id="aa-h">Active agent{workers.length > 1 ? "s" : ""}</h2>
        {running ? <span className="live-pill"><span className="dot working" />live</span> : <span className="faint">{d.finished ? "research finished" : "paused"}</span>}
      </div>
      {workers.length ? workers.map((a) => (
        <button key={a.role} className="agent-row" onClick={() => onOpen(a.role)} style={{ gridTemplateColumns: "52px minmax(0,1fr) auto" }}>
          <Avatar role={a.role} size="lg" />
          <span className="stack" style={{ gap: 2, minWidth: 0 }}>
            <span className="name" style={{ fontSize: 16 }}>{ROLE_NAME[a.role]}</span>
            {a.current.map((s) => (
              <span key={`${s.lane}-${s.step}`} className="sub" style={{ whiteSpace: "normal" }}>
                {s.step}{s.lane && <span className="lane">{s.lane}</span>} · {elapsed(s.at, now)}
              </span>
            ))}
          </span>
          <span className="chev" aria-hidden="true">›</span>
        </button>
      )) : (
        <p className="muted" style={{ fontSize: 14 }}>
          {d.pending.length ? "The team is waiting for your decision." : d.finished ? "All stages finished." : running ? "Moving to the next step…" : "No agent is working right now."}
        </p>
      )}
      {recent.length > 0 && (
        <div className="inset">
          <h4 style={{ marginBottom: 6 }}>{lead ? `${ROLE_NAME[lead]} — recent steps` : "Recent steps"}</h4>
          <ol className="checklist" aria-label="Recent steps">
            {recent.map((s) => (
              <li key={s.seq}>
                <StepIcon st={s.status} />
                <span>{s.step}{s.lane && <span className="lane">{s.lane}</span>}
                  {s.detail && <span className="step-detail">{s.detail}</span>}</span>
                <span className="faint">{s.status === "start" ? elapsed(s.at, now) : s.endAt ? time(s.endAt) : ""}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      {d.liveTool && d.liveTool.open && (
        <details className="inset" open={!!d.liveTool.code}>
          <summary className="row"><span className="dot working" /><strong>{ROLE_NAME[d.liveTool.role] || d.liveTool.role}: {TOOL_NAME[d.liveTool.tool] || d.liveTool.tool}</strong>
            <span className="faint">· {elapsed(d.liveTool.at, now)}</span></summary>
          {d.liveTool.code ? <div style={{ marginTop: 8, maxHeight: 260, overflow: "auto" }}>
            <Code code={d.liveTool.code} lang={d.liveTool.tool === "lean.compile" ? "lean" : "python"} /></div>
            : <p className="faint" style={{ marginTop: 6 }}>{JSON.stringify(d.liveTool.args).slice(0, 200)}</p>}
        </details>
      )}
    </section>
  );
}

/** All agents on the team and their status; clicking opens the agent drawer. */
export function TeamList({ agents, onOpen }: { agents: AgentState[]; onOpen: (role: string) => void }) {
  const waiting = agents.filter((a) => a.status === "idle").length;
  return (
    <section className="card" aria-labelledby="team-h">
      <div className="card-head"><h2 id="team-h">Team</h2><span className="faint">{waiting} waiting</span></div>
      <ul className="stack" style={{ listStyle: "none", margin: 0, padding: 0, gap: 0 }}>
        {agents.map((a) => (
          <li key={a.role}>
            <button className="agent-row" onClick={() => onOpen(a.role)} aria-label={`${ROLE_NAME[a.role]}: ${STATUS_NAME[a.status]}. Open what they said`}>
              <Avatar role={a.role} />
              <span style={{ minWidth: 0 }}>
                <span className="name" style={{ display: "block" }}>{ROLE_NAME[a.role]}</span>
                <span className="sub" style={{ display: "block" }}>{a.current[0]?.step || (a.said ? `${a.said} outputs · ${money(a.costUsd)}` : ROLE_JOB[a.role])}</span>
              </span>
              <span className="row" style={{ gap: 6 }}><span className={`dot ${a.status}`} /><span className="faint">{STATUS_NAME[a.status]}</span></span>
              <span className="chev" aria-hidden="true">›</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SaidItem({ e, pid, defaultOpen }: { e: QEvent; pid: string | null; defaultOpen: boolean }) {
  const p = e.payload;
  const [open, setOpen] = useState(defaultOpen);
  const [full, setFull] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open || full || !pid) return;
    fetch(`/api/projects/${encodeURIComponent(pid)}/blob/${p.sha256}`).then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then(setFull, (err) => setError(`Could not load the full text (${err.message}); showing the preview.`));
  }, [open, full, pid, p.sha256]);
  const text = full ?? p.preview;
  return (
    <article className="said">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="stack" style={{ gap: 2 }}>
          <strong>{PURPOSE_NAME[p.purpose] || p.purpose}{p.lane && <span className="lane">lane {p.lane}</span>}</strong>
          <span className="faint">{time(e.at)} · {e.actor.model?.replace("anthropic/", "")} · {p.chars.toLocaleString("en-US")} characters</span>
        </span>
        <span className="chev" aria-hidden="true">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="said-body">
          {error && <p className="faint">{error}</p>}
          <RichText text={text} collapse={6000} />
          {!full && pid && !error && <p className="faint">Loading the full text…</p>}
          {p.promptPreview && <details><summary className="faint">Task given to the agent (excerpt)</summary><p className="faint" style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>{p.promptPreview}</p></details>}
        </div>
      )}
    </article>
  );
}

/** Agent drawer: who the agent is, what it said (full, readable text) and the steps it took. Closes with Esc; focus stays inside. */
export function AgentDrawer({ role, d, pid, onClose }: { role: string | null; d: Derived; pid: string | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<"said" | "steps">("said");
  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (role && !dlg.open) dlg.showModal();
    if (!role && dlg.open) dlg.close();
  }, [role]);
  const a = d.agents.find((x) => x.role === role);
  const said = d.said.filter((e) => e.payload.role === role).slice().reverse();
  const steps = d.steps.filter((s) => s.role === role).slice().reverse();
  return (
    <dialog ref={ref} className="drawer" aria-labelledby="drawer-h" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      {role && a && (
        <>
          <div className="drawer-head">
            <div className="row" style={{ gap: 12 }}>
              <Avatar role={role} size="lg" />
              <div style={{ minWidth: 0, flex: 1 }}>
                <h2 id="drawer-h" style={{ fontSize: 20 }}>{ROLE_NAME[role]}</h2>
                <p className="muted">{ROLE_JOB[role]}</p>
              </div>
              <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
            </div>
            <div className="row faint">
              <span className={`dot ${a.status}`} /> {STATUS_NAME[a.status]} · {a.calls} model calls · {money(a.costUsd)}{a.model ? ` · ${a.model.replace("anthropic/", "")}` : ""}
            </div>
            <div className="segmented" role="group" aria-label="View">
              <button aria-pressed={tab === "said"} onClick={() => setTab("said")}>What they said ({said.length})</button>
              <button aria-pressed={tab === "steps"} onClick={() => setTab("steps")}>Steps ({steps.length})</button>
            </div>
          </div>
          <div className="drawer-body">
            {tab === "said" && (said.length ? said.map((e, i) => <SaidItem key={e.seq} e={e} pid={pid} defaultOpen={i === 0} />)
              : <p className="muted">This agent has not said anything yet{["director", "verifier"].includes(role) ? " (it mostly works deterministically, without a model)" : ""}.</p>)}
            {tab === "steps" && (steps.length ? (
              <ol className="checklist">
                {steps.map((s) => (
                  <li key={s.seq}><StepIcon st={s.status} /><span>{s.step}{s.lane && <span className="lane">{s.lane}</span>}{s.detail && <span className="step-detail">{s.detail}</span>}</span>
                    <span className="faint">{time(s.at)}</span></li>
                ))}
              </ol>
            ) : <p className="muted">No recorded steps.</p>)}
          </div>
        </>
      )}
    </dialog>
  );
}

export const ALL_ROLES = ROLES;

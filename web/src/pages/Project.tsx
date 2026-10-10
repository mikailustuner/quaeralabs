import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Detail, api, derive, useEventLog } from "../api";
import { Clamp, StateBox, Toast, statusBadge, useLoad } from "../components";
import { go } from "../router";
import { InlineMath } from "../rich";
import { GraphView, buildGraph } from "./Graph";
import { LabView, PhaseBar } from "./Lab";
import { ReportView } from "./Report";
import { TreeView } from "./Tree";

const REFRESH_ON = new Set(["approval.pending", "approval.resolved", "stage.done", "report.written", "run.crashed", "state"]);

export function Project({ pid, tab }: { pid: string; tab: string }) {
  const detail = useLoad(() => api.project(pid), [pid]);
  const { events, status } = useEventLog(pid);
  const [toast, setToast] = useState<string | null>(null);
  const seen = useRef(0);

  // On approval/stage/report events, refetch the details (pending approvals, report, run status).
  useEffect(() => {
    const fresh = events.slice(seen.current);
    seen.current = events.length;
    if (fresh.some((e) => REFRESH_ON.has(e.kind)) && !detail.loading) {
      const t = setTimeout(detail.reload, 300);
      return () => clearTimeout(t);
    }
  }, [events]); // eslint-disable-line react-hooks/exhaustive-deps

  const data = detail.data as Detail | undefined;
  const d = useMemo(() => derive(events, data?.stages || [], data?.capUsd ?? null), [events, data?.stages, data?.capUsd]);
  useEffect(() => { if (data) document.title = `${data.shortTitle || pid} · QuaeraLabs`; }, [data, pid]);

  if (detail.error) return <StateBox kind="error" action={<a className="btn" href="#/research">Back to research</a>}>Could not open the project: {detail.error}</StateBox>;
  if (!data) return <StateBox kind="loading" />;
  const base = `#/p/${encodeURIComponent(pid)}`;
  const tabs: [string, string][] = [["", "Lab"], ["graph", "Evidence graph"], ["tree", "Branches"], ["report", "Report"]];
  const question = [...d.objects.values()].find((o) => o.type === "question");
  const outcome = d.crashed || data.error ? ["bad", "stopped with an error"] : d.finished ? ["good", "Research finished"] : data.running ? ["accent", "Team is working"] : ["warn", "Paused"];

  return (
    <>
      <div className="topbar">
        <nav className="crumbs" aria-label="Breadcrumb">
          <a href="#/research">Research</a><span aria-hidden="true">›</span>
          {data.branchOf && <><a href={`#/p/${encodeURIComponent(data.branchOf)}`}>parent branch</a><span aria-hidden="true">›</span></>}
          <span className="cur" title={data.title}>{data.shortTitle || pid}</span>
        </nav>
        <nav className="tabs" aria-label="Project views">
          {tabs.map(([k, t]) => <a key={k} href={k ? `${base}/${k}` : base} aria-current={tab === k ? "page" : undefined}>{t}</a>)}
          <a href={`#/replay/${encodeURIComponent(pid)}`}>Replay</a>
        </nav>
      </div>
      <header className="stack" style={{ gap: 4 }}>
        <div className="title-row">
          <Title pid={pid} title={data.shortTitle} onSaved={detail.reload} />
          <span className="badge accent">{data.domain === "ml" ? "AI / ML" : "Mathematics"}</span>
          {data.mode === "discover" && <span className="badge info" title="Open-problem mode: multi-model ideation, cross-review, Lean lemma program">Discovery</span>}
        </div>
        <Clamp lines={3} className="question"><InlineMath text={data.title} /></Clamp>
        {question?.scope && <p className="subtitle">Scope: <InlineMath text={question.scope} /></p>}
        <div className="row" style={{ marginTop: 6 }}>
          <span className={`badge ${outcome[0]}`}>{outcome[1]}</span>
          {data.hypothesis && statusBadge(data.hypothesis.status)}
          <span className={`badge ${status === "live" ? "good" : status === "loading" ? "" : "bad"}`} title="Event stream connection">
            {status === "live" ? "live" : status === "loading" ? "connecting" : "disconnected, retrying"}
          </span>
          <span className="spacer" />
          <a className="btn sm" href={`/api/projects/${encodeURIComponent(pid)}/export.zip`}>Evidence package</a>
          <ReportProblem pid={pid} onDone={setToast} />
        </div>
      </header>
      {tab === "" && <PhaseBar domain={data.domain} d={d} mode={data.mode} />}
      {tab === "" && <LabView summary={data} events={events} d={d} pending={data.pending} live onChanged={detail.reload} onToast={setToast}
        onBranched={(id) => go(`/p/${encodeURIComponent(id)}`)} />}
      {tab !== "" && <div style={{ height: 16 }} />}
      {tab === "graph" && <GraphView {...buildGraph([...d.objects.values()])} objects={[...d.objects.values()]} />}
      {tab === "tree" && <TreeView pid={pid} n={events.filter((e) => e.kind.startsWith("branch.") || e.kind === "report.written").length} />}
      {tab === "report" && <ReportView pid={pid} report={data.report} live />}
      <Toast msg={toast} onDone={() => setToast(null)} />
    </>
  );
}


/** Short project name (given by the Project manager); the user can rename it. */
function Title({ pid, title, onSaved }: { pid: string; title: string; onSaved: () => void }) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState(title);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setV(title), [title]);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try { await api.rename(pid, v.trim()); setEdit(false); setErr(null); onSaved(); } catch (x: any) { setErr(x.message); }
  };
  if (!edit) return (
    <h1 className="row" style={{ gap: 6 }}>{title}
      <button className="icon-btn" onClick={() => setEdit(true)} aria-label="Rename project" title="Rename">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
      </button></h1>
  );
  return (
    <form className="row" onSubmit={save} style={{ gap: 6 }}>
      <label className="sr-only" htmlFor="rename">Short title</label>
      <input id="rename" type="text" value={v} maxLength={60} onChange={(e) => setV(e.target.value)} autoFocus style={{ width: "min(420px, 70vw)", fontSize: 20 }}
        onKeyDown={(e) => { if (e.key === "Escape") { setEdit(false); setV(title); } }} aria-invalid={!!err} />
      <button className="btn sm primary" type="submit">Save</button>
      <button className="btn sm" type="button" onClick={() => { setEdit(false); setV(title); }}>Cancel</button>
      {err && <span className="err" role="alert">{err}</span>}
    </form>
  );
}

/** Alpha: saves a problem in a research run as a local case (quaera triage). Nothing is sent anywhere. */
function ReportProblem({ pid, onDone }: { pid: string; onDone: (m: string) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState("");
  const [answer, setAnswer] = useState("");
  const [flags, setFlags] = useState({ mustObject: false, notAnswered: false, mustStop: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const expect: Record<string, unknown> = {};
    if (answer) expect.answer = answer;
    if (flags.mustObject) expect.mustObject = true;
    if (flags.notAnswered) expect.notAnswered = true;
    if (flags.mustStop) expect.mustStop = flags.mustStop === "yes";
    if (note.trim().length < 10) { setError("Describe what went wrong in at least one sentence."); return; }
    if (!Object.keys(expect).length) { setError("What should have happened? Choose at least one expectation."); return; }
    setBusy(true); setError(null);
    try {
      const r = await api.triage(pid, note.trim(), expect);
      ref.current?.close(); setNote(""); setAnswer(""); setFlags({ mustObject: false, notAnswered: false, mustStop: "" });
      onDone(`Case saved (${r.case}). It stays on this machine; to share it run: quaera triage export`);
    } catch (err: any) { setError(err.message); }
    finally { setBusy(false); }
  };
  return (
    <>
      <button className="btn sm" onClick={() => ref.current?.showModal()}>Report a problem</button>
      <dialog ref={ref} className="card dialog" aria-labelledby="rp-h">
        <form className="stack" style={{ gap: 14 }} onSubmit={submit} noValidate>
          <h2 id="rp-h">Report a problem</h2>
          <p className="faint">The case is saved on this machine under <span className="mono">~/.quaera/triage/</span>. Sharing it is your decision.</p>
          <label>What went wrong?
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} aria-invalid={!!error && note.trim().length < 10} />
          </label>
          <fieldset>
            <legend>What should have happened?</legend>
            <label className="row" style={{ fontWeight: 400 }}>Answer
              <select value={answer} onChange={(e) => setAnswer(e.target.value)} style={{ width: "auto" }}>
                <option value="">—</option><option value="yes">yes</option><option value="no">no</option><option value="unclear">unclear</option>
              </select>
            </label>
            <label className="choice"><input type="checkbox" checked={flags.mustObject} onChange={(e) => setFlags({ ...flags, mustObject: e.target.checked })} /><span>The Critic should have objected to the result</span></label>
            <label className="choice"><input type="checkbox" checked={flags.notAnswered} onChange={(e) => setFlags({ ...flags, notAnswered: e.target.checked })} /><span>An open question was reported as answered</span></label>
            <label className="row" style={{ fontWeight: 400 }}>The research
              <select value={flags.mustStop} onChange={(e) => setFlags({ ...flags, mustStop: e.target.value })} style={{ width: "auto" }}>
                <option value="">—</option><option value="yes">should have stopped</option><option value="no">should not have stopped</option>
              </select>
            </label>
          </fieldset>
          {error && <p className="err" role="alert">{error}</p>}
          <div className="row">
            <button className="btn primary" type="submit" disabled={busy}>Save case</button>
            <button className="btn" type="button" onClick={() => ref.current?.close()}>Cancel</button>
          </div>
        </form>
      </dialog>
    </>
  );
}

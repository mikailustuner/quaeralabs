import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { QEvent, STAGE_NAME, derive } from "../api";
import { StateBox } from "../components";
import { GraphView, buildGraph } from "./Graph";
import { LabView } from "./Lab";
import { ProofView, ReportView } from "./Report";

type ReplayFile = { project: string; title: string; domain: "math" | "ml"; stages: string[]; mode?: "verify" | "discover"; events: QEvent[]; report?: string | null };

/** Replay: rebuilds the event log step by step. Needs no model calls or API key. */
export function Replay({ pid }: { pid?: string }) {
  const [file, setFile] = useState<ReplayFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [view, setView] = useState("lab");

  useEffect(() => {
    if (!pid) return;
    fetch(`/api/projects/${encodeURIComponent(pid)}/replay.json`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((f) => { setFile(f); setN(0); }, (e) => setError(`Could not load the log: ${e.message}`));
  }, [pid]);

  const load = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      if (!Array.isArray(data.events) || !Array.isArray(data.stages)) throw new Error("This is not a QuaeraLabs replay file.");
      setFile(data); setN(0); setError(null);
    } catch (err: any) { setError(err.message); }
  };

  const total = file?.events.length || 0;
  useEffect(() => {
    if (!playing) return;
    if (n >= total) { setPlaying(false); return; }
    const t = setTimeout(() => setN((x) => Math.min(total, x + 1)), 1000 / speed);
    return () => clearTimeout(t);
  }, [playing, n, total, speed]);

  const events = useMemo(() => file?.events.slice(0, n) || [], [file, n]);
  const d = useMemo(() => derive(events, file?.stages || []), [events, file]);
  const states = useMemo(() => Object.fromEntries(events.filter((e) => e.kind === "state").map((e) => [e.payload.key, e.payload.value])), [events]);
  const objects = [...d.objects.values()];

  const nextStage = () => {
    if (!file) return;
    const i = file.events.findIndex((e, k) => k >= n && e.kind === "stage.done");
    setN(i < 0 ? total : i + 1);
  };

  return (
    <>
      <div className="page-head">
        <div><h1>Replay</h1><p>Watch a research run's event log step by step from the start. No model is called; the same log always produces the same view.</p></div>
        <label className="btn">Open log file<input type="file" accept="application/json,.json" onChange={load} className="sr-only" /></label>
      </div>
      {error && <p className="err" role="alert">{error}</p>}
      {!file ? (pid && !error ? <StateBox kind="loading" /> : <StateBox kind="empty">Press “Replay” on a project page, or open a replay <span className="mono">.json</span> file.</StateBox>) : (
        <>
          <section className="card stack" aria-label="Playback controls" style={{ marginBottom: 16 }}>
            <div className="row">
              <strong className="clamp">{file.title}</strong><span className="spacer" />
              <span className="faint">event {n}/{total}{d.current ? ` · next stage: ${STAGE_NAME[d.current] || d.current}` : " · finished"}</span>
            </div>
            <div className="row">
              <button className="btn primary" onClick={() => { if (n >= total) setN(0); setPlaying((p) => !p); }} aria-pressed={playing}>{playing ? "Pause" : "Play"}</button>
              <button className="btn" onClick={() => setN((x) => Math.max(0, x - 1))} disabled={n === 0}>‹ Back</button>
              <button className="btn" onClick={() => setN((x) => Math.min(total, x + 1))} disabled={n >= total}>Forward ›</button>
              <button className="btn" onClick={nextStage} disabled={n >= total}>Next stage »</button>
              <label className="row" style={{ fontWeight: 400 }}><span className="faint">Speed</span>
                <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} style={{ width: "auto" }}>
                  {[1, 4, 10, 30].map((s) => <option key={s} value={s}>{s} events/s</option>)}
                </select>
              </label>
            </div>
            <label><span className="sr-only">Position</span>
              <input type="range" min={0} max={total} value={n} onChange={(e) => setN(Number(e.target.value))} style={{ width: "100%", accentColor: "var(--accent)" }} aria-valuetext={`event ${n} / ${total}`} />
            </label>
          </section>
          <nav className="tabs" aria-label="Replay views">
            {[["lab", "Lab"], ["graph", "Evidence graph"], ...(file.domain === "math" ? [["proof", "Proof"]] : []), ["report", "Report"]].map(([k, t]) => (
              <a key={k} href="#" onClick={(e) => { e.preventDefault(); setView(k); }} aria-current={view === k ? "page" : undefined}>{t}</a>
            ))}
          </nav>
          {view === "lab" && <LabView summary={{ id: file.project, title: file.title, domain: file.domain, mode: file.mode, stages: file.stages, running: false, error: null, stopped: states.stopped }}
            events={events} d={d} pending={[]} live={false} />}
          {view === "graph" && <GraphView {...buildGraph(objects)} objects={objects} />}
          {view === "proof" && <ProofView states={states} objects={objects} />}
          {view === "report" && <ReportView pid={file.project} report={d.finished ? file.report ?? null : null} live={false} />}
        </>
      )}
    </>
  );
}

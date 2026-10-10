import { Markdown } from "../rich";
import { QObject, STAGE_NAME, api, money } from "../api";
import { StateBox, statusBadge, useLoad } from "../components";

export function ReportView({ pid, report, live }: { pid: string; report: string | null; live: boolean }) {
  const base = `/api/projects/${encodeURIComponent(pid)}`;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row no-print">
        {report && <button className="btn" onClick={() => window.print()}>Print as PDF</button>}
        {live && report && <a className="btn" href={`${base}/report.md`} download={`${pid}-report.md`}>Download Markdown</a>}
        {live && <a className="btn" href={`${base}/export.zip`}>Evidence package (RO-Crate, signed)</a>}
        {live && <a className="btn plain" href={`${base}/replay.json`} download>Replay file</a>}
      </div>
      <article className="card">
        {report ? <Markdown text={report} className="prose" /> : <p className="muted">The report appears here once the Writer agent completes the final stage.</p>}
      </article>
      {live && <RunMetricsCard pid={pid} />}
      <p className="ai-label">This content was produced by the QuaeraLabs AI agent team. Sentences without a cited source are automatically dropped by the Writer; results are not published without human approval.</p>
    </div>
  );
}

function highlightLean(src: string) {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return src.split("\n").map((line) => {
    const c = line.indexOf("--");
    const code = c >= 0 ? line.slice(0, c) : line, comment = c >= 0 ? line.slice(c) : "";
    const h = esc(code)
      .replace(/\b(theorem|lemma|import|by|have|intro|rcases|with|fun|calc|show|exact|apply|rw|simp|omega|ring|decide|norm_num|nlinarith|linarith|cases|induction|sorry)\b/g, '<span class="kw">$1</span>')
      .replace(/\b(quaera_main)\b/g, '<span class="th">$1</span>');
    return h + (comment ? `<span class="cm">${esc(comment)}</span>` : "");
  }).join("\n");
}

export function ProofView({ states, objects }: { states: Record<string, any>; objects: QObject[] }) {
  const formal = states.formal, proof = states.proof;
  const result = objects.find((o) => o.type === "result" && o.leanProof);
  const ver = objects.find((o) => o.type === "verification");
  if (!formal && !proof) return <p className="muted">The formal statement has not been written yet.</p>;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card" aria-labelledby="pf-s">
        <header><h2 id="pf-s">Formal statement</h2></header>
        <pre className="code mono" dangerouslySetInnerHTML={{ __html: highlightLean(formal?.statement || "") }} />
        <p className="faint" style={{ marginTop: 8 }}>The proof must preserve this statement exactly; if the statement changes, verification counts as failed.</p>
      </section>
      {proof && (
        <section className="card" aria-labelledby="pf-p">
          <header>
            <h2 id="pf-p">Lean 4 proof</h2>
            <div className="row">
              {result?.leanProof && statusBadge(result.leanProof.verified ? "supported" : "refuted")}
              {result?.leanProof?.sorryFree && <span className="badge good">no sorry</span>}
              {ver && statusBadge(ver.reproduced)}
            </div>
          </header>
          <pre className="code mono" dangerouslySetInnerHTML={{ __html: highlightLean(proof.source || "") }} />
          <p className="faint" style={{ marginTop: 8 }}>
            Only the standard axioms (propext, Classical.choice, Quot.sound) are allowed. The Verifier recompiles the proof in a clean Lean process.
          </p>
        </section>
      )}
    </div>
  );
}

const FAILURE_NAME: Record<string, string> = { model_error: "model errors", invalid_json: "malformed answers", degraded: "skipped (no usable answer)",
  budget_block: "blocked by budget", tool_error: "tool errors", crash: "crashes" };

/** Capacity plan O1: where the budget went (per stage and provider) and what went wrong; exported in the evidence package. */
function RunMetricsCard({ pid }: { pid: string }) {
  const m = useLoad(() => api.metrics(pid), [pid]);
  if (m.loading && !m.data) return <StateBox kind="loading" />;
  if (m.error) return <StateBox kind="error">{m.error}</StateBox>;
  const d = m.data!;
  return (
    <section className="card stack no-print" aria-labelledby="rm-h" style={{ gap: 12 }}>
      <div className="card-head" style={{ marginBottom: 0 }}><h2 id="rm-h">Run metrics</h2>
        <span className="faint">{money(d.totals.usd)} · {d.totals.calls} paid calls{d.totals.cached ? ` · ${d.totals.cached} from cache` : ""} · {d.totals.tokens.toLocaleString()} tokens</span></div>
      {d.alerts.length > 0 && <ul className="stack" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }}>
        {d.alerts.map((a, i) => <li key={i} className="badge bad" style={{ whiteSpace: "normal" }}>{a.message || a.kind}</li>)}</ul>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Cost per stage (scrollable)">
        <table className="data-table">
          <thead><tr><th scope="col">Stage</th><th scope="col">Cost</th><th scope="col">Calls</th><th scope="col">Cached</th><th scope="col">Tokens</th>
            <th scope="col">Escalations</th><th scope="col">Repairs</th><th scope="col">Problems</th></tr></thead>
          <tbody>{d.stages.map((s) => (
            <tr key={s.stage}><td>{STAGE_NAME[s.stage] || s.stage}</td><td>{money(s.usd)}</td><td>{s.calls}</td><td>{s.cached}</td><td>{s.tokens.toLocaleString()}</td>
              <td>{s.escalations}</td><td>{s.repairs}</td>
              <td className="faint" style={{ textAlign: "left" }}>{Object.entries(s.failures).map(([k, v]) => `${v} ${FAILURE_NAME[k] || k}`).join(" · ") || "—"}</td></tr>
          ))}</tbody>
        </table>
      </div>
      {d.providers.length > 0 && (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Providers (scrollable)">
          <table className="data-table">
            <thead><tr><th scope="col">Provider</th><th scope="col">Calls</th><th scope="col">Errors</th><th scope="col">Error rate</th><th scope="col">Cost</th></tr></thead>
            <tbody>{d.providers.map((p) => (
              <tr key={p.provider}><td className="mono">{p.provider}</td><td>{p.calls}</td><td>{p.errors}</td>
                <td>{p.errorRate == null ? "—" : `${Math.round(p.errorRate * 100)}%`}</td><td>{money(p.usd)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <p className="faint">Included as <span className="mono">observability.json</span> in the signed evidence package.</p>
    </section>
  );
}

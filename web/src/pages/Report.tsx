import { Markdown } from "../rich";
import { QObject } from "../api";
import { statusBadge } from "../components";

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

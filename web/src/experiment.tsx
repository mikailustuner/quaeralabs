// Visualising the experiment and the proof: what was designed, what was run, what came out, including errors and refutations.
import { useEffect, useMemo, useState } from "react";
import { Derived, QEvent, QObject, time } from "./api";
import { Histogram, SeedChart, thresholdOf } from "./charts";
import { Code, InlineMath, Markdown } from "./rich";

export function statesOf(events: QEvent[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const e of events) if (e.kind === "state") out[e.payload.key] = e.payload.value;
  return out;
}
const last = (events: QEvent[], kind: string) => [...events].reverse().find((e) => e.kind === kind);

/** Text from the content-addressed store (code, Lean file, log). null in replay when there is no server. */
export function useBlob(pid: string | null, sha: string | undefined | null) {
  const [state, setState] = useState<{ text?: string; error?: string }>({});
  useEffect(() => {
    if (!pid || !sha) return;
    let alive = true;
    fetch(`/api/projects/${encodeURIComponent(pid)}/blob/${sha}`).then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((text) => alive && setState({ text }), (e) => alive && setState({ error: e.message }));
    return () => { alive = false; };
  }, [pid, sha]);
  return state;
}

export function BlobCode({ pid, sha, lang }: { pid: string | null; sha?: string; lang: string }) {
  const b = useBlob(pid, sha);
  if (!sha) return null;
  if (b.error) return <p className="faint">Could not fetch the code ({b.error}).</p>;
  if (b.text == null) return <div className="skeleton" style={{ minHeight: 48 }} aria-label="Loading code" />;
  return <Code code={b.text} lang={lang} />;
}

export function Verdict({ tone, icon, title, children }: { tone: "good" | "bad" | "warn" | "info"; icon: string; title: string; children?: React.ReactNode }) {
  return (
    <div className={`verdict ${tone}`} role="status">
      <span className="vi" aria-hidden="true">{icon}</span>
      <div className="stack" style={{ gap: 2 }}><strong style={{ fontSize: 17 }}>{title}</strong>{children && <div className="muted">{children}</div>}</div>
    </div>
  );
}

function Critiques({ objects }: { objects: QObject[] }) {
  const crits = objects.filter((o) => o.type === "critique");
  if (!crits.length) return null;
  return (
    <section className="card" aria-labelledby="crit-h">
      <div className="card-head"><h2 id="crit-h">Critic objections</h2><span className="faint">{crits.length}</span></div>
      <ul className="stack" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {crits.map((c) => (
          <li key={c.id} className="inset stack" style={{ gap: 4, borderLeft: `3px solid var(--${c.status === "open" ? "orange" : "green"})` }}>
            <div className="row"><span className="mono faint">{c.id}</span>
              <span className={`badge ${c.status === "open" ? "warn" : "good"}`}>{({ open: "open", accepted: "accepted", resolved: "resolved", rejected_with_reason: "rejected with reason" } as any)[c.status] || c.status}</span>
              <span className="badge">{({ low: "low", medium: "medium", high: "high", blocking: "blocking" } as any)[c.severity] || c.severity}</span>
              <span className="faint">{c.category}</span></div>
            <InlineMath text={c.body} />
            {c.resolution?.text && <p className="faint">Resolution: {c.resolution.text}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ matematik -----------------------------------------

export function MathExperiment({ d, events, pid }: { d: Derived; events: QEvent[]; pid: string | null }) {
  const st = useMemo(() => statesOf(events), [events]);
  const objs = [...d.objects.values()];
  const h = objs.find((o) => o.id === st.hypothesis_id);
  const ver = objs.filter((o) => o.type === "verification").pop();
  const back = last(events, "statement.backtranslated")?.payload;
  const approved = last(events, "statement.approved")?.payload;
  const explored = st.exploration;
  const search = last(events, "proof.search")?.payload;
  const checks = events.filter((e) => e.kind === "fidelity.check").map((e) => e.payload);
  const vacuous = checks.find((c) => c.check === "vacuity" && c.verified);
  const refuted = st.refutation;

  let verdict: React.ReactNode = null;
  if (vacuous) verdict = <Verdict tone="bad" icon="!" title="Formal statement is vacuously true">The assumptions contradict each other: Lean derived False from them. A proof of such a statement says nothing about the hypothesis; the statement must be rewritten.</Verdict>;
  else if (refuted) verdict = <Verdict tone="bad" icon="✕" title="Hypothesis refuted — counterexample verified in Lean">The negation of the statement was proved in Lean 4{ver?.reproduced === "yes" ? " and recompiled in a clean Lean process" : ""}. This relies on the assumption that the formal statement encodes the hypothesis correctly.</Verdict>;
  else if (st.proof) verdict = <Verdict tone="good" icon="✓" title="Hypothesis proved">The Lean 4 proof compiled without sorry or non-standard axioms{ver?.reproduced === "yes" ? "; the Verifier recompiled it in a clean Lean process" : ""}.</Verdict>;
  else if (search && !search.solved) verdict = <Verdict tone="warn" icon="?" title="No proof found — the hypothesis was neither verified nor refuted">A proof was searched for with {search.modelCalls ?? 0} model calls but none was found{search.stopped ? ` (${search.stopped})` : ""}. This does not show the hypothesis is false. You can change the approach and open a new branch from the research tree.</Verdict>;

  return (
    <div className="stack-lg">
      {verdict}
      <section className="card stack" aria-labelledby="stmt-h" style={{ gap: 12 }}>
        <div className="card-head" style={{ marginBottom: 0 }}><h2 id="stmt-h">Statement</h2>
          {approved && <span className="badge good">Approved by the Critic</span>}</div>
        {h ? <div className="math-statement" style={{ fontSize: 17 }}><InlineMath text={h.statement} /></div> : <p className="muted">No hypothesis selected yet.</p>}
        {st.formal?.statement && (<>
          <h4>Lean 4 statement</h4>
          <Code code={st.formal.statement} lang="lean" />
        </>)}
        {back && (
          <div className="inset stack" style={{ gap: 6 }}>
            <h4>Independent back-translation <span className="faint" style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>— a model that never saw the hypothesis read only the Lean statement</span></h4>
            <InlineMath text={back.translation} />
            {back.oddities?.length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }} className="faint">{back.oddities.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul>}
          </div>
        )}
      </section>

      {explored && (
        <section className="card stack" aria-labelledby="exp-h" style={{ gap: 10 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="exp-h">Exploration: testing small cases</h2>
            <span className={`badge ${explored.counterexample ? "bad" : "good"}`}>{explored.counterexample ? "candidate counterexample" : "no counterexample"}</span></div>
          <dl className="kv"><dt>Checked</dt><dd>{explored.checked}</dd>
            {explored.counterexample && <><dt>Counterexample</dt><dd><code>{JSON.stringify(explored.counterexample.values ?? explored.counterexample)}</code> {explored.counterexample.detail}</dd></>}</dl>
          {explored.observations?.length > 0 && <div><h4 style={{ marginBottom: 4 }}>Observations</h4><ul style={{ margin: 0, paddingLeft: 18 }}>{explored.observations.map((o: string, i: number) => <li key={i}><InlineMath text={o} /></li>)}</ul></div>}
          <p className="faint">Numerical exploration is not proof; it guides the proof. If a candidate counterexample is found, a refutation is attempted in Lean first.</p>
        </section>
      )}

      {(search || checks.length > 0) && (
        <section className="card stack" aria-labelledby="proof-h" style={{ gap: 10 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="proof-h">Proof process</h2>
            {search && <span className="faint">{search.modelCalls ?? 0} model calls</span>}</div>
          <ol className="step-tree">
            {checks.filter((c) => c.check === "vacuity").map((c, i) => (
              <li key={`v${i}`}><details><summary><span className={`ck ${c.verified ? "fail" : "done"}`} aria-hidden="true">{c.verified ? "!" : "✓"}</span>
                <span>Vacuity check: can False be derived from the assumptions?</span><span className="faint">{c.verified ? "contradictory!" : "consistent"}</span></summary></details></li>
            ))}
            {checks.filter((c) => c.check === "refutation").map((c, i) => (
              <li key={`r${i}`}><details><summary><span className={`ck ${c.verified ? "fail" : "done"}`} aria-hidden="true">{c.verified ? "✕" : "✓"}</span>
                <span>Refutation attempt: can the negation of the statement be proved?</span><span className="faint">{c.verified ? "refuted" : "not refuted"}</span></summary></details></li>
            ))}
            {(search?.steps || []).map((s: any, i: number) => <ProofStep key={i} s={s} pid={pid} all={search.steps} />)}
          </ol>
        </section>
      )}

      {(st.proof || refuted) && (
        <section className="card stack" aria-labelledby="final-h" style={{ gap: 10 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="final-h">{st.proof ? "Verified proof" : "Refutation proof (counterexample)"}</h2>
            {ver && <span className={`badge ${ver.reproduced === "yes" ? "good" : "bad"}`}>{ver.reproduced === "yes" ? "recompiled in a clean environment" : "recompilation failed"}</span>}</div>
          <Code code={(st.proof || refuted).source} lang="lean" />
          <p className="faint">Only the standard axioms (propext, Classical.choice, Quot.sound) are allowed; sorry and native_decide are rejected.</p>
        </section>
      )}
      <Critiques objects={objs} />
    </div>
  );
}

function ProofStep({ s, pid, all }: { s: any; pid: string | null; all: any[] }) {
  const ok = s.verified;
  if (s.kind === "model_error") return (
    <li><details><summary><span className="ck fail" aria-hidden="true">!</span><span>Model error</span><span className="faint">call retried</span></summary>
      <div className="body"><p className="faint">{s.error}</p></div></details></li>
  );
  if (s.kind === "lemma" || s.kind === "lemma_automation") return null;   // shown under the sketch
  const title = s.kind === "automation" ? "Automation tactics (no model)" : s.kind === "whole" ? `Whole-proof candidate${s.lane ? ` (lane ${s.lane}: ${s.lane === "A" ? "direct" : "split into steps"})` : ""}`
    : s.kind === "sketch" ? `Proof sketch: ${(s.lemmas || []).length} lemmas` : s.kind === "assembled" ? "Lemmas assembled: full proof" : s.kind;
  const lemmaStatus = (name: string) => {
    const tries = all.filter((x) => x.lemma === name);
    const win = tries.find((x) => x.verified);
    return win ? (win.kind === "lemma_automation" ? ["done", "proved by automation"] : ["done", "proved by model"]) : tries.length ? ["fail", "not proved"] : ["", "not attempted"];
  };
  return (
    <li>
      <details>
        <summary><span className={`ck ${ok ? "done" : "fail"}`} aria-hidden="true">{ok ? "✓" : "✕"}</span><span>{title}</span>
          <span className="faint">{ok ? "verified" : s.kind === "sketch" ? (s.errors?.length ? "did not compile" : "valid sketch") : "failed"}</span></summary>
        <div className="body">
          {s.kind === "sketch" && s.lemmas?.length > 0 && (
            <ul className="sub">
              {s.lemmas.map((l: any) => { const [cls, tr] = lemmaStatus(l.name ?? l); return (
                <li key={l.name ?? l} className="row" style={{ alignItems: "flex-start" }}><span className={`ck ${cls}`} aria-hidden="true">{cls === "done" ? "✓" : cls === "fail" ? "✕" : ""}</span>
                  <span style={{ flex: 1, minWidth: 0 }}><code>{l.statement || l.name || l}</code></span><span className="faint">{tr}</span></li>); })}
            </ul>
          )}
          {s.errors?.length > 0 && <div className="inset"><h4 style={{ marginBottom: 4 }}>Lean compiler error</h4><pre className="faint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{s.errors.join("\n")}</pre></div>}
          {s.sha256 && <BlobCode pid={pid} sha={s.sha256} lang="lean" />}
        </div>
      </details>
    </li>
  );
}

// ------------------------------------------------------------------ ML -------------------------------------------------

export function MLExperiment({ d, events, pid }: { d: Derived; events: QEvent[]; pid: string | null }) {
  const st = useMemo(() => statesOf(events), [events]);
  const objs = [...d.objects.values()];
  const pre = objs.filter((o) => o.type === "preregistration").pop();
  const h = objs.find((o) => o.id === st.hypothesis_id);
  const runs = objs.filter((o) => o.type === "run");
  const full = runs.filter((r) => r.kind === "full").sort((a, b) => a.seed - b.seed);
  const pilots = runs.filter((r) => r.kind === "pilot");
  const verRuns = runs.filter((r) => r.kind === "verification");
  const ver = objs.filter((o) => o.type === "verification").pop();
  const result = objs.filter((o) => o.type === "result").pop();
  const link = objs.filter((o) => o.type === "evidence_link").pop();
  const primary = pre?.primaryMetric;
  const row = primary && st.analysis?.table?.[primary];
  const code = [...events].reverse().find((e) => e.kind === "tool.started" && e.payload.tool === "sandbox.write" && e.payload.args?.path === "experiment.py")?.payload.code;
  const profile = st.data_profile;
  const ds = profile?.files?.find((f: any) => f.preview) || profile?.files?.[0];

  let verdict: React.ReactNode = null;
  if (h?.status === "supported") verdict = <Verdict tone="good" icon="✓" title="Hypothesis supported">The preregistered criterion was met{ver?.reproduced === "yes" ? " and the Verifier reproduced it exactly with the same seed in a clean environment" : ""}.</Verdict>;
  else if (h?.status === "refuted") verdict = <Verdict tone="bad" icon="✕" title="Hypothesis refuted">The results contradict the preregistered criterion{ver?.reproduced === "yes" ? "; the result was reproduced exactly in a clean environment" : ""}. That is a finding too.</Verdict>;
  else if (h?.status === "under_critique") verdict = <Verdict tone="warn" icon="!" title="Unresolved objection">The hypothesis does not count as supported because the Critic's objection is still open.</Verdict>;
  else if (result) verdict = <Verdict tone="warn" icon="?" title="Result inconclusive">No conclusive result was reached under the preregistered criterion.</Verdict>;

  return (
    <div className="stack-lg">
      {verdict}
      {pre && (
        <section className="card stack" aria-labelledby="plan-h" style={{ gap: 12 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="plan-h">Experiment plan</h2>
            {pre.lockedAt && <span className="lock" title={`Locked: ${pre.lockedAt}`}>🔒 preregistration locked</span>}</div>
          <div className="facts">
            <div className="fact"><span className="k">Primary metric</span><span className="v">{primary}</span></div>
            <div className="fact"><span className="k">Seeds</span><span className="v">{pre.seeds}</span><span className="s">each seed resamples the data</span></div>
            {st.plan?.baselines && <div className="fact"><span className="k">Baselines</span><span className="v">{st.plan.baselines.join(", ")}</span></div>}
          </div>
          <div className="inset"><h4 style={{ marginBottom: 4 }}>Success criterion (written before the experiment)</h4><InlineMath text={pre.successCriterion} /></div>
          {st.plan?.method && <details><summary><strong>Method</strong></summary><div style={{ marginTop: 8 }}><Markdown text={st.plan.method} /></div></details>}
        </section>
      )}

      {ds && (
        <section className="card stack" aria-labelledby="data-h" style={{ gap: 12 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="data-h">Dataset</h2><span className="faint">{ds.file}</span></div>
          <div className="grid-2">
            <dl className="kv">
              {(ds.arrays || []).map((a: any) => (<><dt key={`k${a.name}`}>{a.name}</dt><dd key={`v${a.name}`}>{a.shape.join(" × ")} · {a.dtype}
                {a.classes && <> · classes {Object.entries(a.classes).map(([c, n]) => `${c}: ${n}`).join(", ")}</>}
                {a.majority_rate != null && <> · majority {Math.round(a.majority_rate * 100)}%</>}</dd></>))}
            </dl>
            {ds.corr_with_y && <div><h4 style={{ marginBottom: 4 }}>Linear correlation with target</h4>
              <div className="stack" style={{ gap: 4 }}>{ds.corr_with_y.slice(0, 6).map((c: number | null, i: number) => (
                <div key={i} className="meter" style={{ gridTemplateColumns: "40px 1fr 48px" }} aria-label={`x${i}: ${c}`}><span className="faint mono">x{i}</span>
                  <div className="bar"><span style={{ width: `${Math.abs(c || 0) * 100}%`, background: (c || 0) >= 0 ? "var(--accent)" : "var(--red)" }} /></div>
                  <span className="n">{c == null ? "—" : c.toFixed(2)}</span></div>))}</div></div>}
          </div>
          {ds.preview && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrollable)"><h4 style={{ marginBottom: 6 }}>Preview (first 5 rows)</h4>
              <table className="data-table"><thead><tr>{ds.preview.columns.map((c: string) => <th key={c} scope="col">{c}</th>)}</tr></thead>
                <tbody>{ds.preview.rows.map((r: number[], i: number) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody></table></div>
          )}
          {ds.histograms?.length > 0 && (
            <div className="grid-3">{ds.histograms.map((hh: any) => <Histogram key={hh.feature} edges={hh.edges} byClass={hh.byClass} title={`x${hh.feature} (r = ${hh.corr})`} />)}</div>
          )}
        </section>
      )}

      {(full.length > 0 || pilots.length > 0) && (
        <section className="card stack" aria-labelledby="runs-h" style={{ gap: 12 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="runs-h">Runs and result</h2>
            <span className="faint">{full.filter((r) => r.status === "succeeded").length}/{full.length} seeds succeeded · {pilots.length} pilot</span></div>
          {primary && full.length > 0 && (
            <SeedChart label={primary} points={full.map((r) => ({ seed: r.seed, value: Number(r.metrics?.[primary]), ok: r.status === "succeeded" })).filter((p) => Number.isFinite(p.value))}
              mean={row?.mean} ci={row?.ci95} threshold={thresholdOf(pre?.successCriterion)} />
          )}
          {row && <p className="muted" style={{ fontSize: 14 }}>Mean <strong>{row.mean}</strong>, standard deviation {row.std}, 95% confidence interval [{row.ci95?.[0]}, {row.ci95?.[1]}] — {row.n} seeds. These statistics were computed by code, not by a model.</p>}
          {full.length > 0 && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrollable)"><table className="data-table">
              <thead><tr><th scope="col">Seed</th><th scope="col">Status</th>{Object.keys(full[0].metrics || {}).map((m) => <th key={m} scope="col">{m}</th>)}</tr></thead>
              <tbody>{full.map((r) => <tr key={r.id}><td>{r.seed}</td><td><span className={`badge ${r.status === "succeeded" ? "good" : "bad"}`}>{r.status === "succeeded" ? "succeeded" : "failed"}</span></td>
                {Object.keys(full[0].metrics || {}).map((m) => <td key={m}>{r.metrics?.[m]?.toFixed?.(4) ?? "—"}</td>)}</tr>)}</tbody>
            </table></div>
          )}
          {pilots.length > 0 && <details><summary><strong>Pilot runs ({pilots.length})</strong></summary>
            <ol className="step-tree" style={{ marginTop: 8 }}>{pilots.map((r, i) => (
              <li key={r.id}><details><summary><span className={`ck ${r.status === "succeeded" ? "done" : "fail"}`} aria-hidden="true">{r.status === "succeeded" ? "✓" : "✕"}</span>
                <span>Pilot {i + 1}: {r.status === "succeeded" ? "succeeded" : "errored, code fixed"}</span><span className="faint">{time(r.endedAt || r.createdAt)}</span></summary>
                <div className="body"><BlobCode pid={pid} sha={objs.find((o) => o.id === r.logs)?.sha256} lang="text" /></div></details></li>))}</ol></details>}
          {code && <details><summary><strong>Experiment code (experiment.py)</strong></summary><div style={{ marginTop: 8 }}><Code code={code} lang="python" /></div></details>}
        </section>
      )}

      {result && (
        <section className="card stack" aria-labelledby="ana-h" style={{ gap: 10 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="ana-h">Analysis</h2>
            {link && <span className={`badge ${link.relation === "supports" ? "good" : link.relation === "contradicts" ? "bad" : "warn"}`}>
              {({ supports: "supports the hypothesis", contradicts: "contradicts the hypothesis", inconclusive: "inconclusive" } as any)[link.relation] || link.relation}</span>}</div>
          <Markdown text={result.summary} />
          {result.limitations?.length > 0 && <div><h4 style={{ marginBottom: 4 }}>Limitations</h4><ul style={{ margin: 0, paddingLeft: 18 }}>{result.limitations.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul></div>}
        </section>
      )}

      {ver && (
        <section className="card stack" aria-labelledby="ver-h" style={{ gap: 10 }}>
          <div className="card-head" style={{ marginBottom: 0 }}><h2 id="ver-h">Independent reproduction</h2>
            <span className={`badge ${ver.reproduced === "yes" ? "good" : "bad"}`}>{ver.reproduced === "yes" ? "reproduced exactly" : ver.reproduced === "partial" ? "partial" : "not reproduced"}</span></div>
          <p className="muted" style={{ fontSize: 14 }}>The Verifier took the experiment code from the same commit into a clean environment and reran it with the same seed ({ver.tolerance}).</p>
          {verRuns.length > 0 && (() => {
            const vr = verRuns[verRuns.length - 1];
            const orig = full.find((r) => r.seed === vr.seed);
            const keys = Object.keys({ ...(orig?.metrics || {}), ...(vr.metrics || {}) });
            return <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrollable)"><table className="data-table"><thead><tr><th scope="col">Metric (seed {vr.seed})</th><th scope="col">Original</th><th scope="col">Rerun</th><th scope="col">Difference</th></tr></thead>
              <tbody>{keys.map((k) => { const a = orig?.metrics?.[k], b = vr.metrics?.[k]; const diff = a != null && b != null ? Math.abs(a - b) : null;
                return <tr key={k}><td>{k}</td><td>{a?.toFixed?.(6) ?? "—"}</td><td>{b?.toFixed?.(6) ?? "—"}</td><td>{diff == null ? "—" : diff <= 1e-9 ? "0" : diff.toExponential(2)}</td></tr>; })}</tbody></table></div>;
          })()}
        </section>
      )}
      <Critiques objects={objs} />
    </div>
  );
}

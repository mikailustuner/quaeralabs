import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { DiffOp, ROLE_NAME, STAGE_NAME, TreeData, TreeNode, api, money } from "../api";
import { StateBox, useLoad } from "../components";
import { go } from "../router";

const KIND_NAME: Record<string, string> = { hypothesis: "Hypothesis changed", approach: "Approach changed", note: "Note added",
  continue: "Program continued" };
const PART_NAME: Record<string, string> = { verifiedLemmas: "verified lemmas", liveStrategies: "live strategies", bestReview: "best review",
  stages: "stages reached", openCritiques: "open objections", depth: "depth", metricGain: "metric gain" };
const OUTCOME: Record<string, [string, string]> = {
  supported: ["good", "supported"], refuted: ["bad", "refuted"], inconclusive: ["warn", "inconclusive"],
  stopped: ["warn", "stopped"], running: ["info", "running"],
};
const W = 220, H = 92, GX = 28, GY = 54, PAD = 16;

/** Tree layout: depth = vertical level, leaves left to right; a parent sits centred over its children. */
function layout(nodes: TreeNode[], root: string) {
  const kids = new Map<string, TreeNode[]>();
  for (const n of nodes) if (n.parent) kids.set(n.parent, [...(kids.get(n.parent) || []), n]);
  const pos = new Map<string, { x: number; y: number }>();
  let leaf = 0, depthMax = 0;
  const visit = (id: string, depth: number): number => {
    depthMax = Math.max(depthMax, depth);
    const ch = (kids.get(id) || []).sort((a, b) => a.id.localeCompare(b.id));
    let x: number;
    if (!ch.length) x = leaf++;
    else { const xs = ch.map((c) => visit(c.id, depth + 1)); x = (xs[0] + xs[xs.length - 1]) / 2; }
    pos.set(id, { x: PAD + x * (W + GX), y: PAD + depth * (H + GY) });
    return x;
  };
  visit(root, 0);
  return { pos, width: PAD * 2 + Math.max(1, leaf) * (W + GX) - GX, height: PAD * 2 + (depthMax + 1) * (H + GY) - GY };
}

function Diff({ ops }: { ops: DiffOp[] }) {
  return (
    <p className="diff clamp">
      {ops.map(([op, text], i) => op === "=" ? <span key={i}>{text} </span> :
        op === "-" ? <del key={i} aria-label={`deleted: ${text}`}>{text}</del> : <ins key={i} aria-label={`inserted: ${text}`}>{text}</ins>)}
    </p>
  );
}

const fmt = (x: number) => (Math.abs(x) >= 100 ? x.toFixed(1) : Math.abs(x) >= 1 ? x.toFixed(3) : x.toFixed(4));

export function TreeView({ pid, n }: { pid: string; n: number }) {
  const t = useLoad(() => api.tree(pid), [pid, n]);
  const [sel, setSel] = useState<string>(pid);
  const [toast, setToast] = useState<string | null>(null);
  const data = t.data as TreeData | undefined;
  const lay = useMemo(() => (data ? layout(data.nodes, data.root) : null), [data]);
  const anyRunning = !!data?.nodes.some((x) => x.running);
  useEffect(() => {        // branches are separate projects: refresh the tree regularly while one is running
    if (!anyRunning) return;
    const h = setInterval(t.reload, 5000);
    return () => clearInterval(h);
  }, [anyRunning]); // eslint-disable-line react-hooks/exhaustive-deps
  if (t.error) return <StateBox kind="error" action={<button className="btn" onClick={t.reload}>Try again</button>}>{t.error}</StateBox>;
  if (!data || !lay) return <StateBox kind="loading" />;
  const nodes = new Map(data.nodes.map((x) => [x.id, x]));
  const node = nodes.get(sel) || nodes.get(data.root)!;
  const st = data.stats;
  const order = [...data.nodes].sort((a, b) => (lay.pos.get(a.id)!.y - lay.pos.get(b.id)!.y) || (lay.pos.get(a.id)!.x - lay.pos.get(b.id)!.x));
  const onKey = (e: KeyboardEvent, id: string) => {
    const i = order.findIndex((x) => x.id === id);
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? order[i + 1] : e.key === "ArrowLeft" || e.key === "ArrowUp" ? order[i - 1] : undefined;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSel(id); return; }
    if (next) { e.preventDefault(); (document.getElementById(`t-${next.id}`) as unknown as SVGElement | null)?.focus(); }
  };
  const label = (x: TreeNode) => (x.id === data.root ? "root" : x.id.slice(data.root.length + 1));

  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card tree-stats" aria-label="Tree summary">
        <div><span className="big">{st.branches}</span><span className="faint">branches</span></div>
        <div><span className="big" style={{ color: "var(--green)" }}>{st.supported}</span><span className="faint">supported</span></div>
        <div><span className="big" style={{ color: "var(--red)" }}>{st.refuted}</span><span className="faint">refuted</span></div>
        <div><span className="big" style={{ color: "var(--orange)" }}>{st.inconclusive + st.stopped}</span><span className="faint">inconclusive / stopped</span></div>
        <div><span className="big">{st.successRate == null ? "—" : `${Math.round(st.successRate * 100)}%`}</span><span className="faint">success rate</span></div>
        <div><span className="big">{st.conclusiveRate == null ? "—" : `${Math.round(st.conclusiveRate * 100)}%`}</span><span className="faint">conclusive rate</span></div>
        <div><span className="big">{money(st.totalCostUsd)}</span><span className="faint">total cost{st.costPerConclusive ? ` · ${money(st.costPerConclusive)} per conclusive result` : ""}</span></div>
        {st.bestMetric && <div><span className="big">{fmt(st.bestMetric.mean)}</span><span className="faint clamp">best {st.bestMetric.name.slice(0, 40)} ({label(nodes.get(st.bestMetric.project)!)})</span></div>}
        <div className="bar" role="img" aria-label={`Branch outcomes: ${st.supported} supported, ${st.refuted} refuted, ${st.inconclusive + st.stopped} inconclusive`}>
          {(["supported", "refuted", "inconclusive", "stopped", "running"] as const).map((o) => {
            const c = o === "running" ? st.branches - st.finished : st[o];
            return c ? <span key={o} className={`seg ${OUTCOME[o][0]}`} style={{ flex: c }} title={`${OUTCOME[o][1]}: ${c}`} /> : null;
          })}
        </div>
      </section>

      <div className="graph-wrap">
        <div className="graph" tabIndex={0} role="region" aria-label="Research tree (scrollable)">
          <svg width={lay.width} height={lay.height} role="group" aria-label={`Research tree: ${data.nodes.length} branches`}>
            {data.nodes.filter((x) => x.parent && nodes.has(x.parent)).map((x) => {
              const a = lay.pos.get(x.parent!)!, b = lay.pos.get(x.id)!;
              const x1 = a.x + W / 2, y1 = a.y + H, x2 = b.x + W / 2, y2 = b.y, my = (y1 + y2) / 2;
              return (
                <g key={`e-${x.id}`} aria-hidden="true">
                  <path className={`gedge ${sel === x.id ? "hl" : ""}`} d={`M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`} />
                  <text x={(x1 + x2) / 2 + 6} y={my + 4} className="edge-label">{KIND_NAME[x.branch!.kind]}</text>
                </g>
              );
            })}
            {order.map((x) => {
              const p = lay.pos.get(x.id)!;
              const [cls, tr] = OUTCOME[x.outcome];
              return (
                <g key={x.id} id={`t-${x.id}`} className={`gnode tnode ${sel === x.id ? "sel" : ""}`} transform={`translate(${p.x},${p.y})`}
                  tabIndex={0} role="button" aria-pressed={sel === x.id}
                  aria-label={`Branch ${label(x)}: ${tr}${x.branch ? `, ${KIND_NAME[x.branch.kind]}` : ""}${x.metric ? `, ${x.metric.name} ${fmt(x.metric.mean)}` : ""}, cost ${money(x.costUsd)}`}
                  onClick={() => setSel(x.id)} onKeyDown={(e) => onKey(e, x.id)} style={{ cursor: "pointer" }}>
                  <rect width={W} height={H} rx="12" />
                  <rect width="4" height={H - 20} x="0" y="10" rx="2" className={`stripe ${cls}`} />
                  <text x="14" y="22">{label(x)}{x.branch ? ` · ${KIND_NAME[x.branch.kind]}` : ""}</text>
                  <text x="14" y="42" className={`outcome ${cls}`}>{tr}{x.proofVerified ? " · Lean ✓" : x.reproduced === "yes" ? " · reproduced ✓" : ""}</text>
                  <text x="14" y="62" className="sub">{x.metric ? `${x.metric.name.slice(0, 18)}: ${fmt(x.metric.mean)}` : (x.hypothesis?.statement || "").slice(0, 32) + ((x.hypothesis?.statement || "").length > 32 ? "…" : "")}
                    {x.metricDelta != null ? ` (${x.metricDelta >= 0 ? "+" : ""}${fmt(x.metricDelta)})` : ""}</text>
                  <text x="14" y="80" className="sub">branch cost {money(x.costUsd)}{x.progress && x.outcome !== "running" && !["supported", "refuted"].includes(x.outcome)
                    ? ` · score ${x.progress.score.toFixed(2)}${x.closed ? " · closed" : ""}` : ""}</text>
                </g>
              );
            })}
          </svg>
        </div>
        <aside className="card stack" aria-live="polite" aria-label="Selected branch" style={{ gap: 12 }}>
          <div className="row"><h2>Branch: {label(node)}</h2><span className={`badge ${OUTCOME[node.outcome][0]}`}>{OUTCOME[node.outcome][1]}</span></div>
          {node.branch ? (
            <div className="stack" style={{ gap: 6 }}>
              <p><strong>{KIND_NAME[node.branch.kind]}</strong> <span className="faint">· after {STAGE_NAME[node.branch.atStage] || node.branch.atStage} · {node.branch.by.kind === "human" ? "human decision" : `proposed by ${ROLE_NAME[node.branch.by.role || "director"]}`}</span></p>
              <p className="clamp">Reason: {node.branch.reason}</p>
              {node.grid && <p><span className="badge info">Grid cell {node.grid.cell}/{node.grid.of}</span> {node.grid.parameter} = <strong>{node.grid.value}</strong></p>}
              {node.branch.note && <p className="faint clamp">Instructions: {node.branch.note}</p>}
            </div>
          ) : <p className="muted">Root of the tree.</p>}
          {node.diff?.hypothesis && <div><h3>Hypothesis diff</h3><Diff ops={node.diff.hypothesis} /></div>}
          {node.diff?.successCriterion && <div><h3>Success criterion diff</h3><Diff ops={node.diff.successCriterion} /></div>}
          {node.diff?.method && <div><h3>Method diff</h3><Diff ops={node.diff.method} /></div>}
          {node.diff?.formal && <div><h3>Formal statement diff</h3><Diff ops={node.diff.formal} /></div>}
          {!node.diff?.hypothesis && node.hypothesis && <div><h3>Hypothesis</h3><p className="clamp" style={{ fontSize: 13.5 }}>{node.hypothesis.statement}</p></div>}
          <dl className="kv">
            {node.metric && <><dt>{node.metric.name}</dt><dd>{fmt(node.metric.mean)}{node.metric.ci95 ? ` [${fmt(node.metric.ci95[0])}, ${fmt(node.metric.ci95[1])}]` : ""}
              {node.metricDelta != null && <span className={node.metricDelta >= 0 ? "up" : "down"}> {node.metricDelta >= 0 ? "▲" : "▼"} {fmt(Math.abs(node.metricDelta))}</span>}</dd></>}
            {node.answer && <><dt>Answer</dt><dd>{({ yes: "yes", no: "no", unclear: "unclear" } as any)[node.answer] || node.answer}</dd></>}
            <dt>Branch cost</dt><dd>{money(node.costUsd)} <span className="faint">(only after branching)</span></dd>
            {node.progress && !["supported", "refuted", "running"].includes(node.outcome) && <><dt>Search score</dt><dd>
              <strong>{node.progress.score.toFixed(2)}</strong>{node.closed ? <span className="badge" style={{ marginLeft: 6 }}>closed: no new branches</span> : null}
              <span className="faint" style={{ display: "block" }}>{Object.entries(node.progress.parts).map(([k, v]) => `${PART_NAME[k] || k} ${v}`).join(" · ")}</span>
              <span className="faint" style={{ display: "block" }}>The most promising open branch is expanded next; a branch is expanded at most 3 times.
                {node.stopRejected ? ` A stop was overruled ${node.stopRejected}× because untried directions remained.` : ""}</span></dd></>}
            {node.stopped && <><dt>Stop reason</dt><dd className="clamp">{node.stopped}</dd></>}
          </dl>
          <div className="row">
            <a className="btn sm" href={`#/p/${encodeURIComponent(node.id)}`}>Open this branch</a>
            {node.outcome !== "running" && <Iterate pid={node.id} disabled={!["inconclusive", "stopped"].includes(node.outcome)} onDone={(m) => { setToast(m); t.reload(); }} />}
          </div>
          {node.outcome !== "running" && <NewBranch node={node} onDone={(id) => go(`/p/${encodeURIComponent(id)}/tree`)} />}
          <div role="status" aria-live="polite">{toast && <p className="faint">{toast}</p>}</div>
        </aside>
      </div>
    </div>
  );
}

function NewBranch({ node, onDone }: { node: TreeNode; onDone: (id: string) => void }) {
  const [kind, setKind] = useState<"hypothesis" | "approach" | "note">("hypothesis");
  const [hyp, setHyp] = useState(node.hypothesis?.statement || "");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [budget, setBudget] = useState(1.5);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (reason.trim().length < 5) { setError("A reason is required: what changes and why?"); return; }
    setBusy(true); setError(null);
    try {
      const r = await api.branchWith(node.id, { kind, reason: reason.trim(), hypothesis: kind === "hypothesis" ? hyp : undefined,
        note: kind !== "hypothesis" ? note : undefined, atStage: kind === "note" ? "hypothesis_approval" : undefined, budget });
      onDone(r.id);
    } catch (err: any) { setError(err.message); setBusy(false); }
  };
  return (
    <details className="newbranch">
      <summary>New branch from this one, with a change</summary>
      <form className="stack" onSubmit={submit} style={{ gap: 10, marginTop: 10 }} noValidate>
        <fieldset>
          <legend>What changes?</legend>
          {(["hypothesis", "approach", "note"] as const).map((k) => (
            <label key={k} className="choice"><input type="radio" name={`k-${node.id}`} checked={kind === k} onChange={() => setKind(k)} />
              <span>{KIND_NAME[k]}<span className="help" style={{ display: "block" }}>{k === "hypothesis" ? "Branches after the literature stage; the hypothesis you write is tested." :
                k === "approach" ? "The hypothesis stays the same; you give instructions for the experiment/proof approach." : "After hypothesis approval; a note is passed to the team."}</span></span></label>
          ))}
        </fieldset>
        {kind === "hypothesis" ? (
          <label>New hypothesis<textarea rows={3} value={hyp} onChange={(e) => setHyp(e.target.value)} /></label>
        ) : (
          <label>{kind === "approach" ? "Approach instructions" : "Note"}<textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></label>
        )}
        <label>Reason<input type="text" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. the proof failed; let's try the special case" /></label>
        <label style={{ maxWidth: 200 }}>Branch budget (USD)<input type="number" min={0.1} step={0.1} value={budget} onChange={(e) => setBudget(Number(e.target.value))} /></label>
        {error && <p className="err" role="alert">{error}</p>}
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Opening…" : "Open branch and run"}</button>
      </form>
    </details>
  );
}

function Iterate({ pid, disabled, onDone }: { pid: string; disabled: boolean; onDone: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    const per = Number(prompt("Budget per branch (USD)? Your approval will be requested separately for each new branch.", "1.5"));
    if (!per) return;
    setBusy(true);
    try { await api.iterate(pid, { maxBranches: 2, budgetPerBranch: per }); onDone("Iteration started: the Director will propose a change; the approval card is on the lab screen."); }
    catch (e: any) { onDone(`Could not start: ${e.message}`); }
    finally { setBusy(false); }
  };
  return <button className="btn sm" onClick={run} disabled={disabled || busy} title={disabled ? "Only inconclusive branches can be iterated; a refuted hypothesis is a result." : ""}>Let the Director iterate</button>;
}

import { KeyboardEvent, useMemo, useState } from "react";
import { GEdge, GNode, QObject } from "../api";
import { statusBadge } from "../components";

const COLS: Record<string, number> = {
  question: 0, artifact: 0, hypothesis: 1, experiment: 2, preregistration: 2, run: 3, result: 3,
  evidence_link: 4, critique: 4, verification: 4,
};
const TYPE_NAME: Record<string, string> = {
  question: "Question", artifact: "Paper", hypothesis: "Hypothesis", experiment: "Experiment", preregistration: "Preregistration", run: "Run",
  result: "Result", evidence_link: "Evidence link", critique: "Critique", verification: "Verification",
};
const TYPE_COLOR: Record<string, string> = {
  question: "var(--text-2)", artifact: "var(--faint)", hypothesis: "var(--accent)", experiment: "var(--blue)", preregistration: "var(--blue)",
  run: "var(--purple)", result: "var(--purple)", evidence_link: "var(--green)", critique: "var(--orange)", verification: "var(--green)",
};
/** server.py `graph()` ile aynı kurallar; tekrar oynatmada sunucu olmadan da çalışsın diye istemcide. */
export function buildGraph(objects: QObject[]): { nodes: GNode[]; edges: GEdge[] } {
  const label: Record<string, string> = { question: "title", hypothesis: "statement", result: "summary", critique: "body", experiment: "method", preregistration: "successCriterion" };
  const nodes: GNode[] = objects
    .filter((o) => !(o.type === "message" || (o.type === "artifact" && o.kind !== "paper")))
    .map((o) => ({ id: o.id, type: o.type, status: o.status || o.reproduced || o.relation || null,
                   label: String(o[label[o.type]] || o.uri || o.kind || "").slice(0, 160) }));
  const ids = new Set(nodes.map((n) => n.id));
  const refs: [string, string][] = [["questionId", "question"], ["hypothesisId", "hypothesis"], ["experimentId", "experiment"], ["resultId", "result"], ["targetId", "critique"], ["preregistrationId", "preregistration"]];
  const edges: GEdge[] = [];
  for (const o of objects) {
    if (!ids.has(o.id)) continue;
    for (const [f, rel] of refs) if (ids.has(o[f])) edges.push({ from: o.id, to: o[f], label: o.type === "evidence_link" ? o.relation : rel });
    for (const h of o.hypothesisIds || []) if (ids.has(h)) edges.push({ from: o.id, to: h, label: "tests" });
    for (const r of o.derivedFrom || []) if (ids.has(r)) edges.push({ from: o.id, to: r, label: "derived from" });
  }
  return { nodes, edges };
}

const clip = (t: string, k: number) => (t.length > k ? t.slice(0, k - 1) + "…" : t);
const W = 190, H = 56, GX = 48, GY = 14, PAD = 16;

export function GraphView({ nodes, edges, objects }: { nodes: GNode[]; edges: GEdge[]; objects: QObject[] }) {
  const [sel, setSel] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set(["artifact"]));

  const { pos, cols, width, height } = useMemo(() => {
    const visible = nodes.filter((n) => !hidden.has(n.type));
    const cols: GNode[][] = [];
    for (const n of visible) (cols[COLS[n.type] ?? 4] ||= []).push(n);
    const pos = new Map<string, { x: number; y: number }>();
    cols.forEach((col, ci) => col?.forEach((n, ri) => pos.set(n.id, { x: PAD + ci * (W + GX), y: PAD + ri * (H + GY) })));
    const rows = Math.max(1, ...cols.map((c) => c?.length || 0));
    return { pos, cols, width: PAD * 2 + 5 * W + 4 * GX, height: PAD * 2 + rows * (H + GY) };
  }, [nodes, hidden]);

  const order = cols.flat().filter(Boolean);
  const selObj = objects.find((o) => o.id === sel);
  const related = new Set(edges.filter((e) => e.from === sel || e.to === sel).flatMap((e) => [e.from, e.to]));

  const onKey = (e: KeyboardEvent, n: GNode) => {
    const i = order.findIndex((x) => x.id === n.id);
    const ci = COLS[n.type] ?? 4;
    let next: GNode | undefined;
    if (e.key === "ArrowDown") next = order.slice(i + 1).find((x) => (COLS[x.type] ?? 4) === ci);
    if (e.key === "ArrowUp") next = order.slice(0, i).reverse().find((x) => (COLS[x.type] ?? 4) === ci);
    if (e.key === "ArrowRight") next = order.find((x) => (COLS[x.type] ?? 4) > ci);
    if (e.key === "ArrowLeft") next = [...order].reverse().find((x) => (COLS[x.type] ?? 4) < ci);
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSel(n.id); return; }
    if (next) { e.preventDefault(); (document.getElementById(`g-${next.id}`) as unknown as SVGElement | null)?.focus(); }
  };

  if (!nodes.length) return <p className="muted">The graph is empty for now; it fills in as the team produces its first objects.</p>;
  const types = [...new Set(nodes.map((n) => n.type))];

  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row" role="group" aria-label="Node types">
        {types.map((t) => (
          <label key={t} className="choice" style={{ padding: "4px 8px" }}>
            <input type="checkbox" checked={!hidden.has(t)} onChange={() => setHidden((h) => { const n = new Set(h); n.has(t) ? n.delete(t) : n.add(t); return n; })} />
            <span><i aria-hidden="true" style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: TYPE_COLOR[t], marginRight: 6 }} />{TYPE_NAME[t] || t}</span>
          </label>
        ))}
      </div>
      <p className="faint">Move between nodes with the arrow keys; press Enter to open details. Arrows point in the “depends on” direction.</p>
      <div className="graph-wrap">
        <div className="graph" tabIndex={0} role="region" aria-label="Evidence graph area (scrollable)">
          <svg width={width} height={height} role="group" aria-label={`Evidence graph: ${nodes.length} nodes, ${edges.length} links`}>
            <defs>
              <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="rgba(255,255,255,.3)" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const a = pos.get(e.from), b = pos.get(e.to);
              if (!a || !b) return null;
              const forward = a.x > b.x;
              const x1 = forward ? a.x : a.x + W, x2 = forward ? b.x + W : b.x;
              const y1 = a.y + H / 2, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
              return <path key={i} className={`gedge ${sel && (e.from === sel || e.to === sel) ? "hl" : ""}`} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} markerEnd="url(#arr)" aria-hidden="true" />;
            })}
            {order.map((n) => {
              const p = pos.get(n.id)!;
              return (
                <g key={n.id} id={`g-${n.id}`} className={`gnode ${sel === n.id ? "sel" : ""}`} transform={`translate(${p.x},${p.y})`} tabIndex={0}
                  role="button" aria-pressed={sel === n.id} aria-label={`${TYPE_NAME[n.type] || n.type} ${n.id}${n.status ? `, status ${n.status}` : ""}: ${n.label}`}
                  onClick={() => setSel(n.id)} onKeyDown={(e) => onKey(e, n)} style={{ cursor: "pointer", opacity: sel && !related.has(n.id) && sel !== n.id ? 0.45 : 1 }}>
                  <rect width={W} height={H} rx="10" />
                  <rect width="4" height={H - 16} x="0" y="8" rx="2" style={{ fill: TYPE_COLOR[n.type], stroke: "none" }} />
                  <text x="14" y="22">{TYPE_NAME[n.type] || n.type} · {n.id}</text>
                  <text x="14" y="40" className="sub">{clip((n.status ? `${n.status} · ` : "") + n.label, 28)}</text>
                </g>
              );
            })}
          </svg>
        </div>
        <aside className="card" aria-live="polite" aria-label="Selected node">
          {selObj ? (
            <div className="stack">
              <div className="row"><h2>{TYPE_NAME[selObj.type] || selObj.type} {selObj.id}</h2>{statusBadge(selObj.status || selObj.reproduced || selObj.relation)}</div>
              <p className="faint">Created by: {selObj.createdBy?.role || selObj.createdBy?.userId} {selObj.createdBy?.model ? `(${selObj.createdBy.model})` : ""} · revision {selObj.revision}</p>
              <dl className="stack" style={{ margin: 0 }}>
                {Object.entries(selObj).filter(([k]) => !["id", "type", "createdBy", "revision", "createdAt"].includes(k)).map(([k, v]) => (
                  <div key={k}><dt className="faint">{k}</dt><dd className="clamp" style={{ margin: 0, fontSize: 13.5 }}>{typeof v === "string" ? v : <code>{JSON.stringify(v)}</code>}</dd></div>
                ))}
              </dl>
              <h3>Links</h3>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {edges.filter((e) => e.from === sel || e.to === sel).map((e, i) => (
                  <li key={i}><button className="btn plain sm" onClick={() => setSel(e.from === sel ? e.to : e.from)}>{e.from} → {e.to}</button> <span className="faint">{e.label}</span></li>
                ))}
              </ul>
            </div>
          ) : <p className="muted">Select a node to see its details.</p>}
        </aside>
      </div>
    </div>
  );
}

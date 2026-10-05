// Bağımlılıksız, erişilebilir SVG grafikler: her grafiğin metin özeti (aria-label) ve tablo karşılığı vardır.

const COLORS = ["var(--accent)", "var(--blue)", "var(--orange)", "var(--purple)", "var(--red)", "var(--faint)"];
const fmt = (x: number) => (Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 10 ? x.toFixed(1) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toFixed(3));

/** Sınıf bazında histogram (veri profili): her sınıf ayrı renkte, üst üste yarı saydam alanlar. */
export function Histogram({ edges, byClass, title }: { edges: number[]; byClass: Record<string, number[]>; title: string }) {
  const W = 320, H = 150, P = { l: 30, r: 8, t: 8, b: 22 };
  const classes = Object.keys(byClass);
  const max = Math.max(1, ...classes.flatMap((c) => byClass[c]));
  const x = (i: number) => P.l + (i / (edges.length - 1)) * (W - P.l - P.r);
  const y = (v: number) => H - P.b - (v / max) * (H - P.t - P.b);
  const summary = classes.map((c) => `class ${c}: ${byClass[c].reduce((a, b) => a + b, 0)} samples`).join(", ");
  return (
    <figure style={{ margin: 0 }} className="stack">
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} distribution; ${summary}`}>
        {[0, 0.5, 1].map((f) => <line key={f} className="grid" x1={P.l} x2={W - P.r} y1={y(max * f)} y2={y(max * f)} />)}
        <line className="axis" x1={P.l} x2={W - P.r} y1={H - P.b} y2={H - P.b} />
        {classes.map((c, ci) => {
          const pts = byClass[c].map((v, i) => `${x(i + 0.5)},${y(v)}`).join(" ");
          return <polygon key={c} points={`${x(0.5)},${H - P.b} ${pts} ${x(byClass[c].length - 0.5)},${H - P.b}`}
            fill={COLORS[ci]} fillOpacity={0.22} stroke={COLORS[ci]} strokeWidth={1.6} />;
        })}
        <text x={P.l} y={H - 6}>{fmt(edges[0])}</text>
        <text x={W - P.r} y={H - 6} textAnchor="end">{fmt(edges[edges.length - 1])}</text>
        <text x={P.l - 4} y={y(max) + 4} textAnchor="end">{max}</text>
      </svg>
      <figcaption className="legend">
        <span>{title}</span>
        {classes.map((c, ci) => <span key={c}><i style={{ background: COLORS[ci] }} />target = {c}</span>)}
      </figcaption>
    </figure>
  );
}

/** Seed başına birincil metrik: noktalar, ortalama çizgisi, %95 GA bandı ve (varsa) ön kayıtlı eşik çizgisi. */
export function SeedChart({ points, mean, ci, threshold, label }: {
  points: { seed: number; value: number; ok: boolean }[]; mean?: number; ci?: number[]; threshold?: number | null; label: string;
}) {
  const W = 560, H = 210, P = { l: 52, r: 16, t: 14, b: 30 };
  const vals = [...points.map((p) => p.value), ...(ci || []), ...(threshold != null ? [threshold] : []), ...(mean != null ? [mean] : [])];
  if (!vals.length) return <p className="muted">No measurements yet.</p>;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1;
  lo -= pad; hi += pad;
  const n = Math.max(points.length, 1);
  const x = (i: number) => P.l + ((i + 0.5) / n) * (W - P.l - P.r);
  const y = (v: number) => H - P.b - ((v - lo) / (hi - lo)) * (H - P.t - P.b);
  const ticks = [lo + pad, (lo + hi) / 2, hi - pad];
  const desc = `${label}: ${points.map((p) => `seed ${p.seed} = ${fmt(p.value)}`).join(", ")}${mean != null ? `; mean ${fmt(mean)}` : ""}${threshold != null ? `; threshold ${fmt(threshold)}` : ""}`;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={desc}>
      {ticks.map((t, i) => (
        <g key={i}><line className="grid" x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} /><text x={P.l - 6} y={y(t) + 4} textAnchor="end">{fmt(t)}</text></g>
      ))}
      {ci && ci.length === 2 && <rect x={P.l} width={W - P.l - P.r} y={y(ci[1])} height={Math.max(1, y(ci[0]) - y(ci[1]))} fill="var(--accent)" fillOpacity={0.1} />}
      {mean != null && <g><line x1={P.l} x2={W - P.r} y1={y(mean)} y2={y(mean)} stroke="var(--accent)" strokeWidth={2} />
        <text x={W - P.r} y={y(mean) - 6} textAnchor="end" style={{ fill: "var(--accent)", fontWeight: 600 }}>mean {fmt(mean)}</text></g>}
      {threshold != null && <g><line x1={P.l} x2={W - P.r} y1={y(threshold)} y2={y(threshold)} stroke="var(--orange)" strokeWidth={1.6} strokeDasharray="6 5" />
        <text x={P.l + 4} y={y(threshold) - 6} style={{ fill: "var(--orange)", fontWeight: 600 }}>preregistered threshold {fmt(threshold)}</text></g>}
      {points.map((p, i) => (
        <g key={p.seed}>
          <circle cx={x(i)} cy={y(p.value)} r={6} fill={p.ok ? "var(--blue)" : "var(--red)"} stroke="var(--surface)" strokeWidth={2} />
          <text x={x(i)} y={H - 10} textAnchor="middle">seed {p.seed}</text>
        </g>
      ))}
      <line className="axis" x1={P.l} x2={W - P.r} y1={H - P.b} y2={H - P.b} />
    </svg>
  );
}

/** Başarı ölçütü metninden sayısal eşiği çıkarmaya çalışır (ör. "ortalama > 0.75"). Bulunamazsa null — çizgi çizilmez. */
export function thresholdOf(criterion: string | undefined): number | null {
  if (!criterion) return null;
  const m = criterion.match(/(?:>=|<=|>|<|≥|≤|en az|en fazla|üzerinde|altında|at least|at most|above|below)\s*%?\s*(-?\d+(?:[.,]\d+)?)/i);
  return m ? Number(m[1].replace(",", ".")) : null;
}

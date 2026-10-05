import { QEvent, QObject } from "./api";
import { InlineMath } from "./rich";

const CRITERIA: [string, string, string][] = [
  ["testability", "Testability", "Can it be tested decisively with the available tools (Lean or a sandbox experiment)?"],
  ["plausibility", "Plausibility", "Likelihood of being true or instructive, given the literature and the data profile"],
  ["novelty", "Novelty", "Does it avoid repeating known results or the lab's earlier projects?"],
  ["scope", "Scope", "How much of the original question does it answer? (10 = fully)"],
  ["cost", "Cost", "10 = cheap and quick to test"],
];

/** Eleştirmen'in hipotez puanlaması: ne puanlandı, hangi ağırlıkla, kim seçildi ve kim elendi. */
export function HypothesisRanking({ ev, hypotheses }: { ev: QEvent; hypotheses: QObject[] }) {
  const p = ev.payload;
  const weights: Record<string, number> = p.weights || {};
  const chosen = hypotheses.find((h) => !["draft", "rejected"].includes(h.status));
  const offered = new Set(hypotheses.map((h) => h.statement));
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="muted" style={{ fontSize: 14 }}>
        Two Hypothesis agents (lane A: direct, lane B: alternative angles) generated candidates; near-duplicates were merged.
        The Critic scored each candidate 0–10 on the five criteria below. The total is a weighted average; the top three valid candidates were submitted for approval.
      </p>
      <dl className="legend" style={{ margin: 0 }}>
        {CRITERIA.map(([k, l, help]) => (
          <div key={k} title={help}><dt style={{ display: "inline", fontWeight: 600 }}>{l}</dt> <dd style={{ display: "inline", margin: 0 }}>×{weights[k] ?? "—"}</dd></div>
        ))}
      </dl>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrollable)">
        <table className="rank-table">
          <caption className="sr-only">Hypothesis candidate scores</caption>
          <thead><tr><th scope="col">#</th><th scope="col">Candidate</th>{CRITERIA.map(([k, l]) => <th key={k} scope="col">{l}</th>)}<th scope="col">Total</th><th scope="col">Outcome</th></tr></thead>
          <tbody>
            {(p.candidates || []).map((c: any, i: number) => {
              const isChosen = chosen?.statement === c.statement;
              const state = isChosen ? ["good", "selected"] : offered.has(c.statement) ? ["info", "submitted"] : ["plain", "eliminated"];
              return (
                <tr key={i} className={isChosen ? "win" : ""}>
                  <td className="mono">{i + 1}</td>
                  <td style={{ minWidth: 240 }}><InlineMath text={c.statement} /><div className="faint" style={{ marginTop: 4 }}>{c.reason}</div></td>
                  {CRITERIA.map(([k, l]) => (
                    <td key={k}><div className="meter" aria-label={`${l}: ${c[k]} / 10`}><div className="bar"><span style={{ width: `${(Number(c[k]) || 0) * 10}%` }} /></div><span className="n">{c[k]}</span></div></td>
                  ))}
                  <td><span className="score">{c.score}</span><span className="faint">/10</span></td>
                  <td><span className={`badge ${state[0]}`}>{state[1]}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

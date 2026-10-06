// Keşif kipi görünümü: yaklaşım haritası, strateji panosu (kim önerdi / kim inceledi), lemma programı ve turlar.
// Yalnızca Lean'in doğruladığı ifade "verified" olur; sayısal sınama kanıt değildir ve öyle etiketlenir.
import { useState } from "react";
import { QEvent } from "./api";
import { BlobCode, Verdict, statesOf } from "./experiment";
import { InlineMath } from "./rich";

const FAMILY: Record<string, string> = { anthropic: "Claude", openai: "Codex · OpenAI", opencode: "OpenCode", google: "Antigravity · Google", scripted: "scripted" };
export const familyName = (f?: string) => (f ? FAMILY[f] || f : "—");
const STATUS: Record<string, [string, string]> = {
  verified: ["good", "verified in Lean"], refuted: ["bad", "refuted in Lean"], open: ["", "open"], unformalized: ["warn", "not stated in Lean"],
  failed: ["warn", "no proof this round"], numeric: ["info", "numerical check"],
};

function Pill({ s }: { s: string }) {
  const [c, t] = STATUS[s] || ["", s];
  return <span className={`badge ${c}`}>{t}</span>;
}

type Lemma = { id: string; name: string; statement: string; lean?: string; kind: string; status: string; repairOf?: string; strategy: string;
  history: { round: number; family: string; status: string; detail: string; sha256?: string }[]; reverified?: boolean };

export function useDiscovery(events: QEvent[]) {
  const proposed = events.filter((e) => e.kind === "strategy.proposed").map((e) => e.payload);
  const reviews: Record<string, any> = {};
  for (const e of events) if (e.kind === "strategy.reviewed") reviews[e.payload.id] = { ...e.payload, model: e.actor.model };
  const chosen = events.filter((e) => e.kind === "strategy.chosen").map((e) => e.payload.id);
  const dead: Record<string, string> = {};
  for (const e of events) if (e.kind === "strategy.dead") dead[e.payload.id] = e.payload.reason;
  const lemmas: Record<string, Lemma> = {};
  for (const e of events) {
    const p = e.payload;
    if (e.kind === "program.lemma") lemmas[p.id] = { ...p, history: [], status: p.status };
    if (e.kind === "lemma.status" && lemmas[p.id]) {
      lemmas[p.id].history.push(p);
      if (p.status === "verified" || p.status === "refuted") lemmas[p.id].status = p.status;
    }
    if (e.kind === "lemma.reverified" && lemmas[p.id]) lemmas[p.id].reverified = p.verified;
  }
  const rounds = events.filter((e) => e.kind === "attack.round").length;
  const synthesis = [...events].reverse().find((e) => e.kind === "synthesis.done" || e.kind === "synthesis.skipped")?.payload;
  return { proposed, reviews, chosen: chosen[chosen.length - 1], dead, lemmas: Object.values(lemmas), rounds, synthesis,
           failedLanes: events.filter((e) => e.kind === "ideation.lane_failed").map((e) => e.payload) };
}

function Landscape({ land }: { land: any }) {
  const [open, setOpen] = useState(false);
  if (!land) return null;
  return (
    <section className="card stack" aria-labelledby="land-h">
      <div className="card-head" style={{ marginBottom: 0 }}><h2 id="land-h">Research landscape</h2>
        <button className="btn plain sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>{open ? "Hide" : `Show ${land.approaches?.length || 0} approaches · ${land.barriers?.length || 0} barriers`}</button></div>
      {open && (
        <div className="grid-2">
          <div className="stack"><h4>Known approaches</h4>
            {(land.approaches || []).map((a: any, i: number) => (
              <div key={i} className="inset stack" style={{ gap: 4 }}><strong>{a.name}</strong><InlineMath text={a.idea || ""} />
                {a.bestResult && <span className="faint">Best: <InlineMath text={a.bestResult} /></span>}
                {a.obstruction && <span className="faint">Stuck: <InlineMath text={a.obstruction} /></span>}</div>))}
          </div>
          <div className="stack"><h4>Barriers any proof must avoid</h4>
            {(land.barriers || []).map((b: any, i: number) => <div key={i} className="inset stack" style={{ gap: 4 }}><strong>{b.name}</strong><InlineMath text={b.body || ""} /></div>)}
            {(land.openAngles || []).length > 0 && <><h4>Open angles</h4><ul style={{ margin: 0, paddingLeft: 18 }}>{land.openAngles.map((x: string, i: number) => <li key={i}><InlineMath text={x} /></li>)}</ul></>}
          </div>
        </div>
      )}
    </section>
  );
}

function Strategies({ ds }: { ds: ReturnType<typeof useDiscovery> }) {
  if (!ds.proposed.length) return null;
  const order = [...ds.proposed].sort((a, b) => (ds.reviews[b.id]?.score ?? -1) - (ds.reviews[a.id]?.score ?? -1));
  return (
    <section className="card stack" aria-labelledby="str-h">
      <div className="card-head" style={{ marginBottom: 0 }}><h2 id="str-h">Strategy board</h2>
        <span className="faint">{ds.proposed.length} strategies · {new Set(ds.proposed.map((p) => p.family)).size} model families</span></div>
      <p className="faint">Each strategy was proposed by one model family with a creative lens (lane A is always free) and reviewed by a different family.</p>
      {ds.failedLanes.length > 0 && <p className="faint">{ds.failedLanes.length} lane(s) failed: {ds.failedLanes.map((l: any) => `${l.lane} (${familyName(l.family)})`).join(", ")}</p>}
      <div className="strategy-grid">
        {order.map((s) => {
          const r = ds.reviews[s.id];
          const state = ds.chosen === s.id ? (ds.dead[s.id] ? "abandoned" : "pursued") : ds.dead[s.id] ? "abandoned" : ds.chosen ? "fallback" : "candidate";
          return (
            <article key={s.id} className={`strategy ${state}`} aria-label={`Strategy ${s.id}: ${s.title}`}>
              <div className="row" style={{ gap: 6 }}>
                <span className="mono faint">{s.id}</span><span className={`badge ${state === "pursued" ? "accent" : state === "abandoned" ? "bad" : ""}`}>{state}</span>
                <span className="badge">{s.direction === "disprove" ? "aims to disprove" : "aims to prove"}</span>
                <span className="spacer" />{r && <span className="score-chip" title="Cross-review score">{r.score}<span className="faint">/10</span></span>}
              </div>
              <h3>{s.title}</h3>
              <InlineMath text={s.idea || ""} />
              <p className="faint">By <strong>{familyName(s.family)}</strong> · lens: {s.lensName}{s.novelty ? ` · ${s.novelty}` : ""}</p>
              {(s.keySteps || []).length > 0 && <ol className="steps">{s.keySteps.map((k: string, i: number) => <li key={i}><InlineMath text={k} /></li>)}</ol>}
              {r && (
                <div className="review">
                  <span className="faint">Reviewed by {familyName(r.reviewerFamily)}</span>
                  {r.summary && <p><InlineMath text={r.summary} /></p>}
                  {r.fatalFlaw && <p className="flaw"><strong>Fatal flaw:</strong> <InlineMath text={r.fatalFlaw} /></p>}
                  <div className="meters">{(["plausibility", "novelty", "barrierAwareness", "testability"] as const).map((k) => (
                    <span key={k} className="m"><span className="faint">{({ plausibility: "plausible", novelty: "novel", barrierAwareness: "barriers", testability: "testable" })[k]}</span>
                      <span className="meter" aria-hidden="true"><span style={{ width: `${(r[k] ?? 0) * 10}%` }} /></span><span className="mono">{r[k] ?? "—"}</span></span>))}</div>
                </div>
              )}
              {ds.dead[s.id] && <p className="faint">Abandoned: <InlineMath text={ds.dead[s.id]} /></p>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Program({ ds, pid }: { ds: ReturnType<typeof useDiscovery>; pid: string | null }) {
  const [openId, setOpenId] = useState<string | null>(null);
  if (!ds.lemmas.length) return null;
  const counts = (s: string) => ds.lemmas.filter((l) => l.status === s).length;
  return (
    <section className="card stack" aria-labelledby="prog-h">
      <div className="card-head" style={{ marginBottom: 0 }}><h2 id="prog-h">Research program</h2>
        <span className="faint">{counts("verified")} verified · {counts("refuted")} refuted · {counts("open")} open · round {ds.rounds}</span></div>
      <p className="faint">The chosen strategy as a chain of Lean lemmas. Every round sends each open lemma to a different model family; refuted lemmas are repaired or the strategy is abandoned.</p>
      <ol className="lemmas">
        {ds.lemmas.map((l) => {
          const proof = [...l.history].reverse().find((h) => h.sha256 && (h.status === "verified" || h.status === "refuted"));
          const isOpen = openId === l.id;
          return (
            <li key={l.id} className={`lemma ${l.status}`}>
              <div className="row" style={{ gap: 8 }}>
                <span className="mono">{l.id}</span><Pill s={l.status} />
                {l.repairOf && <span className="badge">repair of {l.repairOf}</span>}
                <span className="badge">{l.kind}</span>
                {l.reverified != null && <span className={`badge ${l.reverified ? "good" : "bad"}`}>{l.reverified ? "clean recompile ✓" : "recompile failed"}</span>}
                <span className="spacer" />
                <button className="btn plain sm" onClick={() => setOpenId(isOpen ? null : l.id)} aria-expanded={isOpen}>{isOpen ? "Hide" : "Details"}</button>
              </div>
              <InlineMath text={l.statement} />
              {l.lean && <code className="ic lean-stmt">{l.lean}</code>}
              <ol className="tries" aria-label={`Attempts on ${l.id}`}>
                {l.history.map((h, i) => <li key={i}><span className="faint">r{h.round}</span> <span className="fam">{familyName(h.family)}</span> <Pill s={h.status} /></li>)}
              </ol>
              {isOpen && (
                <div className="stack" style={{ marginTop: 8 }}>
                  {l.history.map((h, i) => h.detail && <p key={i} className="faint">r{h.round} · {familyName(h.family)}: <InlineMath text={h.detail} /></p>)}
                  {proof ? <><h4>{proof.status === "verified" ? "Lean proof" : "Lean refutation"}</h4><BlobCode pid={pid} sha={proof.sha256} lang="lean" /></> : <p className="faint">No Lean proof or refutation yet.</p>}
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {ds.synthesis && <p className={ds.synthesis.solved ? "" : "faint"}>Synthesis of the main theorem: {ds.synthesis.solved ? "succeeded — see the verified proof below" : `not achieved${ds.synthesis.reason ? ` (${ds.synthesis.reason})` : ""}`}.</p>}
    </section>
  );
}

export function DiscoveryView({ events, pid }: { events: QEvent[]; pid: string | null }) {
  const ds = useDiscovery(events);
  const st = statesOf(events);
  const finished = events.some((e) => e.kind === "report.written");
  const verified = ds.lemmas.filter((l) => l.status === "verified").length;
  return (
    <div className="stack-lg">
      {finished && !st.proof && !st.refutation && (
        <Verdict tone="info" icon="?" title="Main claim still open">
          Neither the claim nor its negation was proved in Lean. Discoveries: {verified} verified lemma(s), {ds.lemmas.filter((l) => l.status === "refuted").length} refuted intermediate claim(s),
          {" "}{Object.keys(ds.dead).length} abandoned strategy(ies). These are steps, not evidence for the main claim.
        </Verdict>
      )}
      <Landscape land={st.landscape} />
      <Strategies ds={ds} />
      <Program ds={ds} pid={pid} />
    </div>
  );
}

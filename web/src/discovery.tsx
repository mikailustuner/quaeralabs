// Discovery mode view: approach map, strategy board (who proposed / who reviewed), lemma programme and rounds.
// Only a statement Lean has checked becomes "verified"; numerical testing is not proof and is labelled as such.
import { KeyboardEvent, useRef, useState } from "react";
import { QEvent } from "./api";
import { Clamp } from "./components";
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
  return (
    <section className="card stack" aria-labelledby="land-h">
      <div className="card-head" style={{ marginBottom: 0 }}><h2 id="land-h">Research landscape</h2>
        <span className="faint">{land.approaches?.length || 0} approaches · {land.barriers?.length || 0} barriers</span></div>
      <div className="grid-2">
        <div className="stack"><h4>Known approaches</h4>
          {(land.approaches || []).map((a: any, i: number) => (
            <div key={i} className="inset stack" style={{ gap: 4 }}><strong>{a.name}</strong><Clamp lines={4}><InlineMath text={a.idea || ""} /></Clamp>
              {a.bestResult && <span className="faint">Best: <InlineMath text={a.bestResult} /></span>}
              {a.obstruction && <span className="faint">Stuck: <InlineMath text={a.obstruction} /></span>}</div>))}
        </div>
        <div className="stack"><h4>Barriers any proof must avoid</h4>
          {(land.barriers || []).map((b: any, i: number) => <div key={i} className="inset stack" style={{ gap: 4 }}><strong>{b.name}</strong><Clamp lines={4}><InlineMath text={b.body || ""} /></Clamp></div>)}
          {(land.openAngles || []).length > 0 && <><h4>Open angles</h4><ul style={{ margin: 0, paddingLeft: 18 }}>{land.openAngles.map((x: string, i: number) => <li key={i}><InlineMath text={x} /></li>)}</ul></>}
        </div>
      </div>
    </section>
  );
}

const stateOf = (ds: ReturnType<typeof useDiscovery>, id: string) =>
  ds.chosen === id ? (ds.dead[id] ? "abandoned" : "pursued") : ds.dead[id] ? "abandoned" : ds.chosen ? "fallback" : "candidate";
const stateBadge = (state: string) => <span className={`badge ${state === "pursued" ? "accent" : state === "abandoned" ? "bad" : ""}`}>{state}</span>;

/** Strategy board as list + detail: the list stays compact, only the selected strategy is shown in full. */
function Strategies({ ds }: { ds: ReturnType<typeof useDiscovery> }) {
  const [sel, setSel] = useState<string | null>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const order = [...ds.proposed].sort((a, b) => (ds.reviews[b.id]?.score ?? -1) - (ds.reviews[a.id]?.score ?? -1));
  const s = order.find((x) => x.id === sel) || order.find((x) => x.id === ds.chosen) || order[0];
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = order.findIndex((x) => x.id === s.id);
    const j = e.key === "ArrowDown" || e.key === "ArrowRight" ? i + 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? order.length - 1 : null;
    if (j == null) return;
    e.preventDefault();
    const k = (j + order.length) % order.length;
    setSel(order[k].id); tabs.current[k]?.focus();
  };
  const r = ds.reviews[s.id];
  const state = stateOf(ds, s.id);
  return (
    <section className="card stack" aria-labelledby="str-h">
      <div className="card-head" style={{ marginBottom: 0 }}><h2 id="str-h">Strategy board</h2>
        <span className="faint">{ds.proposed.length} strategies · {new Set(ds.proposed.map((p) => p.family)).size} model families</span></div>
      <p className="faint">Each strategy was proposed by one model family with a creative lens (lane A is always free) and reviewed by a different family.</p>
      {ds.failedLanes.length > 0 && <p className="faint">{ds.failedLanes.length} lane(s) failed: {ds.failedLanes.map((l: any) => `${l.lane} (${familyName(l.family)})`).join(", ")}</p>}
      <div className="board">
        <div className="board-list" role="tablist" aria-orientation="vertical" aria-label="Strategies, best reviewed first" onKeyDown={onKey}>
          {order.map((x, i) => {
            const st = stateOf(ds, x.id);
            const on = x.id === s.id;
            return (
              <button key={x.id} ref={(el) => { tabs.current[i] = el; }} role="tab" id={`st-${x.id}`} aria-selected={on} aria-controls="str-detail" tabIndex={on ? 0 : -1}
                className={`board-item ${st}`} onClick={() => setSel(x.id)}>
                <span className="row" style={{ gap: 6 }}><span className="mono faint">{x.id}</span>{stateBadge(st)}<span className="spacer" />
                  {ds.reviews[x.id] && <span className="score-chip sm">{ds.reviews[x.id].score}<span className="faint">/10</span></span>}</span>
                <span className="bi-title"><InlineMath text={x.title} /></span>
                <span className="faint">{familyName(x.family)} · {x.direction === "disprove" ? "disprove" : "prove"}</span>
              </button>
            );
          })}
        </div>
        <article key={s.id} id="str-detail" role="tabpanel" aria-labelledby={`st-${s.id}`} className={`strategy ${state}`}>
          <div className="row" style={{ gap: 6 }}>
            <span className="mono faint">{s.id}</span>{stateBadge(state)}
            <span className="badge">{s.direction === "disprove" ? "aims to disprove" : "aims to prove"}</span>
            <span className="spacer" />{r && <span className="score-chip" title="Cross-review score">{r.score}<span className="faint">/10</span></span>}
          </div>
          <h3><InlineMath text={s.title} /></h3>
          <p className="faint">By <strong>{familyName(s.family)}</strong> · lens: {s.lensName}{s.novelty ? ` · ${s.novelty}` : ""}</p>
          {ds.dead[s.id] && <p className="dead">Abandoned: <InlineMath text={ds.dead[s.id]} /></p>}
          <div className="strategy-cols">
            <div className="stack" style={{ gap: 8 }}>
              <h4>Idea</h4>
              <Clamp lines={8}><InlineMath text={s.idea || ""} /></Clamp>
              {(s.keySteps || []).length > 0 && <><h4>Key steps</h4><ol className="steps">{s.keySteps.map((k: string, i: number) => <li key={i}><InlineMath text={k} /></li>)}</ol></>}
            </div>
            {r && (
              <div className="review inset">
                <h4>Reviewed by {familyName(r.reviewerFamily)}</h4>
                <div className="meters">{(["plausibility", "novelty", "barrierAwareness", "testability"] as const).map((k) => (
                  <span key={k} className="m"><span className="faint">{({ plausibility: "plausible", novelty: "novel", barrierAwareness: "barriers", testability: "testable" })[k]}</span>
                    <span className="meter" aria-hidden="true"><span style={{ width: `${(r[k] ?? 0) * 10}%` }} /></span><span className="mono">{r[k] ?? "—"}</span></span>))}</div>
                {r.summary && <Clamp lines={6}><InlineMath text={r.summary} /></Clamp>}
                {r.fatalFlaw && <div className="flaw"><strong>Fatal flaw:</strong> <Clamp lines={5}><InlineMath text={r.fatalFlaw} /></Clamp></div>}
              </div>
            )}
          </div>
        </article>
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
              <Clamp lines={3}><InlineMath text={l.statement} /></Clamp>
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

export type DiscoveryTab = "strategies" | "program" | "landscape";

/** The three parts of discovery mode as tabs, so only one long list is on the page at a time. */
export function DiscoveryView({ events, pid, tab, onTab }: { events: QEvent[]; pid: string | null; tab: DiscoveryTab | null; onTab: (t: DiscoveryTab) => void }) {
  const ds = useDiscovery(events);
  const st = statesOf(events);
  const finished = events.some((e) => e.kind === "report.written");
  const verified = ds.lemmas.filter((l) => l.status === "verified").length;
  const tabs = ([["strategies", "Strategies", ds.proposed.length], ["program", "Lemma program", ds.lemmas.length],
    ["landscape", "Landscape", st.landscape ? (st.landscape.approaches?.length || 0) : 0]] as [DiscoveryTab, string, number][])
    .filter(([k, , n]) => n > 0 || (k === "landscape" && st.landscape));
  const active = tabs.find(([k]) => k === tab)?.[0] || (ds.lemmas.length ? "program" : tabs[0]?.[0]);
  return (
    <div className="stack-lg">
      {finished && !st.proof && !st.refutation && (
        <Verdict tone="info" icon="?" title="Main claim still open">
          Neither the claim nor its negation was proved in Lean. Discoveries: {verified} verified lemma(s), {ds.lemmas.filter((l) => l.status === "refuted").length} refuted intermediate claim(s),
          {" "}{Object.keys(ds.dead).length} abandoned strategy(ies). These are steps, not evidence for the main claim.
        </Verdict>
      )}
      {tabs.length > 0 && (
        <div className="stack" id="discovery-panel">
          <div className="segmented" role="tablist" aria-label="Discovery views">
            {tabs.map(([k, t, n]) => (
              <button key={k} role="tab" id={`dt-${k}`} aria-selected={active === k} aria-controls={`dp-${k}`} onClick={() => onTab(k)}>{t}<span className="n">{n}</span></button>
            ))}
          </div>
          <div role="tabpanel" id={`dp-${active}`} aria-labelledby={`dt-${active}`}>
            {active === "strategies" && <Strategies ds={ds} />}
            {active === "program" && <Program ds={ds} pid={pid} />}
            {active === "landscape" && <Landscape land={st.landscape} />}
          </div>
        </div>
      )}
    </div>
  );
}

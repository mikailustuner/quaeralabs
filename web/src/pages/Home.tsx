import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import type { Learning, MemoryHit } from "../api";
import { STAGE_NAME, api, date, money } from "../api";
import { StateBox, statusBadge, useLoad } from "../components";
import { BrandMark, EVAL_PREFIX } from "../ui";
import { InlineMath } from "../rich";
import { go } from "../router";

export function Projects() {
  const { data, error, loading, reload } = useLoad(api.projects, []);
  const [q, setQ] = useState("");
  const [showEval, setShowEval] = useState(false);
  const all = data || [];
  const evalCount = all.filter((p) => EVAL_PREFIX.test(p.id)).length;
  const needle = q.trim().toLowerCase();
  const list = all.filter((p) => (showEval || !EVAL_PREFIX.test(p.id)) && (!needle || `${p.shortTitle} ${p.title}`.toLowerCase().includes(needle)));
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Research</h1><p>Every project keeps its own event log. You can branch from any finished stage and try a different path.</p></div>
        <a className="btn primary" href="#/">New research</a>
      </div>
      {all.length > 0 && (
        <div className="row" style={{ marginBottom: 12 }}>
          <label className="sr-only" htmlFor="p-search">Search research</label>
          <input id="p-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search research…" style={{ flex: "1 1 260px", width: "auto" }} />
          {evalCount > 0 && <label className="choice" style={{ padding: 0 }}><input type="checkbox" checked={showEval} onChange={(e) => setShowEval(e.target.checked)} />
            <span>Show evaluation projects ({evalCount})</span></label>}
        </div>
      )}
      {loading && !data ? <StateBox kind="loading" /> :
        error ? <StateBox kind="error" action={<button className="btn" onClick={reload}>Try again</button>}>Could not reach the server: {error}</StateBox> :
        !all.length ? <StateBox kind="empty" action={<a className="btn primary" href="#/">Start the first research</a>}>No research yet.</StateBox> :
        !list.length ? <StateBox kind="empty">No research matches your search.</StateBox> : (
          <ul className="plist">
            {list.map((p) => (
              <li key={p.id}>
                <a className="pitem" href={`#/p/${encodeURIComponent(p.id)}`}>
                  <div className="stack" style={{ gap: 4, minWidth: 0 }}>
                    <span className="t">{p.shortTitle}</span>
                    <span className="sub clamp"><InlineMath text={p.title} /></span>
                    <span className="row faint">
                      <span className="badge">{p.domain === "ml" ? "AI / ML" : "Mathematics"}</span>
                      <span>{date(p.createdAt)}</span>
                      <span>{p.current ? `stage: ${STAGE_NAME[p.current] || p.current}` : "finished"}</span>
                      <span>{money(p.spentUsd)}</span>
                      {p.branchOf && <span>branch</span>}
                    </span>
                  </div>
                  <div className="row">
                    {p.running && <span className="badge accent">running</span>}
                    {p.error && <span className="badge bad">error</span>}
                    {p.current && !p.running && !p.error ? <span className="badge">paused</span> : p.hypothesis && statusBadge(p.hypothesis.status)}
                  </div>
                </a>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

const greeting = () => {
  const h = new Date().getHours();
  return h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
};
const EXAMPLES = [
  { domain: "math", mode: "discover", q: "Is the Riemann Hypothesis true? Prove or disprove Mathlib's `RiemannHypothesis` exactly as stated." },
  { domain: "math", q: "Is n³ − n divisible by 6 for every natural number n?" },
  { domain: "math", q: "If ∑ 1/aₙ converges for positive integers aₙ, does #{k : a_k ≤ n}/n tend to 0?" },
  { domain: "ml", q: "Does label smoothing improve calibration of a small MLP on this dataset?" },
];

/** Home page: a large centred composer as in the reference; domain, budget and autonomy below it. */
export function NewResearch() {
  const [f, setF] = useState({ question: "", domain: "math", mode: "verify", budget: 2, dataDir: "", scope: "", autonomy: "manual", autoLimit: 0.5, keepTrying: false });
  const settings = useLoad(api.settings, []);
  const models: any[] = (settings.data?.models || []).filter((m: any) => m.enabled);
  const [families, setFamilies] = useState<string[] | null>(null);       // null: all available ones
  const chosen = families ?? [...new Set(models.map((m) => m.family))];
  const discover = f.domain === "math" && f.mode === "discover";
  const maxBudget = discover ? 500 : 50;
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const set = (k: string, v: any) => setF((x) => ({ ...x, [k]: v }));
  useEffect(() => { const t = ta.current; if (t) { t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 320)}px`; } }, [f.question]);
  useEffect(() => { if (f.domain === "ml") setMore(true); }, [f.domain]);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const er: Record<string, string> = {};
    const qlen = f.question.trim().length;
    if (qlen < 8) er.question = "Write a question of at least 8 characters.";
    else if (qlen > 1500) er.question = `Questions can be at most 1500 characters (now ${qlen}). Paste only the question.`;
    if (!(f.budget > 0 && f.budget <= maxBudget)) er.budget = `Budget must be between $0 and $${maxBudget}.`;
    if (families && !families.length) er.question = "Choose at least one model family.";
    if (f.domain === "ml" && !f.dataDir.trim()) er.dataDir = "An ML study needs a data directory.";
    setErrors(er);
    if (Object.keys(er).length) {
      if (er.dataDir || er.budget) setMore(true);
      setTimeout(() => document.getElementById(`f-${Object.keys(er)[0]}`)?.focus(), 0);
      return;
    }
    setBusy(true); setServerError(null);
    try {
      const r = await api.create({ ...f, mode: discover ? "discover" : "verify", question: f.question.trim(), ...(families ? { families } : {}) });
      go(`/p/${encodeURIComponent(r.id)}`);
    }
    catch (err: any) { setServerError(err.message); setBusy(false); }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); };
  const describedBy = (k: string) => (errors[k] ? `e-${k}` : `h-${k}`);

  return (
    <div className="hero">
      <h1 className="hero-title"><BrandMark />{greeting()}</h1>
      <p className="hero-sub">What should the lab investigate? The team surveys the literature, proposes hypotheses, runs the experiment or proof, and asks you at every critical step.</p>
      <form className="composer-hero" onSubmit={submit} noValidate aria-label="New research">
        {serverError && <p className="err" role="alert">{serverError}</p>}
        <label className="sr-only" htmlFor="f-question">Research question</label>
        <textarea id="f-question" ref={ta} rows={2} value={f.question} onChange={(e) => set("question", e.target.value)} onKeyDown={onKey}
          placeholder={discover ? "Name an open problem to attack, e.g. the Riemann Hypothesis…" : "Ask a precise, testable question…"} aria-invalid={!!errors.question} aria-describedby={describedBy("question")} />
        <span id="h-question" className="sr-only">Press Ctrl+Enter to start.</span>
        {errors.question && <p id="e-question" className="err">{errors.question}</p>}
        <div className="composer-bar">
          <div className="composer-controls">
          <div className="segmented" role="radiogroup" aria-label="Domain">
            {([["math", "Mathematics"], ["ml", "AI / ML"]] as const).map(([v, t]) => (
              <button key={v} type="button" role="radio" aria-checked={f.domain === v} onClick={() => set("domain", v)}>{t}</button>
            ))}
          </div>
          {f.domain === "math" && (
            <div className="segmented" role="radiogroup" aria-label="Research mode">
              {([["verify", "Verify"], ["discover", "Discover"]] as const).map(([v, t]) => (
                <button key={v} type="button" role="radio" aria-checked={f.mode === v} onClick={() => set("mode", v)}
                  title={v === "verify" ? "Test a precise claim; the team may propose provable hypotheses" : "Attack an open problem as stated, with several models"}>{t}</button>
              ))}
            </div>
          )}
          <label className="chip-field"><span>Budget $</span>
            <input id="f-budget" type="number" min={0.1} max={maxBudget} step={0.1} value={f.budget} onChange={(e) => set("budget", Number(e.target.value))}
              aria-invalid={!!errors.budget} aria-describedby={describedBy("budget")} />
          </label>
          <label className="chip-field"><span className="sr-only">Autonomy</span>
            <select id="f-autonomy" value={f.autonomy} onChange={(e) => set("autonomy", e.target.value)}>
              <option value="manual">Ask me at every step</option>
              <option value="under">Auto below a limit</option>
              <option value="cap">Auto up to budget cap</option>
            </select>
          </label>
          <button type="button" className="btn plain sm" onClick={() => setMore((m) => !m)} aria-expanded={more}>{more ? "Fewer options" : "More options"}</button>
          </div>
          <button className="send" type="submit" disabled={busy} aria-label={busy ? "Starting…" : "Start research"} title="Start research (Ctrl+Enter)">
            {busy ? <span className="spin" aria-hidden="true" /> : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>}
          </button>
        </div>
        {discover && (
          <div className="discover-note">
            <strong>Discovery mode.</strong> The claim is attacked exactly as asked, never narrowed. Several model families propose strategies in parallel
            (one lane is always free of any lens), a different family cross-reviews each, you pick one, and it is broken into Lean lemmas attacked over
            rounds with rotating models. Only Lean-verified statements count; partial findings are reported as discoveries.
          </div>
        )}
        {models.length > 0 && (
          <fieldset className="models-pick">
            <legend>Models for cross-checks</legend>
            {[...new Map(models.map((m) => [m.family, m])).values()].map((m) => (
              <label key={m.family} className="model-chip">
                <input type="checkbox" checked={chosen.includes(m.family)}
                  onChange={(e) => setFamilies(e.target.checked ? [...new Set([...chosen, m.family])] : chosen.filter((x) => x !== m.family))} />
                <span>{m.name}</span><span className="faint">{m.family}{m.billing?.startsWith("subscription") ? " · subscription" : " · metered"}</span>
              </label>
            ))}
          </fieldset>
        )}
        <p id="h-budget" className="composer-note">No model call can exceed the budget cap; the worst-case cost is checked before every call. {f.autonomy === "cap" ? "Publishing always needs your approval." : ""}</p>
        {errors.budget && <p id="e-budget" className="err">{errors.budget}</p>}
        {more && (
          <div className="composer-more">
            {f.domain === "ml" && (
              <label>Data directory
                <input id="f-dataDir" type="text" value={f.dataDir} onChange={(e) => set("dataDir", e.target.value)} placeholder="~/data/experiment-1"
                  aria-invalid={!!errors.dataDir} aria-describedby={describedBy("dataDir")} />
                <span id="h-dataDir" className="help">Mounted read-only at /data inside the network-less sandbox.</span>
                {errors.dataDir && <span id="e-dataDir" className="err">{errors.dataDir}</span>}
              </label>
            )}
            <label>Scope <span className="help">(optional)</span>
              <input id="f-scope" type="text" value={f.scope} onChange={(e) => set("scope", e.target.value)} aria-describedby="h-scope"
                placeholder={f.domain === "math" ? "Formal proof in Lean 4 + Mathlib" : "Data under /data."} />
              <span id="h-scope" className="help">The team's boundaries. A narrowed result is marked as "restricted scope" in the report.</span>
            </label>
            <label className="check-row">
              <input id="f-keepTrying" type="checkbox" checked={f.keepTrying} onChange={(e) => set("keepTrying", e.target.checked)} aria-describedby="h-keepTrying" />
              <span>Keep trying until the budget is spent
                <span id="h-keepTrying" className="help">If the research ends without an answer, the Director studies every attempt so far and opens a new branch
                  with a different hypothesis or strategy, again and again, until the budget above is used up (at most 10 branches). Each new branch follows the autonomy setting.</span>
              </span>
            </label>
            {f.autonomy === "under" && (
              <label style={{ maxWidth: 240 }}>Auto-approve below (USD)
                <input id="f-autoLimit" type="number" min={0} step={0.1} value={f.autoLimit} onChange={(e) => set("autoLimit", Number(e.target.value))} />
              </label>
            )}
          </div>
        )}
      </form>
      <div className="suggestions" aria-label="Example questions">
        {EXAMPLES.map((x) => (
          <button key={x.q} type="button" className="suggestion" onClick={() => { setF((v) => ({ ...v, question: x.q, domain: x.domain, mode: (x as any).mode || "verify", budget: (x as any).mode ? 25 : v.budget })); ta.current?.focus(); }}>
            <span className="badge">{(x as any).mode === "discover" ? "Discover" : x.domain === "ml" ? "AI / ML" : "Math"}</span><InlineMath text={x.q} />
          </button>
        ))}
      </div>
    </div>
  );
}

export function Settings() {
  const { data, error, loading, reload } = useLoad(api.settings, []);
  return (
    <div className="page">
      <div className="page-head"><div><h1>Settings</h1><p>QuaeraLabs runs locally. Keys are read only from environment variables and are never shown here.</p></div></div>
      {loading && !data ? <StateBox kind="loading" /> : error ? <StateBox kind="error" action={<button className="btn" onClick={reload}>Try again</button>}>{error}</StateBox> : (
        <div className="grid-3">
          <section className="card stack"><h2>Version and data</h2>
            <p>QuaeraLabs <span className="mono">{data.version}</span></p>
            <p className="faint clamp">Projects: <span className="mono">{data.home}</span></p></section>
          <ModelsCard data={data} />
          <section className="card stack"><h2>Sandbox limits</h2>
            <p>Memory {data.sandbox.mem} · CPU {data.sandbox.cpu} · Lean memory {data.sandbox.leanMem}</p>
            <p>{data.sandbox.systemd ? <span className="badge good">systemd limits active</span> : <span className="badge warn">no systemd-run: limits cannot be enforced</span>}</p></section>
        </div>
      )}
    </div>
  );
}

/** Model CLIs found on this machine; "Test" asks a small real question (subscription CLIs report no cost). */
function ModelsCard({ data }: { data: any }) {
  const [res, setRes] = useState<Record<string, any>>({});
  const test = async (id: string) => {
    setRes((r) => ({ ...r, [id]: { busy: true } }));
    try { const out = await api.testProvider(id); setRes((r) => ({ ...r, [id]: out })); }
    catch (e: any) { setRes((r) => ({ ...r, [id]: { ok: false, error: e.message } })); }
  };
  return (
    <section className="card stack" style={{ gridColumn: "1 / -1" }} aria-labelledby="models-h">
      <h2 id="models-h">Model providers</h2>
      <p className="faint">Detected automatically. Different families power cross-review (Critic, Verifier), the second hypothesis and proof lanes, and multi-model ideation in Discovery mode.
        Choose with <span className="mono">QUAERA_PROVIDERS</span> (e.g. <span className="mono">claude,codex,opencode,agy</span>); models with <span className="mono">QUAERA_CODEX_MODEL</span>, <span className="mono">QUAERA_OPENCODE_MODEL</span> and <span className="mono">QUAERA_AGY_MODEL</span>.</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Model providers (scrollable)">
        <table className="data-table">
          <thead><tr><th scope="col">Provider</th><th scope="col">Family</th><th scope="col">Version</th><th scope="col">Billing</th><th scope="col">Status</th><th scope="col">Check</th></tr></thead>
          <tbody>
            {(data.models || []).map((m: any) => {
              const r = res[m.id];
              return (
                <tr key={m.id}>
                  <td>{m.name}{m.model && <span className="faint"> · {m.model}</span>}</td><td className="mono">{m.family}</td><td className="faint">{m.version || "—"}</td>
                  <td className="faint">{m.billing}</td>
                  <td>{m.enabled ? <span className="badge good">enabled</span> : m.ready ? <span className="badge">disabled</span> : <span className="badge warn">{m.note || "not ready"}</span>}</td>
                  <td>
                    {m.ready && <button className="btn sm" onClick={() => test(m.id)} disabled={r?.busy}>{r?.busy ? "Testing…" : "Test"}</button>}
                    {r && !r.busy && <span className={r.ok ? "" : "err"} role="status" style={{ marginLeft: 8 }}>{r.ok ? `✓ ${r.answer} · ${r.seconds}s` : `✗ ${r.error || r.answer}`}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="faint">Project manager chat budget: ${data.managerCapUsd?.toFixed?.(2) ?? "0.50"} per project (<span className="mono">QUAERA_MANAGER_CAP_USD</span>). Subscription CLIs report no per-call charge; Discovery limits them by rounds (<span className="mono">QUAERA_DISCOVERY_ROUNDS</span>).</p>
    </section>
  );
}

const KINDS: [string, string][] = [["", "All"], ["finding", "Findings"], ["refuted", "Refuted"], ["method", "Methods"], ["caveat", "Caveats"], ["open", "Open"], ["stopped", "Stopped"]];

/** Contextual memory: learnings from every experiment result (across projects) + past research in memory. */
export function Memory() {
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const learned = useLoad(() => api.learnings({ limit: 200, q: applied }), [applied]);
  const past = useLoad(() => api.memory(applied), [applied]);
  const items = (learned.data || []).filter((l) => !kind || l.kind === kind || (kind === "caveat" && l.kind === "observation"));
  const byProject = new Map<string, Learning[]>();
  for (const l of items) { if (!byProject.has(l.project)) byProject.set(l.project, []); byProject.get(l.project)!.push(l); }
  const search = (e: FormEvent) => { e.preventDefault(); setApplied(q.trim()); };
  return (
    <div className="page">
      <div className="page-head"><div><h1>Lab memory</h1>
        <p>What the lab has learned across projects. After every experiment result the outcome, the method that worked (or failed) and open caveats are written here.
          New research sees relevant entries for orientation only: memory is never evidence and cannot be cited in a report.</p></div></div>
      <form className="row" role="search" onSubmit={search} style={{ marginBottom: 12 }}>
        <label className="sr-only" htmlFor="ms-q">Search lab memory</label>
        <input id="ms-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Has this been studied before?" style={{ flex: "1 1 260px", width: "auto" }} />
        <button className="btn" type="submit">Search</button>
        {applied && <button className="btn plain sm" type="button" onClick={() => { setQ(""); setApplied(""); }}>Clear</button>}
      </form>
      <div className="memory-layout">
        <section className="card stack" aria-labelledby="ml-h">
          <div className="card-head" style={{ marginBottom: 0 }}>
            <h2 id="ml-h">Lessons</h2>
            <div className="chips" role="group" aria-label="Filter by kind">
              {KINDS.map(([k, t]) => <button key={k} className="chip" aria-pressed={kind === k} onClick={() => setKind(k)}>{t}</button>)}
            </div>
          </div>
          {learned.loading && !learned.data ? <StateBox kind="loading" /> :
            learned.error ? <StateBox kind="error" action={<button className="btn" onClick={learned.reload}>Try again</button>}>{learned.error}</StateBox> :
            !items.length ? <StateBox kind="empty">{applied || kind ? "Nothing matches." : "No lessons yet. They are added after each experiment result."}</StateBox> : (
              <div className="stack-lg">
                {[...byProject.entries()].map(([pid, ls]) => (
                  <article key={pid} className="lesson-group">
                    <h3><a href={`#/p/${encodeURIComponent(pid)}`}>{ls[0].title}</a> <span className="faint">· {date(ls[0].at)}</span></h3>
                    <ul className="lessons">
                      {ls.map((l, i) => (
                        <li key={i}><span className={`badge ${{ finding: "good", refuted: "bad", method: "info", caveat: "warn", stopped: "warn", open: "" }[l.kind] ?? ""}`}>{l.kind}</span>
                          <span className="clamp"><InlineMath text={l.text} /></span></li>
                      ))}
                    </ul>
                  </article>
                ))}
              </div>
            )}
        </section>
        <section className="card stack" aria-labelledby="mp-h">
          <h2 id="mp-h">{applied ? "Similar past research" : "Past research in memory"}</h2>
          {past.loading && !past.data ? <StateBox kind="loading" /> :
            past.error ? <StateBox kind="error">{past.error}</StateBox> :
            !(past.data || []).length ? <StateBox kind="empty">{applied ? "No similar research found." : "Finished projects are added here."}</StateBox> : (
              <ul className="stack" style={{ listStyle: "none", margin: 0, padding: 0, gap: 12 }}>
                {(past.data as MemoryHit[]).map((h) => (
                  <li key={h.project}>
                    <a href={`#/p/${encodeURIComponent(h.project)}`} className="clamp"><InlineMath text={h.title} /></a>
                    <p className="faint clamp"><InlineMath text={h.outcome} /></p>
                  </li>
                ))}
              </ul>
            )}
        </section>
      </div>
    </div>
  );
}


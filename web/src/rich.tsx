// Ayrıştırıcı: ajan çıktıları, raporlar ve ifadeler tek bir yerden görüntülenir.
// Markdown (marked) + matematik (KaTeX: $…$, $$…$$, \(…\), \[…\]) + kod renklendirme (Lean, Python) + JSON → alan/değer görünümü.
// Tüm HTML DOMPurify'dan geçer; ajanın yazdığı içerik sayfada betik çalıştıramaz.
import DOMPurify from "dompurify";
import katex from "katex";
import { Marked } from "marked";
import markedKatex from "marked-katex-extension";
import { ReactNode, useMemo, useState } from "react";
import { autoMath } from "./automath";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const LEAN_KW = /\b(theorem|lemma|def|abbrev|example|import|open|namespace|section|end|by|have|show|from|fun|intro|intros|rcases|obtain|with|at|calc|exact|apply|refine|rw|simp|simp_all|omega|ring|ring_nf|decide|norm_num|nlinarith|linarith|positivity|aesop|cases|induction|use|constructor|push_neg|field_simp|gcongr|first|done|sorry|let|if|then|else|match|Type|Prop)\b/g;
const PY_KW = /\b(def|return|import|from|as|for|in|if|elif|else|while|with|class|try|except|finally|raise|lambda|None|True|False|and|or|not|print|yield|pass|break|continue)\b/g;

/** Basit, bağımlılıksız renklendirme: yorumlar ve dizeler önce ayrılır, kalanında anahtar kelime ve sayılar. */
export function highlight(code: string, lang: string): string {
  const isLean = /lean/i.test(lang), isPy = /py/i.test(lang);
  if (!isLean && !isPy) return esc(code);
  const comment = isLean ? /(--[^\n]*|\/-[\s\S]*?-\/)/ : /(#[^\n]*)/;
  const parts = code.split(new RegExp(`${comment.source}|("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\\\n]|\\\\.)*')`, "g"));
  return parts.filter((p) => p !== undefined && p !== "").map((p) => {
    if (comment.test(p) && (p.startsWith("--") || p.startsWith("/-") || p.startsWith("#"))) return `<span class="cm">${esc(p)}</span>`;
    if (/^["']/.test(p)) return `<span class="st">${esc(p)}</span>`;
    return esc(p).replace(isLean ? LEAN_KW : PY_KW, '<span class="kw">$1</span>')
      .replace(/\b(quaera_main|quaera_refute|quaera_vacuous|quaera_step_\d+)\b/g, '<span class="th">$1</span>')
      .replace(/(?<![\w.])(\d+(?:\.\d+)?)(?![\w])/g, '<span class="num">$1</span>');
  }).join("");
}

const md = new Marked({ gfm: true, breaks: false });
md.use(markedKatex({ throwOnError: false, nonStandard: true, output: "html" }));
md.use({
  renderer: {
    code({ text, lang }) {
      return `<pre class="code" tabindex="0">${highlight(text, lang || "")}</pre>`;
    },
    codespan({ text }) {
      // Satır içi kod: Lean ise renklendirilir (marked metni önceden kaçışlamıştır).
      const raw = text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
      return `<code class="ic">${highlight(raw, /[ℝℕℤ]|=>|\bfun\b|\.\w/.test(raw) ? "lean" : "")}</code>`;
    },
  },
});

/** \( \) ve \[ \] gösterimlerini $ biçimine çevirir (KaTeX eklentisi $ bekler); kod blokları dokunulmadan kalır. */
function normalizeMath(src: string): string {
  return src.split(/(```[\s\S]*?```)/g).map((part, i) => i % 2 ? part :
    part.replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `\n$$\n${m.trim()}\n$$\n`).replace(/\\\((.+?)\\\)/g, (_, m) => `$${m}$`)).join("");
}

export function renderMarkdown(src: string): string {
  const html = md.parse(autoMath(normalizeMath(src)), { async: false }) as string;
  return DOMPurify.sanitize(html, { ADD_ATTR: ["tabindex"] });
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const html = useMemo(() => renderMarkdown(text || ""), [text]);
  return <div className={`rich ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Tek satırlık metin içinde matematik (ör. hipotez ifadesi). */
export function InlineMath({ text }: { text: string }) {
  const html = useMemo(() => {
    const inline = (md.parseInline(autoMath(normalizeMath(text || "")), { async: false }) as string);
    return DOMPurify.sanitize(inline);
  }, [text]);
  return <span className="rich" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function MathBlock({ tex }: { tex: string }) {
  const html = useMemo(() => katex.renderToString(tex, { throwOnError: false, displayMode: true }), [tex]);
  return <div className="rich" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />;
}

export function Code({ code, lang = "" }: { code: string; lang?: string }) {
  return <pre className="code" tabIndex={0} dangerouslySetInnerHTML={{ __html: highlight(code, lang) }} />;
}

// --- JSON çıktılar -----------------------------------------------------------------------------

const LABELS: Record<string, string> = {
  hypotheses: "Hypotheses", statement: "Statement", falsifiabilityNote: "Falsifiability note", expectedSignal: "Expected signal",
  scope: "Scope", relation: "Relation", note: "Note", summary: "Summary", verdict: "Verdict", basis: "Basis", cited: "Citations",
  relevant_mathlib: "Relevant Mathlib definitions", arxiv_queries: "arXiv queries", openalex_queries: "OpenAlex queries",
  mathlib_queries: "Mathlib queries", ranking: "Ranking", index: "Candidate", testability: "Testability", plausibility: "Plausibility",
  novelty: "Novelty", cost: "Cost", reason: "Reason", faithful: "Faithful?", issues: "Issues", severity: "Severity", body: "Description",
  weakerThanQuestion: "Weaker than the question?", scopeNote: "Scope note", concerns: "Concerns", flawed: "Flawed?", categories: "Categories",
  evidence: "Evidence", translation: "Translation", oddities: "Oddities", method: "Method", baselines: "Baselines",
  metrics: "Metrics", primaryMetric: "Primary metric", successCriterion: "Success criterion", analysisPlan: "Analysis plan", seeds: "Number of seeds",
  estimatedMinutes: "Estimated time (min)", name: "Name", direction: "Direction", negative: "Negative result", limitations: "Limitations",
  accept: "Accept", response: "Response", discussion: "Discussion", answer: "Answer", answerReason: "Reason for answer", decision: "Decision",
  newHypothesis: "New hypothesis", instructions: "Instructions", checked: "Checked range", counterexample: "Counterexample", observations: "Observations",
};
const VALUE_NAME: Record<string, string> = {
  true: "yes", false: "no", supports: "supports", contradicts: "contradicts", inconclusive: "inconclusive", full: "full", restricted: "restricted",
  related: "related", novel: "novel", already_done: "already done", partially_done: "partially done", unknown: "unknown",
  low: "low", medium: "medium", high: "high", blocking: "blocking", yes: "yes", no: "no", unclear: "unclear",
  higher_is_better: "higher is better", lower_is_better: "lower is better", hypothesis: "hypothesis change", approach: "approach change", stop: "stop",
};
const label = (k: string) => LABELS[k] || k.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");

function JsonValue({ v }: { v: unknown }): ReactNode {
  if (v === null || v === undefined || v === "") return <span className="faint">—</span>;
  if (typeof v === "boolean") return <span className={`badge ${v ? "good" : "plain"}`}>{VALUE_NAME[String(v)]}</span>;
  if (typeof v === "number") return <span className="mono">{v}</span>;
  if (typeof v === "string") return VALUE_NAME[v] ? <span className="badge">{VALUE_NAME[v]}</span> : <InlineMath text={v} />;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="faint">—</span>;
    if (v.every((x) => typeof x !== "object" || x === null)) return <ul style={{ margin: 0, paddingLeft: 18 }}>{v.map((x, i) => <li key={i}><JsonValue v={x} /></li>)}</ul>;
    return <div className="stack">{v.map((x, i) => <div key={i} className="inset"><JsonTree data={x as Record<string, unknown>} /></div>)}</div>;
  }
  return <div className="nest"><JsonTree data={v as Record<string, unknown>} /></div>;
}

export function JsonTree({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="json-tree">
      {Object.entries(data).filter(([k]) => !k.startsWith("_")).map(([k, v]) => (
        <div key={k}><div className="jk">{label(k)}</div><div className="jv"><JsonValue v={v} /></div></div>
      ))}
    </div>
  );
}

/** Metin JSON mu? Tamamı ya da ```json bloğu. Değilse null. */
export function extractJson(text: string): { before: string; data: unknown; after: string } | null {
  const t = text.trim();
  const fence = t.match(/```(?:json)?\s*\n?([\s\S]*?)```/);
  const tryParse = (s: string) => { try { return JSON.parse(s); } catch { return undefined; } };
  if (fence) {
    const data = tryParse(fence[1].trim());
    if (data !== undefined && typeof data === "object") return { before: t.slice(0, fence.index).trim(), data, after: t.slice((fence.index || 0) + fence[0].length).trim() };
  }
  const start = Math.min(...["{", "["].map((c) => t.indexOf(c)).filter((i) => i >= 0));
  if (Number.isFinite(start)) {
    const data = tryParse(t.slice(start));
    if (data !== undefined && typeof data === "object") return { before: t.slice(0, start).trim(), data, after: "" };
  }
  return null;
}

/** Ajan çıktısı: JSON ise yapılandırılmış görünüm, değilse Markdown + matematik + kod. Uzun metin katlanır. */
export function RichText({ text, collapse = 0 }: { text: string; collapse?: number }) {
  const [open, setOpen] = useState(!collapse || text.length <= collapse);
  const json = useMemo(() => extractJson(text), [text]);
  if (json) {
    return (
      <div className="stack">
        {json.before && <Markdown text={json.before} />}
        {Array.isArray(json.data) ? <JsonValue v={json.data} /> : <JsonTree data={json.data as Record<string, unknown>} />}
        {json.after && <Markdown text={json.after} />}
      </div>
    );
  }
  const shown = open ? text : text.slice(0, collapse) + "…";
  return (
    <div className="stack">
      <Markdown text={shown} />
      {collapse > 0 && text.length > collapse && (
        <button className="more" onClick={() => setOpen((o) => !o)} aria-expanded={open}>{open ? "Show less" : "Show more"}</button>
      )}
    </div>
  );
}

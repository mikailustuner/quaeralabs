// Recognises math in plain text: agents do not always write $…$ ("∑_{k ≥ N} 1/a k < ε", "a : ℕ → ℕ",
// "Summable (fun k => (1:ℝ) / (a k : ℝ))"). Math fragments are wrapped in $…$ for KaTeX, Lean expressions in `…`.
// Code blocks, inline code, existing $…$ and links are preserved. A fragment KaTeX cannot parse is shown as code.
import katex from "katex";

const LEAN_MARK = /(\bfun\b|=>|↦|\b(?:Filter|Set|Finset|Nat|Int|Real|Function|Polynomial|MeasureTheory)\.\w|\bSummable\b|\bTendsto\b|\bnhds\b|\batTop\b|\bHasSum\b|:\s*[ℝℕℤℚℂ]\)|:=|∑'|↑)/;
const STRONG = /[∑∏∫√≤≥≠≈→←↔⇒∞∈∉⊂⊆⊃⊇∪∩∀∃∧∨¬ℕℤℚℝℂεδαβγλμσπθφωΣΠΔ∂·×÷±∘²³¹⁰⁴⁵⁶⁷⁸⁹ⁿ₀₁₂₃₄₅₆₇₈₉ₙₖᵢⱼₘ|{}=<>^]|[A-Za-z0-9)]_[{A-Za-z0-9]/;
const FUNCS = new Set(["limsup", "liminf", "lim", "sup", "inf", "max", "min", "log", "ln", "exp", "sin", "cos", "tan", "gcd", "lcm", "deg", "det", "mod"]);
const LONE_VARS = /^[b-df-hj-np-zB-HJ-NP-Z]$/;          // letters italicised when standing alone (a, e, i, o, u: could be words)
const OPEN = "([{⟨", CLOSE = ")]}⟩";

type Kind = "lean" | "strong" | "var" | "num" | "op" | "word";
interface Unit { text: string; core: string; tail: string; kind: Kind; group: boolean }

function depth(s: string): number {
  let d = 0;
  for (const ch of s) { if (OPEN.includes(ch)) d++; else if (CLOSE.includes(ch)) d--; }
  return d;
}

function classify(core: string, group: boolean): Kind {
  if (!core) return "word";
  if (/:\/\/|www\.|@/.test(core)) return "word";
  if (LEAN_MARK.test(core) || /^[A-Z][A-Za-z]*\.[A-Za-z]/.test(core)) return "lean";
  if (/^[+\-−*/=<>≤≥≠:,|·×]$/.test(core) || core === "->" || core === "<=" || core === ">=") return "op";
  if (/^-?\d+(?:[.,]\d+)?$/.test(core)) return "num";
  if (/^[A-Za-z](?:'|\d+)?$/.test(core) || /^\d+[A-Za-z]$/.test(core) || FUNCS.has(core)) return "var";
  if (STRONG.test(core)) {
    // Groups and symbol fragments: text if they contain a Turkish/English word (≥4 letters, not a math function).
    // The Turkish letters in the character class are intentional, so Turkish words are recognised too.
    const words = core.match(/[A-Za-zçğıöşüÇĞİÖŞÜ]{4,}/g) || [];
    return words.every((w) => FUNCS.has(w) || /^[a-z]*card$/.test(w)) ? "strong" : "word";
  }
  if (group && /^[([{][^\s]*[)\]}]$/.test(core) && /^[([{][A-Za-z0-9+\-−*/ ,]*[)\]}]$/.test(core)) return "var";
  if (/^\d+\/\d+$/.test(core) || /^[A-Za-z0-9()]{1,3}\/[A-Za-z0-9()]{1,3}$/.test(core)) return "strong";
  return "word";
}

/** Whitespace-separated fragments; a fragment with an open bracket is joined until it closes. */
function units(text: string): (Unit | string)[] {
  const raw = text.split(/(\s+)/);
  const out: (Unit | string)[] = [];
  for (let i = 0; i < raw.length; i++) {
    let t = raw[i];
    if (!t || /^\s+$/.test(t)) { if (t) out.push(t); continue; }
    let group = false;
    if (depth(t) > 0) {
      let j = i, acc = t;
      while (depth(acc) > 0 && j + 2 < raw.length && acc.length < 400) { acc += raw[j + 1] + raw[j + 2]; j += 2; }
      if (depth(acc) <= 0) { t = acc; i = j; group = true; }
    }
    const m = t.match(/^(.*?)([.,;:!?]+)$/s);
    const core = m && m[1] && !/[.]{2,}$/.test(t) ? m[1] : t;
    const tail = core === t ? "" : t.slice(core.length);
    out.push({ text: t, core, tail, kind: classify(core, group || /^[([{]/.test(core)), group: group || /^[([{⟨]/.test(core) });
  }
  return out;
}

const SYM: [RegExp, string][] = [
  [/->|→/g, "\\to "], [/<=|≤/g, "\\le "], [/>=|≥/g, "\\ge "], [/!=|≠/g, "\\ne "], [/≈/g, "\\approx "], [/←/g, "\\leftarrow "],
  [/↔/g, "\\leftrightarrow "], [/⇒/g, "\\Rightarrow "], [/↦/g, "\\mapsto "], [/∞/g, "\\infty "], [/∈/g, "\\in "], [/∉/g, "\\notin "],
  [/⊆/g, "\\subseteq "], [/⊂/g, "\\subset "], [/⊇/g, "\\supseteq "], [/⊃/g, "\\supset "], [/∪/g, "\\cup "], [/∩/g, "\\cap "],
  [/∀/g, "\\forall "], [/∃/g, "\\exists "], [/∧/g, "\\land "], [/∨/g, "\\lor "], [/¬/g, "\\neg "], [/∑/g, "\\sum "], [/∏/g, "\\prod "],
  [/∫/g, "\\int "], [/√/g, "\\sqrt "], [/·/g, "\\cdot "], [/×/g, "\\times "], [/÷/g, "\\div "], [/±/g, "\\pm "], [/∘/g, "\\circ "],
  [/∂/g, "\\partial "], [/…/g, "\\dots "], [/−/g, "-"],
  [/ℕ/g, "\\mathbb{N}"], [/ℤ/g, "\\mathbb{Z}"], [/ℚ/g, "\\mathbb{Q}"], [/ℝ/g, "\\mathbb{R}"], [/ℂ/g, "\\mathbb{C}"],
  [/ε/g, "\\varepsilon "], [/δ/g, "\\delta "], [/α/g, "\\alpha "], [/β/g, "\\beta "], [/γ/g, "\\gamma "], [/λ/g, "\\lambda "],
  [/μ/g, "\\mu "], [/σ/g, "\\sigma "], [/π/g, "\\pi "], [/θ/g, "\\theta "], [/φ/g, "\\varphi "], [/ω/g, "\\omega "],
  [/Σ/g, "\\Sigma "], [/Π/g, "\\Pi "], [/Δ/g, "\\Delta "], [/%/g, "\\%"], [/#/g, "\\#"], [/&/g, "\\&"], [/~/g, "\\sim "],
];
const SUP: Record<string, string> = { "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "ⁿ": "n" };
const SUB: Record<string, string> = { "ₙ": "n", "ₖ": "k", "ᵢ": "i", "ⱼ": "j", "ₘ": "m", "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9" };

/** Plain math → TeX. Braces (outside _{…}/^{…}) become \{ \}; " | " inside sets becomes \mid. */
export function toTex(src: string): string {
  let s = src.replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]+/g, (m) => `^{${[...m].map((c) => SUP[c]).join("")}}`)
    .replace(/[₀₁₂₃₄₅₆₇₈₉ₙₖᵢⱼₘ]+/g, (m) => `_{${[...m].map((c) => SUB[c]).join("")}}`);
  // braces
  let out = "";
  const stack: boolean[] = [];
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "{") {
      const grouping = i > 0 && (s[i - 1] === "_" || s[i - 1] === "^");
      stack.push(grouping);
      out += grouping ? "{" : "\\{";
    } else if (ch === "}") {
      out += stack.pop() ? "}" : "\\}";
    } else out += ch;
  }
  s = out.replace(/ \| /g, " \\mid ");
  // Keep "a k" (a applied to k in Lean) from merging into "ak" in KaTeX: thin space between single letters.
  s = s.replace(/(?<![\\A-Za-z])([A-Za-z]) +(?=[A-Za-z](?![A-Za-z]))/g, "$1\\,");
  for (const [re, rep] of SYM) s = s.replace(re, rep);
  s = s.replace(/(?<![\\A-Za-z])([A-Za-z]{2,})(?![A-Za-z{])/g, (w) => {
    if (FUNCS.has(w)) return w === "mod" ? "\\bmod " : `\\${w} `.replace("\\ln ", "\\ln ");
    return w.length >= 3 ? `\\operatorname{${w}}` : w;
  });
  return s.replace(/\s+/g, " ").trim();
}

function renders(tex: string): boolean {
  try { katex.renderToString(tex, { throwOnError: true }); return true; } catch { return false; }
}

function emitMath(text: string): string {
  const tex = toTex(text);
  if (tex && !tex.includes("$") && renders(tex)) return `$${tex}$`;
  return text.includes("`") ? text : `\`${text}\``;
}

/** Marks math and Lean fragments in a piece of text (outside code/math). */
function markPlain(text: string): string {
  const us = units(text);
  const res: string[] = [];
  let i = 0;
  const isU = (x: Unit | string | undefined): x is Unit => typeof x === "object";
  const next = (k: number) => { let j = k + 1; while (j < us.length && !isU(us[j])) j++; return j; };
  while (i < us.length) {
    const u = us[i];
    if (!isU(u)) { res.push(u); i++; continue; }
    // Lean span: starts with a Lean-marked fragment or a CamelCase/dotted name and continues with groups and names.
    const leanHead = u.kind === "lean" || (/^[A-Z][A-Za-z]+$/.test(u.core) && !u.tail && isU(us[next(i)]) && (us[next(i)] as Unit).kind === "lean");
    if (leanHead) {
      let j = i, end = i, hasLean = false;
      while (j < us.length) {
        const v = us[j] as Unit;
        if (!isU(v)) { j++; continue; }
        const ok = v.kind === "lean" || v.group || /^[A-Z][\w.']*$/.test(v.core) || (/^[a-z]{1,2}\d?$/.test(v.core) && isU(us[next(j)]) && (((us[next(j)] as Unit).group) || (us[next(j)] as Unit).kind === "lean"));
        if (!ok) break;
        hasLean ||= v.kind === "lean";
        end = j;
        if (v.tail && /[.;,]/.test(v.tail)) break;
        j = next(j);
      }
      if (hasLean) {
        const parts = us.slice(i, end + 1).map((x) => (typeof x === "string" ? x : x.text)).join("");
        const last = us[end] as Unit;
        const body = last.tail ? parts.slice(0, parts.length - last.tail.length) : parts;
        res.push(body.includes("`") ? body : `\`${body}\``, last.tail);
        i = end + 1;
        continue;
      }
    }
    // Math: the longest run of symbols, variables, numbers and operators.
    if (u.kind !== "word" && u.kind !== "lean") {
      let j = i, end = i, strong = 0, ops = 0, atoms = 0;
      while (j < us.length) {
        const v = us[j];
        if (!isU(v)) { j++; continue; }
        if (v.kind === "word" || v.kind === "lean") break;
        strong += +(v.kind === "strong"); ops += +(v.kind === "op"); atoms += +(v.kind === "var" || v.kind === "num");
        end = j;
        if (v.tail && /[.;!?]/.test(v.tail)) break;    // end of sentence
        if (v.tail === "," && !(isU(us[next(j)]) && /^[0-9A-Za-z]$/.test((us[next(j)] as Unit).core) && us.slice(j + 1, next(j) + 3).some((x) => isU(x) && x.kind !== "word"))) break;
        j = next(j);
      }
      // lone leading/trailing operators stay in the text
      let a = i, b = end;
      while (a <= b && (!isU(us[a]) || (us[a] as Unit).kind === "op")) a++;
      while (b >= a && (!isU(us[b]) || ((us[b] as Unit).kind === "op" && !(us[b] as Unit).tail))) b--;
      const span = us.slice(a, b + 1);
      const real = span.filter(isU);
      const sStrong = real.filter((x) => x.kind === "strong").length, sOps = real.filter((x) => x.kind === "op").length;
      const sAtoms = real.filter((x) => x.kind === "var" || x.kind === "num").length;
      const lone = real.length === 1 && real[0].kind === "var" && LONE_VARS.test(real[0].core);
      const relation = real.some((x) => x.kind === "op" && /^[=<>≤≥≠]$/.test(x.core)) && sAtoms >= 1;
      if (a <= b && (sStrong > 0 || (sOps > 0 && sAtoms >= 2) || relation || lone)) {
        for (const x of us.slice(i, a)) res.push(typeof x === "string" ? x : x.text);
        const last = us[b] as Unit;
        const parts = span.map((x) => (typeof x === "string" ? x : x.text)).join("");
        const body = last.tail ? parts.slice(0, parts.length - last.tail.length) : parts;
        res.push(emitMath(body), last.tail);
        i = b + 1;
        continue;
      }
      void strong; void ops; void atoms;
    }
    res.push(u.text);
    i++;
  }
  return res.join("");
}

const PROTECT = /(```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]|https?:\/\/\S+|<\/?[A-Za-z][\w-]*(?:\s[^<>\n]*)?>)/g;

/** Applies math/Lean markup to the unprotected plain-text parts of the Markdown source (line by line). */
export function autoMath(src: string): string {
  return src.split(PROTECT).map((part, i) => {
    if (i % 2 === 1) return part;
    part = part.replace(/\$(?=\d)/g, "\\$");            // don't mistake a money amount ($1.17) for a math delimiter
    return part.split("\n").map((line) => {
      if (/^\s*\|/.test(line) || /^\s*(```|~~~)/.test(line)) return line;                  // table rows
      const m = line.match(/^(\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)*)(.*)$/s);
      return m ? m[1] + markPlain(m[2]) : markPlain(line);
    }).join("\n");
  }).join("");
}

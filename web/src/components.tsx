import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AVATARS } from "./avatars";

export function Avatar({ role, size = "md" }: { role: string; size?: "sm" | "md" | "lg" }) {
  const svg = AVATARS[role];
  if (!svg) {
    return (
      <svg className={`avatar ${size === "md" ? "" : size}`} viewBox="0 0 44 44" aria-hidden="true">
        <rect width="44" height="44" rx="12" fill="#2F3540" />
        <circle cx="22" cy="18" r="7" fill="#A1A7B0" />
        <path d="M9 38c2-8 7-11 13-11s11 3 13 11z" fill="#A1A7B0" />
      </svg>
    );
  }
  return <svg className={`avatar ${size === "md" ? "" : size}`} viewBox="0 0 44 44" aria-hidden="true" dangerouslySetInnerHTML={{ __html: svg }} />;
}

export function StateBox({ kind, children, action }: { kind: "loading" | "empty" | "error"; children?: ReactNode; action?: ReactNode }) {
  if (kind === "loading") return <div className="stack" aria-busy="true" aria-live="polite"><span className="sr-only">Loading…</span><div className="skeleton" /><div className="skeleton" /></div>;
  return (
    <div className="state" role={kind === "error" ? "alert" : undefined}>
      <p className={kind === "error" ? "err" : ""}>{children}</p>
      {action}
    </div>
  );
}

/** Simple async loader: returns the loading / error / data states explicitly. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, set] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  const [n, reload] = useState(0);
  useEffect(() => {
    let alive = true;
    set((s) => ({ ...s, loading: true }));
    fn().then((data) => alive && set({ data, loading: false }), (e) => alive && set({ error: String(e.message || e), loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { ...state, reload: () => reload((x) => x + 1) };
}

export function Toast({ msg, onDone }: { msg: string | null; onDone: () => void }) {
  useEffect(() => { if (msg) { const t = setTimeout(onDone, 5000); return () => clearTimeout(t); } }, [msg, onDone]);
  return <div role="status" aria-live="polite">{msg && <div className="toast">{msg}</div>}</div>;
}

export function statusBadge(status: string | null | undefined) {
  const map: Record<string, [string, string]> = {
    supported: ["good", "supported"], refuted: ["bad", "refuted"], inconclusive: ["warn", "inconclusive"],
    accepted: ["info", "accepted"], testing: ["info", "testing"], under_critique: ["warn", "under critique"], retracted: ["bad", "retracted"],
    draft: ["", "draft"], rejected: ["bad", "rejected"], yes: ["good", "reproduced"], no: ["bad", "not reproduced"], partial: ["warn", "partial"],
    open: ["warn", "open objection"], resolved: ["good", "resolved"], rejected_with_reason: ["warn", "rejected with reason"],
    supports: ["good", "supports"], contradicts: ["bad", "contradicts"], methodological_only: ["", "methodological only"],
  };
  const [cls, tr] = map[status || ""] || ["", status || "—"];
  return <span className={`badge ${cls}`}>{tr}</span>;
}

/** Long text is clamped to a few lines; "Show more" appears only when the text is actually cut off. */
export function Clamp({ lines, children, className = "" }: { lines: number; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const check = () => setCut(el.scrollHeight > el.clientHeight + 2);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, children]);
  return (
    <div className={`clamp-wrap ${className}`}>
      <div ref={ref} className={open ? "" : "clamp-lines"} style={open ? undefined : { WebkitLineClamp: lines }}>{children}</div>
      {(cut || open) && <button className="more" onClick={() => setOpen((o) => !o)} aria-expanded={open}>{open ? "Show less" : "Show more"}</button>}
    </div>
  );
}

// Proje yöneticisi sohbeti: araştırmayı yalnızca izleyen ajan. Direktör'ün dikkatini dağıtmadan insanın sorularını yanıtlar.
// Yönetici ekibe hiçbir şey göndermez; bir not önerirse ("Send to Director") göndermek insanın tıklamasıyla olur.
import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { ChatTurn, api, money, time } from "./api";
import { Avatar } from "./components";
import { Markdown } from "./rich";

export type ManagerState = {
  turns: ChatTurn[]; usage: { spentUsd: number; capUsd: number } | null; waiting: boolean; error: string | null;
  loaded: boolean; ask: (text: string) => Promise<void>; load: () => void;
};

export function useManager(pid: string | null): ManagerState {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [usage, setUsage] = useState<ManagerState["usage"]>(null);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const load = useCallback(() => {
    if (!pid) return;
    api.manager(pid).then((r) => { setTurns(r.history); setUsage(r.usage); setLoaded(true); }, (e) => setError(String(e.message || e)));
  }, [pid]);
  useEffect(() => { setTurns([]); setLoaded(false); load(); }, [load]);
  const ask = useCallback(async (text: string) => {
    if (!pid) return;
    const mine: ChatTurn = { seq: -Date.now(), at: new Date().toISOString(), from: "human", text };
    setTurns((t) => [...t, mine]); setWaiting(true); setError(null);
    try {
      const r = await api.ask(pid, text);
      setTurns((t) => [...t, r.reply]); setUsage(r.usage);
    } catch (e: any) {
      setError(e.message); setTurns((t) => t.filter((x) => x !== mine));
      throw e;
    } finally { setWaiting(false); }
  }, [pid]);
  return { turns, usage, waiting, error, loaded, ask, load };
}

function Turn({ t, pid, onToast }: { t: ChatTurn; pid: string; onToast?: (m: string) => void }) {
  const [sent, setSent] = useState(false);
  const forward = async () => {
    try { await api.message(pid, "director", t.forward!); setSent(true); onToast?.("Note sent to the Director"); }
    catch (e: any) { onToast?.(`Could not send: ${e.message}`); }
  };
  if (t.from === "human") return <div className="bubble me"><p style={{ whiteSpace: "pre-wrap" }}>{t.text}</p></div>;
  return (
    <div className={`bubble pm${t.error ? " err-bubble" : ""}`}>
      <Avatar role="manager" size="sm" />
      <div className="stack" style={{ gap: 8, minWidth: 0 }}>
        <Markdown text={t.text} />
        {t.forward && (
          <div className="forward">
            <span className="faint">Suggested note for the team</span>
            <p>{t.forward}</p>
            <button className="btn sm" onClick={forward} disabled={sent}>{sent ? "Sent to Director" : "Send to Director"}</button>
          </div>
        )}
        <span className="faint">{time(t.at)}{t.costUsd != null ? ` · ${money(t.costUsd)}` : ""}</span>
      </div>
    </div>
  );
}

/** Sohbet akışı + giriş; hem çekmecede hem yan panelde kullanılır. */
export function ManagerThread({ pid, m, onToast, autoFocus = false, input: withInput = true, onPick }: {
  pid: string; m: ManagerState; onToast?: (m: string) => void; autoFocus?: boolean; input?: boolean; onPick?: (s: string) => void;
}) {
  const [text, setText] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  // Yalnızca sohbet kutusu kaydırılır (scrollIntoView sayfayı da kaydırırdı).
  useEffect(() => { const el = log.current; if (el) el.scrollTop = el.scrollHeight; }, [m.turns.length, m.waiting]);
  useEffect(() => { if (autoFocus) input.current?.focus(); }, [autoFocus]);
  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const v = text.trim();
    if (!v || m.waiting) return;
    setText("");
    try { await m.ask(v); } catch { setText(v); }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  return (
    <div className="pm-thread">
      <div className="pm-log" ref={log} tabIndex={0} role="log" aria-label="Conversation with the project manager" aria-live="polite" aria-relevant="additions">
        {!m.loaded && !m.error ? <p className="faint">Loading conversation…</p>
          : m.turns.length === 0 ? (
            <div className="pm-empty">
              <p>Ask about progress, decisions or results. The manager reads the project record and answers here, so the team keeps working undisturbed.</p>
              <div className="chips">
                {["What is the team doing right now?", "Why was this hypothesis chosen?", "Summarize the result so far"].map((s) => (
                  <button key={s} className="chip" onClick={() => (onPick ? onPick(s) : setText(s))}>{s}</button>
                ))}
              </div>
            </div>
          ) : m.turns.map((t) => <Turn key={t.seq} t={t} pid={pid} onToast={onToast} />)}
        {m.waiting && <div className="bubble pm"><Avatar role="manager" size="sm" /><p className="typing"><span /><span /><span /><span className="sr-only">The manager is reading the project record…</span></p></div>}
      </div>
      {m.error && <p className="err" role="alert">{m.error}</p>}
      {withInput && <form className="pm-input" onSubmit={send}>
        <label className="sr-only" htmlFor={`pm-${pid}`}>Message the project manager</label>
        <textarea id={`pm-${pid}`} ref={input} rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          placeholder="Ask the project manager…" maxLength={4000} />
        <button className="send" type="submit" disabled={m.waiting || !text.trim()} aria-label="Send to project manager">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
        </button>
      </form>}
      {m.usage && <p className="faint pm-usage">Chat budget {money(m.usage.spentUsd)} of {money(m.usage.capUsd)} · separate from the research budget</p>}
    </div>
  );
}

/** Tam ekran sohbet çekmecesi (Esc ile kapanır, odak içeride kalır). */
export function ManagerDrawer({ open, pid, m, onClose, onToast }: { open: boolean; pid: string; m: ManagerState; onClose: () => void; onToast?: (m: string) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) dlg.showModal();
    if (!open && dlg.open) dlg.close();
  }, [open]);
  return (
    <dialog ref={ref} className="drawer" aria-labelledby="pm-h" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      {open && (
        <>
          <div className="drawer-head">
            <div className="row" style={{ gap: 12 }}>
              <Avatar role="manager" size="lg" />
              <div style={{ minWidth: 0, flex: 1 }}>
                <h2 id="pm-h" style={{ fontSize: 20 }}>Project manager</h2>
                <p className="muted">Watches this project and answers you. It never interrupts the Director; notes for the team are sent only when you click.</p>
              </div>
              <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
            </div>
          </div>
          <div className="drawer-body pm-body"><ManagerThread pid={pid} m={m} onToast={onToast} autoFocus /></div>
        </>
      )}
    </dialog>
  );
}

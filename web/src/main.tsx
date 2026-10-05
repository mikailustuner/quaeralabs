import { Component, ReactNode, StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Learning, Summary, api } from "./api";
import { useLoad } from "./components";
import { Memory, NewResearch, Projects, Settings } from "./pages/Home";
import { useTheme } from "./theme";
import { Project } from "./pages/Project";
import { Replay } from "./pages/Replay";
import { useRoute } from "./router";
import { BrandMark, EVAL_PREFIX, ICONS, icon } from "./ui";
import "./styles.css";

/** Bir görünüm çökerse tüm uygulama boşalmasın; hata gösterilir, menü çalışmaya devam eder. */
class Boundary extends Component<{ children: ReactNode; k: string }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(e: Error) { return { error: e.message }; }
  componentDidUpdate(prev: { k: string }) { if (prev.k !== this.props.k && this.state.error) this.setState({ error: null }); }
  render() {
    return this.state.error
      ? <div className="state" role="alert"><p className="err">This view could not be shown: {this.state.error}</p><button className="btn" onClick={() => location.reload()}>Reload page</button></div>
      : this.props.children;
  }
}

const KIND_LABEL: Record<string, string> = {
  finding: "Finding", refuted: "Refuted", method: "Method", caveat: "Caveat", stopped: "Stopped", open: "Open question",
  observation: "Observation", result: "Result",
};

/** Tarihe göre gruplama (referanstaki "Oct 2 / Older" gibi): Today, Yesterday, gün adı, Older. */
function groupByDate(items: Summary[]): [string, Summary[]][] {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const groups = new Map<string, Summary[]>();
  for (const p of items) {
    const d = p.createdAt ? new Date(p.createdAt) : null;
    let key = "Older";
    if (d) {
      const day = new Date(d); day.setHours(0, 0, 0, 0);
      const diff = Math.round((today.getTime() - day.getTime()) / 86400000);
      key = diff <= 0 ? "Today" : diff === 1 ? "Yesterday" : diff < 7 ? day.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "Older";
    }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(p);
  }
  return [...groups.entries()];
}

function Sidebar({ section, pid, onCollapse, onNavigate }: { section: string; pid?: string; onCollapse: () => void; onNavigate: () => void }) {
  const [theme, setTheme] = useTheme();
  const [q, setQ] = useState("");
  const projects = useLoad(api.projects, [section, pid]);
  const learned = useLoad(() => api.learnings({ limit: 6 }), [section, pid]);
  // Kısa adlar arka planda verilir, öğrenilenler her deney sonucunda eklenir: kenar çubuğu kendini tazeler.
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") { projects.reload(); learned.reload(); } }, 8000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const needle = q.trim().toLowerCase();
  const mine = (projects.data || []).filter((p) => !EVAL_PREFIX.test(p.id))
    .filter((p) => !needle || `${p.shortTitle} ${p.title}`.toLowerCase().includes(needle));
  const memory = (learned.data || []).filter((l) => !needle || `${l.text} ${l.title}`.toLowerCase().includes(needle)).slice(0, 5);
  const link = (href: string, key: string, text: string, ic: string) => (
    <li><a className="nav-link" href={href} onClick={onNavigate} aria-current={section === key ? "page" : undefined}>{icon(ic)}{text}</a></li>
  );
  return (
    <nav className="side" aria-label="Main">
      <div className="side-top">
        <a className="brand" href="#/" onClick={onNavigate}><BrandMark />QuaeraLabs</a>
        <button className="icon-btn" onClick={onCollapse} aria-label="Close sidebar" title="Close sidebar">{icon(ICONS.panel)}</button>
      </div>
      <div className="side-search">
        {icon(ICONS.search, 16)}
        <label className="sr-only" htmlFor="side-q">Search projects and memory</label>
        <input id="side-q" type="search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="side-scroll">
        <ul className="nav-list">
          <li><a className="nav-link nav-primary" href="#/" onClick={onNavigate} aria-current={section === "" ? "page" : undefined}>
            <span className="plus">{icon(ICONS.plus, 14)}</span>New research</a></li>
          {link("#/research", "research", "Research", ICONS.list)}
          {link("#/memory", "memory", "Lab memory", ICONS.memory)}
          {link("#/replay", "replay", "Replay", ICONS.replay)}
          {link("#/settings", "settings", "Settings", ICONS.gear)}
        </ul>

        <section className="side-group" aria-labelledby="mem-h">
          <div className="side-label"><span id="mem-h">What the lab learned</span><a href="#/memory" onClick={onNavigate}>All</a></div>
          {learned.loading && !learned.data ? <p className="side-empty">Loading…</p>
            : learned.error ? <p className="side-empty">Memory unavailable</p>
            : memory.length ? (
              <ul className="mem-list">
                {memory.map((l: Learning, i) => (
                  <li key={`${l.project}-${i}`}>
                    <a className="mem-item" href={`#/p/${encodeURIComponent(l.project)}`} onClick={onNavigate} title={l.text}>
                      <span className={`k ${l.kind}`} aria-hidden="true" />
                      <span><span className="sr-only">{KIND_LABEL[l.kind] || l.kind}: </span><span className="t">{l.text}</span><span className="src">{l.title}</span></span>
                    </a>
                  </li>
                ))}
              </ul>
            ) : <p className="side-empty">{needle ? "No matches" : "Lessons appear here after each experiment result."}</p>}
        </section>

        {projects.loading && !projects.data ? <p className="side-empty">Loading projects…</p>
          : projects.error ? <p className="side-empty">Projects unavailable</p>
          : mine.length === 0 ? <p className="side-empty">{needle ? "No matching projects" : "No projects yet"}</p>
          : groupByDate(mine).map(([label, items]) => (
            <section key={label} className="side-group" aria-label={`Projects · ${label}`}>
              <div className="side-label">{label}</div>
              <ul className="nav-list">
                {items.map((p) => (
                  <li key={p.id}>
                    <a className="proj-link" href={`#/p/${encodeURIComponent(p.id)}`} onClick={onNavigate} aria-current={pid === p.id ? "page" : undefined} title={p.title}>
                      <span className={`dot ${p.running ? "working" : p.current ? "" : "done"}`} aria-hidden="true" />
                      <span className="txt">{p.shortTitle}</span>
                      {p.running && <span className="sr-only">(running)</span>}
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          ))}
      </div>
      <div className="side-foot">
        <div className="who"><span className="initial" aria-hidden="true">L</span><span>Local lab · 127.0.0.1</span></div>
        <div className="theme-switch" role="group" aria-label="Theme">
          {([["system", "System theme", ICONS.monitor], ["light", "Light theme", ICONS.sun], ["dark", "Dark theme", ICONS.moon]] as const).map(([k, t, ic]) => (
            <button key={k} aria-pressed={theme === k} onClick={() => setTheme(k)} aria-label={t} title={t}>{icon(ic, 15)}</button>
          ))}
        </div>
      </div>
    </nav>
  );
}

function App() {
  const [a, b, c] = useRoute();
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("quaera.side") === "closed"; } catch { return false; } });
  const [menu, setMenu] = useState(false);
  useEffect(() => { try { localStorage.setItem("quaera.side", collapsed ? "closed" : "open"); } catch { /* yalnızca kolaylık */ } }, [collapsed]);
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);
  const section = a === "p" ? "p" : a || "";
  const page = a === "p" && b ? <Project pid={b} tab={c || ""} /> : a === "research" ? <Projects /> : a === "replay" ? <Replay pid={b} /> :
    a === "settings" ? <Settings /> : a === "memory" ? <Memory /> : <NewResearch />;
  return (
    <div className={`app${collapsed ? " collapsed" : ""}${menu ? " menu-open" : ""}`}>
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>Skip to content</a>
      <header className="mobile-bar">
        <button className="icon-btn" onClick={() => setMenu(true)} aria-label="Open menu" aria-expanded={menu}>{icon(ICONS.menu)}</button>
        <a className="brand" href="#/"><BrandMark />QuaeraLabs</a>
      </header>
      <Sidebar section={section} pid={a === "p" ? b : undefined}
        onCollapse={() => (window.matchMedia("(max-width: 900px)").matches ? setMenu(false) : setCollapsed(true))}
        onNavigate={() => setMenu(false)} />
      {menu && <button className="scrim" aria-label="Close menu" onClick={() => setMenu(false)} />}
      <main id="main" className="main" tabIndex={-1}>
        <div className="main-tools">
          <button className="icon-btn" onClick={() => setCollapsed(false)} aria-label="Open sidebar" title="Open sidebar">{icon(ICONS.panel)}</button>
        </div>
        <Boundary k={[a, b, c].join("/")}>{page}</Boundary>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);

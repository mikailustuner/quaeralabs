// End-to-end user flow + accessibility audit (axe-core, WCAG 2.1 AA).
// Runs against tests/e2e_server.py, which uses fake models: costs no money.
// Usage: node e2e/walkthrough.mjs [output-dir]
import AxeBuilder from "@axe-core/playwright";
import { chromium } from "playwright";
import { mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";

const BASE = process.env.QUAERA_URL || "http://127.0.0.1:8766";
const OUT = process.argv[2] || "test-results";
mkdirSync(OUT, { recursive: true });
const dir = `${homedir()}/.cache/ms-playwright`;
const shell = readdirSync(dir).find((d) => d.startsWith("chromium_headless_shell"));
const browser = await chromium.launch({ executablePath: shell && `${dir}/${shell}/chrome-headless-shell-linux64/chrome-headless-shell` });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const steps = [];
const a11y = [];
const step = (name, ok, detail = "") => { steps.push({ name, ok, detail }); console.log(`${ok ? "✓" : "✗"} ${name}${detail ? " — " + detail : ""}`); };

async function audit(name) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = r.violations.filter((v) => ["serious", "critical"].includes(v.impact));
  a11y.push({ page: name, violations: r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help })) });
  step(`axe: ${name}`, bad.length === 0, r.violations.map((v) => `${v.id}(${v.impact})×${v.nodes.length} [${v.nodes.slice(0, 3)
    .map((n) => `${n.target.join(" ")}: ${(n.any[0]?.message || "").replace(/^Element has insufficient color contrast of /, "").slice(0, 90)}`).join(" ; ")}]`).join(", "));
}

try {
  // 1) Home page: centred composer; empty sidebar
  await page.goto(`${BASE}/#/`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.getByText("No projects yet").waitFor();
  step("home: composer and empty sidebar", true);
  await audit("home (empty)");

  // 2) Invalid submit via keyboard: focus moves to the question field
  await page.getByLabel("Research question").focus();
  await page.keyboard.press("Control+Enter");
  const focused = await page.evaluate(() => document.activeElement?.id);
  step("invalid submit keeps focus on the question", focused === "f-question", `focus: ${focused}`);
  await audit("home (validation error)");

  // 3) Start the research
  await page.getByLabel("Research question").fill("Is the sum of the first n odd numbers n²?");
  await page.getByRole("button", { name: "Start research" }).click();
  await page.waitForURL(/#\/p\//);
  const pid = decodeURIComponent(page.url().split("#/p/")[1]);
  step("project created", true, pid);
  await page.locator(".question .katex").first().waitFor({ timeout: 10000 }).catch(() => undefined);
  const mathInQuestion = await page.locator(".question .katex").count();
  step("parser: math in the question is typeset (KaTeX)", mathInQuestion >= 2, `${mathInQuestion} formulas`);

  // 4) Hypothesis approval
  await page.getByRole("heading", { name: "Which hypothesis should be tested?" }).waitFor({ timeout: 30000 });
  step("hypothesis decision card arrived live", true);
  await page.locator(".side").getByRole("link", { name: "Sum of first n odds" }).waitFor({ timeout: 20000 });
  step("sidebar shows the manager's short project name", true);
  await audit("lab (decision pending)");
  await page.screenshot({ path: `${OUT}/decision.png` });
  await page.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Approve", exact: true }).click();

  // 5) Experiment approval + live view
  await page.getByRole("heading", { name: "Approve the proof plan" }).waitFor({ timeout: 30000 });
  step("experiment decision card with structured facts", await page.locator(".decision .fact").count() > 0);
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  const live = page.locator("section[aria-labelledby=aa-h]");
  await live.getByText(/Compiling/).first().waitFor({ timeout: 30000 });
  const liveCode = await live.locator("pre.code").first().innerText({ timeout: 15000 }).catch(() => "");
  step("live view: current step and the Lean code being compiled", /theorem|import/.test(liveCode), liveCode.split("\n")[0]);
  await page.screenshot({ path: `${OUT}/live.png` });

  // 6) Project manager: default recipient; reply in the right panel, the team is not interrupted
  const before = await page.locator(".feed li").count();
  await page.getByLabel("Message", { exact: true }).fill("What is the status? Please tell the team to mention n = 0.");
  await page.getByRole("button", { name: "Ask the project manager" }).click();
  const card = page.locator(".pm-card");
  await card.getByText("Suggested note for the team").waitFor({ timeout: 15000 });
  const pmMath = await card.locator(".katex").count();
  step("manager answers in the side panel (math typeset)", pmMath > 0, `${pmMath} formulas`);
  const after = await page.locator(".feed li").count();
  step("manager chat does not post to the team feed", !(await page.locator(".feed").getByText("What is the status?").count()), `${before}→${after} feed items`);
  await card.getByRole("button", { name: "Send to Director" }).click();
  await page.locator(".feed").getByText(/@Director: Please also state the/).waitFor({ timeout: 10000 });
  step("suggested note reaches the Director only after the click", true);

  // 7) Note to the team (@Critic)
  await page.getByLabel("Recipient").selectOption("critic");
  await page.getByLabel("Message", { exact: true }).fill("Check the edge cases as well.");
  await page.getByRole("button", { name: "Send note to the team" }).click();
  await page.locator(".feed").getByText("@Critic: Check the edge cases as well.").waitFor({ timeout: 10000 });
  step("@Critic note appears in the feed", true);

  // 8) Finish
  await page.getByText("Research finished", { exact: true }).first().waitFor({ timeout: 60000 });
  step("research finished", true);
  await audit("lab (finished)");

  // 8b) Report a problem
  await page.getByRole("button", { name: "Report a problem" }).click();
  await page.getByRole("button", { name: "Save case" }).click();
  await page.getByRole("dialog").getByRole("alert").waitFor();
  await audit("report a problem dialog");
  await page.getByLabel("What went wrong?").fill("The result rests on one example but is presented as general.");
  await page.getByLabel("The Critic should have objected to the result").check();
  await page.getByRole("button", { name: "Save case" }).click();
  await page.getByText(/Case saved/).waitFor({ timeout: 10000 });
  step("problem report saved as a local case", true);

  // 9) Evidence graph via keyboard
  await page.getByRole("link", { name: "Evidence graph" }).click();
  const first = page.locator(".gnode").first();
  await first.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  const panel = await page.getByRole("complementary", { name: "Selected node" }).innerText();
  step("graph: arrow key + Enter opens details", /Hypothesis H-/.test(panel), panel.split("\n")[0]);
  await audit("evidence graph");

  // 10) Proof, scoring, agent and manager drawers, themes
  await page.getByRole("link", { name: "Lab", exact: true }).click();
  await page.getByRole("heading", { name: "Proof process" }).waitFor();
  await page.getByRole("heading", { name: "Verified proof" }).waitFor();
  step("proof process and verified proof are shown", await page.locator(".step-tree li").count() > 2);
  await page.getByRole("button", { name: /How was it chosen/ }).click();
  const rows = await page.locator(".rank-table tbody tr").count();
  step("hypothesis scoring: criteria and candidates in a table", rows >= 2, `${rows} candidates`);
  await page.getByRole("button", { name: /^Engineer:/ }).click();
  const drawer = page.getByRole("dialog", { name: "Engineer" });
  await drawer.waitFor();
  const saidCount = await drawer.locator(".said").count();
  await drawer.locator(".said pre.code").first().waitFor({ timeout: 10000 });
  step("agent drawer: what the Engineer said is readable", saidCount > 0, `${saidCount} outputs`);
  await audit("agent drawer");
  await page.keyboard.press("Escape");
  await drawer.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Open chat" }).click();
  const pm = page.getByRole("dialog", { name: "Project manager" });
  await pm.getByText(/Suggested note for the team/).first().waitFor();
  await pm.getByLabel("Message the project manager").fill("Summarize the result so far");
  await pm.getByRole("button", { name: "Send to project manager" }).click();
  await pm.locator(".bubble.pm").nth(1).waitFor({ timeout: 15000 });
  step("manager chat drawer keeps the conversation", await pm.locator(".bubble.me").count() >= 2);
  await audit("manager chat drawer");
  await page.keyboard.press("Escape");
  await pm.waitFor({ state: "hidden" });
  step("drawers close with Esc", true);
  await audit("lab (light)");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(300);
  await audit("lab (dark)");
  await page.screenshot({ path: `${OUT}/dark.png` });
  await page.emulateMedia({ colorScheme: "light" });
  await page.getByRole("link", { name: "Report", exact: true }).click();
  await page.locator(".prose h1").first().waitFor();
  step("report shown", true);
  await audit("report");
  const zip = await page.request.get(`${BASE}/api/projects/${encodeURIComponent(pid)}/export.zip`);
  step("evidence package downloads", zip.ok() && (await zip.body()).length > 1000, `${(await zip.body()).length} bytes`);

  // 11) Replay
  await page.goto(`${BASE}/#/replay/${encodeURIComponent(pid)}`);
  await page.getByRole("button", { name: "Next stage »" }).click();
  await page.getByRole("button", { name: "Next stage »" }).click();
  const prog = await page.getByText(/event \d+\/\d+/).first().innerText();
  step("replay advances stage by stage", /event [1-9]\d*\//.test(prog), prog);
  await audit("replay");

  // 12) Branching
  await page.goto(`${BASE}/#/p/${encodeURIComponent(pid)}`);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /All stages/ }).click();
  await page.getByRole("button", { name: "Branch after Hypotheses" }).click();
  await page.waitForURL(/-dal-/);
  step("branching from a stage opens the new project", true, decodeURIComponent(page.url().split("#/p/")[1]));

  // 12b) Research tree
  await page.goto(`${BASE}/#/p/${encodeURIComponent(pid)}/tree`);
  await page.getByText("New branch from this one, with a change").click();
  await page.getByLabel("New hypothesis").fill("For every n the sum of the first n odd numbers is a perfect square.");
  await page.getByLabel("Reason").fill("Let's test a more general claim.");
  await page.getByRole("button", { name: "Open branch and run" }).click();
  await page.waitForURL(/-d\d+\/tree/);
  const child = decodeURIComponent(page.url().split("#/p/")[1].replace("/tree", ""));
  await page.goto(`${BASE}/#/p/${encodeURIComponent(child)}`);
  await page.getByRole("heading", { name: "Which hypothesis should be tested?" }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("heading", { name: "Approve the proof plan" }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByText("Research finished", { exact: true }).first().waitFor({ timeout: 60000 });
  await page.goto(`${BASE}/#/p/${encodeURIComponent(child)}/tree`);
  await page.getByRole("heading", { name: "Hypothesis diff" }).waitFor({ timeout: 15000 });
  const ins = (await page.locator(".diff ins").allInnerTexts()).join(" ");
  const statText = await page.locator(".tree-stats").innerText();
  step("tree shows the hypothesis diff and branch rates", /perfect square/.test(ins) && /branch/i.test(statText), `added: "${ins}"`);
  await audit("research tree");

  // 13) List, memory, settings, sidebar
  await page.goto(`${BASE}/#/research`);
  await page.locator(".pitem").first().waitFor();
  await audit("research list");
  await page.getByRole("link", { name: "Lab memory" }).click();
  await page.getByRole("heading", { name: "Lessons" }).waitFor();
  await page.locator(".lessons li").first().waitFor({ timeout: 10000 }).catch(() => undefined);
  const lessons = await page.locator(".lessons li").count();
  step("lab memory lists lessons learned across projects", lessons >= 2, `${lessons} lessons`);
  const sideMem = await page.locator(".mem-item").count();
  step("sidebar shows what the lab learned", sideMem > 0, `${sideMem} items`);
  await page.getByLabel("Search lab memory").fill("odd numbers");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByRole("heading", { name: "Similar past research" }).waitFor();
  const hit = await page.locator("#mp-h ~ ul a").first().innerText({ timeout: 10000 });
  step("memory search finds past research", /tek|odd/i.test(hit), hit);
  await audit("lab memory");
  await page.goto(`${BASE}/#/settings`);
  await page.getByRole("heading", { name: "Sandbox limits" }).waitFor();
  await audit("settings");
  const models = await page.getByRole("region", { name: "Model providers (scrollable)" }).innerText();
  step("settings detect Codex CLI and OpenCode CLI", /Codex CLI/.test(models) && /OpenCode CLI/.test(models));
  await page.getByRole("row", { name: /Codex CLI/ }).getByRole("button", { name: "Test" }).click();
  await page.getByRole("row", { name: /Codex CLI/ }).getByText(/✓ 51/).waitFor({ timeout: 10000 });
  step("provider test button answers", true);

  // 13b) Discovery mode: multi-model ideation, cross-review, lemma programme, refutation + repair, synthesis
  await page.goto(`${BASE}/#/`);
  await page.getByRole("radio", { name: "Discover" }).click();
  await page.getByText("Discovery mode.").waitFor();
  await page.getByLabel("Research question").fill("Is the sum of the first n odd numbers n² — prove it as stated?");
  await page.getByLabel(/Budget/).fill("25");
  await page.getByRole("button", { name: "Start research" }).click();
  await page.waitForURL(/#\/p\//);
  const disc = decodeURIComponent(page.url().split("#/p/")[1]);
  await page.getByRole("heading", { name: "Confirm the target claim (not narrowed)" }).waitFor({ timeout: 30000 });
  step("discovery: target claim card (not narrowed)", true, disc);
  await page.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("heading", { name: "Choose the strategy to pursue" }).waitFor({ timeout: 45000 });
  const opts = await page.locator(".decision .choice").count();
  step("discovery: cross-reviewed strategies offered for choice", opts >= 2, `${opts} strategies`);
  await audit("discovery (strategy choice)");
  await page.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByText("Research finished", { exact: true }).first().waitFor({ timeout: 120000 });
  await page.getByRole("tab", { name: /^Strategies/ }).click();
  const board = await page.locator(".board-item").count();
  const fams = await page.locator(".card-head:has(#str-h) .faint").first().innerText();
  step("discovery: strategy board shows several model families", board >= 2 && /2 model families/.test(fams), `${board} strategies · ${fams}`);
  await page.locator(".board-item").nth(1).press("ArrowUp");
  await page.locator(".board-item").last().click();
  step("discovery: strategy list selects one detail panel", (await page.locator(".board-item[aria-selected=true]").count()) === 1 && (await page.locator("#str-detail").count()) === 1);
  await page.getByRole("tab", { name: /^Lemma program/ }).click();
  const lemmaText = await page.locator(".lemmas").innerText();
  step("discovery: refuted lemma repaired, lemmas verified in Lean", /refuted in Lean/.test(lemmaText) && /repair of L2/.test(lemmaText) && /clean recompile ✓/.test(lemmaText));
  await page.getByText(/Synthesis of the main theorem: succeeded/).waitFor();
  step("discovery: main theorem synthesized from verified lemmas", true);
  await audit("discovery (finished, light)");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForTimeout(300);
  await audit("discovery (finished, dark)");
  await page.screenshot({ path: `${OUT}/discovery-dark.png` });
  await page.emulateMedia({ colorScheme: "light" });
  await page.screenshot({ path: `${OUT}/discovery.png` });
  const rep = await page.request.get(`${BASE}/api/projects/${encodeURIComponent(disc)}/report.md`);
  step("discovery: report has the discovery program", /## Discovery program/.test(await rep.text()));

  await page.goto(`${BASE}/#/settings`);
  await page.getByRole("button", { name: "Close sidebar" }).click();
  await page.getByRole("button", { name: "Open sidebar" }).click();
  step("sidebar collapses and reopens", await page.locator(".side").isVisible());
  await page.setViewportSize({ width: 375, height: 812 });
  for (const path of ["/", "/research", `/p/${encodeURIComponent(pid)}`, `/p/${encodeURIComponent(pid)}/graph`, "/memory"]) {
    await page.goto(`${BASE}/#${path}`);
    await page.waitForTimeout(800);
    const ov = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    step(`no horizontal overflow at 375px: ${path}`, ov <= 0, `${ov}px`);
  }
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Lab memory" }).click();
  await page.getByRole("heading", { name: "Lab memory", level: 1 }).waitFor();
  step("mobile menu opens and navigates", !(await page.locator(".app.menu-open").count()));
  await audit("mobile lab memory");
  await page.goto(`${BASE}/#/p/${encodeURIComponent(pid)}`);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/mobile-lab.png` });
} catch (e) {
  step("flow", false, String(e.message || e).split("\n")[0]);
  await page.screenshot({ path: `${OUT}/error.png` });
}
step("no JS errors in the browser", errors.length === 0, errors.join(" | "));
await browser.close();
const failed = steps.filter((s) => !s.ok);
const fs = await import("node:fs");
fs.writeFileSync(`${OUT}/walkthrough.json`, JSON.stringify({ steps, a11y, passed: failed.length === 0 }, null, 2));
console.log(failed.length ? `FAILED: ${failed.length} steps` : `ALL STEPS PASSED (${steps.length})`);
process.exit(failed.length ? 1 : 0);

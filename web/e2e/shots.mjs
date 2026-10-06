// Visual check: takes screenshots of the given pages at desktop and 375px width.
import { chromium } from "playwright";
import { mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";

const BASE = process.env.QUAERA_URL || "http://127.0.0.1:8765";
const OUT = process.argv[2] || "shots";
mkdirSync(OUT, { recursive: true });
const exe = (() => {
  const dir = `${homedir()}/.cache/ms-playwright`;
  const shell = readdirSync(dir).find((d) => d.startsWith("chromium_headless_shell"));
  return shell ? `${dir}/${shell}/chrome-headless-shell-linux64/chrome-headless-shell` : undefined;
})();
const pages = JSON.parse(process.argv[3] || "[]");
const browser = await chromium.launch({ executablePath: exe });
for (const [name, path] of pages) {
  for (const [w, h, tag] of [[1440, 900, "d"], [375, 812, "m"]]) {
    const p = await browser.newPage({ viewport: { width: w, height: h }, colorScheme: process.env.THEME === "dark" ? "dark" : "light" });
    const errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await p.goto(`${BASE}/#${path}`);
    await p.waitForTimeout(2500);
    const overflow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (process.env.FULL) {   // full page: stretch the window to the content height (fullPage sometimes fails in headless-shell)
      let full = 0;   // until the content has loaded: when the height is stable across two measurements
      for (let i = 0; i < 20; i++) {
        const h2 = await p.evaluate(() => document.documentElement.scrollHeight);
        if (h2 === full && h2 > h) break;
        full = h2; await p.waitForTimeout(500);
      }
      await p.setViewportSize({ width: w, height: Math.min(full, 8000) });
      await p.waitForTimeout(400);
    }
    await p.screenshot({ path: `${OUT}/${name}-${tag}${process.env.THEME === "dark" ? "-dark" : ""}.png` });
    console.log(name, tag, "horizontal overflow:", overflow, errors.length ? "ERRORS: " + errors.join(" | ") : "");
    await p.close();
  }
}
await browser.close();

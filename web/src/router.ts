import { useEffect, useState } from "react";

/** Hash router: the server serves a single index.html; deep links look like `#/p/<id>/graph`. */
export function useRoute(): string[] {
  const read = () => decodeURIComponent(window.location.hash.replace(/^#\/?/, "")).split("/").filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => { setParts(read()); window.scrollTo(0, 0); document.getElementById("main")?.focus({ preventScroll: true }); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export const go = (path: string) => { window.location.hash = path; };

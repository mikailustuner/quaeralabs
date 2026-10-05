import { useEffect, useState } from "react";

/** Hash yönlendirici: sunucu tek bir index.html sunar, derin bağlantılar `#/p/<id>/grafik` biçimindedir. */
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

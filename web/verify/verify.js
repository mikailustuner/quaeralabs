// QuaeraLabs evidence package verifier (improvement plan E3). Runs entirely in the browser: nothing is uploaded.
// It checks what can be checked without a server: the ed25519 signature over the manifest, the AI label, and the
// sha256 of every file. Citations and the Lean proof need the network or Lean: `quaera verify-package FILE --lean`.

const enc = new TextEncoder();
const dec = new TextDecoder();

// --- zip (stored and deflate entries, read through the central directory) ----------------------------------------
async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function readZip(buf) {
  const u8 = new Uint8Array(buf);
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a zip file");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const files = new Map();
  for (let n = 0; n < count; n++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("damaged zip directory");
    const method = dv.getUint16(p + 10, true);
    const size = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true), extraLen = dv.getUint16(p + 30, true), commentLen = dv.getUint16(p + 32, true);
    const local = dv.getUint32(p + 42, true);
    const name = dec.decode(u8.subarray(p + 46, p + 46 + nameLen));
    const start = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true);
    const raw = u8.subarray(start, start + size);
    if (method !== 0 && method !== 8) throw new Error(`unsupported compression in ${name}`);
    files.set(name, method === 0 ? raw : await inflateRaw(raw));
    p += 46 + nameLen + extraLen + commentLen;
  }
  return files;
}

// --- the manifest's canonical form: Python json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":")) ----
export function canonical(v) {
  if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
  if (v && typeof v === "object") {
    return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canonical(v[k])).join(",") + "}";
  }
  return JSON.stringify(v);
}

const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function verifyPackage(buf) {
  const checks = [];
  let files;
  try {
    files = await readZip(buf);
  } catch (e) {
    return [{ check: "package", status: "fail", detail: `not a readable zip file: ${e.message}` }];
  }
  if (!files.has("quaera-manifest.json")) {
    return [{ check: "package", status: "fail", detail: "no quaera-manifest.json: not a QuaeraLabs evidence package" }];
  }
  const manifest = JSON.parse(dec.decode(files.get("quaera-manifest.json")));
  const sig = manifest.signature;
  delete manifest.signature;
  if (!sig) {
    checks.push({ check: "signature", status: "fail", detail: "the manifest is not signed" });
  } else {
    try {
      const key = await crypto.subtle.importKey("raw", b64(sig.publicKey), { name: "Ed25519" }, false, ["verify"]);
      const ok = await crypto.subtle.verify({ name: "Ed25519" }, key, b64(sig.value), enc.encode(canonical(manifest)));
      checks.push({ check: "signature", status: ok ? "ok" : "fail",
                    detail: ok ? `ed25519, key ${sig.publicKey.slice(0, 12)}…` : "the manifest or the AI label was modified" });
    } catch (e) {
      checks.push({ check: "signature", status: "skipped", detail: `this browser cannot check ed25519 signatures (${e.name})` });
    }
  }
  const label = manifest.aiLabel?.text || "";
  checks.push({ check: "AI label", status: label.includes("QuaeraLabs") ? "ok" : "fail", detail: label || "missing" });
  const bad = [];
  for (const f of manifest.files || []) {
    const data = files.get(f.path);
    if (!data) bad.push(`missing: ${f.path}`);
    else if (hex(await crypto.subtle.digest("SHA-256", data)) !== f.sha256) bad.push(`modified: ${f.path}`);
  }
  checks.push({ check: "file hashes", status: bad.length ? "fail" : "ok",
                detail: bad.join("; ") || `${(manifest.files || []).length} files match the signed manifest` });
  checks.push({ check: "citations and Lean proof", status: "skipped",
                detail: "need the network or Lean: quaera verify-package FILE --lean" });
  return checks;
}

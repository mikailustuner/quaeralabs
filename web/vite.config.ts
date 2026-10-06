import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  build: {
    outDir: "dist", sourcemap: false,
    // KaTeX + markdown parser in a separate chunk: the main bundle stays small and the browser caches it separately.
    rollupOptions: { output: { manualChunks: { rich: ["katex", "marked", "marked-katex-extension", "dompurify"] } } },
  },
});

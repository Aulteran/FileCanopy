/// <reference types="vitest/config" />
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// PDF.js loads fonts, CMaps and image decoders at run time. They are copied into the build under
// /pdfjs/ so that nothing is ever fetched from another origin (strict CSP, ADR-019). The scripting
// sandbox (quickjs) is deliberately left out: PDF scripting stays off.
function pdfjsAssets(): Plugin {
  const root = join(
    createRequire(import.meta.url).resolve("pdfjs-dist/package.json"),
    "..",
  );
  const dirs: [string, (name: string) => boolean][] = [
    ["cmaps", () => true],
    ["standard_fonts", () => true],
    ["wasm", (name) => !name.startsWith("quickjs")],
  ];
  return {
    name: "filecanopy-pdfjs-assets",
    generateBundle() {
      for (const [dir, keep] of dirs) {
        for (const name of readdirSync(join(root, dir))) {
          if (!keep(name)) continue;
          this.emitFile({
            type: "asset",
            fileName: `pdfjs/${dir}/${name}`,
            source: readFileSync(join(root, dir, name)),
          });
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), pdfjsAssets()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { include: ["src/**/*.test.ts"] },
});

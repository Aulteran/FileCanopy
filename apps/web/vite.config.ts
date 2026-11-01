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
    ["iccs", () => true],
    ["wasm", (name) => !name.startsWith("quickjs")],
  ];
  return {
    name: "filecanopy-pdfjs-assets",
    generateBundle() {
      // Apache-2.0 section 4: a copy of the licence travels with every redistribution.
      this.emitFile({
        type: "asset",
        fileName: "pdfjs/LICENSE",
        source: readFileSync(join(root, "LICENSE")),
      });
      this.emitFile({
        type: "asset",
        fileName: "THIRD_PARTY_NOTICES.md",
        source: readFileSync(join(root, "..", "..", "THIRD_PARTY_NOTICES.md")),
      });
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

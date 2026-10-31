# web

React 19 + TypeScript + Vite single-page app (M05). Plain CSS, no UI framework; data fetching is a thin `fetch` client in `src/api.ts` (see `docs/adr/ADR-014-web-ui-stack.md`).

```bash
cd apps/web
npm install
npm run dev     # Vite on :5173, proxies /api to the API on 127.0.0.1:8000
npm run check   # typecheck + unit tests
npm run build   # production bundle into dist/ (the Dockerfile does this)
```

In the Compose stack the `web` image builds the bundle and nginx serves it and proxies `/api/`.

M06b adds the full-page first-sign-in setup screen (`src/Setup.tsx`, shown by `App.tsx` before the browser; "Decide later" keeps the window open and the side navigation keeps a "(set up)" marker), the "Unfinished upload" banner (`src/UnfinishedUploads.tsx`; batch ids are remembered in local storage), the suspended-scan banner (`src/ScanBanner.tsx`) and the folder reading and upload helpers (`src/importer.ts`). nginx streams zip uploads and import entries and accepts folder manifests up to 32m (see `nginx.conf`).

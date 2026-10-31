# tests/e2e

Browser workflow tests (Playwright for Python, Chromium) against a real Compose stack, from M05 onward.

```bash
python -m pip install playwright
python -m playwright install chromium
npm run test:e2e
```

They reuse the Compose helpers in `tests/integration/compose` (random credentials, temp library, isolated project name).

# tests/e2e

Browser workflow tests (Playwright for Python, Chromium) against a real Compose stack, from M05 onward.

```bash
python -m pip install playwright
python -m playwright install chromium
npm run test:e2e
```

They reuse the Compose helpers in `tests/integration/compose` (random credentials, temp library, isolated project name).

`test_browser_ui.py` (M05) performs the first-run setup; `test_setup_import_ui.py` (M06b) reuses its accounts and stack, so run them together or in file order. A folder dropped from the desktop cannot be automated: the drop test replaces `webkitGetAsEntry` in the page with an in-page tree, the folder-picker tests use real directories.

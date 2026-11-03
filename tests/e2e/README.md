# tests/e2e

Browser workflow tests (Playwright for Python, Chromium) against a real Compose stack, from M05 onward.

```bash
python -m pip install playwright
python -m playwright install chromium
npm run test:e2e
```

They reuse the Compose helpers in `tests/integration/compose` (random credentials, temp library, isolated project name).

The first-run test in `test_browser_ui.py` uses a dedicated fresh stack. Other modules use the session stack and the `world` fixture, which creates its own accounts; they can run without the first-run test. Some file-operation cases within a module still share state. Compose ignores the operator's `deploy/.env` and inherited FileCanopy/Compose settings, using random test credentials and temporary storage. A folder dropped from the desktop cannot be automated: the drop test replaces `webkitGetAsEntry` in the page with an in-page tree, the folder-picker tests use real directories.

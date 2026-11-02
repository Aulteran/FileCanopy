# ai

Optional capability layer (M15): provider adapters, routing, the privacy gate, sealed keys and
budgets. Must be replaceable and must never block core CRUD.

- `gateway.py` — the only way to send document text anywhere: `AiGateway.generate`.
- `routing.py` — one provider or none per request; `clear` builds the only accepted input type.
- `providers.py` — Anthropic, OpenAI, Gemini (fixed hosts), Ollama, OpenAI-compatible (local).
- `budget.py` — atomic reserve then settle, monthly caps in micro-dollars.
- `secrets.py` — AES-GCM sealing with a key from `FILECANOPY_SECRETS_KEY`.

Guide: `docs/guides/ai.md`. Design: `docs/milestones/M15-design-and-plan.md`.

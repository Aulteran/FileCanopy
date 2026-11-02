# ai

Optional capability layer (M15): provider adapters, routing, the privacy gate, sealed keys and
budgets. Must be replaceable and must never block core CRUD.

- `gateway.py` — the only way to send document text anywhere: `AiGateway.generate`.
- `routing.py` — one provider or none per request; `clear` builds the only accepted input type.
- `providers.py` — Anthropic, OpenAI, Gemini (fixed hosts), Ollama, OpenAI-compatible (local).
- `budget.py` — atomic reserve then settle, monthly caps in micro-dollars.
- `secrets.py` — AES-GCM sealing with a key from `FILECANOPY_SECRETS_KEY`.
- `organize.py` — M16: typed organization proposals, the deterministic validator, the review queue
  and the only auto-actions (rename/move through `fileops.relocate`, never ACLs, labels or delete).

Guides: `docs/guides/ai.md`, `docs/guides/ai-organize.md`. Design: `docs/milestones/M15-design-and-plan.md`,
`docs/milestones/M16-design-and-plan.md`.

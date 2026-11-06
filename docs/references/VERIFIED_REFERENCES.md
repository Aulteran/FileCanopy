# Verified External References (as checked for 2026-10-30 planning)

These links inform recommended implementations, not endorsements or guarantees. Validate releases and licenses again when actually implementing.

- **Anthropic model lineup and current model IDs:** https://platform.claude.com/docs/en/models/overview — includes Haiku 5.5, Sonnet 5.5, Opus 5.5.
- **Claude Code model selection:** https://support.claude.com/en/articles/11940350-claude-code-model-configuration — `/model` support and names; model access still depends on account.
- **llama.cpp embedding documentation:** https://github.com/ggml-org/llama.cpp/blob/master/examples/embedding/README.md — GGUF embedding generation.
- **llama.cpp server:** https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md — model serving and endpoints.
- **EmbeddingGemma 2 official model card:** https://github.com/google-gemma/docs/blob/main/docs/models/embeddinggemma/model-card-v2.md — modalities/context/parameter dimensions.
- **MCP specification:** https://blog.modelcontextprotocol.io/posts/2026-07-28/ — recent protocol and authorization changes.
- **OWASP File Upload Cheat Sheet:** https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html — validation, parser threats, archive risk, storage.

**Cost disclaimer:** AI pricing, retention terms, free tiers, feature support, and model licensing can change. Never lock business logic to a quoted per-token price in this handoff; fetch current provider rates for optional user-visible cost estimates during implementation. A user's budget cap is app-side metering and cannot guarantee the provider's external charges exactly.

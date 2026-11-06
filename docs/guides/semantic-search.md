# Meaning-based (semantic) search

Optional. Ordinary search (file names and the text inside files) always works. With a model server
on your own network, search can also find files whose **meaning** is close to your words ("vacation"
finds a note about a holiday trip). Design and threat model: `docs/milestones/M19-design-and-plan.md`.

Nothing is sent to a cloud service, ever: the address must be on your own network, and there is no
cloud option for this feature. Confidential files are never used.

## What you need

1. The database must have pgvector. The bundled Compose stack builds PostgreSQL with it
   (`deploy/postgres/Dockerfile`, the same Alpine base as before, so existing data keeps working). If
   you use your own PostgreSQL, install the `vector` extension (the migration creates it when it
   can; otherwise the screen says pgvector is missing and ordinary search is unaffected).
2. A local embedding server. FileCanopy does not download or start a model for you.

### llama.cpp (CPU only)

Any text-embedding GGUF model works, for example EmbeddingGemma. Example (a command, not a service
shipped here, and not tested in CI):

```bash
llama-server -m embeddinggemma.gguf --embeddings --port 8081 --host 0.0.0.0 --n-gpu-layers 0 --threads 4 --parallel 1
```

`--n-gpu-layers 0` keeps it off the GPU (for example when the GPU belongs to Jellyfin); a small
`--threads` value protects the rest of the machine. FileCanopy also sends one request at a time and
pauses 200 ms after each (`FILECANOPY_SEMANTIC_PAUSE_MS`). Measure on your own hardware before
relying on it; no speed claim is made here.

Choose the type "llama.cpp or other OpenAI-compatible". For Ollama choose "Ollama" and use a model
you have pulled (`ollama pull <embedding model>`).

## Turning it on (administrators)

Account menu > AI > Meaning-based search:

1. Enter the type, the address (for example `http://192.168.1.20:8081`; a Docker service name works
   too) and the model name. Some models want text before passages and queries (a "task prefix");
   the model's card says which. Save.
2. **Check connection.** FileCanopy sends two fixed test sentences twice and verifies that the
   answer is a stable, finite, non-zero vector of a supported size (8 to 2000) and that different
   sentences give different vectors. Only after this passes can you switch it on.
3. **Switch on.** Files are embedded in the background; the screen shows how many are ready.

A changed address, model or prefix needs a new check and switches search off until you do it. When
the new check passes, the old vectors are deleted (another model's vectors cannot be compared) and
rebuilt in the background. Ordinary search is not affected at any point. If the server stops
answering, or starts answering with another vector size, meaning-based search switches itself off
and comes back by itself once the same model answers again.

The address must resolve only to loopback or private addresses (`FILECANOPY_AI_LOCAL_HOSTS` can
narrow it to named hosts), the same rule as local AI providers.

## Who is included

- Normal files: yes. Private files: only if their owner allowed local processing (Preview
  settings). Confidential files: never. The shared folder is handled like the shared folder in text
  search.
- Each person can choose **Do not use my files for meaning-based search** (same dialog). Their
  vectors are deleted immediately.
- The administrator role gives no access to anyone's files here either.

What the embedding server sees: the text of eligible files in chunks, and each query. Keep it on a
machine you trust.

## Using it

The search box works as before. When meaning-based search is on, results are a mix of keyword and
meaning matches, and a result found by meaning is tagged "Similar meaning". If the model server is
down, search simply returns keyword results.

API: `GET /api/v1/search?q=...&mode=auto|fulltext|semantic|hybrid` (the response says which mode
ran).

## Settings (environment, optional)

`FILECANOPY_SEMANTIC_PAUSE_MS` (200), `FILECANOPY_SEMANTIC_MIN_SCORE` (0.3: the lowest similarity
shown; raise it for fewer, closer matches), `FILECANOPY_SEMANTIC_CHUNK_CHARS`,
`FILECANOPY_SEMANTIC_MAX_CHUNKS`, `FILECANOPY_SEMANTIC_EMBED_BATCH` and
`FILECANOPY_SEMANTIC_TIMEOUT_SECONDS` (see `apps/api/app/config.py`). Only the first two and
`FILECANOPY_AI_LOCAL_HOSTS` are passed through `deploy/compose.yaml`; add the others to the
`api` service environment if you need them.

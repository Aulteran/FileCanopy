# deploy

Docker Compose files and the optional Docker Stack variant. Compose is primary. Stack support is not claimed until it is integration-tested (M22).

- `docker-compose.yml`: core stack (`postgres`, `migrate`, `api`, `web`) plus the optional `workers` profile (`redis`, `worker`). No AI services.
- `.env.example`: copy to `.env` (git-ignored) and set `POSTGRES_PASSWORD`.

Design and rationale: `docs/adr/ADR-003-compose-layout-and-mounts.md`. Run instructions: root `README.md`.

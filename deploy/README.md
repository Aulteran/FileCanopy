# deploy

Docker Compose files and the optional Docker Stack variant. Compose is primary. Stack support is not claimed until it is integration-tested (M22).

- `docker-compose.yml`: core stack (`postgres`, `migrate`, `api`, `web`) plus the optional `workers` profile (`redis`, `worker`). No AI services.
- `docker-compose.backup.yml`: optional override that mounts a separate backup directory at `/backups` (M06c, ADR-015). Add it with a second `-f`; without it the feature stays off.
- `docker-compose.proxy.yml`: optional override that joins your reverse proxy's Docker network (`FILECANOPY_PROXY_NETWORK`) so a proxy container can reach the app at `http://web:8080` (M07c, ADR-018).
- `proxy/`: tested reverse proxy setups (Nginx Proxy Manager, nginx, Caddy, Traefik, Cloudflare Tunnel). Start with `proxy/README.md`.
- `.env.example`: copy to `.env` (git-ignored) and set `POSTGRES_PASSWORD`.

Design and rationale: `docs/adr/ADR-003-compose-layout-and-mounts.md`. Run instructions: root `README.md`.

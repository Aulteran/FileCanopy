# deploy

Everything needed to run FileCanopy with Docker. Start with the root [`README.md`](../README.md) and [`docs/guides/production.md`](../docs/guides/production.md).

- `compose.yaml`: the whole stack. Core (`init`, `postgres`, `migrate`, `api`, `web`) plus the profile-gated `ocr`, `converter` and `workers`. Published images; no build, no password to set.
- `.env.example`: the optional six-line `.env`. `.env.advanced.example`: every other setting, all commented out.
- `optional/`: two opt-in files added with a second `-f`: `compose.backup.yaml` (in-app backups) and `compose.proxy.yaml` (join your proxy's Docker network).
- `scripts/`: `backup.sh` and `restore.sh`, whole-install disaster recovery.
- `proxy/`: tested reverse proxy setups (Nginx Proxy Manager, nginx, Caddy, Traefik, Cloudflare Tunnel).
- `postgres/`: PostgreSQL 17 plus pgvector, built into the `-postgres` image.
- `contrib/`: not for normal installs. `build.yaml` builds the images from a checkout; `swarm/` is the single-node Docker Swarm variant.

Defaults for every setting live in `apps/api/app/config.py`; `.env` only overrides them. Design and rationale: `docs/adr/ADR-003-compose-layout-and-mounts.md`.

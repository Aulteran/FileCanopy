# FileCanopy on Docker Swarm (Stack variant)

Docker Compose is the primary way to run FileCanopy and the better tested one. Use this only if you already run Docker Swarm. The stack file is [`docker-stack.yml`](docker-stack.yml).

**What was tested:** CI initialises a single-node swarm, deploys this file unchanged with `docker stack deploy`, waits for all three services to converge, checks the app through the overlay network, confirms the services run as the configured user with a read-only root filesystem and with no published ports, and checks the database and your files survive a service restart and a full stack remove and redeploy (`tests/integration/compose/test_swarm_stack.py`). **What was not tested:** more than one node, rolling upgrades across nodes, and pulling the images from a registry (none is published yet; CI tags the locally built images with the release names).

## How it differs from Compose

- **Published images only**, no `build:`. `FILECANOPY_VERSION` is required.
- **No startup ordering** in `docker stack deploy`, so the API runs its own (idempotent, lock-serialised) migrations before it serves, and every service restarts until the database is ready. The first minute can show a few restarts. That is normal.
- **The database password is a Swarm secret**, not an environment variable.
- **One replica of each service**, pinned to the node you label. State lives in a database volume and two folders on that node. Without the pin, a reschedule could start an empty database on another machine, which looks like data loss.
- **Nothing is published to the network.** The web service joins the attachable overlay network `<stack>_edge`; your TLS reverse proxy joins it and proxies to `http://web:8080`.
- The optional converter, OCR and worker profiles of the Compose file are not part of the stack file.

## Deploy

On the manager node, from the repository root:

```bash
# 1. Mark the node that holds the data, and create the two folders there (absolute paths).
docker node update --label-add filecanopy.data=true "$(hostname)"
mkdir -p /srv/filecanopy/library /srv/filecanopy/app-data
chown 10001:10001 /srv/filecanopy/library /srv/filecanopy/app-data   # or your FILECANOPY_UID:GID

# 2. The database password, as a secret (never stored in a file or in the stack spec).
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | docker secret create filecanopy_pg_password -

# 3. Settings for the interpolated variables. docker stack deploy does not read .env,
#    so export it first.
cat > deploy/contrib/swarm/stack.env <<'ENV'
FILECANOPY_VERSION=1.0.0
FILECANOPY_IMAGE_PREFIX=ghcr.io/aulteran/filecanopy
FILECANOPY_LIBRARY_PATH=/srv/filecanopy/library
FILECANOPY_APP_DATA_PATH=/srv/filecanopy/app-data
FILECANOPY_PUBLIC_URL=https://files.example.org
ENV
set -a; . deploy/contrib/swarm/stack.env; set +a

# 4. Deploy.
docker stack deploy --resolve-image never -c deploy/contrib/swarm/docker-stack.yml filecanopy
docker service ls
```

`stack.env` holds no secrets, but keep it out of version control anyway. Replace `1.0.0` with the release you run and point `FILECANOPY_IMAGE_PREFIX` at wherever your images are (a local build tagged `<prefix>-api:<version>`, `-web`, `-postgres`, works).

The one-time setup token is printed once in the API log: `docker service logs filecanopy_api 2>&1 | grep "SETUP TOKEN"`.

## Reverse proxy

Run your proxy as a service on the `filecanopy_edge` network and point it at `http://web:8080`:

```yaml
networks:
  filecanopy_edge:
    external: true
```

Set `FILECANOPY_PUBLIC_URL`, and `FILECANOPY_TRUSTED_PROXIES` to the proxy's address as `web` sees it, as described in [`deploy/proxy/README.md`](../../proxy/README.md). Do not publish port 8080 directly: Swarm's routing mesh would open it on every node's public interface.

## Upgrade, backup and restore

- **Upgrade:** change `FILECANOPY_VERSION`, export it, and run `docker stack deploy` again. The API applies new migrations when its new task starts. Take a backup first. If an upgrade fails, Swarm's automatic rollback only restores the old image; it cannot undo database migrations that already ran. Restore the backup into the old version instead (and remove the stack first).
- **Backup and restore:** the scripts in `deploy/scripts/` drive Docker Compose. For a stack, use the same steps by hand: dump the database from the postgres task (`docker exec <task> pg_dump -U filecanopy -d filecanopy --format=custom > db.dump`), archive `library/` and `app-data/` with `tar`, and restore into an empty stack in the reverse order. Stop the `api` and `web` services (`docker service scale filecanopy_api=0 filecanopy_web=0`) while you copy. The restore drill itself is tested with Compose only.

# Running FileCanopy in production

This guide covers what you need to run FileCanopy for real: install from a pinned release, upgrade, back up, restore, put it behind HTTPS, and (if you use Docker Swarm) the Stack variant. Everything here runs without any AI service.

FileCanopy has no public release yet and no published images. Until there is one, "a release" means a version you build yourself from this repository (`docker compose build`) and tag as shown below. The commands do not change once images are published.

## 1. Install

Two ways, same layout, same settings in `deploy/.env`:

| | From source (today) | From published images |
|---|---|---|
| Command | `docker compose -f deploy/docker-compose.yml up -d --build --wait` | `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.release.yml up -d --wait` |
| Needs | the repository | only `deploy/` and `FILECANOPY_VERSION` |
| Images | built on your machine | `${FILECANOPY_IMAGE_PREFIX}-{postgres,api,web}:${FILECANOPY_VERSION}` |

The release override needs Docker Compose 2.24 or newer. `FILECANOPY_VERSION` has no default, on purpose: you choose the release, and there is no floating `latest`. Every base image inside the images is pinned by digest, so two builds of one release start from the same bytes.

Fresh install checklist:

1. `cp deploy/.env.example deploy/.env` and set `POSTGRES_PASSWORD` (and `FILECANOPY_VERSION` for published images).
2. Create `library/` and `app-data/` as two separate folders. On Linux make them owned by the user in `FILECANOPY_UID`/`FILECANOPY_GID` (default 10001).
3. Start the stack, then create the first administrator from the one-time setup token in the API log (see the README). Nothing else is needed: no AI, no mail, no workers.

## 2. Containers run without privileges

Every container runs as a non-root user with a read-only root filesystem, no Linux capabilities and no privilege escalation. Only the two folders you mount are writable. The database container starts as root only for its own entrypoint and then runs PostgreSQL as the `postgres` user. Nothing publishes a port except the web container, and that is bound to `127.0.0.1`. The compose tests check all of this on every pull request.

## 3. Upgrade

1. Take a backup (section 4) and keep it until you are happy with the new version.
2. Change `FILECANOPY_VERSION` (published images) or `git pull` (source), then run the same `up -d --wait` command as before.
3. The `migrate` service runs first, applies any new database migrations (one at a time, under a database lock, so a second start cannot collide), and only then do the API and web start. Your files are not touched by an upgrade, and the test suite checks that an older database comes forward with all accounts, permissions and files intact.

There is no automatic downgrade. If an upgrade goes wrong, restore the backup you took in step 1 into the old version (section 5).

A restart (`docker compose restart`, a reboot, a Docker update) needs nothing: data lives in `library/`, `app-data/` and the database volume, not in the containers.

## 4. Back up

Two layers, and you want both:

- **In-app backups** (the Backups page, optional): encrypted, per-folder archives of the library on a separate disk, with a history you can restore single folders from. They do not include the database or the app's own secrets.
- **Disaster-recovery backup** (this section): everything needed to rebuild the whole install on a new machine.

```bash
deploy/scripts/backup.sh /mnt/other-disk/filecanopy-backups
```

It writes one new folder holding the database dump, `app-data/` and `library/`, plus checksums. By default it stops the web and API for the copy (a minute for a small library, longer for a big one) and starts them again afterwards, even if the backup fails, so the three parts describe the same moment. Options:

- `--skip-library` when the library already has its own backup (snapshots, rsync, a NAS). Then restore the library the way you back it up.
- `--no-stop` copies a running system. The parts can then disagree slightly (a file added between the dump and the copy); use it only when downtime is not possible.

Run it as the user that owns `library/` and `app-data/` (or root). It needs GNU `tar`; on Windows run it inside WSL.

**Keep the result private.** It contains everyone's files, including Private and Confidential ones, password hashes, sealed provider keys and the secret kept in `app-data/`. Put it on another disk, and encrypt that disk or the copy if it leaves your hands. A backup on the same disk as the library is not a backup. Schedule it with cron or a systemd timer and prune old folders yourself.

## 5. Restore

A restore goes into an **empty** install and never overwrites anything.

```bash
# on the new (or wiped) machine, with deploy/.env in place
deploy/scripts/restore.sh /mnt/other-disk/filecanopy-backups/filecanopy-20261102T120000Z
```

The script checks the checksums, starts only the database, loads the dump, unpacks `app-data/` and `library/`, then starts the whole stack (running any migrations if the backup is older than the code). It stops with a message instead of continuing when the backup is damaged, when `library/` or `app-data/` is not empty, or when the database already has tables. To replace a broken install on the same machine, move the old folders aside and run `docker compose down -v` first, which deletes the old database volume; then run the restore.

If a restore stops partway, the script prints how to clean up and start over (remove the containers and database volume, empty the two folders, run it again); it refuses to continue over a half-filled install on purpose.

A backup made by a **newer** release cannot be restored into an older one: the migration step refuses. Run the same or a newer version.

After a restore: sign in, open a few files, and check the Activity log. The first scan afterwards may take a while on a large library. The restore drill (a destroyed install rebuilt from a backup, with accounts, the database and the files compared byte for byte) runs in the repository's tests.

## 6. HTTPS and a reverse proxy

The web container listens on `127.0.0.1` only. For access from other devices, put a TLS proxy in front of it. Ready-to-use, tested configurations for Nginx Proxy Manager, nginx, Caddy, Traefik and Cloudflare Tunnel are in [`deploy/proxy/`](../../deploy/proxy/README.md): they show the TLS setup, the large-upload settings and the three settings (`FILECANOPY_PUBLIC_URL`, `FILECANOPY_TRUSTED_PROXIES`, `FILECANOPY_HSTS`) that tie the app to the proxy. Keep `FILECANOPY_COOKIE_SECURE=true`. Turn HSTS on only once HTTPS works for good.

## 7. Docker Swarm (the Stack variant)

Compose is the primary, best-tested way to run FileCanopy. If you already run Swarm, [`deploy/stack/`](../../deploy/stack/README.md) has a stack file that uses published images, a Swarm secret for the database password, and one replica of each service pinned to one labelled node. It was tested by deploying it on a real single-node swarm in CI. It was **not** tested on a multi-node swarm; the data node constraint exists so a reschedule cannot start an empty database elsewhere, but treat multi-node as unverified.

## 8. Known limits

- No signed images, SBOM or reproducible-build claim yet; those belong to the release milestone (M24).
- The image names in the release override point to the project's planned registry path. Nothing is published there yet, so use `FILECANOPY_IMAGE_PREFIX` with images you built.
- The disaster-recovery backup is a full copy each time (no incremental runs). The in-app backups are incremental.
- Backup and restore are POSIX shell scripts for Linux and macOS hosts (and WSL); there is no web screen for them.

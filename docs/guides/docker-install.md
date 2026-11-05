# Standalone Docker installation (beta preparation)

No public images or release downloads exist yet. These instructions describe the prepared
bundle; anonymous registry pulls and owner beta acceptance remain release gates.
Docker is the first-beta target. Native Proxmox LXC is deferred until later.

## Install

Use Docker Engine with Linux containers and Docker Compose v2.24 or newer. The preliminary
candidate is tested on Docker Desktop Windows with Linux amd64 containers; this does not
establish ARM64 or other host support. No AI, mail, worker, Git checkout, local image build
or registry login is needed for the basic install once images are public.

Download `compose.yaml` and `.env.example` from the **same release** into a new directory.
For whole-install recovery also download `scripts/backup.sh`, `scripts/restore.sh` and
`scripts/lib.sh`, preserving that layout (or extract the complete release bundle).
Use the release's published checksums to verify downloads. Never download a moving main-branch
file to upgrade an existing installation.

```sh
cp .env.example .env
chmod 600 .env
mkdir library app-data
# Linux: use the configured UID/GID; no recursive permission changes to an existing library.
sudo chown 10001:10001 library app-data
```

Edit `.env`: set `FILECANOPY_VERSION` to the explicit published version and `POSTGRES_PASSWORD`
to a new URL-safe random secret (for example `openssl rand -hex 32`). Set a unique
`COMPOSE_PROJECT_NAME` if another FileCanopy installation exists. Keep library and app-data
separate, outside one another. The API must own both directories; app-data holds private
state and secrets. Never use real documents for candidate validation.

```sh
docker compose up -d
docker compose ps -a
docker compose logs api
```

Wait for healthy API/web and successful `migrate` completion. Open `http://localhost:8080`,
use the one-time FIRST-RUN SETUP TOKEN from the API log and create the administrator.
There are no default credentials or open registration. Leave AI settings off.
Only the web port is published, on loopback. For remote access configure a TLS reverse proxy
to that port, `FILECANOPY_PUBLIC_URL` and the exact proxy address in `FILECANOPY_TRUSTED_PROXIES`.
Keep Secure cookies enabled; enable HSTS only after HTTPS works.

Optional converter/OCR profiles and socket URLs are shown in `.env.example`; they keep
network isolation and resource limits. The optional workers profile adds a heartbeat tier.
Profiles are not AI providers. Additional environment options are listed in `compose.yaml`.

## Restart and upgrade

`docker compose restart` preserves the library, app-data and project database volume.
Back up before upgrading. Save the old bundle, version, `.env` and exact image digests privately.
Download and verify the new matching bundle, review its release notes, change the explicit
version in `.env`, then run:

```sh
docker compose pull
docker compose up -d --wait
```

Migrations finish before API startup. Compare document bytes and account permissions after
upgrading. There is no automatic downgrade: restore the pre-upgrade backup into an empty
installation running its original version. Never delete volumes to fix an upgrade in place.

## Whole-install backup and empty-install recovery

These scripts require POSIX sh, GNU tar and sha256sum (or shasum); use WSL on Windows.
Run as the storage owner or root, from the downloaded bundle directory. Backups contain
everyone's plaintext files, password hashes and app-data secrets. Keep them private,
encrypted when stored elsewhere, on a separate disk. Save `.env` separately and privately;
the disaster backup does not include host configuration or externally supplied secrets.

```sh
sh scripts/backup.sh /mnt/other-disk/backups
# On a NEW, EMPTY installation, with the original bundle and private .env restored:
sh scripts/restore.sh /mnt/other-disk/backups/filecanopy-TIMESTAMP
```

Backup stops API/web for a consistent full copy and restarts them even on failure.
Restore validates checksums and refuses occupied folders or database tables. Do not start
the full app before restore: let the script start the empty database first. Use a new
project name for a recovery drill; never reuse a live installation's database volume.
Check account access, shares, Confidential locks and file bytes after recovery.
Keep the original backup if a restore stops partway and follow the script's cleanup advice
only for the disposable, partially restored project.

In-app encrypted backups are a separate facility and omit database/app secrets. M25 per-root
keys are planned and unimplemented; owner self-restore is not provided. The host administrator
can inspect plaintext data; Confidential is app reauthentication, not disk encryption.

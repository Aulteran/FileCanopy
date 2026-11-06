# Standalone Docker installation

Docker is the first-beta target; native Proxmox LXC is deferred. You need Docker Engine with Linux
containers and Docker Compose v2.24 or newer. No AI, mail, worker, Git checkout or image build is
needed. The images are public on GitHub Container Registry (`ghcr.io/aulteran/filecanopy-*`).

## Install

```sh
mkdir filecanopy && cd filecanopy
curl -fsSLO https://raw.githubusercontent.com/Aulteran/FileCanopy/HEAD/deploy/compose.yaml
docker compose up -d --wait
docker compose logs api | grep "SETUP TOKEN"
```

There is no password to set. On first start the `init` container generates the database password
into the `secrets` volume and gives the app user the top-level `library/` and `app-data/` folders
next to `compose.yaml` (not recursive: nothing inside your library is touched). Keep the two
folders separate and outside one another. Never use real documents for candidate validation.

Open `http://127.0.0.1:8080`, enter the one-time setup token from the log and create the
administrator. There are no default credentials or open registration. Only the web port is
published, on loopback. For remote access configure a TLS reverse proxy, `FILECANOPY_PUBLIC_URL`
and the exact proxy address in `FILECANOPY_TRUSTED_PROXIES`; keep Secure cookies on, and enable HSTS
only after HTTPS works.

To change anything, save [`.env.example`](../../deploy/.env.example) as `.env` beside `compose.yaml`;
every other option is in [`.env.advanced.example`](../../deploy/.env.advanced.example). A release
bundle contains the same files: `compose.yaml`, `compose.backup.yaml`, `compose.proxy.yaml`, both env
examples and `scripts/`, with `SHA256SUMS`. Optional containers (OCR, Office previews, workers) are
profiles in `compose.yaml`: set `COMPOSE_PROFILES=ocr` (and the matching URL) in `.env`. They keep
network isolation and resource limits and are not AI providers.

## Restart and upgrade

`docker compose restart` preserves the library, app-data and project database volume.
Back up before upgrading. Save `compose.yaml`, `.env` and the exact image digests privately.
Review the release notes and, for a pinned install, change `FILECANOPY_VERSION` in `.env`, then run:

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
the disaster backup does not include host configuration or externally supplied secrets. The database
password is regenerated for a new install, so it is not needed to restore.

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

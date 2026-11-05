# FileCanopy

FileCanopy is a self-hosted file manager for your documents. You run it yourself with Docker, and it works on ordinary folders on your disk. Your files stay as normal files in a folder you choose, so you can always open them with any other program. The database only stores accounts, permissions and history. It is not a replacement for your files.

> **Status: preparing the first beta.** FileCanopy is the owner-approved name, but external trademark/namespace clearance is still pending. No public release or published Docker images exist. Build from source and keep independent backups. [Release evidence and remaining gates](docs/release/README.md).

## Key features

- **Your files stay normal files.** The library is a plain folder tree on your disk. Nothing is converted or hidden.
- **Accounts and roles.** Administrator and user roles, no open sign-up, local or optional OpenID Connect sign-in. Invitations and password resets use single-use links, delivered through optional SMTP or copied by an administrator.
- **Personal and shared folders.** Each user gets a personal folder. There is also one shared folder, where folders can be given viewer or editor access.
- **Everyday file work.** Browse folders, upload files and whole folders, download, create folders, rename, move and copy. Keyboard navigation and a phone-friendly layout are included.
- **Import what you already have.** During first setup you can import an existing folder or a zip file.
- **Trash, undo and history.** Deleted items go to a trash you can restore from, and recent actions can be undone. An activity log records actions and downloads.
- **Optional backups.** Encrypted backups of the library can be written to a separate disk you choose. They are off by default.
- **Document tools.** Sharing, Private/Confidential folders, previews, local text search, optional OCR and verified email intake are implemented. PDF split proposals preserve the source; collections reference files without moving them.
- **No AI required.** Basic file work runs without AI. Optional providers, validated organisation proposals, local semantic search and single-turn document chat are implemented. MCP is optional, read-only and off by default. See [feature guides](docs/guides/ai.md).

The prepared [standalone Docker bundle](docs/guides/docker-install.md) will let users install with two downloads and `docker compose up -d` once images are published and anonymous pulls pass acceptance. Today the source route below remains available.

## Quick start (Docker Compose)

You need Docker with Docker Compose v2 (the release override requires 2.24+). The project has been tested on Docker Desktop for Windows and on Linux in CI; this is test history, not a promise for every host or architecture. Docker is the first-beta priority (owner decision 2026-11-04). Native Proxmox helper-script LXC is deferred until later, with [packaging and real-host validation pending](docs/release/proxmox-lxc.md).

```bash
git clone https://github.com/Aulteran/FileCanopy.git
cd FileCanopy
cp deploy/.env.example deploy/.env
```

Open `deploy/.env` in a text editor and set `POSTGRES_PASSWORD` to a long random value. You can generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then create the data folders and start the stack:

```bash
mkdir -p library app-data
docker compose -f deploy/docker-compose.yml up -d --build --wait
curl http://127.0.0.1:8080/api/v1/health
```

The health check should report `ok`. Then open **http://127.0.0.1:8080** in your browser.

## First-run setup

1. **Enter the setup token.** The first visit asks for a one-time setup token. If you did not set `FILECANOPY_SETUP_TOKEN` in `deploy/.env`, the API prints a token to its log. Show it with:

   ```bash
   docker compose -f deploy/docker-compose.yml logs api | grep "SETUP TOKEN"
   ```

   If you set your own token, use that value instead. A restart can issue a new generated token, so check the log again if the old one stops working.
2. **Create the administrator account.** This is the first account, and it is the only one that can be created without an invitation.
3. **Optionally import existing files.** While the setup window is open, you can import an existing folder or zip into the shared folder or your personal folder. The window closes once you add content by hand, finish setup, or skip it.

## Configuration

All settings go in `deploy/.env`. The file `deploy/.env.example` lists every option with a comment. The most useful ones:

| Setting | Default | What it does |
|---|---|---|
| `POSTGRES_PASSWORD` | none (required) | Password for the database. Set it before the first start. |
| `FILECANOPY_LIBRARY_PATH` | `../library` | Folder that holds your files. Relative paths are relative to `deploy/`. |
| `FILECANOPY_APP_DATA_PATH` | `../app-data` | App-private working data. Must not be inside the library or the reverse. |
| `FILECANOPY_HTTP_PORT` | `8080` | Port on your own computer where the web app is published. |
| `FILECANOPY_SETUP_TOKEN` | empty (generated) | Your own setup token, at least 32 characters. |
| `FILECANOPY_COOKIE_SECURE` | `true` | Sign-in cookies are sent over HTTPS only. Browsers exempt `http://localhost`. |
| `FILECANOPY_MAX_UPLOAD_BYTES` | 10 GiB | Largest single upload. |
| `FILECANOPY_UID` / `FILECANOPY_GID` | `10001` | On Linux, the user the app runs as. Set these to the owner of `library/` and `app-data/` (see `id -u` and `id -g`). |

The `.env.example` file also covers import limits, how long import reports are kept, and backups.

### Remote access

The web app listens only on `127.0.0.1`, so it is reachable only from the same machine. To use it from other devices, put a TLS reverse proxy in front of it and keep `FILECANOPY_COOKIE_SECURE=true`. Do not expose the plain HTTP port to a network.

Working, tested setups for **Nginx Proxy Manager**, nginx, Caddy, Traefik and Cloudflare Tunnel are in [`deploy/proxy/`](deploy/proxy/README.md). In short, set these in `deploy/.env`:

| Setting | Meaning |
|---|---|
| `FILECANOPY_PUBLIC_URL` | The address you type in the browser, for example `https://files.example.org`. |
| `FILECANOPY_TRUSTED_PROXIES` | The address of your reverse proxy as the app sees it (its own single address, not a whole network). Empty (default) trusts nobody, and then all users share the proxy's address for sign-in limits and the audit log. |
| `FILECANOPY_HSTS` | `true` sends HSTS. Off by default. |

If your proxy runs in Docker, add `-f deploy/docker-compose.proxy.yml` and set `FILECANOPY_PROXY_NETWORK` so the proxy can reach the app at `http://web:8080`. Uploads can be many GiB: the proxy must allow large bodies and long timeouts (the example files do). Cloudflare limits request bodies to 100 MB on the Free and Pro plans, which also applies to Cloudflare Tunnel; see the proxy guide.

## Where your data lives

- **`library/`** is your files. Shared files are in the shared folder, and each person's files are in their own folder. Personal folders are named by user ID, not by name. Trashed items wait in a hidden `.filecanopy-trash/` folder inside `library/`. You can use this folder with any file tool, and it is never converted or locked.
- **`app-data/`** holds app-private working data, such as staged uploads during imports. Do not edit it by hand.
- **The database** is a Docker volume named `pgdata`. It holds accounts, permissions, trash records and history. Your files do not depend on it: the project's tests check that documents survive a total database loss.

## Updating

Back up your library first (see [Backups](#backups)). Then:

```bash
git pull
docker compose -f deploy/docker-compose.yml up -d --build --wait
```

Database migrations run automatically on start. Your `deploy/.env` file is ignored by git, so `git pull` does not change it.

For a production setup (published images pinned to a version, whole-install backup and restore, HTTPS, the Docker Swarm variant) read the [production guide](docs/guides/production.md). What the containers log and the rate limits are in the [hardening guide](docs/guides/hardening.md).

## Backups

There are two kinds. The **disaster-recovery backup** copies the database, `app-data/` and `library/` in one go and restores into an empty install: `deploy/scripts/backup.sh <folder>` and `deploy/scripts/restore.sh <backup>` (see the [production guide](docs/guides/production.md#4-back-up)). The **in-app backups** described next are optional, encrypted, incremental copies of the library only.

In-app backups are off by default, and the app does not need a backup disk to run. To turn them on:

1. Set `FILECANOPY_BACKUP_PATH` in `deploy/.env` to a folder on a **separate disk or share**. It must not be inside `library/` or `app-data/`, and a copy on the same disk is not a real backup. The API user (`FILECANOPY_UID`/`FILECANOPY_GID`) must own that folder.
2. Start the stack with the backup override file:

   ```bash
   docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.backup.yml up -d --build --wait
   ```

3. Sign in as an administrator, open **Backups**, and turn backups on. The defaults are a run every 24 hours, with a full backup every 7 runs and smaller changed-only runs in between. Backups are encrypted by default.

Things to know:

- A backup copies everyone's files, including private ones.
- **The recovery key is shown once and never stored.** Without it, encrypted backups cannot be read. Store it somewhere safe.
- **Rotate the recovery key after you give it to someone.** Restoring needs the key, and that one key opens every archive on the backup disk. Give it out only for a restore, then create a new key from the Backups page. Older archives stay readable only with the key they were made with. Separate keys per folder are decided (ADR-016) but not built yet.
- Backups of folders that are disabled, missing or belong to removed accounts are kept and never cleaned up automatically, so they keep using space. There is no screen for purging them yet.
- A restore runs inside the web request, so restoring a very large folder can take a long time.
- Restoring a single file or folder from a backup is planned but not built.

## Stopping

```bash
docker compose -f deploy/docker-compose.yml down
```

This stops the containers and keeps your files and database. To start again, run `docker compose -f deploy/docker-compose.yml up -d --wait`.

Adding `-v` also deletes the database volume. That removes accounts, permissions, trash records and history, but it does not delete the files in `library/`.

## Security and privacy

- **Administrators cannot open personal folders.** The administrator role does not let anyone browse, search or list another person's personal files. The only way in is a grant created by the owner of that folder.
- **Passwords** are stored with Argon2id. Sign-in attempts are rate-limited, sessions are kept on the server, and cookies are HTTP-only.
- **Host access is outside the app's trust model.** Anyone who can read the server's disk can read the files in `library/`, the same as any other folder on that machine.
- **Private and Confidential** labels are implemented and inherit downward. Both block cloud AI; local processing requires opt-in, and Confidential content requires an unlock that expires after five minutes of user inactivity in that folder. This is app access control, not encryption at rest or E2E encryption. Host, hypervisor, storage and backup administrators can still access plaintext files and state.
- **External processing needs care.** Normal files may be sent to configured cloud AI after applicable consent. Raising a privacy label cannot recall a previous transmission. MCP clients can forward the Normal data they receive to another provider. No provider retention or compliance guarantee is made.
- **Backups contain protected data.** Disaster-recovery copies include all files and state. ADR-016 per-root backup keys is accepted but unimplemented; the current recovery key opens all archives made with it. See [known limitations](docs/release/known-issues.md).
- **No external security audit** has been done yet. Security work so far has been internal reviews plus automated tests.

## Project status and roadmap

| Area | Status |
|---|---|
| Repository, Docker stack, database | Done |
| Sign-in, first administrator, invitations, password reset | Done |
| Storage roots, permissions, file operations | Done |
| Browser interface, setup import, folder uploads | Done |
| Change detection for external edits, backups (optional) | Done |
| Trash, undo and activity log | Done (M07) |
| Sharing between people and personal-folder isolation | Done (M08) |
| Privacy levels and Confidential unlock | Done (M09) |
| Previews, OCR and full-text search | Done (M10–M11) |
| Email invitations, SMTP, OpenID Connect sign-in | Done (M12) |
| Provider-only sign-in, Google, Confidential PIN lock | Done (M12b) |
| Email intake for personal and shared folders | Done (M13–M14) |
| PDF splitting, duplicate detection, collections | Done (M17–M18), with documented limits |
| Optional AI (providers, organization proposals, local semantic search, chat) | Done (M15–M20), with documented limits |
| Optional MCP integration | Done (M21), scoped read-only Normal content |
| Full UI redesign build | Done (M21b) |
| Production packaging, single-node Stack example, hardening | Done (M22–M23), with documented follow-ups |
| Public beta readiness | In progress (M24); release gates remain open |

The full plan is in `08_IMPLEMENTATION_PLAN.md` and `MILESTONES.json`. The current state is recorded in `PROJECT_STATUS.md`, and design decisions are in `docs/adr/`.

## For developers

Requirements: Node.js 22 or newer, and Docker for the backend and end-to-end checks.

```bash
npm install
npm run check        # lint, format, unit tests and secret scan
npm run check:api    # Python checks in a pinned container
npm run test:compose # real Compose stack tests
npm run check:web    # web type checks and unit tests
npm run test:e2e     # browser tests against a real stack
npm run check:full   # everything above
```

Continuous integration runs on GitHub Actions. See `.github/workflows/ci.yml`.

## Contributing

Report bugs in [GitHub issues](https://github.com/Aulteran/FileCanopy/issues), using synthetic examples and sanitised diagnostics. Include your commit/image identity, deployment method and reproduction steps. Discuss large changes first. [CONTRIBUTING](docs/release/drafts/CONTRIBUTING.md), [SECURITY](SECURITY.md) and [CHANGELOG](docs/release/drafts/CHANGELOG.md) are prepared as drafts pending owner approval. A private security-reporting route still needs configuration and verification; do not post secrets or vulnerability details in public issues.

## License

FileCanopy declares [GNU Affero General Public License v3.0 only](LICENSE) (`AGPL-3.0-only`), consistently with both package manifests. Third-party components retain their own terms. The [runtime dependency audit](docs/release/dependency-audit.md) records exact versions, sources and unresolved licence/redistribution issues; release clearance is pending their resolution and owner sign-off. No legal advice or blanket compatibility conclusion is implied.

# FileCanopy

**A self-hosted file manager that keeps your files as normal files, in a folder you own.**

[![FileCanopy browsing a folder](site/assets/shots/browser-desktop-light.png)](site/index.html)

*Design preview of the interface.*

FileCanopy runs on your own server with Docker and works on ordinary folders on your disk. You can always open your files with any other program, because nothing is converted, hidden or locked in.

- **Your files stay normal files.** The library is a plain folder tree. The database only keeps accounts, permissions and history.
- **Personal and shared folders.** Everyone gets a private folder, and a shared folder gives others viewer or editor access. Administrators cannot open anyone's personal files.
- **Everyday file work.** Browse, upload whole folders, download, rename, move and copy, from the keyboard or on your phone's browser.
- **Trash, undo and history.** Restore deleted items, undo recent actions and see who did what.
- **Private and Confidential folders.** Confidential folders ask for your password again and lock when you step away.
- **Search, previews and optional extras.** Find files by name or content (OCR for scans is optional), preview PDFs, images and Office documents, and back up to a separate disk with encryption. AI is optional and off by default.

## Install in three steps

You need Docker with Docker Compose v2.24 or newer. Nothing is built on your machine, and there is no password to choose.

1. Download the Compose file:

   ```bash
   mkdir filecanopy && cd filecanopy
   curl -fsSLO https://raw.githubusercontent.com/Aulteran/FileCanopy/HEAD/deploy/compose.yaml
   ```

2. Start it:

   ```bash
   docker compose up -d --wait
   ```

3. Open **http://127.0.0.1:8080**, enter the setup token from `docker compose logs api | grep "SETUP TOKEN"`, and create your administrator account.

Your files are ordinary files in the `library/` folder next to `compose.yaml`.

**[Read the documentation](docs/README.md)** for configuration, remote access, backups and updating.

## What's included

- Accounts, invitations, password reset, optional single sign-on (OpenID Connect, Google)
- Personal and shared folders with viewer and editor access
- Upload, download, rename, move, copy, trash, undo and an activity log
- Import an existing folder or zip during first setup, and detection of changes made outside the app
- Private and Confidential labels with a re-authentication lock
- Previews, full-text search and optional OCR; PDF splitting and collections
- Optional encrypted backups, email intake, AI organisation proposals, semantic search, document chat and a read-only MCP endpoint (all off by default)

## Known limits

- This is a **public beta**. There is no stable release yet and no external security audit. Keep your own backups.
- Docker is the supported way to run it, and the beta images are built for amd64 only. Proxmox LXC is not available yet.
- Anyone who can read the server's disk can read the library, like any other folder. Private and Confidential labels control access in the app; they are not encryption at rest.
- Restoring a single file from an in-app backup is not built yet.
- The screenshots are design previews while the interface is being finished.

See the full [known issues](docs/release/known-issues.md) and [roadmap](docs/roadmap.md).

## Contributing, security and license

Bug reports and ideas are welcome in [GitHub issues](https://github.com/Aulteran/FileCanopy/issues); see the [developer guide](docs/development.md). Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md). FileCanopy is licensed under the [AGPL-3.0](LICENSE).

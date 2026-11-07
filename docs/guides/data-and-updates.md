# Your data, updating and stopping

## Where your data lives

- **`library/`** is your files. Server files are in the shared folder, and each person's files are in their own folder. Personal folders are named by user ID, not by name. Trashed items wait in a hidden `.filecanopy-trash/` folder inside `library/`. You can use this folder with any file tool, and it is never converted or locked.
- **`app-data/`** holds app-private working data, such as staged uploads during imports. Do not edit it by hand.
- **The database** is a Docker volume named `pgdata`; its generated password is in the `secrets` volume (keep both together). It holds accounts, permissions, trash records and history. Your files do not depend on it: the project's tests check that documents survive a total database loss.

## Updating

Back up your library first (see [Backups](backups.md)). Then:

```bash
docker compose pull
docker compose up -d --wait
```

Database migrations run automatically on start. (Building from source: `git pull`, then rerun the build command in the [development guide](../development.md).)

For a production setup (published images pinned to a version, whole-install backup and restore, HTTPS, the Docker Swarm variant) read the [production guide](production.md). What the containers log and the rate limits are in the [hardening guide](hardening.md).

## Stopping

```bash
docker compose down
```

This stops the containers and keeps your files and database. To start again, run `docker compose up -d --wait`.

Adding `-v` also deletes the database volume. That removes accounts, permissions, trash records and history, but it does not delete the files in `library/`.

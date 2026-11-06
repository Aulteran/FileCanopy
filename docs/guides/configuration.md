# Configuration

All settings go in `.env` next to `compose.yaml`, and all are optional. [`deploy/.env.example`](../../deploy/.env.example) has the common ones and [`deploy/.env.advanced.example`](../../deploy/.env.advanced.example) lists every option with a comment. The most useful ones:

| Setting | Default | What it does |
|---|---|---|
| `FILECANOPY_LIBRARY_PATH` | `./library` | Folder that holds your files. Relative paths are relative to `compose.yaml`. |
| `FILECANOPY_APP_DATA_PATH` | `./app-data` | App-private working data. Must not be inside the library or the reverse. |
| `FILECANOPY_HTTP_PORT` | `8080` | Port on your own computer where the web app is published. |
| `FILECANOPY_SETUP_TOKEN` | empty (generated) | Your own setup token, at least 32 characters. |
| `FILECANOPY_COOKIE_SECURE` | `true` | Sign-in cookies are sent over HTTPS only. Browsers exempt `http://localhost`. |
| `FILECANOPY_MAX_UPLOAD_BYTES` | 10 GiB | Largest single upload. |
| `FILECANOPY_UID` / `FILECANOPY_GID` | `10001` | On Linux, the user the app runs as. Set these to the owner of `library/` and `app-data/` (see `id -u` and `id -g`). |

The advanced file also covers import limits, how long import reports are kept, and backups.

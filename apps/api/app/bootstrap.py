"""First-start helper for the Docker Compose stack (the `init` service). Runs as root, once.

1. Creates the database password in the secrets volume if it is not there yet (random, or the
   value of POSTGRES_PASSWORD when an existing install brings its own). Never overwritten.
2. Creates the secrets key (32 random bytes, base64) in the same volume if it is not there yet. The
   API seals stored provider API keys with it; only the API's user can read it. An explicit
   FILECANOPY_SECRETS_KEY in the environment wins and then no file is created. Never overwritten.
3. Gives the user the API runs as (FILECANOPY_UID:FILECANOPY_GID) the top-level library and
   app-data folders. Not recursive: files that are already in a library are never touched.
Safe to run again; a second run changes nothing.
"""

import base64
import os
import secrets
import sys
from pathlib import Path


def ensure_password(directory: Path) -> bool:
    target = directory / "pg_password"
    if target.exists():
        return False
    value = os.environ.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(32)
    temp = directory / ".pg_password.tmp"
    temp.write_text(value, encoding="utf-8")
    temp.chmod(0o444)  # read by postgres and the API; the volume is mounted read-only for them
    temp.replace(target)
    return True


def ensure_secrets_key(directory: Path, uid: int, gid: int) -> bool:
    """Create `secrets_key` once. Skipped when FILECANOPY_SECRETS_KEY is set explicitly."""
    target = directory / "secrets_key"
    if target.exists() or os.environ.get("FILECANOPY_SECRETS_KEY", "").strip():
        return False
    temp = directory / ".secrets_key.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(base64.b64encode(secrets.token_bytes(32)).decode())
    try:
        os.chown(temp, uid, gid)  # readable by the API's user only (the volume is read-only there)
    except OSError:
        temp.chmod(0o444)  # cannot hand it over (unusual file system): fall back to world-readable
    temp.replace(target)
    return True


def hand_over(folder: Path, uid: int, gid: int) -> None:
    try:
        info = folder.stat()
        if (info.st_uid, info.st_gid) != (uid, gid):
            os.chown(folder, uid, gid)
    except OSError as exc:  # e.g. a Windows share: Docker Desktop needs no ownership step
        print(f"note: could not change owner of {folder}: {exc.strerror}", file=sys.stderr)


def main() -> None:
    directory = Path(os.environ.get("FILECANOPY_SECRETS_DIR", "/secrets"))
    uid = int(os.environ.get("FILECANOPY_UID", "10001"))
    gid = int(os.environ.get("FILECANOPY_GID", "10001"))
    created = ensure_password(directory)
    if ensure_secrets_key(directory, uid, gid):
        print("created the secrets key")
    for name in ("/library", "/app-data"):
        hand_over(Path(name), uid, gid)
    print("created the database password" if created else "database password already present")


if __name__ == "__main__":
    main()

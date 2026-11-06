"""First-start helper for the Docker Compose stack (the `init` service). Runs as root, once.

1. Creates the database password in the secrets volume if it is not there yet (random, or the
   value of POSTGRES_PASSWORD when an existing install brings its own). Never overwritten.
2. Gives the user the API runs as (FILECANOPY_UID:FILECANOPY_GID) the top-level library and
   app-data folders. Not recursive: files that are already in a library are never touched.
Safe to run again; a second run changes nothing.
"""

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


def hand_over(folder: Path, uid: int, gid: int) -> None:
    try:
        info = folder.stat()
        if (info.st_uid, info.st_gid) != (uid, gid):
            os.chown(folder, uid, gid)
    except OSError as exc:  # e.g. a Windows share: Docker Desktop needs no ownership step
        print(f"note: could not change owner of {folder}: {exc.strerror}", file=sys.stderr)


def main() -> None:
    created = ensure_password(Path(os.environ.get("FILECANOPY_SECRETS_DIR", "/secrets")))
    uid = int(os.environ.get("FILECANOPY_UID", "10001"))
    gid = int(os.environ.get("FILECANOPY_GID", "10001"))
    for name in ("/library", "/app-data"):
        hand_over(Path(name), uid, gid)
    print("created the database password" if created else "database password already present")


if __name__ == "__main__":
    main()

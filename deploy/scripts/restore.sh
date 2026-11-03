#!/bin/sh
# Restore a backup made by backup.sh into an EMPTY install (M22):
#
#   deploy/scripts/restore.sh [--skip-library] BACKUP_DIR
#
# It refuses to overwrite anything: the database must have no tables, and app-data/ and library/
# must be empty or missing. To replace an install on the same machine, move the old folders away
# and run `docker compose down -v` yourself first (this deletes the old database volume).
# --skip-library restores only the database and app-data, for a library you restore another way.
#
# Steps: verify the checksums, start only the database, load the dump, unpack the folders, then
# start the whole stack. Starting runs the migrations, so a backup from an older release is brought
# up to date; a backup from a NEWER release than the code you run stops with a migration error.
# Run it as the same user as the backup (or root) so file owners come back right.
set -eu
. "$(dirname -- "$0")/lib.sh"

library=1
while [ $# -gt 0 ]; do
  case $1 in
    --skip-library) library=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) break ;;
  esac
  shift
done
[ $# -eq 1 ] || die "usage: restore.sh [--skip-library] BACKUP_DIR"
SRC=$(CDPATH= cd -- "$1" 2>/dev/null && pwd -P) || die "$1 is not a directory"
[ -f "$SRC/SHA256SUMS" ] && [ -f "$SRC/MANIFEST" ] || die "$SRC is not a backup made by backup.sh"

echo "Checking the backup ..."
(cd "$SRC" && sha_check SHA256SUMS >/dev/null) || die "checksum mismatch: the backup is damaged"
grep -qx 'format=1' "$SRC/MANIFEST" || die "unknown backup format"
if [ "$library" = 1 ] && [ ! -f "$SRC/library.tar.gz" ]; then
  die "this backup has no library; use --skip-library"
fi

# An unreadable folder is an error, never "empty".
empty_or_missing() {
  [ -e "$1" ] || return 0
  [ -d "$1" ] || die "$1 is not a folder"
  listing=$(ls -A "$1") || die "cannot read $1"
  [ -z "$listing" ]
}
empty_or_missing "$APP_DATA_DIR" || die "$APP_DATA_DIR is not empty"
[ "$library" = 0 ] || empty_or_missing "$LIBRARY_DIR" || die "$LIBRARY_DIR is not empty"

cleanup_help() {
  [ $? -eq 0 ] && return 0
  cat >&2 <<HELP

The restore did not finish. The database and folders may be partly filled, and restore.sh will
refuse to run again until they are empty. To start over:
  1. docker compose down -v         (removes the containers and THE DATABASE VOLUME of this project,
                                     which holds only the half-restored data from this run)
  2. empty $APP_DATA_DIR
     and, unless you used --skip-library, $LIBRARY_DIR
  3. run restore.sh again
The backup in $SRC is not changed by any of this.
HELP
}
fc --profile '*' stop web api worker >/dev/null 2>&1 || true
echo "Starting the database ..."
fc up -d --wait postgres >/dev/null
tables=$(psql_value "select count(*) from information_schema.tables where table_schema = 'public'")
if [ "$tables" != 0 ]; then
  die "the database already has tables, so this is not an empty install. Nothing was changed.
       If it is a live install, leave it alone. 'docker compose down -v' DELETES this database, and
       a second checkout with the same project name shares it. Back it up first, or restore with
       a different COMPOSE_PROJECT_NAME."
fi
# From here on something is written; if it stops partway, say how to start over.
trap cleanup_help EXIT

echo "Loading the database ..."
fc exec -T postgres pg_restore -U filecanopy -d filecanopy --no-owner --exit-on-error --single-transaction < "$SRC/db.dump"

echo "Unpacking app-data ..."
mkdir -p "$APP_DATA_DIR"
tar --numeric-owner -C "$APP_DATA_DIR" -xzpf "$SRC/app-data.tar.gz"
if [ "$library" = 1 ]; then
  echo "Unpacking the library ..."
  mkdir -p "$LIBRARY_DIR"
  tar --numeric-owner -C "$LIBRARY_DIR" -xzpf "$SRC/library.tar.gz"
fi

echo "Starting FileCanopy (migrations run now) ..."
fc up -d --wait
echo "Restored from $SRC. Sign in and check a few files; the first scan may take a while."

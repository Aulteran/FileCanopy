#!/bin/sh
# Disaster-recovery backup of a running FileCanopy install (M22): the database, app-data and the
# library, written to one new folder. Restore it with restore.sh.
#
#   deploy/scripts/backup.sh [--no-stop] [--skip-library] DEST_DIR
#
# By default the web and API containers are stopped while files are copied and started again
# afterwards (even if the backup fails), so the database dump, app-data and library describe the
# same moment. --no-stop copies a live system instead: the pieces can disagree with each other.
# --skip-library leaves the library out when it has its own backup (snapshots, rsync, a NAS).
#
# The result holds every person's files, the database (password hashes, sealed API keys) and the
# secret kept in app-data. Treat DEST_DIR like the library itself, and keep it on another disk.
# Run it as the user that owns library/ and app-data/ (FILECANOPY_UID) or as root.
set -eu
. "$(dirname -- "$0")/lib.sh"

stop=1 library=1
while [ $# -gt 0 ]; do
  case $1 in
    --no-stop) stop=0 ;;
    --skip-library) library=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) break ;;
  esac
  shift
done
[ $# -eq 1 ] || die "usage: backup.sh [--no-stop] [--skip-library] DEST_DIR"
[ -d "$1" ] || die "$1 is not a directory"
DEST=$(CDPATH= cd -- "$1" && pwd -P)

inside() { case "$1/" in "$2"/*) return 0 ;; esac; return 1; }
for d in "$LIBRARY_DIR" "$APP_DATA_DIR"; do
  inside "$DEST" "$d" && die "$DEST is inside $d; choose a folder outside both"
  inside "$d" "$DEST" && die "$DEST contains $d; choose a folder outside both"
done
[ -d "$APP_DATA_DIR" ] || die "app-data folder $APP_DATA_DIR not found"
[ "$library" = 0 ] || [ -d "$LIBRARY_DIR" ] || die "library folder $LIBRARY_DIR not found"

fc ps --status running --services | grep -qx postgres || die "the database container is not running"
running=$(fc ps --status running --services)

umask 077
OUT="$DEST/filecanopy-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$OUT"

restart() {
  for s in api worker web; do
    if echo "$running" | grep -qx "$s"; then fc start "$s" >/dev/null || true; fi
  done
}
if [ "$stop" = 1 ]; then
  # dash does not run the EXIT trap on a signal, so turn Ctrl-C and kill into a normal exit.
  trap restart EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  echo "Stopping web and API while copying ..."
  for s in web api worker; do echo "$running" | grep -qx "$s" && fc stop "$s" >/dev/null; done; true
fi

# A file that changes while being read makes tar exit with 1: fine for --no-stop, a failure otherwise.
tar_to() {
  tar --numeric-owner -C "$1" -czpf "$2" . && return 0
  rc=$?
  if [ "$stop" = 0 ] && [ "$rc" -eq 1 ]; then echo "note: files changed while copying" >&2; return 0; fi
  return "$rc"
}

echo "Dumping the database ..."
fc exec -T postgres pg_dump -U filecanopy -d filecanopy --format=custom --no-owner > "$OUT/db.dump"
[ -s "$OUT/db.dump" ] || die "the database dump is empty"

echo "Copying app-data ..."
tar_to "$APP_DATA_DIR" "$OUT/app-data.tar.gz"
sums="db.dump app-data.tar.gz MANIFEST"
if [ "$library" = 1 ]; then
  echo "Copying the library (this can take a while) ..."
  tar_to "$LIBRARY_DIR" "$OUT/library.tar.gz"
  sums="$sums library.tar.gz"
fi

{
  echo "format=1"
  echo "created=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "schema=$(psql_value 'select version_num from alembic_version')"
  echo "version=$(env_value FILECANOPY_VERSION source)"
  if [ "$library" = 1 ]; then echo "library=included"; else echo "library=skipped"; fi
  if [ "$stop" = 1 ]; then echo "consistent=yes"; else echo "consistent=no"; fi
} > "$OUT/MANIFEST"
# shellcheck disable=SC2086
(cd "$OUT" && sha_sum $sums > SHA256SUMS)

echo "Backup written to $OUT"
echo "It contains everyone's files and the database. Keep it private and on another disk."

# Shared by backup.sh and restore.sh (M22). Sourced, not run.
# Needs: POSIX sh, GNU tar, sha256sum (or shasum), and Docker Compose v2.

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

# Compose picks up COMPOSE_FILE, COMPOSE_PROJECT_NAME and COMPOSE_PROFILES from the environment,
# so a setup with extra override files keeps using them. Default: the core file next to this one.
if [ -z "${COMPOSE_FILE:-}" ]; then
  COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
  export COMPOSE_FILE
fi

fc() { docker compose --project-directory "$DEPLOY_DIR" "$@"; }

die() { echo "error: $*" >&2; exit 1; }

# Value of KEY from the environment, else deploy/.env, else the default.
env_value() {
  key=$1 default=$2
  eval "val=\${$key:-}"
  if [ -z "$val" ] && [ -f "$DEPLOY_DIR/.env" ]; then
    val=$(sed -n "s/^$key=//p" "$DEPLOY_DIR/.env" | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//")
  fi
  echo "${val:-$default}"
}

# Absolute, symlink-free path; relative paths are relative to deploy/ like in the compose file.
abs_path() {
  case $1 in /*|[A-Za-z]:*) p=$1 ;; *) p="$DEPLOY_DIR/$1" ;; esac
  if [ -d "$p" ]; then (CDPATH= cd -- "$p" && pwd -P); else echo "$p"; fi
}

LIBRARY_DIR=$(abs_path "$(env_value FILECANOPY_LIBRARY_PATH ../library)")
APP_DATA_DIR=$(abs_path "$(env_value FILECANOPY_APP_DATA_PATH ../app-data)")

sha_sum() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
sha_check() { if command -v sha256sum >/dev/null 2>&1; then sha256sum -c "$@"; else shasum -a 256 -c "$@"; fi; }

psql_value() { fc exec -T postgres psql -U filecanopy -d filecanopy -tAc "$1" | tr -d '[:space:]'; }

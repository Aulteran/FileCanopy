# Shared by backup.sh and restore.sh (M22). Sourced, not run.
# Needs: POSIX sh, GNU tar, sha256sum (or shasum), and Docker Compose v2.

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)

# The same folder as the Docker CLI wants it: identical on Linux and macOS, "D:/x" under Git Bash.
NATIVE_DIR=$(cd -- "$DEPLOY_DIR" && { pwd -W 2>/dev/null || pwd -P; })

die() { echo "error: $*" >&2; exit 1; }

# Value of KEY from the environment, else deploy/.env, else the default. Reads .env the way
# Compose does for plain values: optional "export", spaces around "=", quotes, trailing " # comment".
env_value() {
  key=$1 default=$2
  eval "val=\${$key:-}"
  if [ -z "$val" ] && [ -f "$DEPLOY_DIR/.env" ]; then
    val=$(sed -n -E "s/^[[:space:]]*(export[[:space:]]+)?$key[[:space:]]*=[[:space:]]*//p" "$DEPLOY_DIR/.env" \
      | tail -n 1 \
      | sed -E "s/\r\$//; s/^\"([^\"]*)\".*\$/\1/; t; s/^'([^']*)'.*\$/\1/; t; s/[[:space:]]+#.*\$//; s/[[:space:]]+\$//")
  fi
  echo "${val:-$default}"
}

# Compose picks up COMPOSE_FILE, COMPOSE_PROJECT_NAME and COMPOSE_PROFILES from the environment
# (or deploy/.env), so a setup with its own override files keeps using them. Without COMPOSE_FILE
# we use the core file, plus the release override when FILECANOPY_VERSION is set: an install run
# from published images must also be restored and checked with those images, not rebuilt.
COMPOSE_FILE=$(env_value COMPOSE_FILE "")
if [ -z "$COMPOSE_FILE" ]; then
  if [ -f "$DEPLOY_DIR/compose.yaml" ]; then
    COMPOSE_FILE="$NATIVE_DIR/compose.yaml"
  else
    COMPOSE_FILE="$NATIVE_DIR/docker-compose.yml"
  fi
  if [ ! -f "$DEPLOY_DIR/compose.yaml" ] && [ -n "$(env_value FILECANOPY_VERSION "")" ]; then
    COMPOSE_FILE="$COMPOSE_FILE${COMPOSE_PATH_SEPARATOR:-:}$NATIVE_DIR/docker-compose.release.yml"
  fi
fi
export COMPOSE_FILE

fc() { docker compose --project-directory "$NATIVE_DIR" "$@"; }

# Absolute, symlink-free path; relative paths are relative to deploy/ like in the compose file.
abs_path() {
  case $1 in /*|[A-Za-z]:*) p=$1 ;; *) p="$DEPLOY_DIR/$1" ;; esac
  if [ -d "$p" ]; then (CDPATH= cd -- "$p" && pwd -P); else echo "$p"; fi
}

if [ -f "$DEPLOY_DIR/compose.yaml" ]; then
  LIBRARY_DEFAULT=./library APP_DATA_DEFAULT=./app-data
else
  LIBRARY_DEFAULT=../library APP_DATA_DEFAULT=../app-data
fi
LIBRARY_DIR=$(abs_path "$(env_value FILECANOPY_LIBRARY_PATH "$LIBRARY_DEFAULT")")
APP_DATA_DIR=$(abs_path "$(env_value FILECANOPY_APP_DATA_PATH "$APP_DATA_DEFAULT")")

sha_sum() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
sha_check() { if command -v sha256sum >/dev/null 2>&1; then sha256sum -c "$@"; else shasum -a 256 -c "$@"; fi; }

# Fails (and so stops a set -e script) when psql does; a pipe straight into tr would hide that.
psql_value() {
  out=$(fc exec -T postgres psql -U filecanopy -d filecanopy -tAc "$1") || die "database query failed: $1"
  printf '%s' "$out" | tr -d '[:space:]'
}

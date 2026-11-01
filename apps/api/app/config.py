"""Runtime configuration. All values come from the environment; nothing here needs AI settings."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """Raised when the deployment layout violates a storage invariant."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FILECANOPY_", extra="ignore")

    database_url: SecretStr
    # Optional components. An empty value means "not configured".
    redis_url: str = ""
    # Ordinary, portable documents (bind mount). The API never writes here in M01.
    library_path: Path = Path("/library")
    # App-private data (previews, staging, trash, ...). Never exposed as user documents.
    app_data_path: Path = Path("/app-data")

    # --- File operations (M04) ---
    max_upload_bytes: int = Field(default=10 * 1024**3, ge=1)
    # Bounds for one recursive copy or cross-device move, checked before anything is written.
    max_tree_entries: int = Field(default=20000, ge=1)
    max_tree_depth: int = Field(default=32, ge=1, le=64)
    # Operations still `planned` after this long are treated as interrupted at startup recovery.
    # The API runs as one process, so at startup every `planned` row is an orphan (default 0).
    # Raise it only if several API processes ever share one database.
    operation_stale_seconds: int = Field(default=0, ge=0)

    # --- Reconciliation (M06, ADR-011 §5, §7, §8) ---
    reconciler_enabled: bool = True
    rescan_interval_seconds: int = Field(default=900, ge=1)
    # Bytes per second for the background content hasher; 0 turns it off.
    hash_rate_bytes: int = Field(default=20 * 1024**2, ge=0)
    watcher_enabled: bool = True
    # auto = detect from /proc/self/mountinfo; yes/no overrides detection.
    network_fs: Literal["auto", "yes", "no"] = "auto"

    # --- Setup import and folder upload (M06b, ADR-011 §4, §5) ---
    max_import_entries: int = Field(default=100_000, ge=1, le=500_000)  # per batch
    max_zip_bytes: int = Field(default=10 * 1024**3, ge=1)  # the uploaded archive itself
    # Total decompressed bytes of one archive, counted while streaming.
    max_zip_uncompressed_bytes: int = Field(default=50 * 1024**3, ge=1)
    # An entry that expands past 1 MiB at more than this ratio is refused as a decompression bomb.
    max_zip_ratio: int = Field(default=1000, ge=1)
    import_expiry_seconds: int = Field(default=7 * 24 * 3600, ge=1)
    # Free space that must remain on the library filesystem after an import.
    import_free_space_reserve_bytes: int = Field(default=1024**3, ge=0)
    # Import reports (import_entry rows) are deleted this many days after a batch completes. The
    # batch summary row is kept indefinitely; stopped batches and batches with failures are kept.
    import_entry_retention_days: int = Field(default=90, ge=1)
    # Folder uploads a person may leave unfinished at once.
    max_open_folder_uploads: int = Field(default=5, ge=1)
    # A running import pauses the reconciler of its root for at most this long (continuous); after
    # that the scan proceeds and the root reports "scan delayed".
    scan_pause_ceiling_seconds: int = Field(default=1800, ge=1)

    # --- Trash retention (M07a, ADR-017) ---
    # The hourly check that deletes trash items past their deadline. The retention itself is an
    # instance setting (`trash_retention_days`, default 30), not an environment value.
    trash_purge_enabled: bool = True
    trash_check_seconds: int = Field(default=3600, ge=1)

    # --- Optional backups (M06c, ADR-015) ---
    # Separate mount for backups. Empty = not configured: the feature stays off and the app is
    # unaffected. Must not equal, contain or sit inside the library or app-data paths.
    backup_path: str = ""
    backup_scheduler_enabled: bool = True
    backup_check_seconds: int = Field(default=60, ge=1)
    # Bytes per second the backup job may read; 0 turns the throttle off.
    backup_rate_bytes: int = Field(default=64 * 1024**2, ge=0)
    # Free space that must remain on the backup volume after a run.
    backup_free_space_reserve_bytes: int = Field(default=1024**3, ge=0)
    # Files up to this size are read into memory so a change during the read can be retried.
    backup_buffer_file_bytes: int = Field(default=4 * 1024**2, ge=0)

    # --- Authentication (M02) ---
    # Optional operator-chosen first-run setup token (>= 32 chars). Empty = generate one at
    # startup and print it once in the API log while no administrator exists.
    setup_token: SecretStr = SecretStr("")
    # Secure cookies need HTTPS (browsers exempt localhost). False only for plain-HTTP LAN use.
    cookie_secure: bool = True
    # Number of trusted hops between the API and the client. 0 means X-Forwarded-For is ignored.
    # Compose sets 1: the bundled nginx (the only peer of the API) overwrites the header with the
    # real client address it worked out from FILECANOPY_TRUSTED_PROXIES (ADR-018). Do not raise it.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    # The address people type in the browser, e.g. https://files.example.org (scheme and host, no
    # path). Recommended behind a reverse proxy: sign-in works even if the proxy does not forward
    # the original Host header. Added to the allowed origins.
    public_url: str = ""
    # Extra allowed browser origins (comma-separated) besides the request's own Host.
    allowed_origins: str = ""
    session_idle_seconds: int = 7 * 24 * 3600
    session_absolute_seconds: int = 30 * 24 * 3600
    invitation_ttl_seconds: int = 72 * 3600
    reset_ttl_seconds: int = 3600
    # Argon2id cost (defaults: RFC 9106 low-memory profile). Lower only in tests.
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=65536, ge=8)
    argon2_parallelism: int = Field(default=4, ge=1)
    # Failed-login throttling (see app/auth/throttle.py).
    throttle_window_seconds: int = 900
    throttle_max_per_account_ip: int = 5
    throttle_max_per_account: int = 20
    throttle_max_per_ip: int = 40

    @property
    def extra_origins(self) -> str:
        """Comma-separated origins accepted besides the request's Host (public URL included)."""
        return ",".join(o for o in (self.public_url, self.allowed_origins) if o)

    @model_validator(mode="after")
    def _paths_must_be_separate(self) -> "Settings":
        library = self.library_path.resolve()
        app_data = self.app_data_path.resolve()
        if library == app_data or library in app_data.parents or app_data in library.parents:
            raise ConfigError(
                "library_path and app_data_path must be separate, non-nested directories"
            )
        if self.backup_path:
            backup = Path(self.backup_path).resolve()
            for other in (library, app_data):
                if backup == other or backup in other.parents or other in backup.parents:
                    raise ConfigError(
                        "backup_path must not equal, contain or sit inside the library or "
                        "app-data paths"
                    )
            # the same directory reached by another path (a second bind mount of one folder)
            backup_id = path_identity(backup)
            if backup_id is not None and backup_id in (
                path_identity(library),
                path_identity(app_data),
            ):
                raise ConfigError(
                    "backup_path is the same directory as the library or the app-data path"
                )
        if self.public_url:
            parsed = urlparse(self.public_url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ConfigError(
                    "FILECANOPY_PUBLIC_URL must look like https://files.example.org "
                    "(scheme and host, no path)"
                )
        configured = self.setup_token.get_secret_value()
        if configured and len(configured) < 32:
            raise ConfigError("FILECANOPY_SETUP_TOKEN must be at least 32 characters")
        return self


def path_identity(path: Path) -> tuple[int, int] | None:
    """(st_dev, st_ino) of a directory, or None when it cannot be read (not there yet)."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_dev, info.st_ino


def backup_dir(settings: Settings) -> Path | None:
    return Path(settings.backup_path) if settings.backup_path else None


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment

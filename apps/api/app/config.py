"""Runtime configuration. All values come from the environment; nothing here needs AI settings."""

from pathlib import Path

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

    # --- Authentication (M02) ---
    # Optional operator-chosen first-run setup token (>= 32 chars). Empty = generate one at
    # startup and print it once in the API log while no administrator exists.
    setup_token: SecretStr = SecretStr("")
    # Secure cookies need HTTPS (browsers exempt localhost). False only for plain-HTTP LAN use.
    cookie_secure: bool = True
    # Number of trusted reverse proxies in front of the API (the bundled nginx is one). 0 means
    # X-Forwarded-For is ignored. Never raise this unless every hop overwrites the header.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
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

    @model_validator(mode="after")
    def _paths_must_be_separate(self) -> "Settings":
        library = self.library_path.resolve()
        app_data = self.app_data_path.resolve()
        if library == app_data or library in app_data.parents or app_data in library.parents:
            raise ConfigError(
                "library_path and app_data_path must be separate, non-nested directories"
            )
        configured = self.setup_token.get_secret_value()
        if configured and len(configured) < 32:
            raise ConfigError("FILECANOPY_SETUP_TOKEN must be at least 32 characters")
        return self


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment

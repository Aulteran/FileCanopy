"""Runtime configuration. All values come from the environment; nothing here needs AI settings."""

from pathlib import Path

from pydantic import SecretStr, model_validator
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

    @model_validator(mode="after")
    def _paths_must_be_separate(self) -> "Settings":
        library = self.library_path.resolve()
        app_data = self.app_data_path.resolve()
        if library == app_data or library in app_data.parents or app_data in library.parents:
            raise ConfigError(
                "library_path and app_data_path must be separate, non-nested directories"
            )
        return self


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment

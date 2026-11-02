"""Runtime configuration. All values come from the environment; nothing here needs AI settings."""

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ADDR_RE = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9.-]+\.[a-z]{2,}$")


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

    # --- Previews, extraction and the optional converter (M10, ADR-019) ---
    # Base URL of the sealed converter container, e.g. http://converter:8090. Empty = not
    # configured: Office files then show "download instead" and everything else is unaffected.
    converter_url: str = ""
    preview_office_max_bytes: int = Field(default=50 * 1024**2, ge=1)
    preview_max_pages: int = Field(default=500, ge=1)
    converter_timeout_seconds: int = Field(default=60, ge=1)
    # Wall-clock limit and memory cap of the child process that parses PDF, DOCX, images, zips.
    preview_worker_timeout_seconds: int = Field(default=20, ge=1)
    preview_worker_memory_bytes: int = Field(default=1024**3, ge=64 * 1024**2)
    # Refuse to parse files when the kernel cannot install the seccomp filter (no sockets, no exec
    # in the parser child). Turn off only on a kernel or container runtime that forbids it.
    preview_require_seccomp: bool = True
    preview_max_workers: int = Field(default=2, ge=1, le=16)
    preview_thumb_max_bytes: int = Field(default=30 * 1024**2, ge=1)
    preview_thumb_max_pixels: int = Field(default=50_000_000, ge=1)
    preview_pdf_extract_max_bytes: int = Field(default=200 * 1024**2, ge=1)
    preview_text_max_chars: int = Field(default=1_000_000, ge=1000)
    preview_zip_max_entries: int = Field(default=1000, ge=1, le=100_000)
    # Cached derivatives (converted PDFs, extracted text, thumbnails) are trimmed oldest first.
    preview_cache_max_bytes: int = Field(default=2 * 1024**3, ge=1)

    # --- Search and OCR (M11) ---
    # Background indexing of file text for full-text search. Filename search always works.
    search_enabled: bool = True
    search_scan_seconds: int = Field(default=30, ge=1)
    search_batch: int = Field(default=200, ge=1, le=5000)
    search_max_attempts: int = Field(default=5, ge=1, le=20)
    search_lease_seconds: int = Field(default=300, ge=10)
    search_body_max_chars: int = Field(default=1_000_000, ge=1000)
    search_text_max_bytes: int = Field(default=5 * 1024**2, ge=1024)
    # Base URL of the sealed OCR container (unix:///run/ocr/ocr.sock). Empty = OCR off.
    ocr_url: str = ""
    ocr_timeout_seconds: int = Field(default=120, ge=5)
    ocr_max_pages: int = Field(default=50, ge=1, le=500)
    ocr_max_bytes: int = Field(default=50 * 1024**2, ge=1)

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
    # --- Outbound mail (M12, ADR-020). Empty host = not configured: invitations and resets are
    # then shared as one-time links by the administrator and "forgot password" is hidden.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # "none" is accepted for a loopback relay only, unless this says otherwise.
    smtp_allow_plaintext: bool = False

    # --- Inbound mailbox (M13, ADR-007). Empty host = off. Read-only IMAP polling; nothing is
    # deleted, moved or flagged in the mailbox. Credentials come from the environment only.
    imap_host: str = ""
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_security: Literal["ssl", "starttls", "none"] = "ssl"  # "none": loopback hosts only
    imap_username: str = ""
    imap_password: SecretStr = SecretStr("")
    imap_folder: str = Field(default="INBOX", max_length=200)
    imap_poll_seconds: int = Field(default=120, ge=30, le=3600)
    imap_timeout_seconds: int = Field(default=30, ge=5, le=300)
    imap_personal_address: str = ""
    imap_ingest_address: str = ""
    # The authserv-id our provider writes into Authentication-Results. Empty = no sender can be
    # authenticated, so every message is quarantined.
    imap_authserv_id: str = ""
    imap_auth_policy: Literal["aligned", "dmarc"] = "aligned"
    imap_recipient_headers: str = "x-original-to,delivered-to"
    imap_max_message_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    imap_max_parts: int = Field(default=200, ge=1, le=2000)
    imap_max_depth: int = Field(default=6, ge=1, le=20)
    imap_max_attachments: int = Field(default=50, ge=0, le=500)
    imap_max_per_poll: int = Field(default=50, ge=1, le=1000)
    mail_quarantine_retention_days: int = Field(default=30, ge=0, le=3650)  # 0 = keep
    mail_quarantine_quota_bytes: int = Field(default=1024**3, ge=0)
    # Bytes of recognised mail waiting to be filed. Separate from the quarantine quota so that
    # unauthenticated mail to the intake address can never starve recognised senders.
    mail_staging_quota_bytes: int = Field(default=1024**3, ge=0)
    # M14: limits applied to an archive attachment before it reaches the library (zip family,
    # gzip, bzip2, xz); no archive inside an archive is allowed.
    mail_archive_max_entries: int = Field(default=1000, ge=1, le=100_000)
    mail_archive_max_uncompressed_bytes: int = Field(default=1024**3, ge=1)
    mail_archive_max_ratio: int = Field(default=100, ge=1)
    # Messages filed per sender per rolling day; the rest wait as `failed` / RATE_LIMITED.
    mail_daily_messages_per_sender: int = Field(default=200, ge=1)
    alias_ttl_seconds: int = 24 * 3600
    max_aliases_per_user: int = Field(default=10, ge=0, le=50)

    # --- OIDC sign-in (M12, ADR-020). Empty issuer or client id = off; local sign-in is unaffected.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_scopes: str = "openid email profile"
    oidc_label: str = Field(default="Single sign-on", max_length=60)
    oidc_require_email_verified: bool = True
    # Lab setups only: allow an http issuer that is not on loopback.
    oidc_allow_http: bool = False
    oidc_timeout_seconds: int = Field(default=10, ge=1, le=60)
    # Google Workspace only (M12b): require this `hd` claim in the ID token. Empty = no pin.
    oidc_hosted_domain: str = ""
    # Break-glass (M12b): ignore the "OIDC-only" switch so password sign-in works again.
    force_local_login: bool = False

    # --- Optional AI (M15, docs/milestones/M15-design-and-plan.md). Nothing here is needed; with no
    # provider configured the app behaves exactly as before.
    # 32 random bytes, base64 (url-safe or standard). Seals provider API keys stored in the
    # database. Empty = keys cannot be stored; local providers (no key) still work.
    secrets_key: SecretStr = SecretStr("")
    # Operator allow-list of hosts local AI providers may use (comma separated names or IPs).
    # Empty = any host that resolves only to loopback or private addresses. The web administrator
    # role is not trusted to widen this; link-local and metadata addresses are refused either way.
    ai_local_hosts: str = ""
    ai_timeout_seconds: int = Field(default=60, ge=1, le=600)
    ai_max_input_chars: int = Field(default=100_000, ge=100, le=2_000_000)
    ai_max_output_tokens: int = Field(default=2048, ge=16, le=32_000)
    ai_max_response_bytes: int = Field(default=1024**2, ge=1024, le=16 * 1024**2)
    # Default monthly caps in micro-dollars (1_000_000 = $1); each can be changed in the UI.
    ai_default_cap_micro: int = Field(default=3_000_000, ge=0)
    ai_breaker_failures: int = Field(default=5, ge=1, le=100)
    ai_breaker_seconds: int = Field(default=60, ge=1, le=3600)

    # --- Confidential PIN (M12b). The pepper keeps a database-only leak from being crackable
    # offline (a six-digit PIN has only 10^6 values). Empty = generate a 256-bit pepper file in
    # app-data on first use.
    pin_pepper: SecretStr = SecretStr("")

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
        return ",".join(o for o in (self._public_origin(), self.allowed_origins) if o)

    def _public_origin(self) -> str:
        """The public URL as a browser sends it in Origin: lower case, no default port."""
        if not self.public_url:
            return ""
        parsed = urlparse(self.public_url)
        host = (parsed.hostname or "").lower()
        if ":" in host:
            host = f"[{host}]"
        default = {"http": 80, "https": 443}[parsed.scheme]
        port = "" if parsed.port in (None, default) else f":{parsed.port}"
        return f"{parsed.scheme}://{host}{port}"

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def imap_configured(self) -> bool:
        return bool(
            self.imap_host
            and self.imap_username
            and self.imap_password.get_secret_value()
            and self.imap_personal_address
            and self.imap_ingest_address
        )

    @property
    def oidc_is_google(self) -> bool:
        return self.oidc_issuer.rstrip("/") == "https://accounts.google.com"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self._public_origin()}/api/v1/auth/oidc/callback"

    @model_validator(mode="after")
    def _mail_and_oidc(self) -> "Settings":
        if self.smtp_host or self.smtp_from:
            if not self.smtp_configured:
                raise ConfigError("smtp_host and smtp_from must both be set")
            if not self.public_url:
                raise ConfigError("SMTP needs FILECANOPY_PUBLIC_URL (links in mail use it)")
            if "@" not in self.smtp_from or any(c in self.smtp_from for c in "\r\n"):
                raise ConfigError("smtp_from must be an email address")
            if (
                self.smtp_security == "none"
                and not self.smtp_allow_plaintext
                and self.smtp_host not in ("localhost", "127.0.0.1", "::1")
            ):
                raise ConfigError(
                    "smtp_security=none is only allowed for a loopback host "
                    "(or set FILECANOPY_SMTP_ALLOW_PLAINTEXT=true)"
                )
        if self.imap_host or self.imap_username or self.imap_personal_address:
            if not self.imap_configured:
                raise ConfigError(
                    "imap_host, imap_username, imap_password, imap_personal_address and "
                    "imap_ingest_address must all be set"
                )
            personal = self.imap_personal_address.strip().lower()
            ingest = self.imap_ingest_address.strip().lower()
            for addr in (personal, ingest):
                if not _ADDR_RE.match(addr):
                    raise ConfigError("imap intake addresses must be plain email addresses")
            if personal == ingest:
                raise ConfigError("the personal and ingest addresses must differ")
            if self.imap_security == "none" and self.imap_host not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                raise ConfigError("imap_security=none is only allowed for a loopback host")
        if self.oidc_issuer or self.oidc_client_id:
            if not self.oidc_enabled:
                raise ConfigError("oidc_issuer and oidc_client_id must both be set")
            if not self.public_url:
                raise ConfigError("OIDC needs FILECANOPY_PUBLIC_URL (the redirect URI uses it)")
            issuer = urlparse(self.oidc_issuer)
            loopback = (issuer.hostname or "") in ("localhost", "127.0.0.1", "::1")
            if issuer.scheme != "https" and not (
                issuer.scheme == "http" and (loopback or self.oidc_allow_http)
            ):
                raise ConfigError("oidc_issuer must be an https URL")
            if not issuer.netloc or issuer.query or issuer.fragment:
                raise ConfigError("oidc_issuer must be a plain URL")
            if self.oidc_hosted_domain and not self.oidc_is_google:
                raise ConfigError("oidc_hosted_domain only applies to Google sign-in")
            if "openid" not in self.oidc_scopes.split():
                raise ConfigError("oidc_scopes must include openid")
        return self

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

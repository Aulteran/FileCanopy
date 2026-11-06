import base64
import os
from pathlib import Path

import pytest

from app import bootstrap
from app.config import Settings

DSN = "postgresql+psycopg://u:p@h/db"


def test_key_created_once_and_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FILECANOPY_SECRETS_KEY", raising=False)
    assert bootstrap.ensure_secrets_key(tmp_path, os.getuid(), os.getgid())
    first = (tmp_path / "secrets_key").read_text()
    assert len(base64.b64decode(first)) == 32
    assert (tmp_path / "secrets_key").stat().st_mode & 0o077 == 0
    assert not bootstrap.ensure_secrets_key(tmp_path, os.getuid(), os.getgid())
    assert (tmp_path / "secrets_key").read_text() == first


def test_explicit_key_means_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILECANOPY_SECRETS_KEY", "x")
    assert not bootstrap.ensure_secrets_key(tmp_path, os.getuid(), os.getgid())
    assert not (tmp_path / "secrets_key").exists()


def test_settings_read_file_and_explicit_wins(tmp_path: Path) -> None:
    f = tmp_path / "k"
    f.write_text("from-file\n")
    s = Settings(database_url=DSN, secrets_key_file=str(f))  # type: ignore[arg-type]
    assert s.secrets_key.get_secret_value() == "from-file"
    s = Settings(database_url=DSN, secrets_key="own", secrets_key_file=str(f))  # type: ignore[arg-type]
    assert s.secrets_key.get_secret_value() == "own"
    s = Settings(database_url=DSN, secrets_key_file=str(tmp_path / "none"))  # type: ignore[arg-type]
    assert s.secrets_key.get_secret_value() == ""


def test_warns_when_env_key_differs_from_the_generated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FILECANOPY_SECRETS_KEY", raising=False)
    bootstrap.ensure_secrets_key(tmp_path, os.getuid(), os.getgid())
    monkeypatch.setenv("FILECANOPY_SECRETS_KEY", "something-else")
    assert not bootstrap.ensure_secrets_key(tmp_path, os.getuid(), os.getgid())
    assert "differs from the generated key file" in capsys.readouterr().err

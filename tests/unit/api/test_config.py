from pathlib import Path

import pytest

from app.config import Settings

DSN = "postgresql+psycopg://u:p@h/db"


def make(library: Path, app_data: Path) -> Settings:
    return Settings(database_url=DSN, library_path=library, app_data_path=app_data)  # type: ignore[arg-type]


def test_separate_directories_accepted(tmp_path: Path) -> None:
    assert make(tmp_path / "library", tmp_path / "app-data").redis_url == ""


@pytest.mark.parametrize("inner", ["library", "app-data"])
def test_nested_directories_rejected(tmp_path: Path, inner: str) -> None:
    outer = tmp_path / "outer"
    pairs = {
        "library": (outer, outer / "app-data"),
        "app-data": (outer / "library", outer),
    }
    with pytest.raises(ValueError, match="separate"):
        make(*pairs[inner])


def test_same_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="separate"):
        make(tmp_path, tmp_path)


def test_symlinked_overlap_rejected(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    try:
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="separate"):
        make(tmp_path / "real", tmp_path / "link")


def test_database_url_is_not_leaked_by_repr(tmp_path: Path) -> None:
    s = make(tmp_path / "a", tmp_path / "b")
    assert "u:p@h" not in repr(s)
    assert "u:p@h" not in str(s)


def test_ai_settings_are_all_optional() -> None:
    """The app starts with no AI configured: every AI setting has a default (M15)."""
    ai = [n for n, f in Settings.model_fields.items() if "ai" in n.split("_") or n == "secrets_key"]
    assert ai
    assert not [n for n in ai if Settings.model_fields[n].is_required()]
    assert Settings.model_fields["secrets_key"].default.get_secret_value() == ""

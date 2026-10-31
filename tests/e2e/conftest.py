"""Browser tests run against the real Compose stack (nginx + API + PostgreSQL), like the
integration suite, and reuse its helpers."""

import secrets
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration" / "compose"))

from test_stack import Stack  # noqa: E402


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    s = Stack(tmp_path_factory.mktemp("e2e"), f"fctest{secrets.token_hex(3)}")
    s.up()
    yield s
    s.down(volumes=True)

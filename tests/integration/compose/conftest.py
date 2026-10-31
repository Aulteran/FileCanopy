"""Shared Compose fixtures.

`app_stack` is one stack for the auth and storage suites (they only add accounts and never stop
core services), so the images are started once instead of twice. Order matters: test_auth_stack
runs first and performs the first-run setup that test_storage_stack then reuses; pass the files
in that order: auth, storage, files (pass them explicitly; alphabetical order puts files first).
"""

import secrets
from collections.abc import Iterator

import pytest
from test_stack import Stack


@pytest.fixture(scope="session")
def app_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    s = Stack(tmp_path_factory.mktemp("app"), f"fctest{secrets.token_hex(3)}")
    s.up()
    yield s
    s.down(volumes=True)

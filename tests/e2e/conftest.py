"""Browser tests run against the real Compose stack (nginx + API + PostgreSQL), like the
integration suite, and reuse its helpers."""

import secrets
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration" / "compose"))

from support import World  # noqa: E402
from test_stack import Stack  # noqa: E402
from test_storage_stack import invite_and_login, root_node, setup_admin, user_id  # noqa: E402


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    s = Stack(tmp_path_factory.mktemp("e2e"), f"fctest{secrets.token_hex(3)}")
    s.up()
    yield s
    s.down(volumes=True)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="session")
def world(stack: Stack) -> World:
    admin = setup_admin(stack)
    tag = uuid.uuid4().hex[:6]
    alice_mail, bob_mail = f"alice{tag}@example.com", f"bob{tag}@example.com"
    alice = invite_and_login(stack, admin, alice_mail)
    invite_and_login(stack, admin, bob_mail)
    uid = user_id(stack, alice_mail)
    return World(
        stack,
        f"http://127.0.0.1:{stack.port}",
        alice_mail,
        bob_mail,
        alice,
        root_node(stack, f"personal/{uid}"),
        stack.library / "personal" / uid,
    )

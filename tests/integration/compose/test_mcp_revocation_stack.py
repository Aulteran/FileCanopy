"""Concurrent HTTP routes on real PostgreSQL, with deterministic account-lock interleavings.

The test app runs inside the API container against the isolated stack's database. TestClient
drives the real routes/dependencies in separate threads; no production test hooks or AI calls.
"""

import json
import uuid

import pytest
from test_stack import Stack
from test_storage_stack import invite_and_login, setup_admin

pytestmark = pytest.mark.compose

RACE = """
import json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.auth import service
from app.config import load_settings
from app.db.models import McpToken, User
from app.main import create_app
from app.mcp import router, tokens

order, email = sys.argv[1:]
app = create_app(load_settings().model_copy(update={"mcp_enabled": True}))
sessions = app.state.auth.sessions
admin = TestClient(app, base_url='https://testserver')
person = TestClient(app, base_url='https://testserver')
def login(client, address):
    r = client.post('/api/v1/auth/login', json={
        'email': address, 'password': 'correct horse battery staple'})
    assert r.status_code == 200, r.text
    return {'X-CSRF-Token': r.json()['csrf_token']}
ah, ph = login(admin, 'admin@example.com'), login(person, email)
with sessions() as db:
    uid = db.scalar(select(User.id).where(User.email == email))

held, waiting, release = threading.Event(), threading.Event(), threading.Event()
waiter = {}
real_lock, real_live, real_mint = service.lock_account, router._live_tokens_locked, tokens.mint

def revocation_lock(db, user_id):
    assert user_id == uid
    if order == 'revoke-first':
        real_lock(db, user_id)
        held.set()
        assert release.wait(15), 'revocation lock never released'
    else:
        waiter['pid'] = db.scalar(text('select pg_backend_pid()'))
        waiting.set()
        real_lock(db, user_id)

def creation_check(db, principal, cfg):
    if order == 'revoke-first' and not waiting.is_set():
        waiter['pid'] = db.scalar(text('select pg_backend_pid()'))
        waiting.set()
    return real_live(db, principal, cfg)

def mint():
    if order == 'create-first':
        held.set()  # after the final session/proof/limit check, just before insert
        assert release.wait(15), 'creation lock never released'
    return real_mint()

service.lock_account = revocation_lock
router._live_tokens_locked = creation_check
tokens.mint = mint
def create():
    return person.post('/api/v1/mcp/tokens', headers=ph, json={
        'name': 'race', 'password': 'correct horse battery staple',
        'acknowledge_remote_ai': True})
def revoke():
    return admin.post(f'/api/v1/admin/users/{uid}/revoke-sessions', headers=ah)

with ThreadPoolExecutor(max_workers=2) as pool:
    first = pool.submit(create if order == 'create-first' else revoke)
    try:
        assert held.wait(15), 'first operation never held the account lock'
        second = pool.submit(revoke if order == 'create-first' else create)
        assert waiting.wait(15), 'second operation never reached the lock'
        # Observe a real PostgreSQL lock wait instead of assuming timing from a sleep.
        deadline = time.monotonic() + 10
        blocked = False
        while time.monotonic() < deadline:
            with sessions() as db:
                blocked = bool(db.scalar(text('select pg_blocking_pids(:pid)'), waiter))
            if blocked:
                break
            time.sleep(0.01)
        assert blocked, 'the opposing request was not serialized on PostgreSQL'
    finally:
        release.set()
    a, b = first.result(timeout=20), second.result(timeout=20)
created, revoked = (a, b) if order == 'create-first' else (b, a)
assert revoked.status_code == 200, revoked.text
assert created.status_code == (201 if order == 'create-first' else 401), created.text
if created.status_code == 201:
    token = created.json()['token']
    response = TestClient(app).post('/api/v1/mcp', headers={'Authorization': f'Bearer {token}'},
        json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'})
    assert response.status_code == 401, response.text
with sessions() as db:
    live = list(db.scalars(select(McpToken.id).where(
        McpToken.user_id == uid, McpToken.revoked_at.is_(None))))
assert not live, 'a token outlived the forced sign-out'
print(json.dumps({'created': created.status_code,
    'revoked': revoked.status_code, 'live': len(live)}))
"""


@pytest.mark.parametrize("order", ["create-first", "revoke-first"])
def test_mcp_creation_and_forced_sign_out_share_the_account_lock(
    app_stack: Stack, order: str
) -> None:
    admin = setup_admin(app_stack)
    email = f"mcp{uuid.uuid4().hex[:10]}@example.com"
    invite_and_login(app_stack, admin, email)
    result = app_stack.compose("exec", "-T", "api", "python", "-c", RACE, order, email)
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out == {"created": 201 if order == "create-first" else 401, "revoked": 200, "live": 0}

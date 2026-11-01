"""M03 against the real stack: migration 0003 + backfill on PostgreSQL, root directories on the
bind-mounted library, symlinked root refusal, admin personal 403 through nginx, lineage index.

Run with the rest of the compose suite (`npm run test:compose`). Random credentials only.
"""

import os
import re

import pytest
from test_auth_stack import PASSWORD, Client
from test_stack import Stack

pytestmark = pytest.mark.compose


@pytest.fixture
def stack(app_stack: Stack) -> Stack:
    return app_stack


def setup_admin(stack: Stack) -> Client:
    """Creates the first administrator, or reuses the one the auth suite already created."""
    anon = Client(stack)
    if anon.call("GET", "/api/v1/setup/status")[1]["setup_required"]:
        logs = stack.compose("logs", "--no-color", "api").stdout
        token = re.findall(r"FIRST-RUN SETUP TOKEN[^:]*: ([\w-]+)", logs)[-1]
        body = {
            "email": "admin@example.com",
            "name": "Ada",
            "password": PASSWORD,
            "setup_token": token,
        }
        assert anon.call("POST", "/api/v1/setup/admin", body)[0] == 201
    admin = Client(stack)
    assert admin.login("admin@example.com") == 200
    return admin


def invite_and_login(stack: Stack, admin: Client, email: str) -> Client:
    status, body, _ = admin.call(
        "POST", "/api/v1/admin/users", {"email": email, "name": "Alice", "role": "member"}
    )
    assert status == 201
    accept = {"token": body["token"]["token"], "password": PASSWORD}
    assert Client(stack).call("POST", "/api/v1/auth/invitations/accept", accept)[0] == 200
    user = Client(stack)
    assert user.login(email) == 200
    return user


def user_id(stack: Stack, email: str) -> str:
    return stack.sql(f"select id from app_user where email = '{email}'")


def root_node(stack: Stack, relative_dir: str) -> str:
    return stack.sql(
        "select n.id from node n join storage_root r on r.id = n.root_id "
        f"where n.parent_id is null and r.relative_dir = '{relative_dir}'"
    )


def test_migration_backfills_existing_accounts_on_postgres(stack: Stack) -> None:
    assert stack.sql("select version_num from alembic_version") == "0014"
    assert stack.sql("select count(*) from storage_root where kind = 'shared'") == "1"
    admin = setup_admin(stack)
    users = stack.sql("select count(*) from app_user")  # the auth suite may have added accounts
    assert stack.sql("select count(*) from storage_root where kind = 'personal'") == users
    # Re-create the pre-M03 state (accounts but no storage tables) and migrate for real.
    stack.sql(
        "drop table search_doc, search_job, confidential_unlock, notification, trash_item, "
        "backup_chain_state, backup_run, "
        "import_entry, import_batch, file_event, scan_run, file_operation, node_grant, node, "
        "storage_root; "
        "delete from app_setting where key = 'shared_tree_default_access'; "
        "update alembic_version set version_num = '0002'"
    )
    stack.compose("run", "--rm", "migrate")
    assert stack.sql("select version_num from alembic_version") == "0014"
    uid = user_id(stack, "admin@example.com")
    assert stack.sql(f"select relative_dir from storage_root where owner_user_id = '{uid}'") == (
        f"personal/{uid}"
    )
    roots = str(int(users) + 1)  # every personal root plus the shared root
    assert stack.sql("select count(*) from node where parent_id is null") == roots
    assert stack.sql("select count(*) from node_grant") == users  # default editor, shared root
    assert stack.sql("select value #>> '{}' from app_setting where key like 'shared_tree_%'") == (
        "editor"
    )
    # 0006: every root of this fresh state is empty, so its setup window is open
    assert stack.sql("select count(*) from storage_root where setup_state = 'open'") == roots
    stack.compose("run", "--rm", "migrate")  # idempotent: no duplicate roots
    assert stack.sql("select count(*) from storage_root") == roots
    assert admin.call("GET", "/api/v1/roots")[0] == 200


def test_roots_directories_isolation_and_symlink_refusal(stack: Stack) -> None:
    admin = Client(stack)
    assert admin.login("admin@example.com") == 200
    alice = invite_and_login(stack, admin, "alice@example.com")
    alice_uid = user_id(stack, "alice@example.com")
    stack.compose("restart", "api")  # ensure_roots creates the directories at startup
    stack.wait_health("ok")
    assert (stack.library / "shared").is_dir()
    alice_dir = stack.library / "personal" / alice_uid
    assert alice_dir.is_dir()
    if hasattr(os, "getuid"):  # Linux host: bind-mount modes are real
        assert alice_dir.stat().st_mode & 0o007 == 0

    # Admin sees root status only, never names or contents; alice's node is a uniform 403.
    alice_root = root_node(stack, f"personal/{alice_uid}")
    admin_login = Client(stack)
    assert admin_login.login("admin@example.com") == 200
    status, body, _ = admin_login.call("GET", f"/api/v1/nodes/{alice_root}")
    assert (status, body) == (403, {"detail": {"code": "FORBIDDEN"}})
    assert admin_login.call("GET", f"/api/v1/nodes/{alice_root}/grants")[0] == 403
    status, overview, _ = admin_login.call("GET", "/api/v1/admin/storage")
    assert status == 200 and "Alice" not in str(overview)
    assert alice.login("alice@example.com") == 200
    status, mine, _ = alice.call("GET", f"/api/v1/nodes/{alice_root}")
    assert status == 200 and mine["my_privileges"] == sorted(
        ["traverse", "list", "read", "write", "share", "manage"]
    )

    # Replace alice's root directory with a symlink out of the library, inside the container.
    swap = (
        "import os, shutil; "
        f"p = '/library/personal/{alice_uid}'; shutil.rmtree(p); os.symlink('/etc', p)"
    )
    stack.compose("exec", "-T", "api", "python", "-c", swap)
    stack.compose("restart", "api")
    stack.wait_health("ok")
    status, overview, _ = admin_login.call("GET", "/api/v1/admin/storage")
    states = {p["user_id"]: p["status"] for p in overview["personal_roots"]}
    assert states[alice_uid] == "missing"
    assert stack.sql(
        f"select status from storage_root where relative_dir = 'personal/{alice_uid}'"
    ) == ("missing")
    # Removing the link lets the next start recreate a real directory.
    stack.compose(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        f"import os; os.unlink('/library/personal/{alice_uid}')",
    )
    stack.compose("restart", "api")
    stack.wait_health("ok")
    assert (stack.library / "personal" / alice_uid).is_dir()
    assert stack.sql(
        f"select status from storage_root where relative_dir = 'personal/{alice_uid}'"
    ) == ("active")


def test_lineage_prefix_query_uses_the_index(stack: Stack) -> None:
    plan = stack.sql(
        "set enable_seqscan = off; "
        "explain select id from node where lineage like '/00000000-0000-0000-0000-000000000000/%'"
    )
    assert "ix_node_lineage" in plan

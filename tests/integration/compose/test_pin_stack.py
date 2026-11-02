"""M12b against the real stack: migration 0015 on PostgreSQL and the PIN attempt counter under
concurrency. Twenty wrong PINs sent at once from one session must cost at most three comparisons
(the free tries), the rest answer 429 without being compared, and the stored counter must equal
the number of recorded failures: the row lock and the commit-before-compare are what hold here,
and SQLite has no row locks. Random credentials only."""

import threading
import uuid

import pytest
from test_auth_stack import PASSWORD
from test_files_stack import js
from test_stack import Stack
from test_trash_stack import Member

pytestmark = pytest.mark.compose

PIN = "482915"


@pytest.fixture
def stack(app_stack: Stack) -> Stack:
    return app_stack


def test_parallel_wrong_pins_are_counted_before_they_are_compared(stack: Stack) -> None:
    assert stack.sql("select version_num from alembic_version") == "0020"
    m = Member(stack, "pin")
    status, out = js(m.client, "PUT", "/api/v1/auth/pin", {"pin": PIN, "password": PASSWORD})
    assert status == 200, out
    folder = m.folder("Secret" + uuid.uuid4().hex[:4])
    status, out = js(m.client, "PUT", f"/api/v1/nodes/{folder}/privacy", {"label": "confidential"})
    assert status == 200, out

    codes: list[int] = []
    lock = threading.Lock()
    gate = threading.Barrier(20)

    def guess() -> None:
        gate.wait()
        st, _ = js(m.client, "POST", f"/api/v1/nodes/{folder}/unlock", {"pin": "000001"})
        with lock:
            codes.append(st)

    threads = [threading.Thread(target=guess) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert len(codes) == 20
    compared = codes.count(403) + codes.count(401)
    assert compared <= 3, codes  # wrong PIN answers: only the free tries get through
    assert codes.count(429) >= 17, codes
    failed = int(
        stack.sql(
            "select count(*) from auth_audit where action = 'pin.failed' "
            f"and actor_user_id = '{m.uid}'"
        )
    )
    assert failed == compared
    assert int(stack.sql(f"select failures from user_pin where user_id = '{m.uid}'")) == failed
    # the right PIN is refused while the wait lasts, and nothing was ever opened
    status, _ = js(m.client, "POST", f"/api/v1/nodes/{folder}/unlock", {"pin": PIN})
    assert status == 429

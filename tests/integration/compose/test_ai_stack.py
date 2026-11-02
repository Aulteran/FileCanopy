"""M15 against the real stack: migration 0018 on PostgreSQL and the budget reservation under
concurrency. SQLite serialises writers, so only PostgreSQL shows that the conditional UPDATE in
`budget.reserve` holds when twenty requests race for a budget that fits six. The script runs inside
the API container (its own database URL and code); no provider is contacted."""

import json

import pytest
from test_stack import Stack

pytestmark = pytest.mark.compose

SCRIPT = """
import json, threading
from app.ai import budget
from app.config import load_settings
from app.db.engine import make_engine
from sqlalchemy.orm import sessionmaker

sessions = sessionmaker(make_engine(load_settings()), expire_on_commit=False)
KEY = "race-test"
with sessions() as db:
    budget.ensure(db, KEY, 1_000_000)
wins = []
gate = threading.Barrier(20)

def worker():
    gate.wait()
    with sessions() as db:
        wins.append(budget.reserve(db, KEY, 150_000, 1_000_000) is not None)

threads = [threading.Thread(target=worker) for _ in range(20)]
[t.start() for t in threads]
[t.join() for t in threads]
print(json.dumps({"wins": sum(wins)}))
"""


@pytest.fixture
def stack(app_stack: Stack) -> Stack:
    return app_stack


def test_budget_reservations_never_pass_the_cap_on_postgres(stack: Stack) -> None:
    assert stack.sql("select version_num from alembic_version") == "0020"
    r = stack.compose("exec", "-T", "api", "python", "-c", SCRIPT)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["wins"] == 6  # six times 150k fit in 1M; a seventh would pass the cap
    reserved = stack.sql("select reserved_micro from ai_budget where scope_key = 'race-test'")
    assert int(reserved) == 900_000
    # the database refuses a second instance provider whatever the application does
    q = (
        "insert into ai_provider (id, scope, kind, privacy_mode, label, endpoint, model, "
        "price_in_micro, price_out_micro, enabled, created_at) values "
        "(gen_random_uuid(), 'instance', 'ollama', 'local', '', '', 'm', 0, 0, false, now())"
    )
    stack.sql(q)
    with pytest.raises(AssertionError):
        stack.sql(q)
    stack.sql("delete from ai_provider where scope = 'instance'")
    stack.sql("delete from ai_budget where scope_key = 'race-test'")

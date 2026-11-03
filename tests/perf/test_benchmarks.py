"""M23 benchmarks. Off by default (`FILECANOPY_PERF=1` turns them on); they print their numbers and
fail only on a generous budget, so they catch an order-of-magnitude regression, not noise.

Honest scope: one process, in-memory SQLite, synthetic names. The PostgreSQL behaviour behind
nginx is exercised by the Compose suites (`test_hardening_stack.py`); these numbers show how the
policy and listing code scale with tree size, which is where an accidental O(n^2) would hide.
Results of the reference run are recorded in docs/perf/M23-benchmarks.md.
"""

import os
import statistics
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from test_acl import Fx
from test_auth import World
from test_files import Api

from app.db.models import Node
from app.policy import engine
from app.policy.model import Actor

pytestmark = pytest.mark.skipif(
    os.environ.get("FILECANOPY_PERF") != "1",
    reason="set FILECANOPY_PERF=1 to run benchmarks",
)

DIRS = 20
FILES_PER_DIR = 1000
Tree = tuple[Fx, list[uuid.UUID], list[uuid.UUID]]


def timed(fn: Callable[[], object], repeat: int = 5) -> float:
    """Median milliseconds over `repeat` runs."""
    runs = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t) * 1000)
    return statistics.median(runs)


@pytest.fixture
def big(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tree:
    fx = Fx(World(tmp_path, monkeypatch))
    dirs: list[uuid.UUID] = []
    files: list[uuid.UUID] = []
    with fx.w.ctx.sessions() as db:
        root = db.get(Node, fx.alice_root)
        assert root is not None
        now = root.created_at
        for d in range(DIRS):
            did = uuid.uuid4()
            db.add(
                Node(
                    id=did,
                    root_id=root.root_id,
                    parent_id=root.id,
                    name=f"dir{d:02}",
                    kind="dir",
                    lineage=f"{root.lineage}{did}/",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            dirs.append(did)
        db.flush()
        for d, did in enumerate(dirs):
            for i in range(FILES_PER_DIR):
                fid = uuid.uuid4()
                db.add(
                    Node(
                        id=fid,
                        root_id=root.root_id,
                        parent_id=did,
                        name=f"file-{d:02}-{i:04}.txt",
                        kind="file",
                        lineage=f"{root.lineage}{did}/{fid}/",
                        status="active",
                        size=10,
                        created_at=now,
                        updated_at=now,
                    )
                )
                files.append(fid)
            db.flush()
        db.commit()
    fx.grant(fx.alice, dirs[0], "bob@example.com", "viewer")
    return fx, dirs, files


def test_listing_a_thousand_entry_folder(big: Tree) -> None:
    fx, dirs, _ = big
    alice = Api(fx, "alice")
    ms = timed(lambda: alice.get(f"/nodes/{dirs[3]}/children"))
    print(f"\nlist 1,000-entry folder: {ms:.0f} ms (median)")
    assert ms < 5000


def test_policy_decisions_scale_linearly(big: Tree) -> None:
    fx, _, files = big
    with fx.w.ctx.sessions() as db:
        owner = Actor(fx.uid("alice@example.com"))
        stranger = Actor(fx.uid("dave@example.com"))
        small, large = files[:200], files[:2000]
        t_small = timed(lambda: engine.filter_visible(db, owner, small), 3)
        t_large = timed(lambda: engine.filter_visible(db, owner, large), 3)
        t_denied = timed(lambda: engine.filter_visible(db, stranger, large), 3)
    print(
        f"\nfilter_visible: 200 ids {t_small:.0f} ms, 2,000 ids {t_large:.0f} ms "
        f"(owner), 2,000 ids {t_denied:.0f} ms (stranger)"
    )
    # ten times the ids must cost roughly ten times as much, not a hundred
    assert t_large < max(t_small, 1) * 40
    assert t_large < 20_000 and t_denied < 20_000


def test_filename_search_in_a_twenty_thousand_file_tree(big: Tree) -> None:
    fx, _, _ = big
    alice = Api(fx, "alice")
    ms = timed(lambda: alice.get("/search", params={"q": "file-07-0042"}), 3)
    print(f"\nfilename search over {DIRS * FILES_PER_DIR:,} files: {ms:.0f} ms (median)")
    assert ms < 10_000

"""M07a startup recovery for trash, restore, permanent delete and cross-root moves: a crash is
simulated by leaving the filesystem and the journal exactly as an interrupted process would, then
running `recover_operations`. A completed rename is rolled forward, one that never started is rolled
back, anything else is `reconcile_needed`, and no file is ever lost."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from test_acl import Fx
from test_auth import World
from test_files import disk, nid, node_row
from test_trash import P, trash_dir, trash_it

from app.db.models import FileEvent, FileOperation, Node, TrashItem
from app.domain import fileops, trash
from app.storage import files


@pytest.fixture
def make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., World]:
    def factory(**overrides: Any) -> World:
        return World(tmp_path, monkeypatch, **overrides)

    return factory


@pytest.fixture
def fx(make_world: Callable[..., World]) -> Fx:
    # long sessions: some tests move the clock by months
    return Fx(make_world(session_idle_seconds=10**9, session_absolute_seconds=10**9))


def recover(fx: Fx) -> dict[str, int]:
    return fileops.recover_operations(
        fx.w.ctx.sessions, fx.w.settings.model_copy(update={"operation_stale_seconds": 0})
    )


def journal(
    fx: Fx, action: str, node: uuid.UUID | None, detail: dict[str, Any], who: str
) -> uuid.UUID:
    with fx.w.ctx.sessions() as db:
        now = fx.w.ctx.sessions and __import__("app.auth.clock", fromlist=["utcnow"]).utcnow()
        op = FileOperation(
            actor_user_id=fx.uid(who),
            action=action,
            status="planned",
            request_hash="h",
            node_id=node,
            detail=detail,
            created_at=now,
            updated_at=now,
        )
        db.add(op)
        db.commit()
        return op.id


def set_status(fx: Fx, node: uuid.UUID, status: str) -> None:
    with fx.w.ctx.sessions() as db:
        db.execute(update(Node).where(Node.id == node).values(status=status))
        db.commit()


def op_row(fx: Fx, op: uuid.UUID) -> FileOperation:
    with fx.w.ctx.sessions() as db:
        row = db.get(FileOperation, op)
        assert row is not None
        db.expunge(row)
        return row


def item_dir(fx: Fx, node: uuid.UUID, trash_id: uuid.UUID) -> Any:
    return trash_dir(fx) / str(node_row(fx, node).root_id) / str(trash_id)


def trash_detail(fx: Fx, node: uuid.UUID, name: str, trash_id: uuid.UUID) -> dict[str, Any]:
    return {
        "src_parent": str(fx.alice_root),
        "src_name": name,
        "trash_id": str(trash_id),
        "reason": "deleted",
        "purge_after": None,
        "src_ino": (disk(fx, node)).stat().st_ino,
    }


# ------------------------------------------------------------------ trash


def test_trash_crash_before_the_rename_rolls_back(fx: Fx) -> None:
    f = nid(P(fx, "alice").upload(fx.alice_root, "a.txt", b"x"))
    tid = uuid.uuid4()
    detail = trash_detail(fx, f, "a.txt", tid)
    set_status(fx, f, "busy")
    op = journal(fx, "trash", f, detail, "alice@example.com")
    counts = recover(fx)
    assert counts["rolled_back"] == 1
    assert node_row(fx, f).status == "active" and disk(fx, f).read_bytes() == b"x"
    assert op_row(fx, op).status == "rolled_back"


def test_trash_crash_after_the_rename_rolls_forward(fx: Fx) -> None:
    f = nid(P(fx, "alice").upload(fx.alice_root, "a.txt", b"x"))
    tid = uuid.uuid4()
    detail = trash_detail(fx, f, "a.txt", tid)
    trash.ensure_trash_base(fx.w.settings.library_path)
    target = item_dir(fx, f, tid)
    target.mkdir(parents=True)
    disk(fx, f).rename(target / "a.txt")
    set_status(fx, f, "busy")
    op = journal(fx, "trash", f, detail, "alice@example.com")
    assert recover(fx)["committed"] == 1
    row = node_row(fx, f)
    assert row.status == "trashed" and row.trash_item_id == tid
    me = P(fx, "alice")
    listed = me.items()
    assert [i["id"] for i in listed] == [str(tid)] and listed[0]["name"] == "a.txt"
    assert op_row(fx, op).status == "committed"
    assert me.restore(str(tid)).status_code == 200 and disk(fx, f).read_bytes() == b"x"
    events = [e.details.get("recovered") for e in fx.w.db_rows(FileEvent) if e.action == "trashed"]
    assert events == [True]


def test_trash_crash_that_cannot_be_decided_is_flagged_not_guessed(fx: Fx) -> None:
    f = nid(P(fx, "alice").upload(fx.alice_root, "a.txt", b"x"))
    detail = trash_detail(fx, f, "a.txt", uuid.uuid4())
    set_status(fx, f, "busy")
    journal(fx, "trash", f, detail, "alice@example.com")
    base = disk(fx, f)
    base.rename(base.with_name("kept-aside.txt"))  # keeps the old inode number in use
    base.write_bytes(b"a different file")  # a new inode under the old name
    assert recover(fx)["reconcile_needed"] == 1
    assert node_row(fx, f).status == "reconcile_needed"
    assert disk(fx, f).read_bytes() == b"a different file"


# ------------------------------------------------------------------ restore


def trashed(fx: Fx, name: str = "a.txt") -> tuple[uuid.UUID, str]:
    me = P(fx, "alice")
    f = nid(me.upload(fx.alice_root, name, b"x"))
    item = trash_it(me, f)["item"]["id"]
    return f, item


def mark_item_busy(fx: Fx, item: str) -> None:
    with fx.w.ctx.sessions() as db:
        db.execute(update(TrashItem).where(TrashItem.id == uuid.UUID(item)).values(status="busy"))
        db.commit()


def test_restore_crash_before_and_after_the_rename(fx: Fx) -> None:
    f, item = trashed(fx)
    source = item_dir(fx, f, uuid.UUID(item)) / "a.txt"
    detail = {
        "trash_id": item,
        "dst_parent": str(fx.alice_root),
        "dst_name": "a.txt",
        "src_ino": source.stat().st_ino,
    }
    mark_item_busy(fx, item)
    op = journal(fx, "restore", f, detail, "alice@example.com")
    assert recover(fx)["rolled_back"] == 1
    assert source.exists() and op_row(fx, op).status == "rolled_back"
    with fx.w.ctx.sessions() as db:
        item_row = db.get(TrashItem, uuid.UUID(item))
        assert item_row is not None and item_row.status == "in_trash"
    # now the rename happened but the database step did not
    mark_item_busy(fx, item)
    source.rename(disk(fx, fx.alice_root) / "a.txt")
    op2 = journal(fx, "restore", f, detail, "alice@example.com")
    assert recover(fx)["committed"] == 1
    assert node_row(fx, f).status == "active" and op_row(fx, op2).status == "committed"
    assert disk(fx, f).read_bytes() == b"x"
    with fx.w.ctx.sessions() as db:
        item_row = db.get(TrashItem, uuid.UUID(item))
        assert item_row is not None and item_row.status == "restored"


def test_restore_crash_with_the_file_gone_everywhere_is_flagged(fx: Fx) -> None:
    f, item = trashed(fx)
    source = item_dir(fx, f, uuid.UUID(item)) / "a.txt"
    detail = {
        "trash_id": item,
        "dst_parent": str(fx.alice_root),
        "dst_name": "a.txt",
        "src_ino": source.stat().st_ino,
    }
    mark_item_busy(fx, item)
    journal(fx, "restore", f, detail, "alice@example.com")
    source.unlink()
    assert recover(fx)["reconcile_needed"] == 1
    with fx.w.ctx.sessions() as db:
        item_row = db.get(TrashItem, uuid.UUID(item))
        assert item_row is not None and item_row.status == "reconcile_needed"
    # a flagged item is never retried by itself: not restorable, never auto-purged
    me = P(fx, "alice")
    assert me.restore(item).status_code == 409
    fx.w.advance(days=400)
    assert trash.purge_due(fx.w.ctx.sessions, fx.w.settings)["purged"] == 0


# ------------------------------------------------------------------ permanent delete


def test_purge_crash_states(fx: Fx) -> None:
    f, item = trashed(fx)
    folder = item_dir(fx, f, uuid.UUID(item))
    mark_item_busy(fx, item)
    op = journal(
        fx,
        "purge",
        f,
        {"trash_id": item, "step": "claimed", "prior": "in_trash"},
        "alice@example.com",
    )
    assert recover(fx)["rolled_back"] == 1  # deletion never began: nothing is lost
    assert folder.exists() and op_row(fx, op).status == "rolled_back"
    with fx.w.ctx.sessions() as db:
        item_row = db.get(TrashItem, uuid.UUID(item))
        assert item_row is not None and item_row.status == "in_trash"
    # deletion was under way and the entry is still there: a person decides
    mark_item_busy(fx, item)
    journal(
        fx,
        "purge",
        f,
        {"trash_id": item, "step": "deleting", "prior": "in_trash"},
        "alice@example.com",
    )
    assert recover(fx)["reconcile_needed"] == 1 and folder.exists()
    # deletion finished and only the database step was lost: roll forward
    with fx.w.ctx.sessions() as db:
        db.execute(update(TrashItem).where(TrashItem.id == uuid.UUID(item)).values(status="busy"))
        db.commit()
    (folder / "a.txt").unlink()
    folder.rmdir()
    journal(
        fx,
        "purge",
        f,
        {"trash_id": item, "step": "deleting", "prior": "in_trash"},
        "alice@example.com",
    )
    assert recover(fx)["committed"] == 1
    assert node_row(fx, f).status == "purged"
    assert [e.details["how"] for e in fx.w.db_rows(FileEvent) if e.action == "purged"] == [
        "recovered"
    ]


def test_a_failed_delete_forever_flags_the_item_instead_of_pretending(
    fx: Fx, monkeypatch: Any
) -> None:
    me = P(fx, "alice")
    f, item = trashed(fx)

    def fail(*a: Any, **k: Any) -> int:
        raise OSError(5, "disk error")

    monkeypatch.setattr(files, "remove_tree", fail)
    token = me.items()[0]["confirm_token"]
    r = me.purge(item, token)
    assert r.status_code == 503
    with fx.w.ctx.sessions() as db:
        row = db.get(TrashItem, uuid.UUID(item))
        assert row is not None and row.status == "reconcile_needed"
    assert (
        node_row(fx, f).status == "trashed"
        and (item_dir(fx, f, uuid.UUID(item)) / "a.txt").exists()
    )


# ------------------------------------------------------------------ cross-root move


def xmove_detail(fx: Fx, node: uuid.UUID, tid: uuid.UUID, dst_name: str) -> dict[str, Any]:
    return {
        "src_parent": str(fx.alice_root),
        "src_name": "m.txt",
        "dst_parent": str(fx.shared_sub),
        "dst_name": dst_name,
        "trash_id": str(tid),
        "src_ino": disk(fx, node).stat().st_ino,
        "step": "copying",
    }


def test_cross_root_crash_after_the_copy_leaves_both_and_flags_it(fx: Fx) -> None:
    f = nid(P(fx, "alice").upload(fx.alice_root, "m.txt", b"payload"))
    detail = xmove_detail(fx, f, uuid.uuid4(), "m.txt")
    copy_at = disk(fx, fx.shared_sub) / "m.txt"
    copy_at.write_bytes(b"payload")  # the verified copy landed; the source was not touched yet
    set_status(fx, f, "busy")
    op = journal(fx, "xmove", f, detail, "alice@example.com")
    assert recover(fx)["reconcile_needed"] == 1
    assert disk(fx, f).read_bytes() == b"payload" and copy_at.read_bytes() == b"payload"
    assert node_row(fx, f).status == "active"  # the original is fully usable again
    assert op_row(fx, op).status == "reconcile_needed"


def test_cross_root_crash_after_the_source_reached_the_trash_puts_it_back(fx: Fx) -> None:
    f = nid(P(fx, "alice").upload(fx.alice_root, "m.txt", b"payload"))
    tid = uuid.uuid4()
    detail = xmove_detail(fx, f, tid, "m.txt")
    copy_at = disk(fx, fx.shared_sub) / "m.txt"
    copy_at.write_bytes(b"payload")
    trash.ensure_trash_base(fx.w.settings.library_path)
    target = item_dir(fx, f, tid)
    target.mkdir(parents=True)
    disk(fx, f).rename(target / "m.txt")  # source is in the trash, database not committed
    set_status(fx, f, "busy")
    op = journal(fx, "xmove", f, detail, "alice@example.com")
    assert recover(fx)["reconcile_needed"] == 1
    back = disk(fx, fx.alice_root) / "m.txt"
    assert back.read_bytes() == b"payload" and copy_at.read_bytes() == b"payload"
    assert node_row(fx, f).status == "active" and op_row(fx, op).status == "reconcile_needed"


def test_cross_root_crash_before_anything_was_written_rolls_back(fx: Fx) -> None:
    f = nid(P(fx, "alice").upload(fx.alice_root, "m.txt", b"payload"))
    detail = xmove_detail(fx, f, uuid.uuid4(), "m.txt")
    set_status(fx, f, "busy")
    journal(fx, "xmove", f, detail, "alice@example.com")
    assert recover(fx)["rolled_back"] == 1
    assert node_row(fx, f).status == "active" and disk(fx, f).read_bytes() == b"payload"


# ------------------------------------------------------------------ failures inside a live call


def test_a_database_failure_after_the_trash_rename_puts_the_file_back(
    fx: Fx, monkeypatch: Any
) -> None:
    me = P(fx, "alice")
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(trash, "_mark_trashed", boom)
    assert me.delete(f, 1).status_code == 500
    monkeypatch.undo()
    assert disk(fx, f).read_bytes() == b"x" and node_row(fx, f).status == "active"
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "trash"]
    assert [o.status for o in ops] == ["rolled_back"]
    assert me.items() == []


def test_an_unrecoverable_failure_after_the_trash_rename_says_reconcile_needed(
    fx: Fx, monkeypatch: Any
) -> None:
    me = P(fx, "alice")
    f = nid(me.upload(fx.alice_root, "a.txt", b"x"))
    real = files.rename_noreplace
    calls = {"n": 0}

    def undo_fails(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            real(*a, **k)
        else:
            raise OSError(5, "cannot put it back")

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(files, "rename_noreplace", undo_fails)
    monkeypatch.setattr(trash, "_mark_trashed", boom)
    assert me.delete(f, 1).status_code == 500
    monkeypatch.undo()
    assert node_row(fx, f).status == "reconcile_needed"
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "trash"]
    assert [o.status for o in ops] == ["reconcile_needed"]
    moved = list(trash_dir(fx).rglob("a.txt"))
    assert len(moved) == 1 and moved[0].read_bytes() == b"x"  # the bytes are safe in the trash


def test_a_database_failure_in_a_cross_root_move_leaves_the_source_in_place(
    fx: Fx, monkeypatch: Any
) -> None:
    me = P(fx, "alice")
    f = nid(me.upload(fx.alice_root, "m.txt", b"payload"))

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(trash, "_mark_trashed", boom)
    assert me.move(f, fx.shared_sub, 1).status_code == 500
    monkeypatch.undo()
    assert disk(fx, f).read_bytes() == b"payload" and node_row(fx, f).status == "active"
    assert not (disk(fx, fx.shared_sub) / "m.txt").exists()  # the copy was removed again
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "xmove"]
    assert [o.status for o in ops] == ["rolled_back"]
    with fx.w.ctx.sessions() as db:
        assert db.scalars(select(Node).where(Node.name == "m.txt", Node.status == "active")).all()


# ------------------------------------------------------------------ review fixes (Opus, M07a)


def test_a_file_added_to_the_source_during_a_cross_root_move_stops_it(
    fx: Fx, monkeypatch: Any
) -> None:
    """The copy cannot contain an entry made after it was taken; it must not go silently to the
    trash with the source."""
    me = P(fx, "alice")
    sub = nid(me.mkdir(fx.alice_root, "Box"))
    me.upload(sub, "a.txt", b"a")
    real = trash._copy_for_move

    def late_write(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        (disk(fx, sub) / "late.txt").write_bytes(b"late")
        return out

    monkeypatch.setattr(trash, "_copy_for_move", late_write)
    r = me.move(sub, fx.shared_sub, 1)
    assert r.status_code == 409, r.text
    monkeypatch.undo()
    assert (disk(fx, sub) / "late.txt").read_bytes() == b"late"
    assert (disk(fx, sub) / "a.txt").read_bytes() == b"a"
    assert node_row(fx, sub).status == "active"
    assert not (disk(fx, fx.shared_sub) / "Box").exists()  # the partial copy was removed
    assert me.items() == []


def test_a_source_that_cannot_be_put_back_keeps_the_copy(fx: Fx, monkeypatch: Any) -> None:
    """The source changed after it reached the trash and its name was taken before it could go
    back: both copies stay and the move says reconcile_needed, never a lost file."""
    me = P(fx, "alice")
    f = nid(me.upload(fx.alice_root, "m.txt", b"payload"))
    real_rename = files.rename_noreplace
    calls = {"n": 0}

    def rename(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            real_rename(*a, **k)  # source into the trash
            return
        raise files.NameExists("taken")  # putting it back fails

    monkeypatch.setattr(trash, "_trash_matches_plan", lambda *a, **k: False)
    monkeypatch.setattr(files, "rename_noreplace", rename)
    r = me.move(f, fx.shared_sub, 1)
    assert r.status_code == 409, r.text
    monkeypatch.undo()
    assert (disk(fx, fx.shared_sub) / "m.txt").read_bytes() == b"payload"  # the copy stays
    in_trash = list(trash_dir(fx).rglob("m.txt"))
    assert len(in_trash) == 1 and in_trash[0].read_bytes() == b"payload"
    assert node_row(fx, f).status == "reconcile_needed"
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "xmove"]
    assert [o.status for o in ops] == ["reconcile_needed"]


def test_a_database_failure_after_the_bytes_were_deleted_is_not_shown_as_in_trash(
    fx: Fx, monkeypatch: Any
) -> None:
    me = P(fx, "alice")
    _, item = trashed(fx)

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(trash, "_finish_purge", boom)
    token = me.items()[0]["confirm_token"]
    assert me.purge(item, token).status_code == 500
    monkeypatch.undo()
    with fx.w.ctx.sessions() as db:
        row = db.get(TrashItem, uuid.UUID(item))
        assert row is not None and row.status == "reconcile_needed"
    ops = [o for o in fx.w.db_rows(FileOperation) if o.action == "purge"]
    assert [o.status for o in ops] == ["reconcile_needed"]
    # a person can still finish the deletion
    token = me.items()[0]["confirm_token"]
    assert me.purge(item, token).status_code == 200


def test_an_item_claimed_without_a_journal_row_is_released_at_startup(fx: Fx) -> None:
    me = P(fx, "alice")
    _, item = trashed(fx)
    mark_item_busy(fx, item)  # crash between the claim and the journal row
    recover(fx)
    assert [i["status"] for i in me.items()] == ["in_trash"]
    assert me.restore(item).status_code == 200

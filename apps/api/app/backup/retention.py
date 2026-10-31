"""Chains, size estimation and pruning (ADR-015 section 4).

A chain is one full run plus the changed-only runs after it; history can only be removed one whole
chain at a time, oldest first, only after a newer run has been written and verified. The newest
`min_chains_kept` chains of every root are never pruned, the chain being extended is one of them,
and the chains of a root that is not active (or whose scan is suspended) are never pruned at all,
so a replaced or empty mount cannot push good backups out.
"""

import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.service import audit
from app.backup.store import BackupConfig
from app.backup.volume import backups_root, folder_usage, inspect_volume
from app.config import Settings
from app.db.models import BackupRun, StorageRoot

log = logging.getLogger("filecanopy.backup")


@dataclass
class Chain:
    root_id: uuid.UUID
    chain_id: str
    started_at: datetime
    size_bytes: int
    runs: int


def list_chains(db: Session) -> list[Chain]:
    """Chains with a verified full run, from `ok` rows that were not pruned."""
    rows = db.scalars(
        select(BackupRun)
        .where(BackupRun.status == "ok", BackupRun.pruned_at.is_(None))
        .order_by(BackupRun.finished_at, BackupRun.seq)
    ).all()
    by_key: dict[tuple[uuid.UUID, str], Chain] = {}
    for r in rows:
        key = (r.root_id, r.chain_id)
        chain = by_key.get(key)
        if chain is None:
            if r.kind != "full":  # an orphaned incremental is not a restorable chain
                continue
            chain = by_key[key] = Chain(r.root_id, r.chain_id, r.finished_at or r.started_at, 0, 0)
        chain.size_bytes += r.size_bytes or 0
        chain.runs += 1
    return sorted(by_key.values(), key=lambda c: c.started_at)


def inactive_roots(db: Session) -> set[uuid.UUID]:
    return {
        r.id
        for r in db.scalars(select(StorageRoot))
        if r.status != "active" or r.scan_state is not None
    }


def protected(
    chains: list[Chain], min_kept: int, frozen_roots: set[uuid.UUID]
) -> set[tuple[uuid.UUID, str]]:
    keep: set[tuple[uuid.UUID, str]] = set()
    per_root: dict[uuid.UUID, list[Chain]] = {}
    for c in chains:
        per_root.setdefault(c.root_id, []).append(c)
    for root_id, items in per_root.items():
        items.sort(key=lambda c: c.started_at)
        keep.update((c.root_id, c.chain_id) for c in items[-min_kept:])
        if root_id in frozen_roots:
            keep.update((c.root_id, c.chain_id) for c in items)
    return keep


@dataclass
class Feasibility:
    ok: bool
    code: str | None
    estimate_bytes: int
    prunable_bytes: int


TAR_OVERHEAD_FIXED = 16 * 1024  # metadata, manifest and listings
TAR_OVERHEAD_PER_ENTRY = 600  # tar header plus manifest line


def estimate_run_bytes(db: Session, root_id: uuid.UUID, raw_bytes: int, entries: int = 0) -> int:
    """Expected archive size: the uncompressed size scaled by the compression ratio the last
    verified run of this root achieved (with a 25% margin, never above 1:1), plus the fixed and
    per-entry overhead. With no earlier run the data is assumed incompressible."""
    overhead = TAR_OVERHEAD_FIXED + TAR_OVERHEAD_PER_ENTRY * entries
    last = db.scalars(
        select(BackupRun)
        .where(
            BackupRun.root_id == root_id,
            BackupRun.status == "ok",
            BackupRun.pruned_at.is_(None),
            BackupRun.uncompressed_bytes > 1_000_000,
        )
        .order_by(BackupRun.finished_at.desc())
    ).first()
    if last is None or not last.size_bytes or not last.uncompressed_bytes:
        return raw_bytes + overhead
    ratio = min(1.0, 1.25 * last.size_bytes / last.uncompressed_bytes)
    return int(raw_bytes * ratio) + overhead


def check_feasible(
    db: Session,
    settings: Settings,
    cfg: BackupConfig,
    *,
    root_id: uuid.UUID,
    kind: str,
    estimate_bytes: int,
) -> Feasibility:
    """Whether a run of this size can be written without breaking the minimum-chains floor."""
    chains = list_chains(db)
    if kind == "full":  # the new chain joins the protected set, which can free an older one
        chains.append(Chain(root_id, "~new", clock.utcnow(), estimate_bytes, 1))
    keep = protected(chains, cfg.min_chains_kept, inactive_roots(db))
    prunable = sum(c.size_bytes for c in chains if (c.root_id, c.chain_id) not in keep)
    vol = inspect_volume(settings)
    free = vol.free_bytes or 0
    if free + prunable < estimate_bytes + settings.backup_free_space_reserve_bytes:
        return Feasibility(False, "BACKUP_VOLUME_TOO_SMALL", estimate_bytes, prunable)
    if cfg.max_size_bytes is not None:
        usage = folder_usage(backups_root(settings))
        if usage - prunable + estimate_bytes > cfg.max_size_bytes:
            return Feasibility(False, "BACKUP_VOLUME_TOO_SMALL", estimate_bytes, prunable)
    return Feasibility(True, None, estimate_bytes, prunable)


def _delete_chain(settings: Settings, db: Session, chain: Chain) -> int:
    base = backups_root(settings)
    rows = db.scalars(
        select(BackupRun).where(
            BackupRun.root_id == chain.root_id,
            BackupRun.chain_id == chain.chain_id,
            BackupRun.pruned_at.is_(None),
        )
    ).all()
    freed = 0
    chain_dirs: set[Path] = set()
    for r in rows:
        if not r.archive_name:
            continue
        archive = base / r.archive_name
        chain_dirs.add(archive.parent)
        for victim in (archive, archive.with_name(archive.name + ".json")):
            try:
                freed += victim.lstat().st_size
                victim.unlink()
            except FileNotFoundError:
                pass
    for d in chain_dirs:
        try:
            d.rmdir()
        except OSError:  # not empty (foreign files) or already gone: leave it
            pass
    now = clock.utcnow()
    db.execute(
        update(BackupRun)
        .where(BackupRun.root_id == chain.root_id, BackupRun.chain_id == chain.chain_id)
        .values(pruned_at=now)
    )
    return freed


def prune_to_limit(db: Session, settings: Settings, cfg: BackupConfig) -> list[Chain]:
    """Removes the oldest unprotected chains until the folder is within `max_size_bytes`. Called
    only after a new run has been verified and recorded."""
    if cfg.max_size_bytes is None:
        return []
    removed: list[Chain] = []
    base = backups_root(settings)
    while True:
        usage = folder_usage(base)
        if usage <= cfg.max_size_bytes:
            break
        chains = list_chains(db)
        keep = protected(chains, cfg.min_chains_kept, inactive_roots(db))
        victims = [c for c in chains if (c.root_id, c.chain_id) not in keep]
        if not victims:
            break  # over the limit but only protected chains remain: reported as a warning
        victim = victims[0]
        free_before = shutil.disk_usage(base).free
        _delete_chain(settings, db, victim)
        db.flush()
        audit(
            db,
            "backup.pruned",
            root_id=str(victim.root_id),
            chain_id=victim.chain_id,
            reason="size_limit",
            bytes_removed=victim.size_bytes,
            usage_before=usage,
            usage_after=folder_usage(base),
            free_before=free_before,
            free_after=shutil.disk_usage(base).free,
        )
        db.commit()
        removed.append(victim)
        log.info("pruned an old backup chain (%s)", victim.root_id)
    return removed

"""Operator recovery for sign-in (M12b), run inside the API container:

    docker compose exec api python -m app.auth.cli status
    docker compose exec api python -m app.auth.cli oidc-only off

`oidc-only off` clears the instance switch in the database so password sign-in works again, and
writes an audit row. It needs the same database access the API has; it is the break-glass for an
identity provider that is down or wrongly configured. (Setting FILECANOPY_FORCE_LOCAL_LOGIN=true
and restarting does the same without touching the database.) Output never contains secrets.
"""

import argparse
import sys
from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock, oidc_only
from app.auth.service import audit
from app.config import Settings, load_settings
from app.db.engine import make_engine
from app.db.models import AppSetting


def run(
    argv: Sequence[str],
    settings: Settings,
    sessions: "sessionmaker[Session]",
    out: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.auth.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show whether OIDC-only mode is switched on and in force")
    mode = sub.add_parser("oidc-only", help="change the OIDC-only switch")
    mode.add_argument("state", choices=["off"], help="only 'off' is offered here, on purpose")
    args = parser.parse_args(list(argv))
    with sessions() as db:
        if args.cmd == "status":
            out(f"oidc configured: {settings.oidc_enabled}")
            out(f"oidc-only switch: {'on' if oidc_only.setting_on(db) else 'off'}")
            out(f"oidc-only in force: {oidc_only.effective(db, settings)}")
            out(f"force local login (environment): {settings.force_local_login}")
            return 0
        row = db.get(AppSetting, oidc_only.SETTING_KEY)
        was_on = row is not None and bool(row.value)
        if row is None:
            db.add(AppSetting(key=oidc_only.SETTING_KEY, value=False))
        else:
            row.value = False
        audit(db, "auth.oidc_only_off", via="cli", was_on=was_on, at=clock.utcnow().isoformat())
        db.commit()
        out("oidc-only switch is off; password sign-in works again" if was_on else "already off")
        return 0


def main() -> int:
    settings = load_settings()
    return run(sys.argv[1:], settings, sessionmaker(make_engine(settings), expire_on_commit=False))


if __name__ == "__main__":
    raise SystemExit(main())

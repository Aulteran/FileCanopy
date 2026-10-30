from sqlalchemy import Engine, create_engine

from app.config import Settings


def make_engine(settings: Settings) -> Engine:
    url = settings.database_url.get_secret_value()
    # pool_pre_ping survives database restarts.
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})

from alembic import context

from app.db.models import Base

# The runner (app.db.migrate) supplies a connection and holds the advisory lock.
connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations with `python -m app.db.migrate`")

context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()

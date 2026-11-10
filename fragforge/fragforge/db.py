from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def init_db(url: str | None = None):
    """Create the engine and tables. Schema is portable across SQLite/Postgres."""
    global _engine, _Session
    from . import models  # noqa: F401  (register tables)

    url = url or get_settings().db_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _Session = sessionmaker(_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _add_missing_columns()
    return _engine


def _add_missing_columns():
    """Tiny forward-only migration for existing databases (Alembic comes with the full stack)."""
    from sqlalchemy import inspect, text

    insp = inspect(_engine)
    cols = {c["name"] for c in insp.get_columns("media_assets")}
    if "profile_config" not in cols:
        with _engine.begin() as conn:
            conn.execute(text("ALTER TABLE media_assets ADD COLUMN profile_config JSON"))


def session():
    if _Session is None:
        init_db()
    return _Session()

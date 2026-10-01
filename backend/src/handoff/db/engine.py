"""SQLite engine and session handling (DATABASE §37, §45, §48, §49)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from handoff.errors import DatabaseInitError
from handoff.paths import AppPaths

log = logging.getLogger(__name__)


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One short transaction: commit on success, roll back on any error."""
        s = Session(self.engine, expire_on_commit=False)
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _configure(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        # Take transaction control away from pysqlite so DDL is transactional too
        # (documented SQLAlchemy recipe), then apply the pragmas.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA busy_timeout = 30000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN")


def open_database(paths: AppPaths) -> Database:
    """Open (creating if absent) the single HandOff database.

    On failure this raises; it never falls back to a different database file.
    """
    engine = create_engine(
        f"sqlite:///{paths.db_path}", connect_args={"check_same_thread": False, "timeout": 30}
    )
    _configure(engine)
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql("PRAGMA quick_check").scalar()
            if result != "ok":
                raise DatabaseInitError("The HandOff database failed its integrity check.")
    except DatabaseInitError:
        engine.dispose()
        raise
    except DatabaseError as exc:
        engine.dispose()
        log.error("Could not open database %s: %s", paths.db_path, exc)
        raise DatabaseInitError(
            "HandOff could not open its database. Its data folder may be damaged."
        ) from exc
    return Database(engine)

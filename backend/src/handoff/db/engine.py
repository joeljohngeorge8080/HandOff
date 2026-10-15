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
        self._immediate = engine.execution_options(handoff_immediate=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One short transaction: commit on success, roll back on any error."""
        with self._open(self.engine) as s:
            yield s

    @contextmanager
    def write_session(self) -> Iterator[Session]:
        """Like `session`, but takes the write lock when the transaction begins.

        Use it where a transaction reads and then writes while other threads write too (the
        parallel transfers of ADR-063). A deferred transaction cannot be upgraded to a writer
        once another writer has committed; SQLite then fails at once with "database is locked",
        whatever the busy timeout. Do not open another session inside one: it would wait for
        this one's lock.
        """
        with self._open(self._immediate) as s:
            yield s

    @staticmethod
    @contextmanager
    def _open(engine: Engine) -> Iterator[Session]:
        s = Session(engine, expire_on_commit=False)
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
        immediate = conn.get_execution_options().get("handoff_immediate")
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


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

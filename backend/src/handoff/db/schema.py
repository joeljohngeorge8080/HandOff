"""Schema creation, versioning and the in-app upgrade routine (DATABASE §47, §48).

Alembic is deliberately not used. Upgrades are explicit functions keyed by the
version they upgrade *from*; each runs in one transaction together with the version
bump, so a failed upgrade leaves the existing database untouched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from sqlalchemy import Connection, inspect, select

from handoff.db.engine import Database
from handoff.db.models import Base, Setting, utcnow
from handoff.errors import DatabaseInitError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REQUIRED_TABLES = frozenset(
    {"devices", "files", "transfers", "transfer_files", "settings", "audit_logs"}
)

Migration = Callable[[Connection], None]
MIGRATIONS: dict[int, Migration] = {}


def _read_version(conn: Connection) -> int | None:
    row = conn.execute(select(Setting.value).where(Setting.key == "schema_version")).first()
    return None if row is None else int(row[0])


def _write_version(conn: Connection, version: int) -> None:
    now = utcnow()
    updated = conn.execute(
        Setting.__table__.update()  # type: ignore[attr-defined]
        .where(Setting.key == "schema_version")
        .values(value=str(version), updated_at=now)
    )
    if updated.rowcount == 0:
        conn.execute(
            Setting.__table__.insert().values(  # type: ignore[attr-defined]
                key="schema_version", value=str(version), value_type="int", updated_at=now
            )
        )


def init_schema(
    db: Database,
    *,
    target: int = SCHEMA_VERSION,
    migrations: Mapping[int, Migration] | None = None,
) -> int:
    """Create missing tables, upgrade an older database, validate. Returns the version."""
    migrations = MIGRATIONS if migrations is None else migrations
    existing = set(inspect(db.engine).get_table_names())

    # create_all only adds missing tables; it never alters or drops anything.
    Base.metadata.create_all(db.engine)

    with db.engine.begin() as conn:
        version = _read_version(conn)
        if version is None:
            version = 1 if existing else target
            _write_version(conn, version)

    if version > target:
        raise DatabaseInitError(
            f"This database was created by a newer HandOff (schema {version}); "
            f"this version understands schema {target}."
        )

    while version < target:
        migrate = migrations.get(version)
        if migrate is None:
            raise DatabaseInitError(f"No upgrade path from database schema {version}.")
        log.info("Upgrading database schema %s -> %s", version, version + 1)
        with db.engine.begin() as conn:
            migrate(conn)
            _write_version(conn, version + 1)
        version += 1

    missing = REQUIRED_TABLES - set(inspect(db.engine).get_table_names())
    if missing:
        raise DatabaseInitError(f"Database is missing required tables: {sorted(missing)}")
    return version

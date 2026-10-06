"""SQLAlchemy models for the six Phase-1 tables (DATABASE §8). Metadata only: no binaries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    TypeDecorator,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_TERMINAL_SQL = "('completed','failed','partially_completed')"


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """ISO 8601 UTC with a trailing Z (API §37)."""
    return None if dt is None else dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class UTCDateTime(TypeDecorator[datetime]):
    """Stores naive UTC, returns timezone-aware UTC. Naive inputs are a bug, so they raise."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to the database; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint("status IN ('available','connecting','connected','offline')"),
        Index("ix_devices_status", "status"),
        Index("ix_devices_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    device_name: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    last_ip: Mapped[str | None] = mapped_column(Text)
    port: Mapped[int | None] = mapped_column(Integer)
    # ADR-048. The private key is never stored in the database.
    public_key: Mapped[str | None] = mapped_column(Text)
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("source IN ('imported','received')"),
        CheckConstraint("size_bytes >= 0"),
        Index("ix_files_source", "source"),
        Index("ix_files_created_at", "created_at"),
        Index("ix_files_deleted_at", "deleted_at"),
        Index("ix_files_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    # Display name; made unique among active files with name(1).ext (ADR-020, ADR-053).
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_name: Mapped[str] = mapped_column(Text, nullable=False)
    extension: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("direction IN ('sent','received')"),
        CheckConstraint(
            "status IN ('created','validating','accepted','transferring',"
            "'completed','failed','partially_completed')"
        ),
        CheckConstraint("file_count >= 0 AND total_size_bytes >= 0 AND bytes_transferred >= 0"),
        Index("ix_transfers_status", "status"),
        Index("ix_transfers_direction", "direction"),
        Index("ix_transfers_created_at", "created_at"),
        Index("ix_transfers_source_device_id", "source_device_id"),
        Index("ix_transfers_destination_device_id", "destination_device_id"),
        # DATABASE §44 / ADR-015: at most one non-terminal transfer exists at any time.
        Index(
            "uq_transfers_one_active",
            text("1"),
            unique=True,
            sqlite_where=text(f"status NOT IN {_TERMINAL_SQL}"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL means "this device"; the local device has no row in `devices`.
    source_device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.device_id"))
    destination_device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.device_id"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_size_bytes: Mapped[int | None] = mapped_column(Integer)
    bytes_transferred: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    files: Mapped[list[TransferFile]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan", order_by="TransferFile.id"
    )


class TransferFile(Base):
    __tablename__ = "transfer_files"
    __table_args__ = (
        CheckConstraint("status IN ('pending','transferring','completed','failed')"),
        CheckConstraint("size_bytes >= 0"),
        Index("ix_transfer_files_transfer_id", "transfer_id"),
        Index("ix_transfer_files_file_id", "file_id"),
        Index("ix_transfer_files_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.id"), nullable=False)
    # NULL for a received file that failed validation and never became a managed file.
    file_id: Mapped[str | None] = mapped_column(ForeignKey("files.id"))
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    transfer: Mapped[Transfer] = relationship(back_populates="files")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class AuditLog(Base):
    """No foreign keys on purpose: audit history must outlive files, devices and transfers."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_event_type", "event_type"),
        Index("ix_audit_logs_device_id", "device_id"),
        Index("ix_audit_logs_file_id", "file_id"),
        Index("ix_audit_logs_transfer_id", "transfer_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(Text)
    transfer_id: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[str | None] = mapped_column("metadata", Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

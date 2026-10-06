"""Repository layer: the only code that builds queries (DATABASE §46).

Repositories take the caller's Session and never commit; the service that owns the
logical state change decides the transaction boundary (DATABASE §41).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from handoff.db.models import AuditLog, Device, File, Setting, Transfer, TransferFile, utcnow
from handoff.errors import HandOffError
from handoff.transfer.states import TERMINAL_STATUSES, TransferStatus, assert_transition

SettingValue = bool | int | str

_TERMINAL_VALUES = [s.value for s in TERMINAL_STATUSES]


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    @staticmethod
    def _decode(row: Setting) -> SettingValue:
        if row.value_type == "bool":
            return row.value == "true"
        if row.value_type == "int":
            return int(row.value)
        return row.value

    def get(self, key: str, default: SettingValue | None = None) -> SettingValue | None:
        row = self.s.get(Setting, key)
        return default if row is None else self._decode(row)

    def set(self, key: str, value: SettingValue) -> None:
        if isinstance(value, bool):
            encoded, vtype = ("true" if value else "false"), "bool"
        elif isinstance(value, int):
            encoded, vtype = str(value), "int"
        else:
            encoded, vtype = value, "str"
        row = self.s.get(Setting, key)
        if row is None:
            self.s.add(Setting(key=key, value=encoded, value_type=vtype, updated_at=utcnow()))
        else:
            row.value, row.value_type, row.updated_at = encoded, vtype, utcnow()

    def all(self) -> dict[str, SettingValue]:
        return {r.key: self._decode(r) for r in self.s.scalars(select(Setting))}


class DeviceRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get_by_device_id(self, device_id: str) -> Device | None:
        return self.s.scalar(select(Device).where(Device.device_id == device_id))

    def list_all(self) -> list[Device]:
        return list(self.s.scalars(select(Device).order_by(Device.device_name, Device.id)))

    def upsert(
        self,
        device_id: str,
        device_name: str,
        platform: str,
        *,
        last_ip: str | None = None,
        port: int | None = None,
        public_key: str | None = None,
        status: str = "available",
    ) -> Device:
        now = utcnow()
        dev = self.get_by_device_id(device_id)
        if dev is None:
            dev = Device(
                device_id=device_id,
                device_name=device_name,
                platform=platform,
                last_ip=last_ip,
                port=port,
                public_key=public_key,
                is_trusted=False,
                status=status,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            self.s.add(dev)
        else:
            dev.device_name, dev.platform, dev.status = device_name, platform, status
            dev.last_ip, dev.port = last_ip or dev.last_ip, port or dev.port
            dev.public_key = public_key or dev.public_key
            dev.last_seen_at = dev.updated_at = now
        self.s.flush()
        return dev

    def set_trusted(self, device_id: str, trusted: bool) -> None:
        dev = self.get_by_device_id(device_id)
        if dev is None:
            raise HandOffError("DEVICE_NOT_FOUND", "Unknown device.")
        dev.is_trusted = trusted
        dev.updated_at = utcnow()


class FileRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def add(self, file: File) -> File:
        self.s.add(file)
        self.s.flush()
        return file

    def get(self, file_id: str) -> File | None:
        return self.s.get(File, file_id)

    def get_active(self, file_id: str) -> File | None:
        f = self.s.get(File, file_id)
        return None if f is None or f.deleted_at is not None else f

    def list_active(self) -> list[File]:
        stmt = select(File).where(File.deleted_at.is_(None)).order_by(File.created_at, File.id)
        return list(self.s.scalars(stmt))

    def active_names(self) -> list[str]:
        return list(self.s.scalars(select(File.original_name).where(File.deleted_at.is_(None))))

    def mark_deleted(self, file: File, when: datetime | None = None) -> None:
        file.deleted_at = file.updated_at = when or utcnow()


class TransferRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def add(self, transfer: Transfer, files: list[TransferFile] | None = None) -> Transfer:
        """Insert a transfer (and its files). Enforces one active transfer at DB level."""
        transfer.files = files or []
        self.s.add(transfer)
        try:
            self.s.flush()
        except IntegrityError as exc:
            if "uq_transfers_one_active" in str(exc.orig):
                raise HandOffError("INVALID_STATE", "Another transfer is already active.") from exc
            raise
        return transfer

    def get(self, transfer_id: str) -> Transfer | None:
        stmt = (
            select(Transfer).where(Transfer.id == transfer_id).options(selectinload(Transfer.files))
        )
        return self.s.scalar(stmt)

    def get_active(self) -> Transfer | None:
        stmt = (
            select(Transfer)
            .where(Transfer.status.not_in(_TERMINAL_VALUES))
            .options(selectinload(Transfer.files))
        )
        return self.s.scalar(stmt)

    def list_history(self, limit: int = 50, offset: int = 0) -> list[Transfer]:
        stmt = (
            select(Transfer)
            .order_by(Transfer.created_at.desc(), Transfer.id.desc())
            .limit(limit)
            .offset(offset)
            .options(selectinload(Transfer.files))
        )
        return list(self.s.scalars(stmt))

    def set_status(
        self,
        transfer: Transfer,
        new: TransferStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> Transfer:
        assert_transition(TransferStatus(transfer.status), new)
        now = now or utcnow()
        transfer.status = new.value
        if new is TransferStatus.TRANSFERRING and transfer.started_at is None:
            transfer.started_at = now
        if new in TERMINAL_STATUSES:
            transfer.completed_at = now
        if error_code is not None:
            transfer.error_code, transfer.error_message = error_code, error_message
        return transfer

    def fail_all_non_terminal(self, error_code: str, error_message: str) -> list[str]:
        """Crash recovery: anything left unfinished can never be reported as successful."""
        stmt = select(Transfer).where(Transfer.status.not_in(_TERMINAL_VALUES))
        failed: list[str] = []
        for t in self.s.scalars(stmt):
            self.set_status(
                t, TransferStatus.FAILED, error_code=error_code, error_message=error_message
            )
            for tf in t.files:
                if tf.status in ("pending", "transferring"):
                    tf.status, tf.failure_code = "failed", error_code
                    tf.failure_message = error_message
            failed.append(t.id)
        return failed

    def delete_terminal_before(self, cutoff: datetime) -> int:
        """History retention. Only finished transfers; audit_logs are never touched."""
        ids = list(
            self.s.scalars(
                select(Transfer.id).where(
                    Transfer.status.in_(_TERMINAL_VALUES), Transfer.created_at < cutoff
                )
            )
        )
        if not ids:
            return 0
        self.s.execute(delete(TransferFile).where(TransferFile.transfer_id.in_(ids)))
        self.s.execute(delete(Transfer).where(Transfer.id.in_(ids)))
        return len(ids)


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.s = session

    def list(
        self,
        *,
        event_type: str | None = None,
        transfer_id: str | None = None,
        file_id: str | None = None,
        device_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if event_type:
            stmt = stmt.where(AuditLog.event_type == event_type)
        if transfer_id:
            stmt = stmt.where(AuditLog.transfer_id == transfer_id)
        if file_id:
            stmt = stmt.where(AuditLog.file_id == file_id)
        if device_id:
            stmt = stmt.where(AuditLog.device_id == device_id)
        return list(self.s.scalars(stmt))

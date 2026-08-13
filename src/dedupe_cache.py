"""SQLite-backed state for idempotent attachment processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlite_utils


@dataclass(frozen=True)
class CacheEntry:
    """An attachment that is already being processed or has been processed."""

    message_id: str
    status: str
    task_id: str | None
    checksum: str
    paperless_document_id: int | None
    claimed_at: str | None


class DedupeCache:
    """Atomically claim attachments before uploading them to Paperless."""

    TABLE = "processed_attachments"
    STALE_CLAIM_AFTER = timedelta(minutes=15)

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite_utils.Database(str(db_path))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db[self.TABLE].create(
            {
                "message_id": str,
                "internet_message_id": str,
                "attachment_id": str,
                "checksum": str,
                "paperless_document_id": int,
                "paperless_task_id": str,
                "status": str,
                "claimed_at": str,
                "processed_at": str,
            },
            pk=("message_id", "attachment_id"),
            if_not_exists=True,
        )

        columns = {
            row[1] for row in self.db.conn.execute(f"PRAGMA table_info({self.TABLE})")
        }
        migrations = {
            "paperless_task_id": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "claimed_at": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in columns:
                self.db.conn.execute(
                    f"ALTER TABLE {self.TABLE} ADD COLUMN {column} {definition}"
                )
        self.db.conn.commit()

    def claim(
        self, *, message_id: str, internet_message_id: str, attachment_id: str
    ) -> CacheEntry | None:
        """Claim an attachment, or return its existing state.

        A database uniqueness constraint makes the claim safe when multiple scheduled
        runs inspect the same mailbox at once. ``None`` means this caller owns the
        upload attempt.
        """
        now = self._now()
        with self.db.conn:
            existing = self._fetch_entry(message_id, internet_message_id, attachment_id)
            if existing is not None:
                if existing.status == "failed" or self._is_stale_upload(existing):
                    cursor = self.db.conn.execute(
                        f"""
                        UPDATE {self.TABLE}
                        SET message_id = ?, status = 'uploading', claimed_at = ?, paperless_task_id = NULL
                        WHERE message_id = ? AND attachment_id = ?
                          AND status = ? AND claimed_at IS ?
                        """,
                        (
                            message_id,
                            now,
                            existing.message_id,
                            attachment_id,
                            existing.status,
                            existing.claimed_at,
                        ),
                    )
                    if cursor.rowcount == 1:
                        return None
                return existing

            cursor = self.db.conn.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    message_id, internet_message_id, attachment_id, checksum,
                    paperless_document_id, paperless_task_id, status, claimed_at, processed_at
                ) VALUES (?, ?, ?, '', NULL, NULL, 'uploading', ?, NULL)
                ON CONFLICT(message_id, attachment_id) DO NOTHING
                """,
                (message_id, internet_message_id, attachment_id, now),
            )
            if cursor.rowcount == 1:
                return None
            return self._fetch_entry(message_id, internet_message_id, attachment_id)

    def mark_pending(
        self,
        *,
        message_id: str,
        attachment_id: str,
        checksum: str,
        task_id: str,
    ) -> None:
        self._update(
            message_id=message_id,
            attachment_id=attachment_id,
            checksum=checksum,
            status="pending",
            task_id=task_id,
            document_id=None,
            processed_at=None,
        )

    def mark_complete(
        self,
        *,
        message_id: str,
        attachment_id: str,
        checksum: str,
        paperless_document_id: int | None,
    ) -> None:
        self._update(
            message_id=message_id,
            attachment_id=attachment_id,
            checksum=checksum,
            status="completed",
            task_id=None,
            document_id=paperless_document_id,
            processed_at=self._now(),
        )

    def mark_failed(self, *, message_id: str, attachment_id: str) -> None:
        self.db.conn.execute(
            f"""
            UPDATE {self.TABLE}
            SET status = 'failed', paperless_task_id = NULL
            WHERE message_id = ? AND attachment_id = ?
            """,
            (message_id, attachment_id),
        )
        self.db.conn.commit()

    def _fetch_entry(
        self, message_id: str, internet_message_id: str, attachment_id: str
    ) -> CacheEntry | None:
        row = self.db.conn.execute(
            f"""
            SELECT message_id, status, paperless_task_id, checksum, paperless_document_id, claimed_at
            FROM {self.TABLE}
            WHERE (message_id = ? AND attachment_id = ?)
               OR (internet_message_id <> '' AND internet_message_id = ? AND attachment_id = ?)
            LIMIT 1
            """,
            (message_id, attachment_id, internet_message_id, attachment_id),
        ).fetchone()
        if row is None:
            return None
        return CacheEntry(
            message_id=row[0],
            status=row[1] or "completed",
            task_id=row[2],
            checksum=row[3] or "",
            paperless_document_id=row[4],
            claimed_at=row[5],
        )

    def _update(
        self,
        *,
        message_id: str,
        attachment_id: str,
        checksum: str,
        status: str,
        task_id: str | None,
        document_id: int | None,
        processed_at: str | None,
    ) -> None:
        self.db.conn.execute(
            f"""
            UPDATE {self.TABLE}
            SET checksum = ?, status = ?, paperless_task_id = ?,
                paperless_document_id = ?, processed_at = ?
            WHERE message_id = ? AND attachment_id = ?
            """,
            (
                checksum,
                status,
                task_id,
                document_id,
                processed_at,
                message_id,
                attachment_id,
            ),
        )
        self.db.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=UTC).isoformat()

    def _is_stale_upload(self, entry: CacheEntry) -> bool:
        if entry.status != "uploading" or not entry.claimed_at:
            return False
        try:
            claimed_at = datetime.fromisoformat(entry.claimed_at)
        except ValueError:
            return True
        return claimed_at < datetime.now(tz=UTC) - self.STALE_CLAIM_AFTER

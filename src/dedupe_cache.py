"""SQLite-backed cache to prevent duplicate uploads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import sqlite_utils


class DedupeCache:
    """Store processed message+attachment IDs."""

    TABLE = "processed_attachments"

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
                "processed_at": str,
            },
            pk=("message_id", "attachment_id"),
            if_not_exists=True,
        )

    def seen(self, message_id: str, attachment_id: str) -> bool:
        table = self.db[self.TABLE]
        return (
            table.count_where(
                "message_id = ? and attachment_id = ?", [message_id, attachment_id]
            )
            > 0
        )

    def record(
        self,
        *,
        message_id: str,
        internet_message_id: str,
        attachment_id: str,
        checksum: str,
        paperless_document_id: Optional[int],
    ) -> None:
        table = self.db[self.TABLE]
        table.upsert(
            {
                "message_id": message_id,
                "internet_message_id": internet_message_id,
                "attachment_id": attachment_id,
                "checksum": checksum,
                "paperless_document_id": paperless_document_id,
                "processed_at": datetime.now(tz=UTC).isoformat(),
            },
            pk=("message_id", "attachment_id"),
        )


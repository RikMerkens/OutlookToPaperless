"""Paperless-ngx uploader."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from .config import Settings
from .utils import ensure_utc

logger = logging.getLogger(__name__)


class PaperlessClient:
    """Upload documents and metadata to Paperless-ngx."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.base_url = str(settings.paperless_base_url).rstrip("/")

    def upload_document(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        title: str,
        created: datetime,
        metadata: Dict[str, Any],
    ) -> int | None:
        """Upload a document and return the Paperless document ID (if available)."""
        url = f"{self.base_url}/api/documents/post_document/"
        headers = {"Authorization": f"Token {self.settings.paperless_api_token}"}

        data: Dict[str, Any] = {
            "title": title,
            "created": ensure_utc(created).isoformat(),
            "metadata": json.dumps(metadata),
        }

        if self.settings.paperless_document_type_id:
            data["document_type"] = str(self.settings.paperless_document_type_id)
        if self.settings.paperless_correspondent_id:
            data["correspondent"] = str(self.settings.paperless_correspondent_id)
        if self.settings.paperless_tag_ids:
            data["tags"] = ",".join(str(tag_id) for tag_id in self.settings.paperless_tag_ids)

        files = {
            "document": (filename, file_bytes, metadata.get("content_type") or "application/octet-stream")
        }

        logger.info("Uploading '%s' to Paperless", title)
        response = self.session.post(url, headers=headers, data=data, files=files, timeout=60)

        if response.status_code >= 400:
            logger.error("Paperless upload failed (%s): %s", response.status_code, response.text)
            response.raise_for_status()

        payload = self._parse_response_body(response)
        document_id = self._extract_document_id(payload)
        if document_id is None:
            logger.warning(
                "Paperless response did not include a document id; response=%s", payload
            )
        return document_id

    @staticmethod
    def _parse_response_body(response) -> dict | str:
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _extract_document_id(payload) -> int | None:
        if isinstance(payload, dict):
            doc_id = payload.get("id") or payload.get("document", {}).get("id")
            return int(doc_id) if doc_id is not None else None
        if isinstance(payload, str):
            stripped = payload.strip()
            if stripped.isdigit():
                return int(stripped)
        return None


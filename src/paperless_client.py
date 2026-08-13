"""Paperless-ngx uploader."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict

import requests

from .config import Settings
from .utils import ensure_utc

logger = logging.getLogger(__name__)


class PaperlessTaskFailed(RuntimeError):
    """Raised when Paperless reports an asynchronous consumption failure."""


@dataclass(frozen=True)
class UploadReceipt:
    """The immediate result of submitting a document to Paperless."""

    task_id: str | None
    document_id: int | None


@dataclass(frozen=True)
class TaskCompletion:
    """A successfully consumed Paperless task."""

    document_id: int | None


class PaperlessClient:
    """Upload documents and wait for their Paperless consumption tasks."""

    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    MAX_REQUEST_ATTEMPTS = 4
    TASK_POLL_SECONDS = 2
    TASK_TIMEOUT_SECONDS = 300

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
    ) -> UploadReceipt:
        """Submit a document and return its task ID or immediate document ID."""
        url = f"{self.base_url}/api/documents/post_document/"
        headers = {"Authorization": f"Token {self.settings.paperless_api_token}"}
        data = [
            ("title", title),
            ("created", ensure_utc(created).isoformat()),
        ]

        if self.settings.paperless_document_type_id:
            data.append(("document_type", str(self.settings.paperless_document_type_id)))
        if self.settings.paperless_correspondent_id:
            data.append(("correspondent", str(self.settings.paperless_correspondent_id)))
        data.extend(("tags", str(tag_id)) for tag_id in self.settings.paperless_tag_ids)

        files = {
            "document": (filename, file_bytes, metadata.get("content_type") or "application/octet-stream")
        }

        logger.info("Submitting '%s' to Paperless", title)
        response = self._request("post", url, headers=headers, data=data, files=files, timeout=60)
        return self._extract_upload_receipt(self._parse_response_body(response))

    def wait_for_task(self, task_id: str) -> TaskCompletion | None:
        """Wait for a queued Paperless task, returning its document ID if provided."""
        deadline = time.monotonic() + self.TASK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status, document_id = self._get_task_status(task_id)
            if status in {"SUCCESS", "COMPLETED"}:
                return TaskCompletion(document_id=document_id)
            if status in {"FAILURE", "FAILED", "REVOKED"}:
                raise PaperlessTaskFailed(f"Paperless task {task_id} ended with status {status}.")
            time.sleep(self.TASK_POLL_SECONDS)
        return None

    def _get_task_status(self, task_id: str) -> tuple[str, int | None]:
        url = f"{self.base_url}/api/tasks/"
        headers = {"Authorization": f"Token {self.settings.paperless_api_token}"}
        response = self._request("get", url, headers=headers, params={"task_id": task_id}, timeout=30)
        payload = self._parse_response_body(response)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if isinstance(payload, dict) and "status" not in payload:
            results = payload.get("results", [])
            payload = payload.get(task_id)
            if payload is None and isinstance(results, list):
                payload = results[0] if results else {}
        if not isinstance(payload, dict) or "status" not in payload:
            raise RuntimeError(f"Unexpected Paperless task response for {task_id}: {payload}")
        return str(payload["status"]).upper(), self._extract_document_id(payload.get("result"))

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        request = getattr(self.session, method)
        for attempt in range(self.MAX_REQUEST_ATTEMPTS):
            try:
                response = request(url, **kwargs)
            except requests.RequestException:
                if attempt == self.MAX_REQUEST_ATTEMPTS - 1:
                    raise
                self._sleep_before_retry(None, attempt)
                continue

            if response.status_code not in self.RETRYABLE_STATUS_CODES:
                if response.status_code >= 400:
                    logger.error("Paperless request failed (%s): %s", response.status_code, response.text)
                    response.raise_for_status()
                return response

            if attempt == self.MAX_REQUEST_ATTEMPTS - 1:
                logger.error("Paperless request failed (%s): %s", response.status_code, response.text)
                response.raise_for_status()

            logger.warning("Paperless request returned %s; retrying", response.status_code)
            self._sleep_before_retry(response, attempt)
            response.close()

        raise RuntimeError("Paperless request retry loop terminated unexpectedly.")

    @staticmethod
    def _sleep_before_retry(response: requests.Response | None, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                try:
                    delay = max(0.0, (parsedate_to_datetime(retry_after) - datetime.now().astimezone()).total_seconds())
                except (TypeError, ValueError):
                    delay = float(2**attempt)
        else:
            delay = float(2**attempt)
        time.sleep(delay)

    @staticmethod
    def _parse_response_body(response: requests.Response) -> dict | list | str:
        try:
            return response.json()
        except ValueError:
            return response.text

    @classmethod
    def _extract_upload_receipt(cls, payload: dict | list | str) -> UploadReceipt:
        document_id = cls._extract_document_id(payload)
        if document_id is not None:
            return UploadReceipt(task_id=None, document_id=document_id)
        if isinstance(payload, dict):
            task_id = payload.get("task_id") or payload.get("id")
        elif isinstance(payload, str):
            task_id = payload.strip()
        else:
            task_id = None
        if not task_id:
            raise RuntimeError(f"Paperless upload returned no task or document ID: {payload}")
        return UploadReceipt(task_id=str(task_id), document_id=None)

    @staticmethod
    def _extract_document_id(payload: Any) -> int | None:
        if isinstance(payload, dict):
            doc_id = payload.get("document_id")
            document = payload.get("document")
            if doc_id is None and isinstance(document, dict):
                doc_id = document.get("id")
            if doc_id is None and isinstance(document, int):
                doc_id = document
            if doc_id is None and isinstance(payload.get("id"), int):
                doc_id = payload["id"]
            return int(doc_id) if doc_id is not None else None
        if isinstance(payload, int):
            return payload
        if isinstance(payload, str) and payload.strip().isdigit():
            return int(payload.strip())
        return None

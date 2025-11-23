"""Microsoft Graph helper focused on message + attachment retrieval."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Tuple
from urllib.parse import quote

import msal
import requests
from requests import Response

from .config import Settings
from .models import AttachmentMetadata, MessageMetadata
from .utils import isoformat_utc, parse_graph_datetime

logger = logging.getLogger(__name__)


class GraphClient:
    """Thin wrapper that authenticates with Graph and yields attachments."""

    GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.scopes = settings.graph_scopes or ["Mail.Read"]
        self.auth_mode = settings.graph_auth_mode
        self.authority = settings.authority_url
        self._token_cache = None

        if self.auth_mode == "client_credentials":
            self.app = msal.ConfidentialClientApplication(
                client_id=settings.graph_client_id,
                client_credential=settings.graph_client_secret,
                authority=self.authority,
            )
        else:
            token_cache = msal.SerializableTokenCache()
            cache_path = settings.graph_token_cache
            if cache_path.exists():
                token_cache.deserialize(cache_path.read_text())
            self._token_cache = token_cache
            self.app = msal.PublicClientApplication(
                client_id=settings.graph_client_id,
                authority=self.authority,
                token_cache=token_cache,
            )

    def iter_messages(
        self, received_since: datetime | None = None, max_messages: int | None = None
    ) -> Iterator[Tuple[MessageMetadata, list[AttachmentMetadata]]]:
        """Yield messages (with attachment metadata) that have file attachments."""
        url = f"{self.GRAPH_BASE}{self._messages_root()}/messages"
        server_filter_supported = self.settings.graph_mailbox is not None

        params = {
            "$select": "id,subject,internetMessageId,from,receivedDateTime,webLink,categories,bodyPreview,hasAttachments",
            "$orderby": "receivedDateTime desc",
            "$top": self.settings.graph_page_size,
        }

        if server_filter_supported:
            filter_clauses = ["hasAttachments eq true"]
            if received_since:
                filter_clauses.append(f"receivedDateTime ge {isoformat_utc(received_since)}")
            params["$filter"] = " and ".join(filter_clauses)

        yielded = 0
        while url:
            logger.debug("Fetching Graph messages page %s", url)
            response = self._get(url, params=params)
            payload = response.json()

            for raw in payload.get("value", []):
                if not raw.get("hasAttachments"):
                    continue

                message = self._to_message(raw)

                if not server_filter_supported and received_since and message.received < received_since:
                    return
                attachments = self._list_file_attachments(message.message_id)

                if not attachments:
                    continue

                yield message, attachments
                yielded += 1
                if max_messages and yielded >= max_messages:
                    return

            url = payload.get("@odata.nextLink")
            params = None  # only pass params to the first call

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download attachment bytes."""
        url = f"{self.GRAPH_BASE}{self._messages_root()}/messages/{message_id}/attachments/{attachment_id}/$value"
        response = self._get(url, stream=True)
        return response.content

    def _get(self, url: str, params: dict | None = None, stream: bool = False) -> Response:
        headers = {"Authorization": f"Bearer {self._acquire_token()}"}
        resp = self.session.get(url, headers=headers, params=params, stream=stream, timeout=30)
        if resp.status_code >= 400:
            logger.error("Graph request failed (%s): %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp

    def _acquire_token(self) -> str:
        if self.auth_mode == "client_credentials":
            return self._acquire_token_client_credentials()
        return self._acquire_token_device_flow()

    def _acquire_token_client_credentials(self) -> str:
        result = self.app.acquire_token_silent(self.GRAPH_SCOPE, account=None)
        if not result:
            result = self.app.acquire_token_for_client(scopes=self.GRAPH_SCOPE)
        if "access_token" not in result:
            raise RuntimeError(f"Unable to obtain Graph token: {result.get('error_description')}")
        return result["access_token"]

    def _acquire_token_device_flow(self) -> str:
        accounts = self.app.get_accounts()
        result = None
        if accounts:
            result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
        if not result:
            flow = self.app.initiate_device_flow(scopes=self.scopes)
            if "user_code" not in flow:
                raise RuntimeError(f"Unable to start device code flow: {flow}")
            logger.info(flow.get("message"))
            result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Unable to obtain Graph token: {result.get('error_description')}")
        self._persist_token_cache()
        return result["access_token"]

    def _persist_token_cache(self) -> None:
        if not self._token_cache or not self._token_cache.has_state_changed:
            return
        cache_path: Path = self.settings.graph_token_cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(self._token_cache.serialize())

    def _messages_root(self) -> str:
        if self.settings.graph_mailbox:
            mailbox = quote(self.settings.graph_mailbox)
            return f"/users/{mailbox}"
        return "/me"

    def _list_file_attachments(self, message_id: str) -> list[AttachmentMetadata]:
        url = f"{self.GRAPH_BASE}{self._messages_root()}/messages/{message_id}/attachments"
        params = {"$select": "id,name,contentType,size,isInline"}
        attachments: List[AttachmentMetadata] = []

        while url:
            response = self._get(url, params=params)
            payload = response.json()
            for raw in payload.get("value", []):
                if raw.get("@odata.type") != "#microsoft.graph.fileAttachment":
                    continue
                attachments.append(self._to_attachment(raw))
            url = payload.get("@odata.nextLink")
            params = None

        return attachments

    @staticmethod
    def _to_message(raw: dict) -> MessageMetadata:
        sender = (raw.get("from") or {}).get("emailAddress") or {}
        return MessageMetadata(
            message_id=raw["id"],
            internet_message_id=raw.get("internetMessageId", ""),
            subject=raw.get("subject", ""),
            sender_email=sender.get("address", ""),
            sender_name=sender.get("name"),
            received=parse_graph_datetime(raw["receivedDateTime"]),
            web_link=raw.get("webLink"),
            categories=raw.get("categories") or [],
            body_preview=raw.get("bodyPreview"),
            raw=raw,
        )

    @staticmethod
    def _to_attachment(raw: dict) -> AttachmentMetadata:
        return AttachmentMetadata(
            attachment_id=raw["id"],
            name=raw.get("name", ""),
            content_type=raw.get("contentType", "application/octet-stream"),
            size=raw.get("size", 0),
            is_inline=raw.get("isInline", False),
        )


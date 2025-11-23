"""Typed containers shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class MessageMetadata:
    """Essential metadata about an Outlook message."""

    message_id: str
    internet_message_id: str
    subject: str
    sender_email: str
    sender_name: Optional[str]
    received: datetime
    web_link: Optional[str]
    categories: list[str]
    body_preview: Optional[str]
    raw: dict[str, Any]


@dataclass
class AttachmentMetadata:
    """Metadata for a file attachment."""

    attachment_id: str
    name: str
    content_type: str
    size: int
    is_inline: bool = False


@dataclass
class AttachmentPayload:
    """Attachment bytes coupled with the message context."""

    message: MessageMetadata
    attachment: AttachmentMetadata
    content: bytes
    checksum: str


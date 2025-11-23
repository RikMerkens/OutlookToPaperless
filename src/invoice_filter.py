"""Heuristics that decide whether an attachment looks like an invoice."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from .models import AttachmentMetadata, MessageMetadata

logger = logging.getLogger(__name__)


class InvoiceFilter:
    """Evaluate subject/sender/file heuristics to spot invoices."""

    def __init__(
        self,
        subject_keywords: Iterable[str],
        filename_patterns: Iterable[str],
        sender_whitelist: Iterable[str],
        allow_all: bool = False,
    ) -> None:
        self.subject_keywords = [kw.lower() for kw in subject_keywords]
        self.filename_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in filename_patterns if pattern
        ]
        self.sender_whitelist = {sender.lower() for sender in sender_whitelist}
        self.allow_all = allow_all

    def looks_like_invoice(self, message: MessageMetadata, attachment: AttachmentMetadata) -> bool:
        """Return True if any heuristic indicates an invoice."""
        if self.allow_all:
            logger.debug(
                "process_all_attachments enabled, auto-accepting attachment %s", attachment.attachment_id
            )
            return True

        sender = (message.sender_email or "").lower()
        if sender and sender in self.sender_whitelist:
            logger.debug("Sender %s whitelisted as invoice", sender)
            return True

        subject = (message.subject or "").lower()
        if any(keyword in subject for keyword in self.subject_keywords):
            logger.debug("Subject '%s' matched invoice keyword", message.subject)
            return True

        filename = attachment.name or ""
        if any(pattern.search(filename) for pattern in self.filename_patterns):
            logger.debug("Attachment '%s' matched invoice filename pattern", filename)
            return True

        logger.debug(
            "Attachment %s from message %s did not match invoice heuristics",
            attachment.attachment_id,
            message.message_id,
        )
        return False


"""Entry point that moves Outlook invoice attachments into Paperless-ngx."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

from dotenv import load_dotenv
import requests

# Ensure project root is on sys.path when running as a script
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Settings
from src.dedupe_cache import DedupeCache
from src.graph_client import GraphClient
from src.invoice_filter import InvoiceFilter
from src.paperless_client import PaperlessClient, PaperlessTaskFailed
from src.utils import ensure_utc, sha256_hex

load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Outlook invoice attachments to Paperless.")
    parser.add_argument("--since", type=parse_datetime, help="ISO8601 timestamp (UTC) to start from")
    parser.add_argument(
        "--since-days",
        type=int,
        help="Shortcut for '--since' expressed as N days ago (integers only)",
    )
    parser.add_argument("--max-messages", type=int, help="Limit how many messages to inspect")
    parser.add_argument("--dry-run", action="store_true", help="List actions without downloading/uploading")
    return parser


def parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid ISO timestamp: {value}") from exc


def resolve_since(args: argparse.Namespace) -> datetime | None:
    if args.since and args.since_days:
        raise SystemExit("Use either --since or --since-days, not both.")
    if args.since:
        return ensure_utc(args.since)
    if args.since_days:
        return datetime.now(tz=UTC) - timedelta(days=args.since_days)
    return None


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = Settings()
    configure_logging(settings.log_level)
    since = resolve_since(args)

    graph_client = GraphClient(settings)
    paperless_client = PaperlessClient(settings)
    cache = DedupeCache(settings.attachment_cache_db)
    invoice_filter = InvoiceFilter(
        subject_keywords=settings.graph_invoice_subject_keywords,
        filename_patterns=settings.graph_invoice_filename_patterns,
        sender_whitelist=settings.graph_sender_whitelist,
        allow_all=settings.process_all_attachments,
    )

    stats = {"processed": 0, "skipped": 0, "uploaded": 0, "pending": 0, "failed": 0}

    for message, attachments in graph_client.iter_messages(
        received_since=since, max_messages=args.max_messages
    ):
        for attachment in attachments:
            if attachment.is_inline:
                logging.debug(
                    "Skipping inline attachment %s for message %s",
                    attachment.name,
                    message.message_id,
                )
                stats["skipped"] += 1
                continue

            if not invoice_filter.looks_like_invoice(message, attachment):
                stats["skipped"] += 1
                continue

            if args.dry_run:
                logging.info(
                    "[DRY-RUN] Would upload '%s' from message '%s'",
                    attachment.name,
                    message.subject,
                )
                stats["processed"] += 1
                continue

            existing = cache.claim(
                message_id=message.message_id,
                internet_message_id=message.internet_message_id,
                attachment_id=attachment.attachment_id,
            )
            if existing is not None:
                if existing.status == "pending" and existing.task_id:
                    try:
                        completion = paperless_client.wait_for_task(existing.task_id)
                    except PaperlessTaskFailed as exc:
                        logging.error("Paperless task %s failed: %s", existing.task_id, exc)
                        cache.mark_failed(
                            message_id=existing.message_id,
                            attachment_id=attachment.attachment_id,
                        )
                        stats["failed"] += 1
                    except (requests.RequestException, RuntimeError) as exc:
                        logging.warning("Unable to check Paperless task %s: %s", existing.task_id, exc)
                        stats["pending"] += 1
                    else:
                        if completion is None:
                            logging.info("Paperless task %s is still pending", existing.task_id)
                            stats["pending"] += 1
                        else:
                            cache.mark_complete(
                                message_id=existing.message_id,
                                attachment_id=attachment.attachment_id,
                                checksum=existing.checksum,
                                paperless_document_id=completion.document_id,
                            )
                            stats["uploaded"] += 1
                    continue

                logging.info(
                    "Attachment %s for message %s is already %s; skipping",
                    attachment.name,
                    message.internet_message_id,
                    existing.status,
                )
                stats["skipped"] += 1
                continue

            stats["processed"] += 1

            try:
                content = graph_client.download_attachment(message.message_id, attachment.attachment_id)
                checksum = sha256_hex(content)
                metadata = {
                    "sender_email": message.sender_email,
                    "sender_name": message.sender_name,
                    "subject": message.subject,
                    "internet_message_id": message.internet_message_id,
                    "graph_message_id": message.message_id,
                    "graph_web_link": message.web_link,
                    "categories": message.categories,
                    "checksum": checksum,
                    "content_type": attachment.content_type,
                    "size": attachment.size,
                }
                receipt = paperless_client.upload_document(
                    file_bytes=content,
                    filename=attachment.name,
                    title=settings.invoice_title(message.subject, attachment.name),
                    created=message.received,
                    metadata=metadata,
                )
            except (requests.RequestException, RuntimeError) as exc:
                logging.error("Unable to upload attachment '%s': %s", attachment.name, exc)
                cache.mark_failed(
                    message_id=message.message_id,
                    attachment_id=attachment.attachment_id,
                )
                stats["failed"] += 1
                continue

            if receipt.document_id is not None:
                cache.mark_complete(
                    message_id=message.message_id,
                    attachment_id=attachment.attachment_id,
                    checksum=checksum,
                    paperless_document_id=receipt.document_id,
                )
                stats["uploaded"] += 1
                continue

            cache.mark_pending(
                message_id=message.message_id,
                attachment_id=attachment.attachment_id,
                checksum=checksum,
                task_id=receipt.task_id,
            )
            try:
                completion = paperless_client.wait_for_task(receipt.task_id)
            except PaperlessTaskFailed as exc:
                logging.error("Paperless task %s failed: %s", receipt.task_id, exc)
                cache.mark_failed(
                    message_id=message.message_id,
                    attachment_id=attachment.attachment_id,
                )
                stats["failed"] += 1
            except (requests.RequestException, RuntimeError) as exc:
                logging.warning("Unable to check Paperless task %s: %s", receipt.task_id, exc)
                stats["pending"] += 1
            else:
                if completion is None:
                    logging.info("Paperless task %s is still pending", receipt.task_id)
                    stats["pending"] += 1
                else:
                    cache.mark_complete(
                        message_id=message.message_id,
                        attachment_id=attachment.attachment_id,
                        checksum=checksum,
                        paperless_document_id=completion.document_id,
                    )
                    stats["uploaded"] += 1

            # Placeholder for optional OneDrive sync hook.
            # onedrive_client.upload_if_enabled(...)

    logging.info(
        "Run complete: processed=%s uploaded=%s pending=%s failed=%s skipped=%s",
        stats["processed"],
        stats["uploaded"],
        stats["pending"],
        stats["failed"],
        stats["skipped"],
    )


if __name__ == "__main__":
    main()


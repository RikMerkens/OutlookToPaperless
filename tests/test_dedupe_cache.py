from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.dedupe_cache import DedupeCache


class DedupeCacheTests(unittest.TestCase):
    def test_pending_task_is_reused_after_a_message_move(self):
        with TemporaryDirectory() as temporary_directory:
            cache = DedupeCache(Path(temporary_directory) / "cache.db")

            self.assertIsNone(
                cache.claim(
                    message_id="immutable-message-id",
                    internet_message_id="<message@example.com>",
                    attachment_id="attachment-id",
                )
            )
            cache.mark_pending(
                message_id="immutable-message-id",
                attachment_id="attachment-id",
                checksum="checksum",
                task_id="task-id",
            )

            existing = cache.claim(
                message_id="moved-message-id",
                internet_message_id="<message@example.com>",
                attachment_id="attachment-id",
            )

            self.assertIsNotNone(existing)
            self.assertEqual(existing.status, "pending")
            self.assertEqual(existing.task_id, "task-id")
            self.assertEqual(existing.checksum, "checksum")

    def test_failed_claim_can_be_retried(self):
        with TemporaryDirectory() as temporary_directory:
            cache = DedupeCache(Path(temporary_directory) / "cache.db")
            claim = {
                "message_id": "message-id",
                "internet_message_id": "<message@example.com>",
                "attachment_id": "attachment-id",
            }

            self.assertIsNone(cache.claim(**claim))
            cache.mark_failed(message_id="message-id", attachment_id="attachment-id")

            self.assertIsNone(cache.claim(**claim))


if __name__ == "__main__":
    unittest.main()

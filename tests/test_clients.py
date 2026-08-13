from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from src.graph_client import GraphClient
from src.paperless_client import PaperlessClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class GraphClientTests(unittest.TestCase):
    def test_client_credentials_filter_orders_received_time_first(self):
        client = GraphClient.__new__(GraphClient)
        client.settings = SimpleNamespace(graph_mailbox="mailbox", graph_page_size=25)
        client._messages_collection_url = lambda: "https://graph.example/messages"
        params_seen = []

        def get(url, params=None, stream=False):
            params_seen.append(params)
            return FakeResponse({"value": []})

        client._get = get

        list(client.iter_messages(received_since=datetime(2025, 1, 1, tzinfo=UTC)))

        self.assertEqual(params_seen[0]["$orderby"], "receivedDateTime desc")
        self.assertEqual(
            params_seen[0]["$filter"],
            "receivedDateTime ge 2025-01-01T00:00:00Z and hasAttachments eq true",
        )

    def test_client_credentials_without_since_omits_orderby(self):
        client = GraphClient.__new__(GraphClient)
        client.settings = SimpleNamespace(graph_mailbox="mailbox", graph_page_size=25)
        client._messages_collection_url = lambda: "https://graph.example/messages"
        params_seen = []
        client._get = lambda url, params=None, stream=False: (
            params_seen.append(params) or FakeResponse({"value": []})
        )

        list(client.iter_messages())

        self.assertEqual(params_seen[0]["$filter"], "hasAttachments eq true")
        self.assertNotIn("$orderby", params_seen[0])


class PaperlessClientTests(unittest.TestCase):
    def test_upload_sends_repeated_tag_fields_and_parses_task_id(self):
        client = PaperlessClient.__new__(PaperlessClient)
        client.base_url = "https://paperless.example"
        client.settings = SimpleNamespace(
            paperless_api_token="token",
            paperless_document_type_id=None,
            paperless_correspondent_id=None,
            paperless_tag_ids=[4, 9],
        )
        captured = {}

        def request(method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return FakeResponse("task-uuid")

        client._request = request

        receipt = client.upload_document(
            file_bytes=b"contents",
            filename="invoice.pdf",
            title="Invoice",
            created=datetime(2025, 1, 1, tzinfo=UTC),
            metadata={"content_type": "application/pdf"},
        )

        self.assertEqual(receipt.task_id, "task-uuid")
        self.assertIsNone(receipt.document_id)
        self.assertEqual(captured["data"].count(("tags", "4")), 1)
        self.assertEqual(captured["data"].count(("tags", "9")), 1)


if __name__ == "__main__":
    unittest.main()

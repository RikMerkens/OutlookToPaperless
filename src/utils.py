"""Utility helpers shared across modules."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Iterable


def parse_graph_datetime(value: str) -> datetime:
    """Convert Graph ISO strings (with trailing Z) into aware UTC datetimes."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Force a datetime into UTC without altering instant."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def isoformat_utc(dt: datetime) -> str:
    """Return an ISO string that Graph and Paperless accept."""
    return ensure_utc(dt).isoformat().replace("+00:00", "Z")


def chunked(iterable: Iterable, size: int):
    """Yield successive sized chunks from an iterable."""
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def sha256_hex(payload: bytes) -> str:
    """Convenience wrapper for hex digests."""
    return sha256(payload).hexdigest()


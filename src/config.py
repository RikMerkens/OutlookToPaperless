"""Configuration management for the Outlook→Paperless pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Literal, Sequence

from dotenv import load_dotenv
from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env early so BaseSettings can pick values up seamlessly.
load_dotenv()


def _split_list(value: str | Sequence[str] | None, coerce_lower: bool = True) -> list[str]:
    """Turn delimiter-separated env strings into cleaned lists."""
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[;,]", value)
    else:
        items = list(value)
    cleaned: list[str] = []
    for item in items:
        trimmed = item.strip()
        if not trimmed:
            continue
        cleaned.append(trimmed.lower() if coerce_lower else trimmed)
    return cleaned


class Settings(BaseSettings):
    """App configuration derived from environment variables."""

    graph_tenant_id: str | None = Field(None, alias="GRAPH_TENANT_ID")
    graph_client_id: str = Field(..., alias="GRAPH_CLIENT_ID")
    graph_client_secret: str | None = Field(None, alias="GRAPH_CLIENT_SECRET")
    graph_mailbox: str | None = Field(None, alias="GRAPH_MAILBOX")
    graph_auth_mode: Literal["client_credentials", "device_code"] = Field(
        "device_code", alias="GRAPH_AUTH_MODE"
    )
    graph_authority: str | None = Field(None, alias="GRAPH_AUTHORITY")
    graph_scopes_raw: str = Field("Mail.Read", alias="GRAPH_SCOPES")
    graph_page_size: int = Field(25, alias="GRAPH_PAGE_SIZE")
    graph_mail_folder: str | None = Field("Inbox", alias="GRAPH_MAIL_FOLDER")
    graph_invoice_subject_keywords_raw: str = Field(
        "invoice;rechnung", alias="GRAPH_INVOICE_SUBJECT_KEYWORDS"
    )
    graph_invoice_filename_patterns_raw: str = Field(
        "invoice;rechnung", alias="GRAPH_INVOICE_FILENAME_PATTERNS"
    )
    graph_sender_whitelist_raw: str = Field("", alias="GRAPH_SENDER_WHITELIST")
    process_all_attachments: bool = Field(False, alias="PROCESS_ALL_ATTACHMENTS")
    graph_token_cache: Path = Field(Path("data/msal_token_cache.bin"), alias="GRAPH_TOKEN_CACHE")

    paperless_base_url: HttpUrl = Field(..., alias="PAPERLESS_BASE_URL")
    paperless_api_token: str = Field(..., alias="PAPERLESS_API_TOKEN")
    paperless_document_type_id: int | None = Field(None, alias="PAPERLESS_DOCUMENT_TYPE_ID")
    paperless_correspondent_id: int | None = Field(
        None, alias="PAPERLESS_CORRESPONDENT_ID"
    )
    paperless_tag_ids_raw: str = Field("", alias="PAPERLESS_TAG_IDS")
    paperless_default_title_template: str = Field(
        "{subject}", alias="PAPERLESS_DEFAULT_TITLE_TEMPLATE"
    )
    paperless_timezone: str = Field("UTC", alias="PAPERLESS_TIMEZONE")

    attachment_cache_db: Path = Field(Path("data/processed_emails.db"), alias="ATTACHMENT_CACHE_DB")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _validate_authentication(self):
        if self.graph_auth_mode == "client_credentials":
            if not self.graph_client_secret:
                raise ValueError("GRAPH_CLIENT_SECRET is required for client_credentials mode.")
            if not self.graph_mailbox:
                raise ValueError("GRAPH_MAILBOX is required for client_credentials mode.")
            if not (self.graph_tenant_id or self.graph_authority):
                raise ValueError(
                    "GRAPH_TENANT_ID or GRAPH_AUTHORITY must be provided for client_credentials mode."
                )
        else:
            if self.graph_mailbox:
                raise ValueError(
                    "GRAPH_MAILBOX must be omitted for device_code mode; the signed-in mailbox is used."
                )
        return self

    @field_validator(
        "graph_tenant_id",
        "graph_client_secret",
        "graph_mailbox",
        "graph_authority",
        "paperless_document_type_id",
        "paperless_correspondent_id",
        "graph_mail_folder",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value):
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("graph_mail_folder", mode="before")
    @classmethod
    def _normalize_mail_folder(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    def invoice_title(self, message_subject: str, fallback: str) -> str:
        """Resolve the template-driven title for Paperless."""
        template = self.paperless_default_title_template or fallback
        return template.format(subject=message_subject or fallback)

    @property
    def authority_url(self) -> str:
        if self.graph_authority:
            return self.graph_authority.rstrip("/")
        if self.graph_tenant_id:
            return f"https://login.microsoftonline.com/{self.graph_tenant_id}"
        return "https://login.microsoftonline.com/consumers"

    @property
    def graph_scopes(self) -> list[str]:
        """Scopes requested for delegated Graph auth."""
        scopes = _split_list(self.graph_scopes_raw, coerce_lower=False)
        return scopes or ["Mail.Read"]

    @property
    def graph_invoice_subject_keywords(self) -> list[str]:
        keywords = _split_list(self.graph_invoice_subject_keywords_raw, coerce_lower=True)
        return keywords or ["invoice"]

    @property
    def graph_invoice_filename_patterns(self) -> list[str]:
        patterns = _split_list(
            self.graph_invoice_filename_patterns_raw, coerce_lower=False
        )
        return patterns or ["invoice", "rechnung"]

    @property
    def graph_sender_whitelist(self) -> list[str]:
        return _split_list(self.graph_sender_whitelist_raw, coerce_lower=True)

    @property
    def paperless_tag_ids(self) -> list[int]:
        raw_items = _split_list(self.paperless_tag_ids_raw, coerce_lower=False)
        return [int(item) for item in raw_items]


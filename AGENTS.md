# AGENTS.md

## Cursor Cloud specific instructions

### What this is
`OutlookToPaperless` is a single-product Python **run-to-completion CLI batch job** (not a long-running server; it exposes **no ports**). It reads invoice attachments from Outlook via Microsoft Graph and uploads them to a Paperless-ngx instance. Entry point: `scripts/outlook_to_paperless.py`. Core logic lives in `src/` (`config.py`, `graph_client.py`, `paperless_client.py`, `invoice_filter.py`, `dedupe_cache.py`). Standard install/run/Docker commands are documented in `README.md`.

### Environment
- Runs on Python 3.11+ (the VM has 3.12; the `Dockerfile` pins `python:3.14-slim`). Dependencies come from `requirements.txt` and install cleanly with plain `pip install -r requirements.txt` (goes to user site-packages; no venv required).
- There is **no test suite and no linter/formatter config** in this repo. For a quick static check use `python3 -m py_compile scripts/outlook_to_paperless.py src/*.py`. Do not claim tests exist.

### Running / testing gotchas
- `src/config.py` uses pydantic `BaseSettings` and reads a `.env` (copy from `.env.example`). Startup fails fast with a validation error unless `GRAPH_CLIENT_ID`, `PAPERLESS_BASE_URL`, and `PAPERLESS_API_TOKEN` are set.
- End-to-end runs require **two live external services with real credentials**: Microsoft Graph (Azure AD app registration, delegated `Mail.Read`, device-code flow) and a reachable Paperless-ngx instance. These are not provisioned by this repo and there are no mock servers checked in.
- Even `--dry-run` still authenticates to Graph and reads the mailbox (it only skips download/upload), so it is **not** a credential-free smoke test.
- To exercise the pipeline without live services, stub the Graph source and point Paperless at a local mock: monkeypatch `scripts.outlook_to_paperless.GraphClient` to yield synthetic `MessageMetadata`/`AttachmentMetadata`, set `PAPERLESS_BASE_URL` to a local HTTP server implementing `POST /api/documents/post_document/` (return JSON `{"id": <int>}`), then call `main()`. This runs the real `InvoiceFilter`, `DedupeCache` (SQLite), and `PaperlessClient` HTTP upload.
- Local state (SQLite dedupe DB `data/processed_emails.db` and MSAL token cache `data/msal_token_cache.bin`) is auto-created under `data/` (gitignored). Delete these files to reset dedupe/auth state between test runs.

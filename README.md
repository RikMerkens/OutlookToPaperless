# OutlookToPaperless

Automation pipeline that downloads invoice attachments from Outlook (via Microsoft Graph) and uploads them to Paperless-ngx. OneDrive export can be added later; the current focus is Outlook ➜ Paperless with strong metadata retention and deduplication.

## Prerequisites
- Python 3.11+
- Azure AD app registered for personal Microsoft accounts. Grant the delegated Microsoft Graph permission `Mail.Read` and enable device code flow.
- Paperless-ngx instance with an API token
- `pip install -r requirements.txt`

## Configuration
1. Copy `.env.example` to `.env` and fill in the Graph and Paperless secrets.
   - For personal Outlook.com mailboxes set `GRAPH_AUTH_MODE=device_code` (default), leave `GRAPH_MAILBOX` blank, and keep `GRAPH_AUTHORITY=https://login.microsoftonline.com/consumers`.
   - Run the script once interactively; you'll see a device-code prompt (`https://microsoft.com/devicelogin`). After approving, the refresh token is written to `GRAPH_TOKEN_CACHE` so future runs can be automated.
   - If you still need Microsoft 365 app-only behaviour, switch `GRAPH_AUTH_MODE=client_credentials` and supply `GRAPH_TENANT_ID`, `GRAPH_CLIENT_SECRET`, and `GRAPH_MAILBOX`.
2. Optional tuning fields:
   - `GRAPH_INVOICE_SUBJECT_KEYWORDS` – semicolon-separated keywords matched against message subjects.
   - `GRAPH_INVOICE_FILENAME_PATTERNS` – semicolon-separated substrings or regex fragments matched against attachment names.
   - `GRAPH_SENDER_WHITELIST` – semicolon-separated list of trusted invoice senders.
   - `GRAPH_SCOPES` – delegated Graph scopes requested during device code login (default `Mail.Read`).
   - `GRAPH_TOKEN_CACHE` – path where the MSAL refresh token cache lives (default `data/msal_token_cache.bin`); keep this stable across scheduled runs.
   - `PROCESS_ALL_ATTACHMENTS=true` – bypass invoice heuristics and forward every non-inline attachment (default `false` uses subject/filename/sender heuristics).
   - `PAPERLESS_TAG_IDS` / `PAPERLESS_DOCUMENT_TYPE_ID` – Paperless IDs applied on upload.
   - `ATTACHMENT_CACHE_DB` – SQLite path (default `data/processed_emails.db`) that stores processed message+attachment IDs to prevent duplicates.

## Usage
```bash
python scripts/outlook_to_paperless.py --since 2024-11-01T00:00:00Z --max-messages 200
```

Key flags:
- `--since` ISO 8601 timestamp to start scanning from (falls back to none).
- `--max-messages` caps how many messages to inspect this run.
- `--dry-run` walks the inbox and logs what would happen without downloading or uploading anything.

To schedule the sync, run the script via Windows Task Scheduler or cron (inside WSL/Linux). Point the task to a virtual environment and reuse the same `.env` plus the persisted MSAL cache so the dedupe DB and token refresh continue to work unattended.

## Running inside Docker
1. Build the image:
   ```bash
   docker build -t docpush-paperless .
   ```
2. Ensure `.env` exists on the host and contains your secret values. The container reads it at runtime via `--env-file`.
3. Mount the `data/` directory so the SQLite dedupe DB and MSAL token cache persist between runs:
   ```bash
   docker run --rm -it \
     --env-file .env \
     -v ${PWD}/data:/app/data \
     docpush-paperless \
     --dry-run --since-days 7
   ```

### Authenticating (device code flow)
- The first container run needs to be interactive (`-it`) so you can see the Microsoft sign-in instructions.
- When prompted, browse to the printed URL (e.g., `https://www.microsoft.com/link`) and enter the code shown in the container logs. Approve the app using the same Outlook personal account whose mailbox you want to process.
- After approval, the refresh token is stored in `data/msal_token_cache.bin` (thanks to the bind mount). Subsequent container runs can be non-interactive (omit `-it` if desired) because they reuse the cached token.

### Non-interactive runs
Once the token cache exists, schedule/run the container without `--dry-run`, keeping the same volume mount:
```bash
docker run --rm \
  --env-file .env \
  -v ${PWD}/data:/app/data \
  docpush-paperless \
  --since-days 2
```

#### Built-in timer inside the container
Set `RUN_INTERVAL_SECONDS` to make the container rerun the script on a fixed cadence:
```bash
docker run --rm \
  --env-file .env \
  -e RUN_INTERVAL_SECONDS=600 \
  -v ${PWD}/data:/app/data \
  docpush-paperless \
  --since-days 2
```
The example above loops every 10 minutes (600 seconds). Leave the variable unset/zero to run just once.

### docker-compose helper
`docker-compose.yml` is included for convenience:
```yaml
services:
  docpush:
    build: .
    env_file:
      - .env
    environment:
      - RUN_INTERVAL_SECONDS=600
    volumes:
      - ./data:/app/data
    command: ["--since-days", "2"]
```
Bring the stack up with:
```bash
docker compose up --build
```

## Scheduling / automation options
- **Host Cron / Task Scheduler:** Run the same `docker run ...` command every 10 minutes on your Docker host. Example cron entry:
  ```
  */10 * * * * cd /path/to/OutlookToPaperless && docker run --rm --env-file .env -v $(pwd)/data:/app/data docpush-paperless --since-days 2 >> /var/log/docpush.log 2>&1
  ```
  On Windows, use Task Scheduler to run `powershell.exe` with the `docker run` command every 10 minutes.
- **docker-compose + cron:** Keep the stack up (`docker compose up -d`), then have cron invoke `docker compose run --rm docpush --since-days 2`. This reuses the built image and shared data volume.
- **Container-internal scheduling:** For lightweight setups, you can wrap the script with a shell loop:
  Use the built-in `RUN_INTERVAL_SECONDS` variable (see above) so the entrypoint re-runs automatically without external schedulers. Host-level schedulers still offer clearer observability, but the env flag lets you keep everything self-contained when desired.


## Extending toward OneDrive
The orchestration script leaves a placeholder hook for forwarding uploaded files to OneDrive. Once ready, plug in an uploader that uses the same in-memory `AttachmentPayload` so metadata and dedupe entries stay consistent.


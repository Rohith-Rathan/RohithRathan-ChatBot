"""Google Drive access via OAuth (read-only)."""
from __future__ import annotations

import io
import logging
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
log = logging.getLogger(__name__)


class DriveAuthError(RuntimeError):
    pass


def get_credentials(credentials_file: Path, token_file: Path, interactive: bool) -> Credentials:
    """Load cached token, refresh it, or (if interactive) run the browser OAuth flow."""
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except RefreshError as e:
            log.warning("Stored Google token could not be refreshed: %s", e)

    if not interactive:
        raise DriveAuthError(
            "No valid Google token. Run `python -m src.test_drive` once interactively to authorize."
        )
    if not credentials_file.exists():
        raise DriveAuthError(f"OAuth client file not found: {credentials_file}")

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


class DriveClient:
    def __init__(self, creds: Credentials):
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def list_txt_files(self, folder_id: str) -> list[dict]:
        """All non-trashed .txt files directly inside the folder (handles pagination)."""
        query = f"'{folder_id}' in parents and trashed = false"
        files, page_token = [], None
        while True:
            resp = (
                self.service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum)",
                    pageSize=1000,
                    pageToken=page_token,
                    orderBy="modifiedTime",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                if f["name"].lower().endswith(".txt") or f.get("mimeType") == "text/plain":
                    files.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                return files

    def download_text(self, file_id: str) -> str:
        buf = io.BytesIO()
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return decode_bytes(buf.getvalue())


def decode_bytes(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")

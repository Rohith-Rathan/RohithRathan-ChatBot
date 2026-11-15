"""Drive connectivity test. Run interactively once: python -m src.test_drive

Authorizes via browser (creates token.json), lists .txt files in the folder,
and prints a short preview of the first file.
"""
from __future__ import annotations

import sys

from src.config import load_settings
from src.drive_client import DriveClient, get_credentials


def main() -> int:
    s = load_settings(require_anthropic=False)
    creds = get_credentials(s.google_credentials_file, s.google_token_file, interactive=True)
    drive = DriveClient(creds)
    files = drive.list_txt_files(s.gdrive_folder_id)
    print(f"Connected. Found {len(files)} .txt file(s) in folder.")
    for f in files[:20]:
        print(f"  - {f['name']}  ({f.get('size', '?')} bytes, modified {f.get('modifiedTime')})")
    if files:
        text = drive.download_text(files[0]["id"])
        print(f"\nPreview of {files[0]['name']}:\n{'-' * 60}\n{text[:800]}\n{'-' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

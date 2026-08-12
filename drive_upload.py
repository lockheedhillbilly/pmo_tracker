"""Google Drive upload for meeting summaries. One-time interactive OAuth consent
(`python drive_upload.py --authorize`, run once by hand) caches a refresh token locally at
.gdrive_token.json (gitignored); meeting_watcher.py then uploads silently thereafter, no
browser popup needed again until the token is revoked.

Needs GOOGLE_OAUTH_CLIENT_SECRET_PATH (path to the OAuth client secret JSON downloaded from
Google Cloud Console for a Desktop app) and GOOGLE_DRIVE_FOLDER_ID (target folder) in .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    # Needed behind a corporate TLS-intercepting proxy on Windows dev machines — see db.py's
    # identical guard for why this must not run unconditionally on other platforms.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv(Path(__file__).resolve().parent / ".env")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_PATH = Path(__file__).parent / ".gdrive_token.json"


def _get_credentials() -> Credentials:
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    if not creds or not creds.valid:
        client_secret_path = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_PATH")
        if not client_secret_path:
            raise RuntimeError(
                "No cached Google Drive token and GOOGLE_OAUTH_CLIENT_SECRET_PATH not set — "
                "run `python drive_upload.py --authorize` once, interactively, to set it up."
            )
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return creds


def upload_summary(file_path: str | Path, title: str) -> str:
    """Uploads the file at file_path into GOOGLE_DRIVE_FOLDER_ID, returns its webViewLink."""
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID not set in .env")

    creds = _get_credentials()
    service = build("drive", "v3", credentials=creds)
    media = MediaFileUpload(str(file_path), mimetype="text/markdown", resumable=False)
    file = service.files().create(
        body={"name": title, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return file["webViewLink"]


if __name__ == "__main__":
    if "--authorize" in sys.argv:
        _get_credentials()
        print(f"Authorized. Token cached at {TOKEN_PATH}")
    else:
        print("Run with --authorize to complete the one-time Google Drive OAuth consent.")

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .util import q_escape


SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(RuntimeError):
    pass


def authenticate(
    *,
    token_path: Path,
    credentials_path: Path | None = None,
    force: bool = False,
) -> Credentials:
    """
    Authenticate a desktop user.

    `drive.file` intentionally limits Zan to files it creates/opens itself
    instead of granting broad access to the user's whole Drive.
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds: Credentials | None = None

    if token_path.exists() and not force:
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path),
                SCOPES,
            )
        except (ValueError, json.JSONDecodeError):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token and not force:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            creds = None

    if credentials_path is None:
        raise DriveError(
            "Google Drive is not authenticated. Run:\n"
            "  zan auth --credentials path/to/credentials.json"
        )

    if not credentials_path.exists():
        raise DriveError(
            f"OAuth client credentials file not found: {credentials_path}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_path),
        SCOPES,
    )
    creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


class DriveClient:
    def __init__(self, credentials: Credentials) -> None:
        self.service = build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    def _execute(self, request):
        try:
            return request.execute(num_retries=3)
        except HttpError as exc:
            status = getattr(exc.resp, "status", "unknown")
            raise DriveError(
                f"Google Drive API error {status}: {exc}"
            ) from exc

    def ensure_root_folder(self, name: str = "Zan") -> dict:
        query = (
            f"mimeType = '{FOLDER_MIME}' "
            "and 'root' in parents "
            "and trashed = false "
            "and appProperties has { key='zan_role' and value='root' }"
        )

        result = self._execute(
            self.service.files().list(
                q=query,
                spaces="drive",
                fields="files(id,name,appProperties)",
                pageSize=10,
            )
        )
        folders = result.get("files", [])

        if folders:
            folder = folders[0]
            if folder.get("name") != name:
                folder = self._execute(
                    self.service.files().update(
                        fileId=folder["id"],
                        body={"name": name},
                        fields="id,name,appProperties",
                    )
                )
            return folder

        return self._execute(
            self.service.files().create(
                body={
                    "name": name,
                    "mimeType": FOLDER_MIME,
                    "parents": ["root"],
                    "appProperties": {"zan_role": "root"},
                },
                fields="id,name,appProperties",
            )
        )

    def ensure_collection_folder(
        self,
        *,
        root_folder_id: str,
        collection_key: str,
        collection_name: str,
        override_name: str | None = None,
    ) -> dict:
        query = (
            f"mimeType = '{FOLDER_MIME}' "
            f"and '{q_escape(root_folder_id)}' in parents "
            "and trashed = false "
            "and appProperties has "
            f"{{ key='zotero_collection_key' and value='{q_escape(collection_key)}' }}"
        )

        result = self._execute(
            self.service.files().list(
                q=query,
                spaces="drive",
                fields="files(id,name,appProperties)",
                pageSize=10,
            )
        )
        folders = result.get("files", [])
        desired_name = override_name or collection_name

        if folders:
            folder = folders[0]
            if folder.get("name") != desired_name:
                folder = self._execute(
                    self.service.files().update(
                        fileId=folder["id"],
                        body={"name": desired_name},
                        fields="id,name,appProperties",
                    )
                )
            return folder

        return self._execute(
            self.service.files().create(
                body={
                    "name": desired_name,
                    "mimeType": FOLDER_MIME,
                    "parents": [root_folder_id],
                    "appProperties": {
                        "zan_role": "collection",
                        "zotero_collection_key": collection_key,
                    },
                },
                fields="id,name,appProperties",
            )
        )

    def find_attachment_file(
        self,
        *,
        folder_id: str,
        collection_key: str,
        attachment_key: str,
    ) -> dict | None:
        query = (
            f"'{q_escape(folder_id)}' in parents "
            "and trashed = false "
            "and appProperties has "
            f"{{ key='zotero_collection_key' and value='{q_escape(collection_key)}' }} "
            "and appProperties has "
            f"{{ key='zotero_attachment_key' and value='{q_escape(attachment_key)}' }}"
        )

        result = self._execute(
            self.service.files().list(
                q=query,
                spaces="drive",
                fields=(
                    "files(id,name,md5Checksum,size,modifiedTime,appProperties)"
                ),
                pageSize=10,
            )
        )

        files = result.get("files", [])
        return files[0] if files else None

    def get_file(self, file_id: str) -> dict | None:
        try:
            return self.service.files().get(
                fileId=file_id,
                fields="id,name,trashed,appProperties,md5Checksum,size",
            ).execute(num_retries=3)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                return None
            raise DriveError(f"Could not read Drive file {file_id}: {exc}") from exc

    def upload_pdf(
        self,
        *,
        path: Path,
        folder_id: str,
        drive_name: str,
        collection_key: str,
        attachment_key: str,
        content_hash: str,
    ) -> dict:
        media = MediaFileUpload(
            str(path),
            mimetype="application/pdf",
            resumable=True,
        )

        return self._execute(
            self.service.files().create(
                body={
                    "name": drive_name,
                    "parents": [folder_id],
                    "appProperties": {
                        "zan_role": "zotero_pdf",
                        "zotero_collection_key": collection_key,
                        "zotero_attachment_key": attachment_key,
                        "zan_sha256": content_hash,
                    },
                },
                media_body=media,
                fields="id,name,appProperties,md5Checksum,size,modifiedTime",
            )
        )

    def update_pdf(
        self,
        *,
        file_id: str,
        path: Path,
        drive_name: str,
        collection_key: str,
        attachment_key: str,
        content_hash: str,
        upload_content: bool,
    ) -> dict:
        body = {
            "name": drive_name,
            "appProperties": {
                "zan_role": "zotero_pdf",
                "zotero_collection_key": collection_key,
                "zotero_attachment_key": attachment_key,
                "zan_sha256": content_hash,
            },
        }

        media = None
        if upload_content:
            media = MediaFileUpload(
                str(path),
                mimetype="application/pdf",
                resumable=True,
            )

        return self._execute(
            self.service.files().update(
                fileId=file_id,
                body=body,
                media_body=media,
                fields="id,name,appProperties,md5Checksum,size,modifiedTime",
            )
        )

    def trash_file(self, file_id: str) -> None:
        self._execute(
            self.service.files().update(
                fileId=file_id,
                body={"trashed": True},
                fields="id,trashed",
            )
        )

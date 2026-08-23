from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from .models import Collection, PdfAttachment


class ZoteroError(RuntimeError):
    pass


class ZoteroClient:
    def __init__(
        self,
        base_url: str = "http://localhost:23119/api/users/0",
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Zotero-API-Version": "3"})

    def _get(self, path: str) -> requests.Response:
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ZoteroError(
                "Could not reach Zotero. Make sure Zotero Desktop is running."
            ) from exc

        if response.status_code == 403:
            raise ZoteroError(
                "Zotero Local API returned 403. In Zotero enable: "
                "Settings -> Advanced -> Allow other applications on this "
                "computer to communicate with Zotero."
            )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ZoteroError(
                f"Zotero API error {response.status_code}: {response.text[:300]}"
            ) from exc

        return response

    def ping(self) -> None:
        self._get("/collections?limit=1")

    def collections(self) -> list[Collection]:
        payload = self._get("/collections").json()
        result: list[Collection] = []

        for raw in payload:
            data = raw.get("data", {})
            result.append(
                Collection(
                    key=raw["key"],
                    name=data.get("name", "(unnamed)"),
                    parent_key=data.get("parentCollection") or None,
                )
            )

        return result

    def resolve_collection(self, identifier: str) -> Collection:
        """
        Resolve either an exact Zotero collection key or a case-insensitive name.
        """
        collections = self.collections()

        for collection in collections:
            if collection.key == identifier:
                return collection

        matches = [
            c for c in collections
            if c.name.casefold() == identifier.casefold()
        ]

        if not matches:
            raise ZoteroError(f'Collection not found: "{identifier}"')

        if len(matches) > 1:
            options = ", ".join(f"{c.name} [{c.key}]" for c in matches)
            raise ZoteroError(
                f'Multiple collections are named "{identifier}". '
                f"Use a collection key instead: {options}"
            )

        return matches[0]

    def collection_top_items(self, collection_key: str) -> list[dict]:
        return self._get(
            f"/collections/{collection_key}/items/top"
        ).json()

    def item_children(self, item_key: str) -> list[dict]:
        return self._get(f"/items/{item_key}/children").json()

    def attachment_path(self, attachment_key: str) -> Path:
        response = self._get(f"/items/{attachment_key}/file/view/url")
        file_url = response.text.strip()

        if not file_url:
            raise ZoteroError(
                f"Zotero returned no local file URL for attachment {attachment_key}"
            )

        parsed = urlparse(file_url)
        if parsed.scheme != "file":
            raise ZoteroError(
                f"Unexpected attachment URL for {attachment_key}: {file_url}"
            )

        path_text = unquote(parsed.path)

        # file:///C:/Users/... -> C:/Users/... on Windows
        if os.name == "nt" and path_text.startswith("/"):
            path_text = path_text[1:]

        # UNC file URLs can carry the host in netloc.
        if parsed.netloc:
            if os.name == "nt":
                path_text = f"//{parsed.netloc}{path_text}"
            else:
                path_text = f"/{parsed.netloc}{path_text}"

        return Path(path_text)

    @staticmethod
    def _is_pdf_item(raw: dict) -> bool:
        data = raw.get("data", {})
        if data.get("itemType") != "attachment":
            return False

        content_type = (data.get("contentType") or "").casefold()
        filename = (data.get("filename") or "").casefold()

        return content_type == "application/pdf" or filename.endswith(".pdf")

    def collection_pdfs(
        self,
        collection_identifier: str,
    ) -> tuple[Collection, list[PdfAttachment], list[str]]:
        collection = self.resolve_collection(collection_identifier)
        top_items = self.collection_top_items(collection.key)

        pdfs: list[PdfAttachment] = []
        warnings: list[str] = []

        for item in top_items:
            item_data = item.get("data", {})
            item_key = item["key"]
            paper_title = item_data.get("title") or "(untitled Zotero item)"

            candidates: list[tuple[dict, str, str]] = []

            # Rare but valid: a standalone PDF is itself a collection item.
            if self._is_pdf_item(item):
                candidates.append((item, item_key, paper_title))
            else:
                for child in self.item_children(item_key):
                    if self._is_pdf_item(child):
                        candidates.append((child, item_key, paper_title))

            for attachment, parent_key, parent_title in candidates:
                attachment_data = attachment.get("data", {})
                attachment_key = attachment["key"]

                try:
                    path = self.attachment_path(attachment_key)
                except ZoteroError as exc:
                    warnings.append(
                        f"{parent_title}: could not resolve attachment "
                        f"{attachment_key}: {exc}"
                    )
                    continue

                if not path.exists():
                    warnings.append(
                        f"{parent_title}: PDF is not available locally: {path}"
                    )
                    continue

                if not path.is_file():
                    warnings.append(
                        f"{parent_title}: attachment path is not a file: {path}"
                    )
                    continue

                pdfs.append(
                    PdfAttachment(
                        collection_key=collection.key,
                        parent_item_key=parent_key,
                        attachment_key=attachment_key,
                        paper_title=parent_title,
                        attachment_title=attachment_data.get("title") or "",
                        filename=attachment_data.get("filename") or path.name,
                        path=path,
                        zotero_version=attachment.get("version"),
                    )
                )

        return collection, pdfs, warnings

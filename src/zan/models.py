from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Collection:
    key: str
    name: str
    parent_key: str | None = None


@dataclass(frozen=True)
class PdfAttachment:
    collection_key: str
    parent_item_key: str
    attachment_key: str
    paper_title: str
    attachment_title: str
    filename: str
    path: Path
    zotero_version: int | None = None

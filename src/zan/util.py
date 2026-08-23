from __future__ import annotations

import hashlib
import re
from pathlib import Path


INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_drive_filename(title: str, fallback_filename: str, max_length: int = 180) -> str:
    """
    Produce a readable PDF name for Drive/NotebookLM.

    Prefer the Zotero paper title. Fall back to the attachment filename.
    """
    fallback = Path(fallback_filename).stem or "paper"
    stem = (title or "").strip() or fallback
    stem = INVALID_WINDOWS_CHARS.sub(" ", stem)
    stem = WHITESPACE.sub(" ", stem).strip(" .")
    if not stem:
        stem = fallback
    stem = stem[:max_length].rstrip(" .")
    return f"{stem}.pdf"


def q_escape(value: str) -> str:
    """Escape a string for a Google Drive `q` literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")

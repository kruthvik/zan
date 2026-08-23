from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .drive import DriveClient
from .models import PdfAttachment
from .state import StateStore
from .util import safe_drive_filename, sha256_file
from .zotero import ZoteroClient


@dataclass
class SyncResult:
    collection_name: str
    collection_key: str
    drive_folder_id: str | None = None
    uploaded: int = 0
    updated: int = 0
    renamed: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _choose_names(pdfs: list[PdfAttachment]) -> dict[str, str]:
    """
    Make NotebookLM-friendly names while avoiding collisions inside a collection.
    """
    preliminary = {
        pdf.attachment_key: safe_drive_filename(
            pdf.paper_title,
            pdf.filename,
        )
        for pdf in pdfs
    }

    counts: dict[str, int] = {}
    for name in preliminary.values():
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1

    result: dict[str, str] = {}
    for pdf in pdfs:
        name = preliminary[pdf.attachment_key]
        if counts[name.casefold()] == 1:
            result[pdf.attachment_key] = name
            continue

        # Same paper title can occur twice. Keep names deterministic.
        stem = Path(name).stem
        original_stem = Path(pdf.filename).stem
        suffix = original_stem if original_stem and original_stem != stem else "copy"
        result[pdf.attachment_key] = (
            f"{stem} - {suffix} - {pdf.attachment_key}.pdf"
        )

    return result


def sync_collection(
    *,
    zotero: ZoteroClient,
    drive: DriveClient,
    state: StateStore,
    collection_identifier: str,
    root_folder_name: str = "Zan",
    drive_folder_name: str | None = None,
    dry_run: bool = False,
    delete_removed: bool = False,
) -> SyncResult:
    collection, pdfs, zotero_warnings = zotero.collection_pdfs(
        collection_identifier
    )

    result = SyncResult(
        collection_name=collection.name,
        collection_key=collection.key,
        warnings=list(zotero_warnings),
    )

    if dry_run:
        names = _choose_names(pdfs)
        previous = state.list_collection(collection.key)
        previous_keys = {r.attachment_key for r in previous}
        current_keys = {p.attachment_key for p in pdfs}

        for pdf in pdfs:
            content_hash = sha256_file(pdf.path)
            record = state.get(collection.key, pdf.attachment_key)
            if not record:
                result.uploaded += 1
            elif record.content_hash != content_hash:
                result.updated += 1
            elif record.drive_name != names[pdf.attachment_key]:
                result.renamed += 1
            else:
                result.unchanged += 1

        removed_keys = previous_keys - current_keys
        if delete_removed:
            result.removed = len(removed_keys)
        elif removed_keys:
            result.warnings.append(
                f"{len(removed_keys)} previously synced file(s) are no longer "
                "in this collection. They will remain in Drive unless you use "
                "--delete-removed."
            )
        return result

    root_folder = drive.ensure_root_folder(root_folder_name)
    collection_folder = drive.ensure_collection_folder(
        root_folder_id=root_folder["id"],
        collection_key=collection.key,
        collection_name=collection.name,
        override_name=drive_folder_name,
    )
    result.drive_folder_id = collection_folder["id"]

    names = _choose_names(pdfs)
    current_attachment_keys: set[str] = set()

    for pdf in pdfs:
        current_attachment_keys.add(pdf.attachment_key)
        drive_name = names[pdf.attachment_key]

        try:
            content_hash = sha256_file(pdf.path)
            record = state.get(collection.key, pdf.attachment_key)

            remote: dict | None = None

            # Fast path: reuse the known Drive ID.
            if record:
                remote = drive.get_file(record.drive_file_id)
                if remote and remote.get("trashed"):
                    remote = None

            # Recovery path: state DB was deleted or Drive ID changed.
            if remote is None:
                remote = drive.find_attachment_file(
                    folder_id=collection_folder["id"],
                    collection_key=collection.key,
                    attachment_key=pdf.attachment_key,
                )

            if remote is None:
                created = drive.upload_pdf(
                    path=pdf.path,
                    folder_id=collection_folder["id"],
                    drive_name=drive_name,
                    collection_key=collection.key,
                    attachment_key=pdf.attachment_key,
                    content_hash=content_hash,
                )

                state.upsert(
                    collection_key=collection.key,
                    attachment_key=pdf.attachment_key,
                    drive_file_id=created["id"],
                    drive_folder_id=collection_folder["id"],
                    drive_name=drive_name,
                    content_hash=content_hash,
                    zotero_version=pdf.zotero_version,
                    last_synced_at=_now_iso(),
                )
                result.uploaded += 1
                continue

            remote_props = remote.get("appProperties") or {}
            remote_hash = remote_props.get("zan_sha256")
            content_changed = remote_hash != content_hash
            name_changed = remote.get("name") != drive_name

            # If remote properties came from an older Zan version, our local
            # state is another safe comparison point.
            if remote_hash is None and record:
                content_changed = record.content_hash != content_hash

            if content_changed or name_changed:
                updated = drive.update_pdf(
                    file_id=remote["id"],
                    path=pdf.path,
                    drive_name=drive_name,
                    collection_key=collection.key,
                    attachment_key=pdf.attachment_key,
                    content_hash=content_hash,
                    upload_content=content_changed,
                )

                state.upsert(
                    collection_key=collection.key,
                    attachment_key=pdf.attachment_key,
                    drive_file_id=updated["id"],
                    drive_folder_id=collection_folder["id"],
                    drive_name=drive_name,
                    content_hash=content_hash,
                    zotero_version=pdf.zotero_version,
                    last_synced_at=_now_iso(),
                )

                if content_changed:
                    result.updated += 1
                else:
                    result.renamed += 1
            else:
                state.upsert(
                    collection_key=collection.key,
                    attachment_key=pdf.attachment_key,
                    drive_file_id=remote["id"],
                    drive_folder_id=collection_folder["id"],
                    drive_name=drive_name,
                    content_hash=content_hash,
                    zotero_version=pdf.zotero_version,
                    last_synced_at=_now_iso(),
                )
                result.unchanged += 1

        except Exception as exc:
            result.failed += 1
            result.warnings.append(
                f"{pdf.paper_title} [{pdf.attachment_key}]: {exc}"
            )

    previous = state.list_collection(collection.key)
    removed_records = [
        record
        for record in previous
        if record.attachment_key not in current_attachment_keys
    ]

    if delete_removed:
        for record in removed_records:
            try:
                drive.trash_file(record.drive_file_id)
                state.delete(collection.key, record.attachment_key)
                result.removed += 1
            except Exception as exc:
                result.failed += 1
                result.warnings.append(
                    f"Could not trash removed Drive file "
                    f"{record.drive_name}: {exc}"
                )
    elif removed_records:
        result.warnings.append(
            f"{len(removed_records)} previously synced file(s) are no longer "
            "in this Zotero collection. They were left in Drive. "
            "Use --delete-removed if you want Zan to trash them."
        )

    return result

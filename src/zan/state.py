from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SyncRecord:
    collection_key: str
    attachment_key: str
    drive_file_id: str
    drive_folder_id: str
    drive_name: str
    content_hash: str
    zotero_version: int | None
    last_synced_at: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS sync_records (
                collection_key TEXT NOT NULL,
                attachment_key TEXT NOT NULL,
                drive_file_id TEXT NOT NULL,
                drive_folder_id TEXT NOT NULL,
                drive_name TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                zotero_version INTEGER,
                last_synced_at TEXT NOT NULL,
                PRIMARY KEY (collection_key, attachment_key)
            );

            CREATE INDEX IF NOT EXISTS idx_sync_collection
            ON sync_records(collection_key);
            """
        )
        self.conn.commit()

    def get(self, collection_key: str, attachment_key: str) -> SyncRecord | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM sync_records
            WHERE collection_key = ? AND attachment_key = ?
            """,
            (collection_key, attachment_key),
        ).fetchone()

        return SyncRecord(**dict(row)) if row else None

    def list_collection(self, collection_key: str) -> list[SyncRecord]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM sync_records
            WHERE collection_key = ?
            ORDER BY drive_name
            """,
            (collection_key,),
        ).fetchall()

        return [SyncRecord(**dict(row)) for row in rows]

    def upsert(
        self,
        *,
        collection_key: str,
        attachment_key: str,
        drive_file_id: str,
        drive_folder_id: str,
        drive_name: str,
        content_hash: str,
        zotero_version: int | None,
        last_synced_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sync_records (
                collection_key,
                attachment_key,
                drive_file_id,
                drive_folder_id,
                drive_name,
                content_hash,
                zotero_version,
                last_synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_key, attachment_key)
            DO UPDATE SET
                drive_file_id = excluded.drive_file_id,
                drive_folder_id = excluded.drive_folder_id,
                drive_name = excluded.drive_name,
                content_hash = excluded.content_hash,
                zotero_version = excluded.zotero_version,
                last_synced_at = excluded.last_synced_at
            """,
            (
                collection_key,
                attachment_key,
                drive_file_id,
                drive_folder_id,
                drive_name,
                content_hash,
                zotero_version,
                last_synced_at,
            ),
        )
        self.conn.commit()

    def delete(self, collection_key: str, attachment_key: str) -> None:
        self.conn.execute(
            """
            DELETE FROM sync_records
            WHERE collection_key = ? AND attachment_key = ?
            """,
            (collection_key, attachment_key),
        )
        self.conn.commit()

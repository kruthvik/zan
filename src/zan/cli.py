from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .drive import DriveClient, DriveError, authenticate
from .state import StateStore
from .sync import SyncResult, sync_collection
from .zotero import ZoteroClient, ZoteroError


APP_DIR = Path.home() / ".zan"
TOKEN_PATH = APP_DIR / "token.json"
STATE_PATH = APP_DIR / "state.db"


def _print_result(result: SyncResult, *, dry_run: bool) -> None:
    title = "DRY RUN" if dry_run else "SYNC COMPLETE"
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    print(f"Collection : {result.collection_name}")
    print(f"Zotero key : {result.collection_key}")
    if result.drive_folder_id:
        print(f"Drive ID   : {result.drive_folder_id}")
    print()
    print(f"Uploaded   : {result.uploaded}")
    print(f"Updated    : {result.updated}")
    print(f"Renamed    : {result.renamed}")
    print(f"Unchanged  : {result.unchanged}")
    print(f"Removed    : {result.removed}")
    print(f"Failed     : {result.failed}")

    if result.warnings:
        print()
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True

    print("Zotero:")
    try:
        ZoteroClient().ping()
        print("  OK - local API is reachable")
    except ZoteroError as exc:
        ok = False
        print(f"  ERROR - {exc}")

    print("Google Drive:")
    if TOKEN_PATH.exists():
        try:
            creds = authenticate(token_path=TOKEN_PATH)
            DriveClient(creds)
            print(f"  OK - authenticated ({TOKEN_PATH})")
        except DriveError as exc:
            ok = False
            print(f"  ERROR - {exc}")
    else:
        ok = False
        print("  NOT AUTHENTICATED")
        print("  Run: zan auth --credentials credentials.json")

    print("Local state:")
    print(f"  {STATE_PATH}")

    return 0 if ok else 1


def cmd_collections(args: argparse.Namespace) -> int:
    zotero = ZoteroClient()
    collections = zotero.collections()

    if not collections:
        print("No Zotero collections found.")
        return 0

    print(f"{'NAME':45} KEY")
    print("-" * 58)
    for collection in sorted(collections, key=lambda c: c.name.casefold()):
        print(f"{collection.name[:45]:45} {collection.key}")

    return 0


def cmd_pdfs(args: argparse.Namespace) -> int:
    zotero = ZoteroClient()
    collection, pdfs, warnings = zotero.collection_pdfs(args.collection)

    print(f"{collection.name} [{collection.key}]")
    print(f"PDFs: {len(pdfs)}")
    print()

    for index, pdf in enumerate(pdfs, start=1):
        print(f"{index:>3}. {pdf.paper_title}")
        print(f"     attachment: {pdf.attachment_key}")
        print(f"     file:       {pdf.path}")

    if warnings:
        print()
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")

    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    credentials_path = Path(args.credentials).expanduser().resolve()
    authenticate(
        token_path=TOKEN_PATH,
        credentials_path=credentials_path,
        force=args.force,
    )
    print(f"Google Drive authentication saved to: {TOKEN_PATH}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    zotero = ZoteroClient()

    if args.dry_run:
        # Dry-run does not need Google Drive auth because it compares against
        # local sync state only.
        class _NoDrive:
            pass

        drive = _NoDrive()
    else:
        creds = authenticate(token_path=TOKEN_PATH)
        drive = DriveClient(creds)

    state = StateStore(STATE_PATH)
    try:
        result = sync_collection(
            zotero=zotero,
            drive=drive,  # type: ignore[arg-type]
            state=state,
            collection_identifier=args.collection,
            root_folder_name=args.root_folder,
            drive_folder_name=args.drive_folder,
            dry_run=args.dry_run,
            delete_removed=args.delete_removed,
        )
    finally:
        state.close()

    _print_result(result, dry_run=args.dry_run)
    return 1 if result.failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    zotero = ZoteroClient()
    collection = zotero.resolve_collection(args.collection)

    state = StateStore(STATE_PATH)
    try:
        records = state.list_collection(collection.key)
    finally:
        state.close()

    print(f"{collection.name} [{collection.key}]")
    print(f"Tracked Drive files: {len(records)}")
    print()

    for record in records:
        print(f"- {record.drive_name}")
        print(f"  Zotero attachment: {record.attachment_key}")
        print(f"  Drive file ID:     {record.drive_file_id}")
        print(f"  Last synced:       {record.last_synced_at}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zan",
        description=(
            "Sync PDF attachments from Zotero collections to Google Drive."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor",
        help="Check Zotero and Google Drive configuration.",
    )
    doctor.set_defaults(func=cmd_doctor)

    collections = sub.add_parser(
        "collections",
        help="List Zotero collections and keys.",
    )
    collections.set_defaults(func=cmd_collections)

    pdfs = sub.add_parser(
        "pdfs",
        help="Show PDFs Zan can see in a Zotero collection.",
    )
    pdfs.add_argument("collection", help="Collection name or Zotero key.")
    pdfs.set_defaults(func=cmd_pdfs)

    auth = sub.add_parser(
        "auth",
        help="Authenticate Zan with Google Drive.",
    )
    auth.add_argument(
        "--credentials",
        required=True,
        help="Path to Google Desktop OAuth credentials.json.",
    )
    auth.add_argument(
        "--force",
        action="store_true",
        help="Run the OAuth flow again even if a token exists.",
    )
    auth.set_defaults(func=cmd_auth)

    sync = sub.add_parser(
        "sync",
        help="Sync one Zotero collection to Google Drive.",
    )
    sync.add_argument("collection", help="Collection name or Zotero key.")
    sync.add_argument(
        "--root-folder",
        default="Zan",
        help='Drive root folder name (default: "Zan").',
    )
    sync.add_argument(
        "--drive-folder",
        default=None,
        help="Override the Drive folder name for this collection.",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without touching Drive.",
    )
    sync.add_argument(
        "--delete-removed",
        action="store_true",
        help=(
            "Trash Drive files previously synced from this collection "
            "when they are no longer present in Zotero."
        ),
    )
    sync.set_defaults(func=cmd_sync)

    status = sub.add_parser(
        "status",
        help="Show local sync state for a collection.",
    )
    status.add_argument("collection", help="Collection name or Zotero key.")
    status.set_defaults(func=cmd_status)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        code = args.func(args)
    except (ZoteroError, DriveError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        code = 130

    raise SystemExit(code)


if __name__ == "__main__":
    main()

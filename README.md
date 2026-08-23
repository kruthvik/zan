# Zan

Zan syncs PDF attachments from a Zotero collection into a matching Google Drive folder so those files can be selected as sources in NotebookLM.

## What it does

```text
Zotero collection
        |
        v
discover local PDFs
        |
        v
Google Drive / Zan / <collection>
        |
        v
NotebookLM
```

Zan:

- reads Zotero through the local Zotero API
- resolves the actual local PDF files
- creates a `Zan` folder in Google Drive
- creates one Drive subfolder per synced Zotero collection
- uploads new PDFs
- updates changed PDFs in place
- renames Drive files when the Zotero paper title changes
- avoids duplicates by attaching Zotero IDs to Drive files
- stores sync state in `~/.zan/state.db`
- can optionally trash Drive files removed from the Zotero collection
- uses the narrower Google Drive `drive.file` OAuth scope

It does **not** automatically add brand-new Drive files to a consumer NotebookLM notebook. After syncing, select the Drive files as NotebookLM sources. Once a Drive source is in NotebookLM, Drive-backed source updates can be resynced by NotebookLM.

---

## 1. Install

This project is set up for `uv`.

```bash
uv sync
```

Check that the CLI works:

```bash
uv run zan --help
```

## 2. Enable Zotero's local API

Keep Zotero Desktop running.

In Zotero:

```text
Settings
-> Advanced
-> Allow other applications on this computer to communicate with Zotero
```

Then:

```bash
uv run zan doctor
```

It is normal for Drive to say "NOT AUTHENTICATED" before step 4.

List Zotero collections:

```bash
uv run zan collections
```

Inspect PDFs in a collection before touching Drive:

```bash
uv run zan pdfs GRAIL
```

You can pass either a collection name or its Zotero key.

---

## 3. Create Google OAuth credentials

In Google Cloud:

1. Create or choose a Google Cloud project.
2. Enable the **Google Drive API**.
3. Configure the Google Auth consent screen.
4. Create an OAuth client with application type **Desktop app**.
5. Download the client JSON file.
6. Save it somewhere local, for example:

```text
C:\Projects\zan\credentials.json
```

Do not commit this file.

If your OAuth app is in testing mode and uses an External audience, add your own Google account as a test user.

---

## 4. Authenticate Drive

```bash
uv run zan auth --credentials credentials.json
```

Your browser opens Google's OAuth flow.

Zan saves the resulting user token at:

```text
~/.zan/token.json
```

The project requests:

```text
https://www.googleapis.com/auth/drive.file
```

This is intentionally narrower than full Drive access.

Run:

```bash
uv run zan doctor
```

You now want both Zotero and Google Drive to show `OK`.

---

## 5. First sync

Start with a dry run:

```bash
uv run zan sync GRAIL --dry-run
```

Then sync for real:

```bash
uv run zan sync GRAIL
```

The first run should produce something like:

```text
SYNC COMPLETE
Collection : GRAIL

Uploaded   : 12
Updated    : 0
Renamed    : 0
Unchanged  : 0
Removed    : 0
Failed     : 0
```

In Google Drive you should now have:

```text
My Drive/
└── Zan/
    └── GRAIL/
        ├── Paper One.pdf
        ├── Paper Two.pdf
        └── Paper Three.pdf
```

Zan names Drive PDFs from their Zotero paper titles because names like `document(7).pdf` are not a serious research interface.

---

## 6. Re-sync

Later:

```bash
uv run zan sync GRAIL
```

If nothing changed:

```text
Uploaded   : 0
Updated    : 0
Renamed    : 0
Unchanged  : 12
```

If a Zotero PDF was replaced:

```text
Updated    : 1
```

Zan updates the **same Drive file** instead of creating another copy.

---

## Removed files

By default Zan is conservative.

If a PDF disappears from the Zotero collection, the Drive copy is left alone and a warning is printed.

To trash Drive copies that no longer belong to the Zotero collection:

```bash
uv run zan sync GRAIL --delete-removed
```

This only affects files Zan previously synced for that collection.

---

## Useful commands

```bash
uv run zan doctor
uv run zan collections
uv run zan pdfs GRAIL
uv run zan auth --credentials credentials.json
uv run zan sync GRAIL --dry-run
uv run zan sync GRAIL
uv run zan sync GRAIL --delete-removed
uv run zan status GRAIL
```

Custom Drive names:

```bash
uv run zan sync GRAIL --root-folder Research --drive-folder GRAIL-Papers
```

---

## Local data

Zan stores:

```text
~/.zan/token.json
~/.zan/state.db
```

`token.json` contains Google OAuth credentials. Treat it like a secret.

The SQLite state database stores mappings such as:

```text
Zotero collection key
Zotero attachment key
Drive file ID
content hash
last sync time
```

Drive files also receive private `appProperties` containing their Zotero keys. That lets Zan recover matching even if the local SQLite state is lost.

---

## NotebookLM

After the Drive sync:

1. Open/create the corresponding NotebookLM notebook.
2. Add sources from Google Drive.
3. Select the PDFs in `Zan/<collection>`.

The Drive file IDs stay stable across Zan updates, so the bridge updates the existing Drive objects rather than spraying duplicate PDFs into your account.

---

## Current scope

Version 0.1 intentionally focuses on the useful core:

```text
Zotero collection -> PDFs -> Drive
```

Good next additions would be:

- Zotero annotations and notes
- Better BibTeX citation keys
- multiple collections in one command
- filesystem/watch mode
- a small desktop UI
- direct NotebookLM source creation if/when an appropriate API is available for the account being used

# Implant Inventory 2.7.0 — shared-folder edition

UI update: Manufacturer and Component Type share one horizontal filter row. Implant Type and Export shown rows share the second row. The Implant Type checkbox dropdown remains open while choices are selected. Inventory text is bold. Reorder is a fixed column with white YES/NO labels on red/green backgrounds, independent of manufacturer row colors. Rebuild and replace the desktop client folder; no host update is required.

The desktop apps and one always-on host exchange files through a folder you can already access, such as `\\SERVER\Perio\IMPLANTS\Inventory Exchange`. There are **no application accounts, access keys, URLs, certificates, or listening firewall ports**. Windows access to the shared folder controls access to the inventory, including component editing and imports.

The host keeps the live SQLite database on its own local drive. Staff PCs never open that database over the network. Completed backups and exchange files go into the shared folder. Staff can keep entering usage or stocking during an outage; each desktop retains a local queue and synchronizes when the host and folder become available.

This ZIP contains source and Windows build scripts, not precompiled EXEs. Rebuild both applications on Windows before using version 2. Windows GUI, packaging, DPAPI, and your office's actual SMB share need the acceptance check below.

## Desktop appearance

Choose **Light** or **Dark** in the status bar. The selection is saved for the next launch. Rounded navigation, content panels, blue primary actions, and a quieter footer establish the new hierarchy. See `UI_DESIGN.md` for layout, padding, typography, and platform limitations. This desktop update works with the existing version 2.4/2.4.1 host.

## Start here

1. Close the old desktop apps and stop the old host using **Ctrl+C**. Keep the existing database, `server.json`, and each PC's local cache.
2. Extract this package into a **new local folder** and run `BUILD_WINDOWS.cmd`. If Python 3.12 is missing, the script uses Windows Package Manager (`winget`) to install it for the current user. If `winget` is unavailable, it tells you to install Python 3.12 manually. The build skips automated tests and proceeds directly to dependency installation and EXE creation.
3. On the computer that will remain on, run **`SETUP_HOST.cmd`** from the extracted folder. If you built on a different computer, copy the complete extracted folder including `dist` to this host first.
4. The setup script installs the host application under `%LOCALAPPDATA%\ImplantInventoryHost\App`. When asked for `server.json`, select your **existing configuration** to keep its database. It detects the previous default at `C:\ImplantInventoryServer\server.json` when present. If you never set up a host, accept the new default.
5. Enter the full shared-folder path. The host will create its exchange subfolders using your existing Windows permissions. Choose a dedicated subfolder rather than the root of a share.
6. Choose whether to start the host automatically when you sign in. Setup creates a **Start Inventory Host** desktop shortcut, and optionally a Startup shortcut for your current Windows account. No administrator task or firewall change is required by this setup.
7. Copy the complete rebuilt `dist\ImplantInventory` folder to each staff PC, keeping `_internal` next to the EXE. Open the new `ImplantInventory.exe`.
8. On every PC, open **Setup**, enter the **same exchange folder**, and click **Save folder and sync**. There are no other connection fields.

Keep the host awake, connected to the office network, and signed in. Locking its screen is fine. With the optional Startup shortcut, the host restarts when you sign in after a reboot; it does not run while signed out. If an older Task Scheduler task was configured for the previous EXE, disable that old task before using the new shortcut so the old server does not restart alongside the new host.

If office policy blocks PowerShell scripts, use the manual executable commands below or the office's approved script-running method. The supplied scripts do not change execution policy or Windows permissions.

## Upgrade details

The guided setup makes a local database backup before upgrading an existing database. It preserves the `database` setting and copies the previous configuration to a dated `.bak` file beside it. It removes the old accounts, port, and certificate settings from the active configuration. The old backup may still contain the obsolete settings; it is not used by the new app.

The desktop uses the same `%LOCALAPPDATA%\ImplantInventory` cache as version 1, so unsent entries survive the upgrade when you keep the same Windows account and local cache. Old credential fields are ignored on load and removed when settings are saved. Select the exchange belonging to the host with your existing database; **do not re-import Excel if you already imported it**.

The database still has the same four tables and schema. Existing component rows, movements, and counts are preserved. Names on past entries remain as historical information. Newly entered Assistant and Signed off by names are ordinary recordkeeping fields, not authenticated identities. When neither exists, the entry records the originating computer name.

The host stores a non-secret identifier in `host.json` to prevent a different host/database from claiming the same exchange folder. This is not a login or access key. Once a desktop has synchronized with that host, it checks the identifier before sending further entries. For a completely separate test inventory, launch the desktop with `--config` pointing to a new local folder.

## Manual host setup

After building, keep the entire `dist\InventoryServer` folder together. Open PowerShell in that folder. To convert your existing configuration:

```powershell
.\InventoryServer.exe --config "C:\ImplantInventoryServer\server.json" --upgrade --exchange "\\SERVER\Perio\IMPLANTS\Inventory Exchange"
```

For a new configuration, substitute `--init` for `--upgrade`. If you do not have permission to create folders on `C:\`, choose a configuration path under your own Documents or LocalAppData folder. Keep it outside OneDrive and the shared folder. Relative database paths resolve beside the configuration file.

Then start the host:

```powershell
.\InventoryServer.exe --config "C:\ImplantInventoryServer\server.json"
```

The console prints the local database path and exchange folder. Leave it open. There is no web address or port. A default new `server.json` contains:

| Setting | Purpose |
|---|---|
| `database` | Host-local SQLite path; default `data/inventory.sqlite3` beside the config |
| `exchange_folder` | Full shared-folder path used by all PCs |
| `host_id` | Automatically generated, non-secret host identifier; preserve it |
| `poll_seconds` | Host checks for entries every 2 seconds by default |
| `backup_directory` | Empty means the exchange folder's `backups` subfolder; an existing custom setting is preserved on upgrade |
| `backup_hours` | Backup interval; default 12 hours |
| `fields` | Configurable Usage and Stocking fields |

The database path must remain local to the host. UNC and Windows mapped network drives are rejected for the live database. The exchange and backup paths may be UNC paths. Prefer a UNC path to a mapped drive letter so every PC uses the same location.

## How folder synchronization works

| Location | Contents |
|---|---|
| `inbox` | Complete, uniquely named requests from staff PCs |
| `responses` | Host acknowledgements and requested snapshots |
| `snapshots` | Latest shared inventory and recent history |
| `backups` | Completed database backups, unless a custom backup path is configured |
| `invalid` | Files with invalid message filenames, retained for inspection |
| `host.json` | Identifies the host and local database that own this exchange |

Files are first written to temporary names and then renamed when complete. The processor ignores incomplete transfers. Each stock movement retains its original UUID across retries. A movement and its quantity adjustment commit together, so an acknowledgement lost after a commit does not deduct stock a second time. Catalog updates and initial imports can also be retried after an uncertain response.

The host publishes inventory snapshots and responds to requests. Desktop caches and receipt removal commit together. Snapshot generations prevent an older shared snapshot from replacing a newer acknowledged view. A stale snapshot is reported as a host/folder issue; check that the host is open and the PCs' clocks are correct.

The host checks every 2 seconds; desktops sync every 15 seconds while open and also try immediately after saving. Typical visible changes take a few seconds to one desktop sync interval. The first sync must succeed before a new desktop has a component catalog for offline entry.

If the share or host is unavailable, **Usage and Stocking still save on the PC**. Its estimated quantity includes its own pending entries. Other disconnected PCs' entries appear only when they reconnect. The host can also process a fully published request while the sending desktop is closed. On reopening, the desktop retries safely and confirms delivery.

Component maintenance and imports need the host to confirm completion. If a dialog times out after publishing a request, that request can still be processed later. Sync and retry the same reviewed change rather than assuming it was cancelled. The application never deletes unconfirmed usage entries from the local queue.

Completed abandoned responses and incomplete transfer files expire after 24 hours. Pending request files are not removed by that cleanup. Logs, invalid files, and backups remain until deliberately archived or removed. The exchange files contain inventory and patient information readable by people with access to that folder; use the existing office folder permissions appropriate for that information. No file signatures or application access restrictions are applied in this edition.

## Forms, dashboard, and corrections

- **Usage:** date, REF ID, positive quantity used, tooth, a locked two-choice **Placed / O Box** selector, patient, periodontist, referring dentist, assistant, notes, and configurable extra fields. Entry controls use compact widths so the form does not stretch short values across the window.
- **Stocking:** date, REF ID, positive quantity added, signed off by, notes, and configurable extra fields.
- **REF ID lookup:** typing in the form filters existing REF IDs, with prefix matches first. Press Enter to autofill the best match. **Search / select** opens a searchable selector where typing continues to narrow REF IDs while the choices are visible. Each result includes its manufacturer, implant type, and size. An exact match immediately shows a green verification panel with the item name, component type, and estimated quantity.
- **Inventory:** one Quantity column shows the local estimated quantity; brief differences are possible while clients synchronize. Host, pending, Attention, and Other Parts columns are omitted from this view. Click headings to sort and combine manual column filters with the three compact checkbox menus. An empty menu means **Any**; each checked Manufacturer, Component Type, or Implant Type is included. Implant Type choices are naturally sorted and recalculated from the selected manufacturers and component types. Long lists flow into additional menu columns instead of expanding the dashboard vertically. A red marker appears only in the Reorder? cell when ordering is needed. A negative Quantity cell shows a yellow error marker.
- **History:** separate usage and addition tables show the latest 2,000 entries per log. Older records remain in the host database. Filtered CSV exports retain custom fields.
- **Sync queue:** pending entries, entries awaiting receipt confirmation, and rejected entries with reasons. Resolve an error then retry, or cancel a rejected entry and enter its replacement.
- **Setup:** one exchange-folder setting and the Excel migration preview. Anyone with folder access can edit components and import into an empty database.

Placed and O Box are usage classifications; both deduct the quantity. Negative current stock is retained and highlighted if disconnected PCs collectively report more usage than the last count. Reconcile physical counts using an addition or usage for the difference with an explanatory note.

Version 2.4.1 retains the larger type, compact entry layout, and searchable REF selector while correcting checkbox semantics, moving the filter choices into compact menus, and replacing the reversal workflow with direct usage-entry removal.

To remove an incorrect posted usage, select it in **Usage history** and choose **Remove selected entry**. Confirm the deletion; no written note is requested. The host permanently removes the row and restores its deducted quantity in one database transaction. This requires the host and exchange folder to be available and cannot be undone from the application. Stocking history remains read-only.

Edit `fields.usage` and `fields.addition` in the host's `server.json`, then restart the host to change form labels, required fields, or dropdown suggestions. For example:

```json
{"key": "assistant", "label": "Assistant", "required": true, "choices": ["Staff A", "Staff B"]}
```

Keep stable field keys. New custom keys such as `lot_number` are stored in `extra` and shown in history. Date, REF ID, and quantity remain required. Adding required fields while PCs are offline can make their older queued entries require attention at sync. Manufacturer, implant type, and component dropdown suggestions are configurable in `client.json`; see `examples/client.example.json`.

## Initial Excel migration

Skip this if your existing database is already initialized. For a new database, review `WORKBOOK_REVIEW.md` and preserve the original workbooks.

1. Select **Setup → Open Excel import**.
2. Choose the dashboard workbook and the standalone supply log workbook. The default mapping reads Base Supply at header row 4, reorder levels from Inventory Full Supply at header row 5, and Component Change Log at header row 5.
3. Enter a verified **end-of-day opening date**. The supplied Base Supply indicates 2026-08-27; confirm the count and date before using it.
4. Preview the validation report, projected quantities, and history. Reconcile the negative starting quantity for REF ID **36645**, and review the flagged identical log entries.
5. Check the verification box and import. An invalid import rolls back completely; a repeated successful bundle does not duplicate history.

Entries on/before the cutoff are retained as history and do not change the opening balance. Later entries change stock. Zero-quantity rows are reported and omitted. Identical source rows are preserved and flagged, not silently deleted. Unknown references and invalid opening quantities block the import. Ref IDs are uppercased while preserving leading zeros and punctuation. No Excel macro is executed.

For other layouts or a current-count snapshot, customize `examples/import-profile.json`. Initial import requires an empty database; it does not overwrite an existing inventory. Normal desktop entries must be after the opening date and no later than today.

## Backups and recovery

The host makes consistent database backups at startup and every 12 hours by default. Completed backups go to the configured backup folder and are not automatically deleted. Monitor available space. Failed share backups do not erase the live local database; the console reports the failure. For a manual backup:

```powershell
.\InventoryServer.exe --config "C:\ImplantInventoryServer\server.json" --backup "C:\InventoryBackups\inventory-manual.sqlite3"
```

Backups are produced to temporary files and only given their final names after completion. Do not copy the active database alone while it is running, because its WAL may contain recent commits.

To restore or move the host, stop every host and pause desktop syncing. Retain the current database files, configuration, and exchange folder before recovery. Restore to a new local database path, update the configuration, and archive `host.json` from the exchange folder so the new host can establish its new owner record. Preserve `host_id` when continuing the same inventory. Do not run two hosts against that exchange. Do not mix an old WAL with a restored database.

Recover and reconcile entries newer than the selected backup before resuming. Already acknowledged desktop entries are not replayed automatically after an old database restore. Retained requests may replay, so compare restored history and counts before allowing new work. Changing to a completely different inventory requires a separate client cache (`--config` with a new local folder).

## Complete uninstall

Run **`UNINSTALL.cmd`** under each Windows user profile where the desktop app or
host was used. The guided uninstaller stops both executables and removes that
profile's desktop cache, pending entries, host application, local host database,
shortcuts, and scheduled tasks that launch `InventoryServer.exe`. It offers a
final recovery backup on the Desktop before deleting the local database.

The shared exchange folder is retained by default because deleting it affects
every workstation. Removing it requires a separate confirmation phrase. The
desktop EXE folder may have been copied anywhere, so the uninstaller optionally
accepts that folder's path and removes it only when `ImplantInventory.exe` is
present. Python remains installed because other programs may use it.

Local desktop payloads still use Windows DPAPI encryption, tied to the original Windows profile. This does not require an application login. Keep that profile and disk to recover unsent entries; copying the encrypted cache to a different Windows account is not a recovery method.

## Build and acceptance check

`BUILD_WINDOWS.cmd` requires Windows. It locates Python 3.12 x64 or installs the per-user Python 3.12 package through `winget`, installs the pinned build dependencies, skips the test suite, and then produces:

- `dist\ImplantInventory\ImplantInventory.exe`
- `dist\InventoryServer\InventoryServer.exe`

Copy each entire output folder, including `_internal`. No Python installation is required on staff PCs using the built applications. Build once on a computer that can install the build dependencies; daily operation uses the existing shared folder and needs no internet.

After installation, verify both EXEs on Windows using a test entry: record usage on one PC, see the updated count on another, stop the host, record another entry, restart the host, and verify it is applied once. Reopen the desktop to confirm the local queue survives. Run the host shortcut after a restart/sign-in and confirm it publishes updates. Actual Windows and SMB behavior must be checked in your environment; `TEST_RESULTS.md` states what was executed during development.

## Source map

| File | Responsibility |
|---|---|
| `inventory/core.py` | Four tables, validation, transactions, imports, backups |
| `inventory/exchange.py` | Complete-file publishing, folder transport, host locks |
| `inventory/server.py` | Folder processor, host configuration, backups |
| `inventory/client.py` | Local outbox, cached quantities, synchronization |
| `inventory/desktop.py` | Forms, tables, setup, migration preview |
| `inventory/settings.py` | Configuration and form defaults |
| `inventory/secure.py` | Windows local-cache encryption |
| `inventory/migration.py` | Read-only Excel migration parsing |
| `setup_host.ps1` | Guided per-user installation and optional sign-in startup |
| `uninstall.ps1` / `UNINSTALL.cmd` | Guided local removal, optional backup, and separately confirmed shared-data removal |

Run tests from the project folder using `python -m unittest discover -s tests -v`.
#   i m p l a n t - t r a c k e r  
 #   i m p l a n t - t r a c k e r  
 #   i m p l a n t - t r a c k e r  
 
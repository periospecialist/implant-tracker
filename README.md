# Implant Inventory 2.8.0 — shared-folder edition

[![Build and release Windows apps](https://github.com/periospecialist/implant-tracker/actions/workflows/windows-release.yml/badge.svg)](https://github.com/periospecialist/implant-tracker/actions/workflows/windows-release.yml)

The Windows desktop app is available as a ready-to-run download and can check for new builds from its Setup tab. Every commit to the `master` branch creates a GitHub Release containing separate desktop and host ZIP files. See [USER_GUIDE.md](USER_GUIDE.md) for plain-language installation, updating, and host-moving instructions.

The desktop apps and one always-on host exchange files through a folder you can already access, such as `\\SERVER\Perio\IMPLANTS\Inventory Exchange`. There are **no application accounts, access keys, URLs, certificates, or listening firewall ports**. Windows access to the shared folder controls access to the inventory, including component editing and imports.

The host keeps the live SQLite database on its own local drive. Staff PCs never open that database over the network. Completed backups and exchange files go into the shared folder. Staff can keep entering usage or stocking during an outage; each desktop retains a local queue and synchronizes when the host and folder become available.

No Python installation is needed when using the prebuilt Windows downloads. Source builds are still supported for developers. Windows GUI, packaging, DPAPI, and your office's actual SMB share need the acceptance check below.

## Desktop appearance

Choose **Light** or **Dark** in the status bar. The selection is saved for the next launch. Rounded navigation, content panels, blue primary actions, and a quieter footer establish the new hierarchy. See `UI_DESIGN.md` for layout, padding, typography, and platform limitations. Desktop and host releases are built from the same commit.

## Start here — prebuilt Windows downloads

1. Open the [latest GitHub Release](https://github.com/periospecialist/implant-tracker/releases/latest).
2. On each staff PC, download `ImplantInventory-Windows.zip`, choose **Extract all**, then double-click **`INSTALL.cmd`**. It installs the program for the current Windows user and creates an **Implant Inventory** desktop shortcut. Windows SmartScreen may require **More info → Run anyway** because the executable is not code-signed.
3. On the computer that will remain on, download and extract `InventoryServer-Windows.zip`, then run **`SETUP_HOST.cmd`**. Keep an existing `server.json` and database when upgrading.
4. The setup script installs the host application under `%LOCALAPPDATA%\ImplantInventoryHost\App`. When asked for `server.json`, select your **existing configuration** to keep its database. It detects the previous default at `C:\ImplantInventoryServer\server.json` when present. If you never set up a host, accept the new default.
5. Enter the full shared-folder path. The host will create its exchange subfolders using your existing Windows permissions. Choose a dedicated subfolder rather than the root of a share.
6. Choose whether to start the host automatically when you sign in. Setup creates a **Start Inventory Host** desktop shortcut, and optionally a Startup shortcut for your current Windows account. No administrator task or firewall change is required by this setup.
7. Open **Implant Inventory** from the new desktop shortcut on each staff PC.
8. On every PC, open **Setup**, enter the **same exchange folder**, and click **Save folder and sync**. There are no other connection fields.

Keep the host awake, connected to the office network, and signed in. Locking its screen is fine. With the optional Startup shortcut, the host restarts when you sign in after a reboot; it does not run while signed out. If an older Task Scheduler task was configured for the previous EXE, disable that old task before using the new shortcut so the old server does not restart alongside the new host.

If office policy blocks PowerShell scripts, use the manual executable commands below or the office's approved script-running method. The supplied scripts do not change execution policy or Windows permissions.

## Desktop updates

The app quietly checks the repository's latest Release after startup. Open **Setup → Application updates** to check manually. When an update is available, choose **Download and install update**. The app downloads the GitHub HTTPS release asset, closes, replaces only its application folder, and reopens. Its configuration, encrypted offline cache, and unsent entries remain under `%LOCALAPPDATA%\ImplantInventory` and are not removed.

For a manual update, download and extract `ImplantInventory-Windows.zip`, then run `INSTALL.cmd` again. The underlying `Update.ps1` can also copy a centrally managed build:

```powershell
.\Update.ps1 -Source "P:\Implant_Inventory_Application\dist\ImplantInventory"
```

The destination is `%LOCALAPPDATA%\ImplantInventory\App`; the data folder itself is deliberately not replaced.

## Moving to a different host computer

This is a data move, not just a fresh installation. Stop the old host, take a final backup, and keep the old computer offline while moving. Copy its `server.json` and the complete local database directory to the new computer, preserving relative paths. Install the new host ZIP, remove or archive the exchange folder's `host.json`, then start the new host with the copied configuration. Preserve the existing `host_id`, never run both hosts, and verify inventory and a test sync before retiring the old machine. Detailed safeguards and a copyable prompt for working through the move with an assistant are in [USER_GUIDE.md](USER_GUIDE.md#move-the-host-to-another-computer).

## Upgrade details

The guided setup makes a local database backup before upgrading an existing database. It preserves the `database` setting and copies the previous configuration to a dated `.bak` file beside it. It removes the old accounts, port, and certificate settings from the active configuration. The old backup may still contain the obsolete settings; it is not used by the new app.

The desktop uses the same `%LOCALAPPDATA%\ImplantInventory` cache as version 1, so unsent entries survive the upgrade when you keep the same Windows account and local cache. Old credential fields are ignored on load and removed when settings are saved. Select the exchange belonging to the host with your existing database; **do not re-import Excel if you already imported it**.

Schema version 2 preserves the four original tables and adds an O-Box adjustment log. On first opening a version 1 database, the host creates a `.before-v2-<id>.bak` backup beside it, then atomically migrates both quantity constraints to allow zero. Update the host before desktops; older hosts cannot open the upgraded database. Existing component rows, movements, and counts are preserved. Names on past entries remain as historical information. Newly entered Assistant and Signed off by names are ordinary recordkeeping fields, not authenticated identities. When neither exists, the entry records the originating computer name.

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
| `inventory_csv_path` | Exact host-written current inventory CSV path; configured in Setup |
| `csv_backup_directory` | Folder for host-written, dated inventory CSV backups; configured in Setup |
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

- **Usage:** date, REF ID, nonnegative whole-number quantity used, tooth, a locked two-choice **Placed / O Box** selector, patient, periodontist, referring dentist, assistant, notes, and configurable extra fields. Entry controls use compact widths so the form does not stretch short values across the window.
- **Stocking:** date, REF ID, nonnegative whole-number quantity added, signed off by, notes, and configurable extra fields.
- **REF ID lookup:** typing in the form filters existing REF IDs, with prefix matches first. Press Enter to autofill the best match. **Search / select** opens a searchable selector where typing continues to narrow REF IDs while the choices are visible. Each result includes its manufacturer, implant type, and size. An exact match immediately shows a green verification panel with the item name, component type, and estimated quantity.
- **Name lookup:** Periodontist, Referring Dentist, and Assistant use the same prefix-first substring filtering and **Search / select** workflow as REF ID.
- **Inventory:** one Quantity column shows the local estimated quantity; brief differences are possible while clients synchronize. Host, pending, Attention, and Other Parts columns are omitted from this view. Click headings to sort and combine manual column filters with the three compact checkbox menus. An empty menu means **Any**; each checked Manufacturer, Component Type, or Implant Type is included. Implant Type choices are naturally sorted and recalculated from the selected manufacturers and component types. Long lists flow into additional menu columns instead of expanding the dashboard vertically. A red marker appears only in the Reorder? cell when ordering is needed. A negative Quantity cell shows a yellow error marker. Double-click a **Reorder at** cell to edit it directly; Enter or leaving the cell sends the revision-checked change to the host.
- **O-Box:** next to Inventory, with the same component columns, sorting, manufacturer/component/implant-type filters, and CSV export. Counts include all recorded O-Box usage (not just the latest 2,000 entries), minus reversed entries, plus audited count adjustments and this computer's pending O-Box usage. Empty components are hidden unless **Show empty components** is checked. Use **Set selected count** to record placement/removal or a physical recount without deducting supply again; a reason and a reachable updated host are required. Sync queued entries for the component first. Concurrent changes are rejected so staff can sync and review. Initial counts are historical estimates: reconcile against the physical box, since past removals were not recorded. Review counts again after correcting/deleting historical O-Box usage or replacing an Excel import. Negative counts remain visible for review.
- **History:** separate usage and addition tables show the latest 2,000 entries per log. Older records remain in the host database. Filtered CSV exports retain custom fields.
- **Sync queue:** pending entries, entries awaiting receipt confirmation, and rejected entries with reasons. Resolve an error then retry, or cancel a rejected entry and enter its replacement.
- **Setup:** the exchange-folder setting, host-owned automatic CSV settings, clinical name directories, and the Excel migration preview. Add or remove Periodontists, Referring Dentists, and Assistants centrally. Referring Dentists can be imported from a chosen CSV column; blank and case-insensitively matching names are skipped automatically. Set an exact current-inventory `.csv` path for an Excel dashboard and a separate folder for dated CSV backups. Only `InventoryServer.exe` writes these files, and it exports all inventory rows whenever authoritative inventory data changes. Leaving either destination blank disables that output. Anyone with folder access can edit components and import into an empty database.

Placed and O Box are usage classifications; both deduct the quantity from general supply. O Box also increases its separate O-Box count. Usage and Stocking accept quantity 0 for history-only backfill, including dates on/before the opening balance; those entries are marked as not changing stock and contribute zero to quantity totals. The default quantity remains 1. Negative current stock is retained and highlighted if disconnected PCs collectively report more usage than the last count. Reconcile physical counts using an addition or usage for the difference with an explanatory note.

The current interface retains the larger type, compact entry layout, searchable REF selector, compact filter menus, and direct usage-entry removal.

To remove an incorrect posted usage, select it in **Usage history** and choose **Remove selected entry**. Confirm the deletion; no written note is requested. The host permanently removes the row and restores its deducted quantity in one database transaction. This requires the host and exchange folder to be available and cannot be undone from the application. Stocking history remains read-only.

Edit `fields.usage` and `fields.addition` in the host's `server.json`, then restart the host to change form labels, required fields, or dropdown suggestions. For example:

```json
{"key": "assistant", "label": "Assistant", "required": true, "choices": ["Staff A", "Staff B"]}
```

Keep stable field keys. New custom keys such as `lot_number` are stored in `extra` and shown in history. Date, REF ID, and quantity remain required. Adding required fields while PCs are offline can make their older queued entries require attention at sync. Manufacturer, implant type, and component dropdown suggestions are configurable in `client.json`; see `examples/client.example.json`.

## Excel migration

For a new database, review `WORKBOOK_REVIEW.md` and preserve the original workbooks. If the previous Excel migration was incorrect, the same screen can replace it without deleting normal inventory activity entered afterward.

1. Select **Setup → Open Excel import**.
2. Choose the dashboard workbook and the standalone supply log workbook. The default mapping reads Base Supply at header row 4, reorder levels from Inventory Full Supply at header row 5, and Component Change Log at header row 5.
3. Enter a verified **end-of-day opening date**. The supplied Base Supply indicates 2026-08-27; confirm the count and date before using it.
4. Preview the validation report, projected quantities, and history. Reconcile the negative starting quantity for REF ID **36645**, and review the flagged identical log entries.
5. Check the verification box and import. An invalid import rolls back completely; a repeated successful bundle does not duplicate history.

To correct a completed migration, preview the corrected workbooks and select **Replace the previous Excel migration** before importing. Confirm the warning to remove the old imported opening inventory and imported history, install the corrected migration, and recalculate quantities. Entries created normally in the application are preserved and reapplied to the corrected opening balances.

Every imported REF ID with normal activity must remain in the corrected workbook. The replacement is blocked if a removed imported REF ID has normal activity, if the corrected workbook conflicts with a manually created component, or if a normal correction refers to imported history. Fix the reported conflict and preview again; a failed replacement rolls back completely.

Entries on/before the cutoff are retained as history and do not change the opening balance. Later entries change stock. Zero-quantity rows are reported and omitted. Identical source rows are preserved and flagged, not silently deleted. Unknown references and invalid opening quantities block the import. Ref IDs are uppercased while preserving leading zeros and punctuation. No Excel macro is executed.

For other layouts or a current-count snapshot, customize `examples/import-profile.json`. The first import requires an empty database. Later imports require the explicit replacement checkbox and replace only data from the previous Excel migration; they do not erase normal application entries. Positive-quantity desktop entries must be after the opening date and no later than today. Zero-quantity entries can predate the opening balance, but cannot be future-dated.

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

## Developer build and acceptance check

### Automated Windows builds and releases

GitHub Actions tests and builds both Windows x64 applications on every pull request and every push to `master`. A successful `master` push publishes the latest Release containing:

- `ImplantInventory-Windows.zip`
- `InventoryServer-Windows.zip`
- `SHA256SUMS.txt`

Pushing a version tag such as `v2.8.0` publishes the same verified packages under that version. The desktop ZIP contains the executable, `_internal`, installer, and updater at its root. The host ZIP contains the guided setup scripts and complete PyInstaller host folder. Manual workflow runs also publish a uniquely numbered Release.

`BUILD_WINDOWS.cmd` requires Windows. It locates Python 3.12 x64 or installs the per-user Python 3.12 package through `winget`, installs the pinned build dependencies, skips the test suite, and then produces:

- `dist\ImplantInventory\ImplantInventory.exe`
- `dist\InventoryServer\InventoryServer.exe`

Copy each entire output folder, including `_internal`. No Python installation is required on staff PCs using the built applications. Build once on a computer that can install the build dependencies; ordinary inventory operation uses the existing shared folder and needs no internet. Checking for or downloading an update does require internet access to GitHub.

The GitHub workflow in `.github/workflows/windows-release.yml` runs tests, builds both applications on Windows, packages the ready-to-run ZIPs, writes SHA-256 checksums, and publishes a latest Release for every commit to `master`.

After installation, verify both EXEs on Windows using a test entry: record usage on one PC, see the updated count on another, stop the host, record another entry, restart the host, and verify it is applied once. Reopen the desktop to confirm the local queue survives. Run the host shortcut after a restart/sign-in and confirm it publishes updates. Actual Windows and SMB behavior must be checked in your environment; `TEST_RESULTS.md` states what was executed during development.

## Source map

| File | Responsibility |
|---|---|
| `inventory/core.py` | Four tables, validation, transactions, imports, backups |
| `inventory/exchange.py` | Complete-file publishing, folder transport, host locks |
| `inventory/server.py` | Folder processor, host configuration, backups |
| `inventory/client.py` | Local outbox, cached quantities, synchronization |
| `inventory/desktop.py` | Forms, tables, setup, migration preview |
| `inventory/update.py` | GitHub Release discovery, safe ZIP extraction, updater hand-off |
| `inventory/settings.py` | Configuration and form defaults |
| `inventory/secure.py` | Windows local-cache encryption |
| `inventory/migration.py` | Read-only Excel migration parsing |
| `setup_host.ps1` | Guided per-user installation and optional sign-in startup |
| `Update.ps1` / `INSTALL.cmd` | Safe per-user desktop installation and program-only replacement |
| `uninstall.ps1` / `UNINSTALL.cmd` | Guided local removal, optional backup, and separately confirmed shared-data removal |

Run tests from the project folder using `python -m unittest discover -s tests -v`.

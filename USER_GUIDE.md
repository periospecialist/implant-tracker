# Implant Inventory user guide

This guide is for staff members and office administrators. You do not need Python or programming tools.

## Install the desktop app

1. Open the [latest Implant Inventory release](https://github.com/periospecialist/implant-tracker/releases/latest).
2. Download `ImplantInventory-Windows.zip`.
3. Right-click the ZIP, choose **Extract All**, and open the extracted folder.
4. Double-click `INSTALL.cmd`. If Windows SmartScreen appears, choose **More info → Run anyway** only if the download came from the repository above.
5. Open **Implant Inventory** from the desktop shortcut.
6. Open **Setup**, browse to the Inventory Exchange shared folder used by your office, and choose **Save folder and sync**.

The installer places program files in `%LOCALAPPDATA%\ImplantInventory\App`. Settings, the encrypted cache, and entries waiting to sync are stored separately in `%LOCALAPPDATA%\ImplantInventory`, so reinstalling the program does not erase them.

## Install or update the host

The host is the one Windows computer that stays on and owns the live database.

1. Download `InventoryServer-Windows.zip` from the [latest release](https://github.com/periospecialist/implant-tracker/releases/latest) and extract it.
2. Close the existing **Inventory Host** console with **Ctrl+C** if this is an update.
3. Double-click `SETUP_HOST.cmd`.
4. For an update, select the existing `server.json`. Do not create a new configuration or re-import Excel unless you are deliberately correcting the previous migration as described below.
5. Enter the same Inventory Exchange shared-folder path used by every desktop.
6. Allow the setup to make its upgrade backup. Start the host and confirm that desktops show **Folder sync ready**.

The host ZIP and desktop ZIP are separate. Installing a desktop update does not update the host.

## Correct a previous Excel migration

Use this only when the opening inventory or history imported from Excel was incorrect. It replaces data created by the previous Excel migration while preserving entries staff created normally in the application.

1. Preserve the original and corrected workbooks, and make a current host backup.
2. In the desktop app, select **Setup → Open Excel import** and choose the corrected workbooks.
3. Enter and verify the end-of-day opening date, then select **Preview and validate**.
4. Review the opening quantities, projected quantities, history, and every warning.
5. Select **Replace the previous Excel migration**, check the opening-inventory verification box, and choose **Import reviewed data**.
6. Read and confirm the replacement warning. When it finishes, compare several REF IDs and recent normal entries with your records.

The replacement removes the previous imported opening inventory and imported history, then recalculates stock from the corrected migration plus normal application entries. It does not remove normal additions or usages. If a REF ID has normal activity, keep that REF ID in the corrected workbook. Conflicts block the replacement and leave the existing database unchanged.

## Update a desktop

The app checks for releases after it opens. You can also open **Setup** and choose **Check for updates**.

If an update is available, choose **Download and install update**. The app downloads it, closes, replaces its program files, and reopens. Do not shut down Windows during this short replacement. If the automatic step is blocked by office policy, download the newest desktop ZIP and run `INSTALL.cmd` again.

## Move the host to another computer

Moving the host must preserve the database and its identity. Do this while the office is not entering inventory.

1. Make sure every desktop says **Folder sync ready** and has zero queued entries.
2. Stop the old host with **Ctrl+C** and do not start it again during the move.
3. Copy the existing `server.json` and its complete database directory, including any `-wal` or `-shm` files if present, to protected temporary storage. Prefer the most recent completed backup made after the host stopped.
4. Record the Inventory Exchange path. Keep the shared `inbox`, `responses`, and other exchange contents.
5. Install the host ZIP on the new computer. Place the copied configuration and database in a local, non-OneDrive folder. If the configuration uses a relative database path, keep the same layout.
6. In the shared Inventory Exchange folder, rename `host.json` to a dated backup such as `host.json.old-20260922`. Do not delete the other exchange folders.
7. Run `SETUP_HOST.cmd`, select the copied `server.json`, and enter the existing exchange path. This preserves the existing `host_id` while registering the new computer and local database path.
8. Start only the new host. Confirm that its console shows the intended database and exchange paths, then check inventory and make one test entry from a desktop.
9. Keep the old computer and backups unchanged until the test entry appears exactly once and all desktops reconnect.

Never run the old and new hosts at the same time. Never put the live SQLite database in the shared folder or OneDrive.

### Ask an assistant to guide the move

Copy this prompt into your trusted support assistant, then answer one step at a time:

> Help me move Implant Inventory to a new Windows host without losing or duplicating entries. Use the repository's USER_GUIDE.md and README.md as the authority. First ask me for the old server.json path, the database path it resolves to, the shared Inventory Exchange path, whether every desktop has zero queued entries, and whether the old host is stopped. Do not tell me to create a new database, change host_id, delete exchange data, or run both hosts. Give me one verified step at a time and pause before renaming host.json or starting the new host.

## Switch a desktop to another inventory host

Only do this when the desktop is intentionally joining a different inventory database.

1. Sync the current desktop until **Sync queue** is empty. Unsent entries belong to the old inventory and must not be carried into another one.
2. Open **Setup**, choose the new host's Inventory Exchange folder, and choose **Save folder and sync**.
3. If the app reports that the folder belongs to another host, close it. Keep the existing `%LOCALAPPDATA%\ImplantInventory` folder as a recovery copy and ask support to create a separate client configuration/cache for the new inventory.

Copyable support prompt:

> Help me switch this Implant Inventory desktop to a different host. First make me verify that Sync queue is empty and preserve the old LocalAppData cache. Treat a host-identity mismatch as a safety check; do not erase or edit the cache just to bypass it. Help me use a separate --config location if this is a different inventory database.

## Getting help safely

When asking an assistant for help, share error text and paths but do not upload patient names, exported history, the database, or exchange files. An assistant can diagnose installation and path problems without those records.

## O-Box and history-only backfill

Update the Inventory Host before updating desktops. The host automatically backs up the existing version 1 database beside the database file and upgrades it to version 2. Keep that backup for recovery; the old host cannot open the upgraded database. Do not re-import Excel for this update.

The **O-Box** tab beside Inventory uses the same sorting and component filters. Initially, its counts come from recorded O-Box usage. Review these against the physical box because historical removals were not recorded. Select a component and choose **Set selected count**, enter the physical quantity remaining, and give a reason. Use **Show empty components** to initialize a component that has no recorded O-Box stock. Count changes require the host; sync any queued entries for that component first.

When an implant leaves O-Box, reduce its O-Box count with **Set selected count**. This does not reduce general supply again. If you also need a patient usage record, enter it under Usage with quantity **0** and placement **Placed**, recording the actual number and circumstances in Notes. Counts and patient history are separate actions; finish both when needed.

In Usage and Stocking, quantity **0** saves a history entry without changing supply. This also allows dates on or before the opening balance date. Other required fields still apply, and future dates are rejected. The default remains **1**. Zero entries do not add to implant quantity totals or O-Box counts.

Correcting/deleting historical O-Box entries or replacing an Excel import can change O-Box counts; review the physical box afterward. Negative counts stay visible so discrepancies can be corrected.

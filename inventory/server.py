"""One host processes a shared folder; the four-table database stays on its local drive."""
import argparse
import csv
from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from uuid import uuid4

from .core import Database, ValidationError, normalize_event
from .exchange import PROTOCOL, atomic_json, check_local_database, exchange_path, host_lock, read_json
from .settings import DEFAULT_FIELDS, validate_config


INVENTORY_CSV_COLUMNS = [
    ("ref_id", "REF ID"), ("manufacturer", "Manufacturer"),
    ("implant_type", "Implant type"), ("size", "Size"),
    ("component_type", "Component"), ("quantity", "Quantity"),
    ("reorder_level", "Reorder at"), ("reorder", "Reorder?"),
]
STAFF_CHOICE_KEYS = ("periodontist", "referring_dentist", "assistant")


def normalize_names(values):
    """Return clean, case-insensitively deduplicated names in display order."""
    if not isinstance(values, list) or len(values) > 10000:
        raise ValidationError("Names must be a list with at most 10,000 entries")
    result, seen = [], set()
    for value in values:
        name = " ".join(str(value or "").split())
        if not name:
            continue
        if len(name) > 250:
            raise ValidationError("A name cannot exceed 250 characters")
        identity = name.casefold()
        if identity not in seen:
            seen.add(identity)
            result.append(name)
    return result


def update_staff_choices(fields, data):
    """Apply one additive/removal directory change without overwriting other fields."""
    if not isinstance(data, dict) or data.get("field") not in STAFF_CHOICE_KEYS:
        raise ValidationError("Choose Periodontist, Referring Dentist, or Assistant")
    action = data.get("action")
    if action not in ("add", "remove"):
        raise ValidationError("Directory action must be add or remove")
    key = data["field"]
    requested = normalize_names(data.get("values", []))
    updated = deepcopy(fields)
    definition = next((field for field in updated.get("usage", []) if field.get("key") == key), None)
    if definition is None:
        raise ValidationError(f"The {key.replace('_', ' ')} field is not configured")
    existing = normalize_names(definition.get("choices", []))
    if action == "add":
        definition["choices"] = normalize_names(existing + requested)
    else:
        removed = {name.casefold() for name in requested}
        definition["choices"] = [name for name in existing if name.casefold() not in removed]
    return updated


def validate_export_settings(data):
    """Validate host-owned CSV destinations while allowing either export to be disabled."""
    if not isinstance(data, dict):
        raise ValidationError("Export settings must be an object")
    csv_path = str(data.get("inventory_csv_path") or "").strip()
    backup_directory = str(data.get("csv_backup_directory") or "").strip()
    if csv_path and Path(csv_path).suffix.casefold() != ".csv":
        raise ValidationError("The automatic inventory export must have a .csv file name")
    if csv_path and not Path(csv_path).is_absolute():
        raise ValidationError("Enter the full path and file name for the automatic inventory CSV")
    if backup_directory and not Path(backup_directory).is_absolute():
        raise ValidationError("Enter the full path to the dated CSV backup folder")
    return {"inventory_csv_path": csv_path, "csv_backup_directory": backup_directory}


def inventory_csv_bytes(rows):
    """Build the same complete, spreadsheet-safe inventory view exported by the desktop."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow([label for _, label in INVENTORY_CSV_COLUMNS])
    for row in rows:
        values = []
        for key, _label in INVENTORY_CSV_COLUMNS:
            value = ("Yes" if row.get("quantity", 0) <= row.get("reorder_level", 0) else "No") \
                if key == "reorder" else row.get(key, "")
            text = str(value if value is not None else "")
            if text.lstrip().startswith(("=", "+", "-", "@")) and not isinstance(value, (int, float)):
                text = "'" + text
            values.append(text)
        writer.writerow(values)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class FolderProcessor:
    def __init__(self, database, folder, host_id, fields=None, export_settings=None,
                 save_export_settings=None, save_fields=None):
        self.database = database
        self.root = exchange_path(folder)
        self.host_id = host_id
        self.fields = fields or DEFAULT_FIELDS
        self.export_settings = validate_export_settings(export_settings or {})
        self.save_export_settings = save_export_settings
        self.save_fields = save_fields
        self.claim = {"protocol": PROTOCOL, "host_id": host_id, "computer": socket.gethostname(),
                      "database": os.path.normcase(database.path)}
        self.last_snapshot = 0
        self.last_cleanup = 0
        self.generation = 0
        self.last_export_digest = None

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        owner_path = self.root / "host.json"
        try:
            with owner_path.open("x", encoding="utf-8") as stream:
                json.dump(self.claim, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        if read_json(owner_path) != self.claim:
            raise RuntimeError("This exchange folder belongs to another host/database. Use its existing server.json and database, or choose a separate exchange folder.")
        for name in ("inbox", "responses", "snapshots", "backups", "invalid"):
            (self.root / name).mkdir(exist_ok=True)
        if self.generation == 0 and (self.root / "snapshots" / "latest.json").exists():
            previous = read_json(self.root / "snapshots" / "latest.json")
            self.generation = int(previous.get("generation", 0))

    def snapshot(self, known_ids=()):
        self.generation = max(self.generation + 1, time.time_ns())
        return {**self.database.snapshot(known_ids), "fields": self.fields,
                "export_settings": dict(self.export_settings),
                "host_id": self.host_id, "protocol": PROTOCOL, "host_computer": socket.gethostname(),
                "generation": self.generation}

    def handle(self, operation, data, computer):
        if not isinstance(data, dict):
            raise ValidationError("Request data must be an object")
        if operation == "/snapshot":
            ids = data.get("known_ids", [])
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValidationError("Receipt IDs must be a list")
            return self.snapshot(ids)
        if operation == "/events":
            events = data.get("events", [])
            if not isinstance(events, list) or not 1 <= len(events) <= 100:
                raise ValidationError("Send between 1 and 100 entries")
            results = []
            for data in events:
                try:
                    clean = normalize_event(data)
                    if clean["reversal_of"]:
                        # Reconstruct the exact opposite from the authoritative row. This also
                        # lets older desktop builds reverse without resupplying required fields.
                        with self.database.connect() as db:
                            original = self.database._find(db, clean["reversal_of"])
                        if not original:
                            raise ValidationError("The original entry was not found; sync history and try again")
                        clean.update(kind="addition" if original["kind"] == "usage" else "usage",
                                     ref_id=original["ref_id"], quantity=original["quantity"])
                    name = clean["assistant"] if clean["kind"] == "usage" else clean["signed_by"]
                    result = self.database.apply(clean, name or f"Computer: {computer}", self.fields[clean["kind"]])
                    results.append({**result, "status": "accepted"})
                except (ValueError, TypeError) as error:
                    results.append({"id": data.get("id", "") if isinstance(data, dict) else "",
                                    "status": "error", "error": str(error)})
            return {"results": results}
        if operation == "/delete_usage":
            result = self.database.delete_usage(data.get("id"))
            return {**result, "snapshot": self.snapshot()}
        if operation == "/components":
            return self.database.save_component(data)
        if operation == "/export_settings":
            settings = validate_export_settings(data)
            if self.save_export_settings is None:
                raise ValidationError("This host cannot persist export settings")
            self.save_export_settings(settings)
            self.export_settings = settings
            self.last_export_digest = None
            try:
                self.export_inventory()
                return {"saved": True, **settings}
            except OSError as error:
                logging.exception("CSV export settings were saved, but the first export failed")
                return {"saved": True, "warning": str(error), **settings}
        if operation == "/staff_choices":
            if self.save_fields is None:
                raise ValidationError("This host cannot persist directory changes")
            fields = update_staff_choices(self.fields, data)
            validate_config({"fields": fields})
            self.save_fields(fields)
            self.fields = fields
            definition = next(field for field in fields["usage"] if field["key"] == data["field"])
            return {"saved": True, "field": data["field"], "choices": definition["choices"]}
        if operation == "/import":
            return self.database.import_bundle(data, f"Excel import on {computer}")
        raise ValidationError("Unknown exchange operation")

    def process_file(self, path):
        identity = path.stem
        if not re.fullmatch(r"[0-9a-f]{32}", identity):
            os.replace(path, self.root / "invalid" / (uuid4().hex + ".json"))
            return
        response_path = self.root / "responses" / (identity + ".json")
        if response_path.exists():
            path.unlink(missing_ok=True)
            return
        response = {"id": identity, "host_id": self.host_id}
        try:
            request = read_json(path)
            if request.get("protocol") != PROTOCOL or request.get("id") != identity:
                raise ValidationError("Request ID or format is invalid")
            computer = str(request.get("computer", "Unknown"))[:120]
            response["result"] = self.handle(request.get("operation"), request.get("data"), computer)
        except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as error:
            response["error"] = str(error)
        # Operational errors leave the request in place; committed movements can retry safely.
        atomic_json(response_path, response)
        path.unlink(missing_ok=True)

    def run_once(self):
        self.initialize()
        paths = sorted((self.root / "inbox").glob("*.json"), key=lambda p: p.name)
        changed = False
        for path in paths[:200]:
            try:
                self.process_file(path)
                changed = True
            except FileNotFoundError:
                continue
            except (OSError, sqlite3.OperationalError):
                logging.exception("Could not process exchange request %s; it remains queued", path.name)
        if changed or self.last_snapshot == 0 or time.monotonic() - self.last_snapshot >= 5:
            atomic_json(self.root / "snapshots" / "latest.json", self.snapshot())
            self.last_snapshot = time.monotonic()
        try:
            self.export_inventory()
        except OSError:
            logging.exception("CSV export failed; the host will retry without interrupting folder sync")
        if time.monotonic() - self.last_cleanup >= 3600:
            self.cleanup()
            self.last_cleanup = time.monotonic()

    def export_inventory(self):
        """Write current and dated CSVs only when authoritative inventory changes."""
        if not any(self.export_settings.values()):
            return
        rows = self.database.snapshot(history_limit=0)["inventory"]
        content = inventory_csv_bytes(rows)
        digest = hashlib.sha256(content).digest()
        if digest == self.last_export_digest:
            return
        csv_path = self.export_settings["inventory_csv_path"]
        backup_directory = self.export_settings["csv_backup_directory"]
        if csv_path:
            atomic_bytes(csv_path, content)
        if backup_directory:
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            atomic_bytes(Path(backup_directory) / f"inventory-{stamp}.csv", content)
        self.last_export_digest = digest

    def cleanup(self):
        # Expire abandoned responses and partial transfer files, never queued requests.
        cutoff = time.time() - 86400
        for path in (self.root / "responses").glob("*.json"):
            if path.stat().st_mtime < cutoff and not (self.root / "inbox" / path.name).exists():
                path.unlink(missing_ok=True)
        for folder in ("inbox", "responses", "snapshots"):
            for path in (self.root / folder).glob("*.tmp"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)

    def run(self, stop, poll_seconds=2):
        while not stop.is_set():
            try:
                self.run_once()
            except (OSError, sqlite3.OperationalError):
                logging.exception("Exchange unavailable; the host will retry")
            stop.wait(poll_seconds)


def configure(path, folder, upgrade=False):
    path = Path(path).resolve()
    exchange_path(folder)
    if path.exists():
        if not upgrade:
            raise SystemExit("Configuration already exists. Use --upgrade --exchange FOLDER to retain its database and switch to shared-folder mode.")
        config = json.loads(path.read_text(encoding="utf-8"))
        backup = path.with_name(path.name + ".before-folder-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
        with backup.open("x", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
    else:
        config = {"database": "data/inventory.sqlite3", "backup_directory": "",
                  "backup_hours": 12, "fields": DEFAULT_FIELDS}
    for key in ("users", "host", "port", "certificate", "private_key"):
        config.pop(key, None)
    config.update(exchange_folder=folder, poll_seconds=2, host_id=config.get("host_id") or uuid4().hex)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, config)
    print(f"Configuration saved: {path}\nExchange folder: {folder}\nRun again without --init/--upgrade to start the host.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="server.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="Create shared-folder configuration")
    mode.add_argument("--upgrade", action="store_true", help="Keep existing database and switch to shared-folder mode")
    parser.add_argument("--exchange", help="Full path to the shared Inventory Exchange folder")
    parser.add_argument("--backup", metavar="FILE", help="Make a consistent backup and exit")
    args = parser.parse_args()
    if args.init or args.upgrade:
        if not args.exchange:
            parser.error("--exchange is required with --init or --upgrade")
        return configure(args.config, args.exchange, args.upgrade)
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    database_path = check_local_database(config_path.parent / config["database"])
    database = Database(database_path)
    if args.backup:
        print(database.backup(Path(args.backup).resolve()))
        return
    if not config.get("exchange_folder") or not config.get("host_id"):
        raise SystemExit("First run with --upgrade --exchange followed by your shared folder path. This keeps the existing database.")
    validate_config({"fields": config.get("fields", DEFAULT_FIELDS)})
    def save_export_settings(settings):
        config.update(settings)
        atomic_json(config_path, config)
    def save_fields(fields):
        config["fields"] = fields
        atomic_json(config_path, config)
    processor = FolderProcessor(
        database, config["exchange_folder"], config["host_id"], config.get("fields"),
        {key: config.get(key, "") for key in ("inventory_csv_path", "csv_backup_directory")},
        save_export_settings,
        save_fields,
    )
    stop = threading.Event()
    backup_directory = (config_path.parent / config["backup_directory"]).resolve() if config.get("backup_directory") else processor.root / "backups"
    def backups():
        while not stop.is_set():
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                database.backup(backup_directory / f"inventory-{stamp}.sqlite3")
            except Exception:
                logging.exception("Backup failed; check folder access. Live entries remain in the local database.")
            stop.wait(max(1, float(config.get("backup_hours", 12))) * 3600)
    with host_lock(database_path):
        print(f"Inventory host running\nDatabase: {database_path}\nExchange: {processor.root}\nKeep this window open. Ctrl+C to stop.")
        backup_thread = threading.Thread(target=backups, daemon=True)
        backup_thread.start()
        try:
            processor.run(stop, max(0.2, float(config.get("poll_seconds", 2))))
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            backup_thread.join(timeout=5)


if __name__ == "__main__":
    main()

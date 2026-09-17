"""Behavioral tests: concurrency, loss of acknowledgements, restart, migration and permissions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from uuid import uuid4

from inventory.client import LocalStore, RemoteError, SyncEngine, Transport
from inventory.core import Database, ValidationError, normalize_event
from inventory.migration import prepare
from inventory.server import FolderProcessor
from inventory.settings import default_config, load_config, save_config


TODAY = date.today().isoformat()
CUTOFF = (date.today() - timedelta(days=2)).isoformat()


def component(quantity=20):
    return {"ref_id": "001.TEST", "manufacturer": "Example", "implant_type": "Test type",
            "size": "4x10mm", "component_type": "Implant Body", "reorder_level": 5,
            "opening_quantity": quantity, "opening_date": CUTOFF}


def event(kind="usage", quantity=1, **values):
    return {"id": str(uuid4()), "kind": kind, "date": TODAY, "ref_id": "001.TEST", "quantity": quantity,
            "patient": "Synthetic patient", "assistant": "Test assistant", "periodontist": "Test provider",
            "placement": "Placed", "signed_by": "Test receiver", **values}


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "server.db")

    def tearDown(self):
        self.temp.cleanup()


class DatabaseTests(BaseTest):
    def test_exactly_four_tables_and_valid_foreign_keys(self):
        with self.db.connect() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"component_list", "inventory_supply", "component_usage_log", "component_addition_log"})
        with self.assertRaises(ValidationError):
            self.db.apply(event(), "Tester")

    def test_12_writers_no_lost_updates(self):
        self.db.save_component(component(500))
        def writer(number):
            for _ in range(25):
                self.db.apply(event(), f"Station {number}")
                self.db.snapshot(history_limit=1)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(writer, range(12)))
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["inventory"][0]["quantity"], 200)
        self.assertEqual(snapshot["totals"]["usage"], 300)

    def test_simultaneous_duplicate_retries_apply_once(self):
        self.db.save_component(component())
        movement = event(quantity=3)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: self.db.apply(movement, "Staff"), range(24)))
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 17)
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 1)
        with self.assertRaises(ValidationError):
            self.db.apply({**movement, "quantity": 4}, "Staff")
        with self.assertRaises(ValidationError):
            self.db.apply({**movement, "kind": "addition"}, "Staff")

    def test_negative_balance_preserves_actual_usage(self):
        self.db.save_component(component(1))
        self.db.apply(event(quantity=2), "Staff")
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], -1)

    def test_reversal_once_and_original_retained(self):
        self.db.save_component(component())
        movement = event(quantity=2)
        self.db.apply(movement, "Staff")
        reversal = event(kind="addition", quantity=2, reversal_of=movement["id"], notes="Entered twice")
        self.db.apply(reversal, "Staff")
        with self.assertRaises(ValidationError):
            self.db.apply({**reversal, "id": str(uuid4())}, "Staff")
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["inventory"][0]["quantity"], 20)
        self.assertEqual(snapshot["totals"], {"usage": 1, "addition": 1})

    def test_delete_usage_removes_history_and_restores_quantity(self):
        self.db.save_component(component())
        movement = event(quantity=3)
        self.db.apply(movement, "Staff")
        self.assertTrue(self.db.delete_usage(movement["id"])["deleted"])
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["inventory"][0]["quantity"], 20)
        self.assertEqual(snapshot["totals"], {"usage": 0, "addition": 0})
        self.assertFalse(self.db.delete_usage(movement["id"])["deleted"])

    def test_delete_reversed_usage_removes_pair_without_changing_quantity(self):
        self.db.save_component(component())
        movement = event(quantity=2)
        self.db.apply(movement, "Staff")
        self.db.apply(event(kind="addition", quantity=2, reversal_of=movement["id"], notes="Legacy correction"), "Staff")
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 20)
        self.assertEqual(self.db.delete_usage(movement["id"])["removed_rows"], 2)
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["inventory"][0]["quantity"], 20)
        self.assertEqual(snapshot["totals"], {"usage": 0, "addition": 0})

    def test_optimistic_catalog_conflict(self):
        self.db.save_component(component())
        row = self.db.snapshot()["inventory"][0]
        self.db.save_component({**row, "reorder_level": 8})
        with self.assertRaises(ValidationError):
            self.db.save_component({**row, "reorder_level": 9})

    def test_dates_and_quantities_are_validated(self):
        self.db.save_component(component())
        for changes in ({"quantity": 0}, {"quantity": 1.5}, {"quantity": True},
                        {"date": CUTOFF}, {"date": "not a date"},
                        {"date": (date.today() + timedelta(days=1)).isoformat()}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.db.apply(event(**changes), "Staff")

    def test_retry_survives_changed_required_fields(self):
        self.db.save_component(component())
        movement = event()
        self.db.apply(movement, "Staff")
        self.db.apply(movement, "Staff", [{"key": "new_field", "label": "New", "required": True}])
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 1)

    def test_import_cutoff_and_idempotency(self):
        old = event(quantity=8, date=CUTOFF)
        new = event(quantity=2)
        bundle = {"version": 1, "cutoff": CUTOFF, "components": [component()], "events": [old, new]}
        self.db.import_bundle(bundle, "Admin")
        self.assertTrue(self.db.import_bundle(bundle, "Admin")["already_imported"])
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["inventory"][0]["quantity"], 18)
        self.assertEqual(sorted(r["affects_stock"] for r in snapshot["logs"]["usage"]), [0, 1])
        with self.assertRaises(ValidationError):
            self.db.import_bundle({**bundle, "events": [old]}, "Admin")

    def test_failed_import_rolls_back_everything(self):
        bundle = {"version": 1, "cutoff": CUTOFF, "components": [component()],
                  "events": [event(), event(ref_id="DOES-NOT-EXIST")]}
        with self.assertRaises(ValidationError):
            self.db.import_bundle(bundle, "Admin")
        self.assertEqual(self.db.snapshot()["inventory"], [])
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 0)

    def test_backup_is_restorable(self):
        self.db.save_component(component())
        self.db.apply(event(), "Staff")
        target = self.root / "backup.db"
        self.db.backup(target)
        restored = Database(target)
        self.assertEqual(restored.snapshot()["inventory"][0]["quantity"], 19)
        with restored.connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class LocalTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.db.save_component(component())
        self.store = LocalStore(self.root / "local.db")
        self.store.install_snapshot(self.db.snapshot())

    def test_offline_queue_survives_restart(self):
        self.store.queue(event(quantity=2))
        reopened = LocalStore(self.root / "local.db")
        self.assertEqual(len(reopened.entries()), 1)
        self.assertEqual(reopened.view()[2][0]["estimated_quantity"], 18)

    def test_lost_acknowledgment_and_snapshot_recovery(self):
        movement = event(quantity=2)
        self.store.queue(movement)
        db = self.db
        class FakeTransport:
            fail_post, fail_snapshot = True, True
            def post(fake, path, data):
                if path == "/events":
                    results = [{**db.apply(e, "Staff"), "status": "accepted"} for e in data["events"]]
                    if fake.fail_post:
                        fake.fail_post = False
                        raise RemoteError("Response lost after commit")
                    return {"results": results}
                if fake.fail_snapshot:
                    fake.fail_snapshot = False
                    raise RemoteError("Snapshot unavailable")
                return db.snapshot(data["known_ids"])
        engine = SyncEngine(self.store, FakeTransport())
        for _ in range(2):
            with self.assertRaises(RemoteError):
                engine.sync()
            self.assertEqual(self.store.view()[2][0]["estimated_quantity"], 18)
        engine.sync()
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(self.store.view()[2][0]["estimated_quantity"], 18)
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 1)

    def test_rejected_entry_is_visible_and_can_retry(self):
        identity = self.store.queue(event())
        self.store.record_results([{"id": identity, "status": "error", "error": "Missing new field"}])
        self.assertEqual(self.store.entries()[0]["state"], "error")
        self.assertEqual(self.store.view()[2][0]["estimated_quantity"], 20)
        self.store.retry(identity)
        self.assertEqual(self.store.entries()[0]["state"], "pending")
        with self.assertRaises(ValidationError):
            self.store.cancel_rejected(identity)
        self.store.record_results([{"id": identity, "status": "error", "error": "Missing new field"}])
        self.store.cancel_rejected(identity)
        self.assertEqual(self.store.entries(), [])

    def test_snapshot_receipts_not_limited_by_history_window(self):
        movement = event()
        self.store.queue(movement)
        self.db.apply(movement, "Staff")
        for _ in range(3):
            self.db.apply(event(), "Staff")
        self.store.record_results([{"id": movement["id"], "status": "accepted"}])
        self.store.install_snapshot(self.db.snapshot([movement["id"]], history_limit=1))
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(self.store.view()[2][0]["estimated_quantity"], 16)

    def test_large_offline_queue_uses_bounded_receipts(self):
        db = self.db
        class FakeTransport:
            fail = True
            def post(fake, path, data):
                if path == "/events":
                    return {"results": [{**db.apply(e, "Staff"), "status": "accepted"} for e in data["events"]]}
                if fake.fail:
                    fake.fail = False
                    raise RemoteError("Snapshot unavailable")
                return db.snapshot(data["known_ids"])
        for _ in range(510):
            self.store.queue(event(kind="addition"))
        engine = SyncEngine(self.store, FakeTransport())
        with self.assertRaises(RemoteError):
            engine.sync()
        engine.sync()
        self.assertEqual(self.store.view()[2][0]["estimated_quantity"], 530)
        engine.sync()
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 530)


class FolderTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.db.save_component(component(100))
        self.folder = self.root / "exchange"
        self.server = FolderProcessor(self.db, str(self.folder), "test-host")
        self.server.run_once()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.server.run, args=(self.stop, 0.01), daemon=True)
        self.thread.start()
        # Windows CI can take several seconds to durably commit 60 movements while
        # twelve clients poll the same folder. Production uses a 30-second timeout.
        self.config = {"exchange_folder": str(self.folder), "response_timeout": 15}

    def tearDown(self):
        self.stop.set()
        self.thread.join()
        super().tearDown()

    def test_no_credentials_needed_for_catalog_or_inventory(self):
        transport = Transport(self.config)
        self.assertNotIn("user", transport.post("/snapshot", {}))
        self.assertTrue(transport.post("/components", {**component(), "ref_id": "NEW"})["saved"])
        self.assertEqual(len(self.db.snapshot()["inventory"]), 2)

    def test_12_clients_read_write_through_folder(self):
        def client(number):
            store = LocalStore(self.root / f"client-{number}.db")
            for _ in range(5):
                store.queue(event())
            SyncEngine(store, Transport(self.config)).sync()
            self.assertEqual(store.entries(), [])
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(client, range(12)))
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 40)
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 60)

    def test_required_fields_and_custom_data(self):
        transport = Transport(self.config)
        response = transport.post("/events", {"events": [event(patient="")]})
        self.assertEqual(response["results"][0]["status"], "error")
        response = transport.post("/events", {"events": [event(extra={"lot": "ABC-123"})]})
        self.assertEqual(response["results"][0]["status"], "accepted")
        accepted_id = response["results"][0]["id"]
        self.assertEqual(transport.post("/snapshot", {"known_ids": [accepted_id]})["logs"]["usage"][0]["extra"], {"lot": "ABC-123"})

    def test_host_settings_operations_through_folder_transport(self):
        saved_exports, saved_fields = [], []
        self.server.save_export_settings = saved_exports.append
        self.server.save_fields = saved_fields.append
        transport = Transport(self.config)
        export_result = transport.post("/export_settings", {
            "inventory_csv_path": "", "csv_backup_directory": ""})
        directory_result = transport.post("/staff_choices", {
            "field": "assistant", "action": "add", "values": ["New Assistant"]})
        self.assertTrue(export_result["saved"])
        self.assertEqual(saved_exports[-1], {"inventory_csv_path": "", "csv_backup_directory": ""})
        self.assertEqual(directory_result["choices"], ["New Assistant"])
        self.assertEqual(saved_fields[-1], self.server.fields)

    def test_url_is_rejected_as_folder(self):
        with self.assertRaises(ValueError):
            Transport({"exchange_folder": "https://server:8765"})


class SettingsTests(BaseTest):
    def test_config_roundtrip_and_fields_validation(self):
        path = self.root / "client.json"
        config = default_config()
        config["exchange_folder"] = str(self.root / "exchange")
        save_config(path, config)
        self.assertEqual(load_config(path)["exchange_folder"], config["exchange_folder"])
        config["fields"]["usage"].append({"key": "quantity", "label": "Duplicate"})
        with self.assertRaises(ValueError):
            save_config(path, config)


if __name__ == "__main__":
    unittest.main()

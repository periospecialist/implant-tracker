"""Failure cases for the actual file exchange protocol; no network service required."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from inventory.client import LocalStore, SyncEngine, Transport, RemoteError
from inventory.core import Database
from inventory.exchange import PROTOCOL, atomic_json, host_lock, read_json
from inventory.server import FolderProcessor, configure
from inventory.settings import load_config, save_config
from test_system import component, event


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "inventory.db")
        self.db.save_component(component(20))
        self.folder = self.root / "exchange"
        self.host = FolderProcessor(self.db, str(self.folder), "host-A")
        self.host.run_once()
        self.config = {"exchange_folder": str(self.folder), "response_timeout": 0.05}

    def tearDown(self):
        self.temp.cleanup()

    def request(self, operation, data):
        identity = uuid4().hex
        path = self.folder / "inbox" / (identity + ".json")
        atomic_json(path, {"protocol": PROTOCOL, "id": identity, "operation": operation, "data": data, "computer": "Test PC"})
        return path

    def test_commit_then_response_failure_recovers_once(self):
        movement = event(quantity=3)
        path = self.request("/events", {"events": [movement]})
        with patch("inventory.server.atomic_json", side_effect=OSError("Share disconnected")):
            with self.assertRaises(OSError):
                self.host.process_file(path)
        self.assertTrue(path.exists())
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 17)
        self.host.process_file(path)
        response = read_json(self.folder / "responses" / path.name)
        self.assertTrue(response["result"]["results"][0]["duplicate"])
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 1)

    def test_stopped_host_then_reopened_client_delivers_once(self):
        store = LocalStore(self.root / "client.db")
        store.install_snapshot(self.host.snapshot())
        store.queue(event(quantity=2))
        transport = Transport(self.config)
        transport.poll_seconds = 0.01
        with self.assertRaises(RemoteError):
            SyncEngine(store, transport).sync()
        self.assertEqual(len(list((self.folder / "inbox").glob("*.json"))), 1)
        self.assertEqual(len(store.entries()), 1)
        # The host processes the original request while the desktop is closed.
        self.host.run_once()
        stop = threading.Event()
        worker = threading.Thread(target=self.host.run, args=(stop, 0.01))
        worker.start()
        try:
            reopened = LocalStore(self.root / "client.db")
            SyncEngine(reopened, Transport({**self.config, "response_timeout": 3})).sync()
            self.assertEqual(reopened.entries(), [])
            self.assertEqual(reopened.view()[2][0]["estimated_quantity"], 18)
            self.assertEqual(self.db.snapshot()["totals"]["usage"], 1)
        finally:
            stop.set()
            worker.join()

    def test_unavailable_share_preserves_local_entry(self):
        store = LocalStore(self.root / "client.db")
        store.queue(event())
        with patch("inventory.exchange.atomic_json", side_effect=PermissionError("Folder unavailable")):
            with self.assertRaises(RemoteError):
                SyncEngine(store, Transport(self.config)).sync()
        self.assertEqual(store.entries()[0]["state"], "pending")

    def test_partial_files_are_not_processed(self):
        path = self.folder / "inbox" / "incomplete.tmp"
        path.write_text('{"unfinished":', encoding="utf-8")
        self.host.run_once()
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 0)
        self.assertTrue(path.exists())

    def test_duplicate_requests_do_not_duplicate_events(self):
        movement = event()
        self.request("/events", {"events": [movement]})
        self.request("/events", {"events": [movement]})
        self.host.run_once()
        self.assertEqual(self.db.snapshot()["totals"]["usage"], 1)

    def test_reversal_is_reconstructed_and_clears_the_client_queue(self):
        original = event(kind="usage", quantity=3)
        self.db.apply(original, "Staff")
        self.host.run_once()
        store = LocalStore(self.root / "reversal-client.db")
        store.install_snapshot(self.host.snapshot())
        # Simulate the sparse correction created by the UI, including stale/wrong
        # copied movement details. The host must use its authoritative original.
        store.queue({"kind": "usage", "date": original["date"], "ref_id": "STALE.REF",
                     "quantity": 999, "reversal_of": original["id"], "notes": "Entered in error"})
        stop = threading.Event()
        worker = threading.Thread(target=self.host.run, args=(stop, 0.01))
        worker.start()
        try:
            SyncEngine(store, Transport({**self.config, "response_timeout": 3})).sync()
        finally:
            stop.set()
            worker.join()
        snapshot = self.db.snapshot()
        self.assertEqual(store.entries(), [])
        self.assertEqual(snapshot["inventory"][0]["quantity"], 20)
        self.assertEqual(snapshot["totals"], {"usage": 1, "addition": 1})
        self.assertEqual(snapshot["logs"]["addition"][0]["reversal_of"], original["id"])

    def test_delete_usage_through_exchange_restores_stock(self):
        original = event(kind="usage", quantity=3)
        self.db.apply(original, "Staff")
        path = self.request("/delete_usage", {"id": original["id"]})
        self.host.run_once()
        response = read_json(self.folder / "responses" / path.name)["result"]
        self.assertTrue(response["deleted"])
        self.assertEqual(response["snapshot"]["inventory"][0]["quantity"], 20)
        self.assertEqual(response["snapshot"]["totals"]["usage"], 0)

    def test_delete_usage_retry_after_lost_response_does_not_restore_twice(self):
        original = event(kind="usage", quantity=3)
        self.db.apply(original, "Staff")
        path = self.request("/delete_usage", {"id": original["id"]})
        with patch("inventory.server.atomic_json", side_effect=OSError("Share disconnected")):
            with self.assertRaises(OSError):
                self.host.process_file(path)
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 20)
        self.host.process_file(path)
        response = read_json(self.folder / "responses" / path.name)["result"]
        self.assertFalse(response["deleted"])
        self.assertEqual(response["snapshot"]["inventory"][0]["quantity"], 20)

    def test_component_create_and_edit_survive_lost_response(self):
        new_component = {**component(9), "ref_id": "SECOND"}
        path = self.request("/components", new_component)
        with patch("inventory.server.atomic_json", side_effect=OSError("Share unavailable")):
            with self.assertRaises(OSError):
                self.host.process_file(path)
        self.host.process_file(path)
        row = next(c for c in self.db.snapshot()["inventory"] if c["ref_id"] == "SECOND")
        edited = {**row, "reorder_level": 6}
        self.db.save_component(edited)
        self.assertTrue(self.db.save_component(edited)["duplicate"])
        self.assertEqual(next(c for c in self.db.snapshot()["inventory"] if c["ref_id"] == "SECOND")["revision"], 2)

    def test_stale_snapshot_does_not_roll_back_local_cache(self):
        store = LocalStore(self.root / "client.db")
        old = self.host.snapshot()
        self.db.apply(event(quantity=2), "Staff")
        new = self.host.snapshot()
        store.install_snapshot(new)
        store.install_snapshot(old)
        self.assertEqual(store.view()[2][0]["server_quantity"], 18)

    def test_wrong_host_is_rejected_before_sending(self):
        transport = Transport(self.config)
        transport.expected_host_id = "other-host"
        with self.assertRaises(RemoteError):
            transport.post("/events", {"events": [event()]})
        self.assertEqual(list((self.folder / "inbox").glob("*.json")), [])

    def test_second_host_cannot_claim_exchange(self):
        other = FolderProcessor(Database(self.root / "other.db"), str(self.folder), "host-B")
        with self.assertRaises(RuntimeError):
            other.initialize()

    def test_second_local_processor_is_blocked(self):
        with host_lock(self.db.path):
            with self.assertRaises(RuntimeError):
                with host_lock(self.db.path):
                    pass

    def test_stale_heartbeat_reports_offline(self):
        snapshot = self.host.snapshot()
        snapshot["as_of"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        atomic_json(self.folder / "snapshots" / "latest.json", snapshot)
        with self.assertRaises(RemoteError):
            Transport(self.config).post("/snapshot", {})

    def test_cleanup_keeps_pending_requests(self):
        path = self.request("/events", {"events": [event()]})
        os.utime(path, (1, 1))
        self.host.cleanup()
        self.assertTrue(path.exists())

    def test_upgrade_preserves_existing_database_and_removes_accounts(self):
        path = self.root / "server.json"
        path.write_text(json.dumps({"database": "inventory.db", "users": [{"token": "obsolete"}],
                                   "port": 8765, "certificate": "old.pem"}), encoding="utf-8")
        configure(path, str(self.folder), upgrade=True)
        config = read_json(path)
        self.assertEqual(config["database"], "inventory.db")
        self.assertNotIn("users", config)
        self.assertNotIn("port", config)
        self.assertNotIn("certificate", config)
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 20)
        self.assertEqual(len(list(self.root.glob("server.json.before-folder-*.bak"))), 1)

    def test_old_client_credentials_are_removed_without_decryption(self):
        path = self.root / "client.json"
        path.write_text(json.dumps({"server_url": "http://old", "token": "obsolete", "token_protected": "not-decryptable"}), encoding="utf-8")
        config = load_config(path)
        save_config(path, config)
        self.assertNotIn("token", read_json(path))
        self.assertNotIn("token_protected", read_json(path))
        self.assertNotIn("server_url", read_json(path))


if __name__ == "__main__":
    unittest.main()

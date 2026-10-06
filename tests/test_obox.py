"""O-Box balances and schema upgrades must never double-deduct general supply."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from uuid import uuid4

from test_system import BaseTest, component, event, CUTOFF
from inventory.client import LocalStore
from inventory.core import Database, ValidationError
from inventory.server import FolderProcessor


class OBoxTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.db.save_component(component())

    def adjustment(self, expected, quantity):
        return {"id": str(uuid4()), "ref_id": "001.TEST", "expected": expected,
                "quantity": quantity, "reason": "Physical count / placed implants"}

    def test_zero_backfill_both_logs_and_delete_preserve_supply(self):
        for kind in ("usage", "addition"):
            entry = event(kind=kind, quantity=0, date=CUTOFF)
            self.db.apply(entry, "Staff")
            self.assertTrue(self.db.apply(entry, "Staff")["duplicate"])
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["inventory"][0]["quantity"], 20)
        self.assertEqual(snapshot["totals"], {"usage": 1, "addition": 1})
        for rows in snapshot["logs"].values():
            self.assertEqual(rows[0]["affects_stock"], 0)
        self.db.delete_usage(snapshot["logs"]["usage"][0]["id"])
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 20)

    def test_full_history_removal_retry_and_conflict(self):
        self.db.apply(event(quantity=3, placement="O Box"), "Staff")
        self.db.apply(event(quantity=1), "Staff")
        snapshot = self.db.snapshot(history_limit=0)
        self.assertEqual(snapshot["logs"]["usage"], [])
        self.assertEqual(snapshot["obox"]["001.TEST"], 3)
        change = self.adjustment(3, 1)
        self.db.set_obox_quantity(change, "Staff")
        self.assertTrue(self.db.set_obox_quantity(change, "Staff")["duplicate"])
        with self.assertRaises(ValidationError):
            self.db.set_obox_quantity({**change, "quantity": 0}, "Staff")
        with self.assertRaises(ValidationError):
            self.db.set_obox_quantity(self.adjustment(3, 0), "Staff")
        self.db.apply(event(quantity=2, placement="O Box"), "Staff")
        snapshot = self.db.snapshot()
        self.assertEqual(snapshot["obox"]["001.TEST"], 3)
        self.assertEqual(snapshot["inventory"][0]["quantity"], 14)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM obox_adjustments").fetchone()[0], 1)

    def test_concurrent_counts_only_one_wins(self):
        def change(quantity):
            try:
                self.db.set_obox_quantity(self.adjustment(0, quantity), "Staff")
                return True
            except ValidationError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(change, (2, 3))), [False, True])
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 20)

    def test_reversal_and_deletion_remove_box_contribution(self):
        entry = event(quantity=3, placement="O Box")
        self.db.apply(entry, "Staff")
        self.db.apply(event(kind="addition", quantity=3, reversal_of=entry["id"], notes="Correction"), "Staff")
        self.assertEqual(self.db.snapshot()["obox"]["001.TEST"], 0)
        self.db.delete_usage(entry["id"])
        self.assertEqual(self.db.snapshot()["obox"]["001.TEST"], 0)
        self.assertEqual(self.db.snapshot()["inventory"][0]["quantity"], 20)

    def test_pending_counts_and_zero_entries_sync_once(self):
        store = LocalStore(self.root / "client.db")
        store.install_snapshot(self.db.snapshot())
        entry = event(quantity=2, placement="O Box")
        store.queue(entry)
        zero = event(quantity=0, date=CUTOFF)
        store.queue(zero)
        snapshot, entries, rows = store.view()
        self.assertEqual(rows[0]["estimated_quantity"], 18)
        self.assertEqual(store.obox_rows(snapshot, entries)[0]["estimated_quantity"], 2)
        for item in (entry, zero):
            self.db.apply(item, "Staff")
        store.record_results([{"id": item["id"], "status": "accepted"} for item in (entry, zero)])
        store.install_snapshot(self.db.snapshot([entry["id"], zero["id"]]))
        snapshot, entries, rows = store.view()
        self.assertEqual(entries, [])
        self.assertEqual(rows[0]["estimated_quantity"], 18)
        self.assertEqual(store.obox_rows(snapshot, entries)[0]["estimated_quantity"], 2)
        self.assertEqual(store.obox_rows({"inventory": rows}, []), [])

    def test_host_endpoint_persists_count_and_returns_snapshot(self):
        host = FolderProcessor(self.db, self.root / "exchange", "test-host")
        response = host.handle("/obox_quantity", self.adjustment(0, 4), "Test computer")
        self.assertEqual(response["snapshot"]["obox"]["001.TEST"], 4)
        self.assertEqual(response["snapshot"]["inventory"][0]["quantity"], 20)

    def test_v1_upgrade_preserves_rows_hashes_indexes_and_backup(self):
        entry = event(quantity=2, placement="O Box")
        self.db.apply(entry, "Staff")
        # Reconstruct the released v1 constraints with populated logs.
        with self.db.connect(write=True) as db:
            before = dict(db.execute("SELECT * FROM component_usage_log").fetchone())
            for table in ("component_usage_log", "component_addition_log"):
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
                db.execute(f"ALTER TABLE {table} RENAME TO {table}_new")
                db.execute(schema.replace("CHECK(quantity>=0)", "CHECK(quantity>0)"))
                db.execute(f"INSERT INTO {table} SELECT * FROM {table}_new")
                db.execute(f"DROP TABLE {table}_new")
            db.execute("DROP TABLE obox_adjustments")
            db.execute("PRAGMA user_version=1")
        upgraded = Database(self.db.path)
        with upgraded.connect() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM component_usage_log").fetchone()), before)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(len(db.execute("PRAGMA index_list(component_usage_log)").fetchall()), 3)
        backups = list(self.root.glob("*.before-v2-*.bak"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertTrue(upgraded.apply(entry, "Staff")["duplicate"])
        for kind in ("usage", "addition"):
            upgraded.apply(event(kind=kind, quantity=0, date=CUTOFF), "Staff")
        self.assertEqual(upgraded.snapshot()["inventory"][0]["quantity"], 18)
        Database(self.db.path)
        self.assertEqual(len(list(self.root.glob("*.before-v2-*.bak"))), 1)

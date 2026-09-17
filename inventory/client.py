"""Durable local outbox. Networking never runs on the GUI thread."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from .core import DETAIL_FIELDS, ValidationError, encoded, normalize_event, now
from .secure import protect, unprotect
from .exchange import SharedFolderTransport as Transport, RemoteError


class LocalStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, payload BLOB NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload BLOB NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def pack(value):
        return protect(encoded(value).encode())

    @staticmethod
    def unpack(value):
        return json.loads(unprotect(value).decode())

    def queue(self, data):
        event = normalize_event({**data, "id": data.get("id") or str(uuid4())})
        with self.connect() as db:
            db.execute("INSERT INTO outbox(id,payload,created_at) VALUES (?,?,?)",
                       (event["id"], self.pack(event), now()))
        return event["id"]

    def _entries(self, db):
        return [{**self.unpack(r["payload"]), "state": r["state"], "error": r["error"]}
                for r in db.execute("SELECT * FROM outbox WHERE state!='cancelled' ORDER BY created_at,rowid")]

    def entries(self):
        with self.connect() as db:
            return self._entries(db)

    def snapshot(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM cache WHERE key='snapshot'").fetchone()
            return self.unpack(row[0]) if row else {"inventory": [], "logs": {"usage": [], "addition": []},
                                                     "as_of": "Never", "totals": {"usage": 0, "addition": 0}}

    def record_results(self, results):
        with self.connect() as db:
            for result in results:
                state = "accepted" if result["status"] == "accepted" else "error"
                db.execute("UPDATE outbox SET state=?,error=? WHERE id=?", (state, result.get("error", ""), result["id"]))

    def install_snapshot(self, snapshot):
        # Cache replacement and receipt removal must commit together to avoid double counting.
        with self.connect() as db:
            current = db.execute("SELECT payload FROM cache WHERE key='snapshot'").fetchone()
            if current:
                previous = self.unpack(current[0])
                if previous.get("host_id") and previous["host_id"] != snapshot.get("host_id"):
                    raise RemoteError("This computer is paired with another inventory host. Use a separate local client folder for a different inventory.")
                if previous.get("generation", 0) > snapshot.get("generation", 0):
                    return  # A shared snapshot can lag a just-received private receipt snapshot.
            db.execute("INSERT OR REPLACE INTO cache VALUES ('snapshot',?)", (self.pack(snapshot),))
            db.executemany("DELETE FROM outbox WHERE id=? AND state IN ('pending','accepted')",
                           [(identity,) for identity in snapshot["receipts"]])

    def retry(self, identity):
        with self.connect() as db:
            db.execute("UPDATE outbox SET state='pending',error='' WHERE id=? AND state='error'", (identity,))

    def cancel_rejected(self, identity):
        # Never cancel an uncertain/accepted event: it may already exist on the server.
        with self.connect() as db:
            result = db.execute("UPDATE outbox SET state='cancelled' WHERE id=? AND state='error'", (identity,))
            if not result.rowcount:
                raise ValidationError("Only a server-rejected entry can be cancelled")

    def view(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM cache WHERE key='snapshot'").fetchone()
            snapshot = self.unpack(row[0]) if row else {"inventory": [], "logs": {"usage": [], "addition": []}, "as_of": "Never", "totals": {"usage": 0, "addition": 0}}
            entries = self._entries(db)
        movements = {}
        for entry in entries:
            if entry["state"] in ("pending", "accepted"):
                movements[entry["ref_id"]] = movements.get(entry["ref_id"], 0) + entry["quantity"] * (-1 if entry["kind"] == "usage" else 1)
        rows = []
        for component in snapshot["inventory"]:
            local = movements.get(component["ref_id"], 0)
            estimated = component["quantity"] + local
            rows.append({**component, "server_quantity": component["quantity"], "pending_change": local,
                         "estimated_quantity": estimated, "reorder": "Yes" if estimated <= component["reorder_level"] else "No",
                         "attention": "Negative balance" if estimated < 0 else ""})
        return snapshot, entries, rows


class SyncEngine:
    def __init__(self, store, transport):
        self.store, self.transport = store, transport
        self.lock = threading.Lock()
        if isinstance(transport, Transport):
            transport.expected_host_id = store.snapshot().get("host_id")

    def sync(self):
        with self.lock:
            if isinstance(self.transport, Transport):
                self.transport.expected_host_id = self.store.snapshot().get("host_id")
            entries = self.store.entries()
            accepted_count = sum(e["state"] == "accepted" for e in entries)
            pending = [e for e in entries if e["state"] == "pending"]
            # Corrections should not sit behind a long offline backlog.
            pending.sort(key=lambda entry: (not bool(entry.get("reversal_of")), entry.get("date", ""), entry["id"]))
            pending = pending[:max(0, 500 - accepted_count)]
            for offset in range(0, len(pending), 100):
                batch = [{k: v for k, v in entry.items() if k not in ("state", "error")}
                         for entry in pending[offset:offset + 100]]
                result = self.transport.post("/events", {"events": batch})
                self.store.record_results(result["results"])
            known = [e["id"] for e in self.store.entries() if e["state"] == "accepted"][:1000]
            snapshot = self.transport.post("/snapshot", {"known_ids": known})
            self.store.install_snapshot(snapshot)
            return self.store.snapshot()

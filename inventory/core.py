"""Four authoritative tables; each movement and stock update is one transaction."""
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4


class ValidationError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def text(value, limit=500):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ValidationError(f"Text exceeds {limit} characters")
    return value


def ref_id(value):
    value = text(value, 80).upper()
    if not value:
        raise ValidationError("REF ID is required")
    return value


def integer(value, minimum=0):
    if isinstance(value, bool):
        raise ValidationError("Enter a whole-number quantity")
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        raise ValidationError("Enter a whole-number quantity") from None
    if not minimum <= number <= 1_000_000:
        raise ValidationError(f"Quantity must be between {minimum} and 1,000,000")
    return number


def day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValidationError("Date must be YYYY-MM-DD") from None


DETAIL_FIELDS = ("tooth", "placement", "patient", "periodontist", "referring_dentist",
                 "assistant", "signed_by", "notes")
COMPONENT_FIELDS = ("manufacturer", "implant_type", "size", "component_type", "other_parts")
TABLES = {"usage": "component_usage_log", "addition": "component_addition_log"}


def normalize_event(data):
    if not isinstance(data, dict):
        raise ValidationError("Entry must be an object")
    try:
        identity = str(UUID(str(data.get("id", ""))))
    except ValueError:
        raise ValidationError("Entry ID must be a UUID") from None
    kind = data.get("kind")
    if kind not in TABLES:
        raise ValidationError("Entry must be usage or addition")
    event = {"id": identity, "kind": kind, "date": day(data.get("date")),
             "ref_id": ref_id(data.get("ref_id")), "quantity": integer(data.get("quantity"), 1)}
    for key in DETAIL_FIELDS:
        event[key] = text(data.get(key), 4000 if key == "notes" else 250)
    extra = data.get("extra", {})
    if not isinstance(extra, dict) or len(extra) > 30:
        raise ValidationError("Custom fields must be an object with at most 30 fields")
    event["extra"] = {text(k, 80): text(v, 1000) for k, v in extra.items()}
    event["reversal_of"] = text(data.get("reversal_of"), 80)
    return event


def normalize_component(data):
    result = {"ref_id": ref_id(data.get("ref_id")),
              "reorder_level": integer(data.get("reorder_level", 0))}
    result.update({key: text(data.get(key)) for key in COMPONENT_FIELDS})
    return result


class Database:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as setup:
            setup.execute("PRAGMA journal_mode=WAL")
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported database schema; use a matching application version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS component_list (
                    ref_id TEXT PRIMARY KEY COLLATE NOCASE,
                    reorder_level INTEGER NOT NULL CHECK(reorder_level>=0),
                    manufacturer TEXT NOT NULL, implant_type TEXT NOT NULL,
                    size TEXT NOT NULL, component_type TEXT NOT NULL, other_parts TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, import_batch TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory_supply (
                    ref_id TEXT PRIMARY KEY REFERENCES component_list(ref_id),
                    opening_quantity INTEGER NOT NULL, opening_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL
                );
            """)
            for table in TABLES.values():
                db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY, date TEXT NOT NULL,
                    ref_id TEXT NOT NULL REFERENCES component_list(ref_id),
                    quantity INTEGER NOT NULL CHECK(quantity>0),
                    tooth TEXT NOT NULL, placement TEXT NOT NULL, patient TEXT NOT NULL,
                    periodontist TEXT NOT NULL, referring_dentist TEXT NOT NULL,
                    assistant TEXT NOT NULL, signed_by TEXT NOT NULL, notes TEXT NOT NULL,
                    extra TEXT NOT NULL, reversal_of TEXT NOT NULL,
                    affects_stock INTEGER NOT NULL CHECK(affects_stock IN (0,1)),
                    actor TEXT NOT NULL, received_at TEXT NOT NULL, payload_hash TEXT NOT NULL
                )""")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_ref_date ON {table}(ref_id,date)")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_reversal ON {table}(reversal_of)")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _find(self, db, identity):
        for kind, table in TABLES.items():
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
            if row:
                return {**dict(row), "kind": kind}
        return None

    def _apply(self, db, event, actor, historical=False):
        existing = self._find(db, event["id"])
        if existing:
            if existing["payload_hash"] != digest(event):
                raise ValidationError("Entry ID already exists with different contents")
            return {"id": event["id"], "duplicate": True}
        inventory = db.execute("SELECT * FROM inventory_supply WHERE ref_id=?", (event["ref_id"],)).fetchone()
        if not inventory:
            raise ValidationError("Unknown REF ID; add the component on the server first")
        affects = not historical
        if affects and event["date"] <= inventory["opening_date"]:
            raise ValidationError("Entry date must be after the opening balance date; use history import for older entries")
        if event["date"] > date.today().isoformat():
            raise ValidationError("Future dates are not allowed")
        if event["reversal_of"]:
            original = self._find(db, event["reversal_of"])
            if not original or not original["affects_stock"] or original["reversal_of"]:
                raise ValidationError("Only a posted stock movement can be reversed")
            if (original["kind"] == event["kind"] or original["ref_id"] != event["ref_id"]
                    or original["quantity"] != event["quantity"]):
                raise ValidationError("A reversal must exactly offset the original entry")
            for table in TABLES.values():
                if db.execute(f"SELECT 1 FROM {table} WHERE reversal_of=?", (original["id"],)).fetchone():
                    raise ValidationError("This entry has already been reversed")
            if not event["notes"]:
                raise ValidationError("A correction reason is required")
        row = {k: v for k, v in event.items() if k != "kind"}
        row.update(extra=encoded(row["extra"]), affects_stock=int(affects),
                   actor=actor, received_at=now(), payload_hash=digest(event))
        columns = list(row)
        db.execute(f"INSERT INTO {TABLES[event['kind']]} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                   [row[k] for k in columns])
        if affects:
            delta = event["quantity"] * (-1 if event["kind"] == "usage" else 1)
            db.execute("UPDATE inventory_supply SET quantity=quantity+? WHERE ref_id=?", (delta, event["ref_id"]))
        return {"id": event["id"], "duplicate": False}

    def apply(self, data, actor, required_fields=()):
        event = normalize_event(data)
        with self.connect(write=True) as db:
            if not self._find(db, event["id"]) and not event["reversal_of"]:
                for field in required_fields:
                    if field.get("required") and not (event.get(field["key"]) or event["extra"].get(field["key"])):
                        raise ValidationError(field["label"] + " is required")
            return self._apply(db, event, actor)

    def delete_usage(self, identity):
        """Permanently remove one usage row and undo its stock effect atomically."""
        try:
            identity = str(UUID(str(identity)))
        except ValueError:
            raise ValidationError("Entry ID must be a UUID") from None
        with self.connect(write=True) as db:
            original = db.execute("SELECT * FROM component_usage_log WHERE id=?", (identity,)).fetchone()
            if not original:
                return {"deleted": False, "id": identity}
            rows = [{**dict(original), "kind": "usage"}]
            # Remove a linked legacy reversal too, keeping inventory unchanged when
            # an older client already reversed the selected row.
            for kind, table in TABLES.items():
                rows.extend({**dict(row), "kind": kind} for row in
                            db.execute(f"SELECT * FROM {table} WHERE reversal_of=?", (identity,)))
            for row in rows:
                if row["affects_stock"]:
                    applied_delta = row["quantity"] * (-1 if row["kind"] == "usage" else 1)
                    db.execute("UPDATE inventory_supply SET quantity=quantity-? WHERE ref_id=?",
                               (applied_delta, row["ref_id"]))
            for row in rows:
                db.execute(f"DELETE FROM {TABLES[row['kind']]} WHERE id=?", (row["id"],))
            return {"deleted": True, "id": identity, "removed_rows": len(rows)}

    def save_component(self, data):
        component = normalize_component(data)
        with self.connect(write=True) as db:
            current = db.execute("SELECT * FROM component_list WHERE ref_id=?", (component["ref_id"],)).fetchone()
            if current:
                requested_revision = integer(data.get("revision", 0))
                if current["revision"] == requested_revision + 1 and all(current[k] == v for k, v in component.items()):
                    if requested_revision != 0:
                        return {"saved": True, "duplicate": True}
                    opening = db.execute("SELECT * FROM inventory_supply WHERE ref_id=?", (component["ref_id"],)).fetchone()
                    if opening["opening_quantity"] == integer(data.get("opening_quantity", 0)) and opening["opening_date"] == day(data.get("opening_date")):
                        return {"saved": True, "duplicate": True}
                if requested_revision != current["revision"]:
                    raise ValidationError("Component changed on another computer; sync and reopen it")
                columns = [key for key in component if key != "ref_id"]
                db.execute(f"UPDATE component_list SET {','.join(k+'=?' for k in columns)}, revision=revision+1 WHERE ref_id=?",
                           [component[k] for k in columns] + [component["ref_id"]])
            else:
                opening = integer(data.get("opening_quantity", 0))
                cutoff = day(data.get("opening_date"))
                if cutoff >= date.today().isoformat():
                    raise ValidationError("Use an opening balance dated before today")
                self._insert_component(db, component, opening, cutoff)
        return {"saved": True}

    def _insert_component(self, db, component, opening, cutoff, batch=""):
        columns = list(component)
        db.execute(f"INSERT INTO component_list ({','.join(columns)},import_batch) VALUES ({','.join('?' for _ in columns)},?)",
                   [component[k] for k in columns] + [batch])
        db.execute("INSERT INTO inventory_supply VALUES (?,?,?,?)", (component["ref_id"], opening, cutoff, opening))

    def import_bundle(self, bundle, actor):
        if not isinstance(bundle, dict) or bundle.get("version") != 1:
            raise ValidationError("Unsupported import format")
        cutoff = day(bundle.get("cutoff"))
        if cutoff >= date.today().isoformat():
            raise ValidationError("Opening balances must be dated before today")
        components = bundle.get("components", [])
        events = bundle.get("events", [])
        if not components or len(components) > 10000 or len(events) > 100000:
            raise ValidationError("Import requires 1–10,000 components and at most 100,000 entries")
        batch = digest(bundle)
        with self.connect(write=True) as db:
            existing = db.execute("SELECT import_batch FROM component_list").fetchall()
            if existing:
                if any(r[0] == batch for r in existing):
                    return {"already_imported": True}
                raise ValidationError("Initial migration requires an empty database; this database already contains components")
            for component in components:
                self._insert_component(db, normalize_component(component),
                                       integer(component.get("opening_quantity")), cutoff, batch)
            for data in events:
                event = normalize_event(data)
                self._apply(db, event, actor, historical=event["date"] <= cutoff)
        return {"components": len(components), "events": len(events), "already_imported": False}

    def snapshot(self, known_ids=(), history_limit=2000):
        if len(known_ids) > 1000:
            raise ValidationError("At most 1,000 receipt IDs per sync")
        with self.connect() as db:
            inventory = [dict(r) for r in db.execute("""SELECT c.*,s.opening_quantity,s.opening_date,s.quantity
                         FROM component_list c JOIN inventory_supply s USING(ref_id) ORDER BY c.ref_id""")]
            logs = {}
            for kind, table in TABLES.items():
                logs[kind] = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY received_at DESC,rowid DESC LIMIT ?", (history_limit,))]
                for row in logs[kind]:
                    row["extra"] = json.loads(row["extra"])
                    row["kind"] = kind
                    row.pop("payload_hash")
            receipts = [identity for identity in known_ids if self._find(db, identity)]
            totals = {kind: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for kind, table in TABLES.items()}
            return {"inventory": inventory, "logs": logs, "receipts": receipts,
                    "totals": totals, "as_of": now()}

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination == Path(self.path):
            raise ValidationError("Backup destination must differ from the live database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
        try:
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return str(destination)

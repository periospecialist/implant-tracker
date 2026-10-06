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
MIGRATION_ACTOR_PREFIX = "Excel import on "


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
             "ref_id": ref_id(data.get("ref_id")), "quantity": integer(data.get("quantity"), 0)}
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
            if version not in (0, 1, 2):
                raise RuntimeError("Unsupported database schema; use a matching application version")
            if version == 1:
                self.backup(Path(self.path).with_name(Path(self.path).name + ".before-v2-" + uuid4().hex + ".bak"))
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
            # executescript commits its transaction; migrate both logs atomically.
            db.execute("BEGIN IMMEDIATE")
            if version == 1:
                for table in TABLES.values():
                    schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                    db.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
                    db.execute(schema.replace("CHECK(quantity>0)", "CHECK(quantity>=0)"))
                    db.execute(f"INSERT INTO {table} SELECT * FROM {table}_old")
                    db.execute(f"DROP TABLE {table}_old")
            for table in TABLES.values():
                db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY, date TEXT NOT NULL,
                    ref_id TEXT NOT NULL REFERENCES component_list(ref_id),
                    quantity INTEGER NOT NULL CHECK(quantity>=0),
                    tooth TEXT NOT NULL, placement TEXT NOT NULL, patient TEXT NOT NULL,
                    periodontist TEXT NOT NULL, referring_dentist TEXT NOT NULL,
                    assistant TEXT NOT NULL, signed_by TEXT NOT NULL, notes TEXT NOT NULL,
                    extra TEXT NOT NULL, reversal_of TEXT NOT NULL,
                    affects_stock INTEGER NOT NULL CHECK(affects_stock IN (0,1)),
                    actor TEXT NOT NULL, received_at TEXT NOT NULL, payload_hash TEXT NOT NULL
                )""")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_ref_date ON {table}(ref_id,date)")
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_reversal ON {table}(reversal_of)")
            db.execute("""CREATE TABLE IF NOT EXISTS obox_adjustments (
                id TEXT PRIMARY KEY, ref_id TEXT NOT NULL REFERENCES component_list(ref_id),
                delta INTEGER NOT NULL, payload_hash TEXT NOT NULL,
                reason TEXT NOT NULL, actor TEXT NOT NULL, received_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS obox_adjustments_ref ON obox_adjustments(ref_id)")
            db.execute("PRAGMA user_version=2")

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
        affects = not historical and event["quantity"] > 0
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
        replace_existing = bundle.get("replace_existing_migration") is True
        batch = digest({key: value for key, value in bundle.items()
                        if key != "replace_existing_migration"})
        with self.connect(write=True) as db:
            existing = db.execute("SELECT ref_id,import_batch FROM component_list").fetchall()
            if existing:
                if any(r["import_batch"] == batch for r in existing) and not replace_existing:
                    return {"already_imported": True}
                if not replace_existing:
                    raise ValidationError("Initial migration requires an empty database; choose Replace previous Excel migration to import again")
                self._replace_migration(db, components, cutoff, batch)
            else:
                for component in components:
                    self._insert_component(db, normalize_component(component),
                                           integer(component.get("opening_quantity")), cutoff, batch)
            migration_actor = actor if str(actor).startswith(MIGRATION_ACTOR_PREFIX) \
                else MIGRATION_ACTOR_PREFIX + str(actor)
            for data in events:
                event = normalize_event(data)
                self._apply(db, event, migration_actor, historical=event["date"] <= cutoff)
            if replace_existing:
                self._recalculate_quantities(db)
        return {"components": len(components), "events": len(events),
                "already_imported": False, "replaced": replace_existing}

    def _replace_migration(self, db, components, cutoff, batch):
        """Replace rows created by a prior Excel import while retaining normal activity."""
        imported = {row["ref_id"]: row for row in db.execute(
            "SELECT * FROM component_list WHERE import_batch<>''")}
        if not imported:
            raise ValidationError("There is no previous Excel migration to replace")

        prepared = {}
        for source in components:
            component = normalize_component(source)
            identity = component["ref_id"]
            if identity in prepared:
                raise ValidationError(f"Migration contains duplicate REF ID {identity}")
            prepared[identity] = (component, integer(source.get("opening_quantity")))

        manual_refs = {row["ref_id"] for row in db.execute(
            "SELECT ref_id FROM component_list WHERE import_batch='' ")}
        conflicts = sorted(manual_refs.intersection(prepared))
        if conflicts:
            sample = ", ".join(conflicts[:5])
            raise ValidationError(f"Replacement would overwrite manually created component(s): {sample}")

        actor_pattern = MIGRATION_ACTOR_PREFIX + "%"
        for table in TABLES.values():
            for row in db.execute(
                f"SELECT reversal_of FROM {table} WHERE actor NOT LIKE ? AND reversal_of<>''",
                (actor_pattern,),
            ):
                original = self._find(db, row["reversal_of"])
                if original and original["actor"].startswith(MIGRATION_ACTOR_PREFIX):
                    raise ValidationError(
                        "A normal correction refers to imported history; remove that correction before replacing the migration"
                    )

        removed_refs = set(imported).difference(prepared)
        for identity in sorted(removed_refs):
            used = any(db.execute(
                f"SELECT 1 FROM {table} WHERE ref_id=? AND actor NOT LIKE ? LIMIT 1",
                (identity, actor_pattern),
            ).fetchone() for table in TABLES.values())
            if used or db.execute("SELECT 1 FROM obox_adjustments WHERE ref_id=? LIMIT 1", (identity,)).fetchone():
                raise ValidationError(
                    f"REF ID {identity} is missing from the new workbook but has normal activity; include it before replacing the migration"
                )

        for table in TABLES.values():
            db.execute(f"DELETE FROM {table} WHERE actor LIKE ?", (actor_pattern,))

        for identity in removed_refs:
            db.execute("DELETE FROM inventory_supply WHERE ref_id=?", (identity,))
            db.execute("DELETE FROM component_list WHERE ref_id=?", (identity,))

        for identity, (component, opening) in prepared.items():
            if identity in imported:
                columns = [key for key in component if key != "ref_id"]
                db.execute(
                    f"UPDATE component_list SET {','.join(key+'=?' for key in columns)}, "
                    "revision=revision+1, import_batch=? WHERE ref_id=?",
                    [component[key] for key in columns] + [batch, identity],
                )
                db.execute(
                    "UPDATE inventory_supply SET opening_quantity=?,opening_date=?,quantity=? WHERE ref_id=?",
                    (opening, cutoff, opening, identity),
                )
            else:
                self._insert_component(db, component, opening, cutoff, batch)

    def _recalculate_quantities(self, db):
        db.execute("""
            UPDATE inventory_supply
            SET quantity = opening_quantity
                + COALESCE((SELECT SUM(quantity) FROM component_addition_log
                            WHERE component_addition_log.ref_id=inventory_supply.ref_id
                              AND affects_stock=1), 0)
                - COALESCE((SELECT SUM(quantity) FROM component_usage_log
                            WHERE component_usage_log.ref_id=inventory_supply.ref_id
                              AND affects_stock=1), 0)
        """)

    def _obox_balances(self, db):
        # Read full history, independently of the desktop history window.
        return {row["ref_id"]: row["quantity"] for row in db.execute("""
            SELECT c.ref_id,
                COALESCE((SELECT SUM(u.quantity) FROM component_usage_log u
                    WHERE u.ref_id=c.ref_id AND lower(trim(u.placement)) IN ('o box','o-box','"o" box')
                    AND u.reversal_of=''
                    AND NOT EXISTS (SELECT 1 FROM component_addition_log a WHERE a.reversal_of=u.id)),0)
                + COALESCE((SELECT SUM(delta) FROM obox_adjustments o WHERE o.ref_id=c.ref_id),0) AS quantity
            FROM component_list c
        """)}

    def set_obox_quantity(self, data, actor):
        identity = str(UUID(str(data.get("id", ""))))
        reference = ref_id(data.get("ref_id"))
        quantity = integer(data.get("quantity"))
        expected = integer(data.get("expected"), -1_000_000)
        reason = text(data.get("reason"), 4000)
        if not reason:
            raise ValidationError("A reason is required for an O-Box count change")
        payload = digest({"ref_id": reference, "quantity": quantity, "expected": expected, "reason": reason})
        with self.connect(write=True) as db:
            existing = db.execute("SELECT payload_hash FROM obox_adjustments WHERE id=?", (identity,)).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValidationError("O-Box adjustment ID already exists with different contents")
                return {"saved": True, "duplicate": True}
            current = self._obox_balances(db).get(reference)
            if current is None:
                raise ValidationError("Unknown REF ID")
            if current != expected:
                raise ValidationError("O-Box count changed on another computer. Sync and try again.")
            db.execute("INSERT INTO obox_adjustments VALUES (?,?,?,?,?,?,?)",
                       (identity, reference, quantity-current, payload, reason, actor, now()))
        return {"saved": True}

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
            return {"inventory": inventory, "obox": self._obox_balances(db), "logs": logs, "receipts": receipts,
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

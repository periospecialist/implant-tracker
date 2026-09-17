"""Shared-folder messages: complete JSON files, unique IDs, no network listener."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time
from uuid import uuid4

from .core import encoded, now

PROTOCOL = 2
MAX_BYTES = 64 * 1024 * 1024


class RemoteError(Exception):
    pass


def atomic_json(path, value):
    """Readers see either the previous complete document or the new one."""
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Exchange document is too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Exchange document must be an object")
    return value


def exchange_path(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("Choose the shared Inventory Exchange folder in Setup")
    if "://" in value:
        raise ValueError("Enter a folder path such as \\\\SERVER\\Perio\\IMPLANTS\\Inventory Exchange")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Use a full shared-folder or local folder path")
    return path


def check_local_database(path):
    """Block UNC paths and Windows mapped network drives for SQLite."""
    path = Path(path).resolve()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("The live database must be on the host's local drive; only the exchange folder is shared")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        kernel.GetDriveTypeW.restype = wintypes.UINT
        if kernel.GetDriveTypeW(path.anchor) == 4:
            raise ValueError("A mapped network drive cannot hold the live database")
    return path


@contextmanager
def host_lock(database_path):
    """An OS lock is released even if the host process crashes."""
    path = Path(str(database_path) + ".processor.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("An inventory host is already running for this database. Close the extra instance.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class SharedFolderTransport:
    def __init__(self, config):
        # Allow startup before the folder is configured so Setup remains usable.
        self.folder = str(config.get("exchange_folder", "")).strip()
        if self.folder:
            exchange_path(self.folder)
        self.timeout = float(config.get("response_timeout", 30))
        self.poll_seconds = 0.2
        self.inflight = {}
        self.expected_host_id = None

    def post(self, operation, data):
        try:
            root = exchange_path(self.folder)
            owner = read_json(root / "host.json")
            if owner.get("protocol") != PROTOCOL:
                raise RemoteError("This exchange folder needs the updated inventory host")
            if self.expected_host_id and owner.get("host_id") != self.expected_host_id:
                raise RemoteError("This exchange folder belongs to a different inventory host. Queued entries have not been sent.")
            if operation == "/snapshot" and not data.get("known_ids"):
                snapshot = read_json(root / "snapshots" / "latest.json")
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot["as_of"])).total_seconds()
                if snapshot.get("host_id") != owner["host_id"] or age > 45 or age < -120:
                    raise RemoteError("The host has not published a recent inventory snapshot. Keep the host open and check the computers' clocks.")
                return {**snapshot, "receipts": []}
            if operation not in ("/events", "/components", "/import", "/snapshot", "/delete_usage"):
                raise RemoteError("Unknown exchange operation")
            key = encoded([operation, data])
            identity = self.inflight.setdefault(key, uuid4().hex)
            request_path = root / "inbox" / (identity + ".json")
            response_path = root / "responses" / (identity + ".json")
            if not request_path.exists() and not response_path.exists():
                atomic_json(request_path, {"protocol": PROTOCOL, "id": identity, "operation": operation,
                    "data": data, "computer": socket.gethostname(), "created_at": now()})
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    response = read_json(response_path)
                except FileNotFoundError:
                    response = None
                if response is not None:
                    if response.get("id") != identity or response.get("host_id") != owner["host_id"]:
                        raise RemoteError("Exchange response does not match this request or host")
                    self.inflight.pop(key, None)
                    if not request_path.exists():
                        try:
                            response_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    if "error" in response:
                        raise RemoteError(response["error"])
                    return response["result"]
                if time.monotonic() >= deadline:
                    raise RemoteError("Waiting for the inventory host. Your entries remain saved; keep InventoryServer.exe running on the host computer.")
                time.sleep(self.poll_seconds)
        except RemoteError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RemoteError("Cannot use the exchange folder. Check its path, folder access, and that the host is running. " + str(error)) from error

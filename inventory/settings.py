"""User-editable fields and connection settings; no practice names in code."""
from copy import deepcopy
import json
import os
from pathlib import Path

DEFAULT_FIELDS = {
    "usage": [
        {"key": "tooth", "label": "Tooth"},
        {"key": "placement", "label": "Placed / O Box", "choices": ["Placed", "O Box"], "required": True},
        {"key": "patient", "label": "Patient name", "required": True},
        {"key": "periodontist", "label": "Periodontist", "required": True, "choices": []},
        {"key": "referring_dentist", "label": "Referring dentist", "choices": []},
        {"key": "assistant", "label": "Assistant", "required": True, "choices": []},
        {"key": "notes", "label": "Notes"},
    ],
    "addition": [
        {"key": "signed_by", "label": "Signed off by", "required": True, "choices": []},
        {"key": "notes", "label": "Notes"},
    ],
}


def data_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "ImplantInventory"


def default_config():
    return {"exchange_folder": "", "response_timeout": 30,
            "sync_seconds": 15, "fields": deepcopy(DEFAULT_FIELDS),
            "manufacturer_choices": ["Straumann", "Nobel"],
            "implant_type_choices": ["BL NC", "BL RC", "BLT NC", "BLT RC", "BLC RB", "BLC WB",
                                     "Active NP", "Active RP", "Parallel NP", "Parallel RP"],
            "component_type_choices": ["Implant Body", "Cover Screw", "Healing Abutment", "AHA XC", "Crown", "Other"]}


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Settings must be a JSON object")
    interval = config.get("sync_seconds", 15)
    if not isinstance(interval, int) or not 5 <= interval <= 3600:
        raise ValueError("Sync interval must be between 5 and 3600 seconds")
    reserved = {"id", "kind", "date", "ref_id", "quantity", "extra", "reversal_of"}
    for kind in ("usage", "addition"):
        fields = config.get("fields", DEFAULT_FIELDS).get(kind, [])
        if not isinstance(fields, list) or len(fields) > 30:
            raise ValueError("Use at most 30 fields per form")
        keys = set()
        for field in fields:
            key = field.get("key", "")
            if not key.isidentifier() or len(key) > 80 or key in reserved or key in keys:
                raise ValueError(f"Invalid or duplicate field key: {key}")
            if not field.get("label") or not isinstance(field.get("choices", []), list):
                raise ValueError(f"Field {key} needs a label and a list of choices")
            keys.add(key)
    return config


def load_config(path):
    config = default_config()
    if Path(path).exists():
        config.update(json.loads(Path(path).read_text(encoding="utf-8")))
    for key in ("server_url", "token", "token_protected", "ca_file"):
        config.pop(key, None)  # Old credentials are unnecessary; don't even attempt to decrypt them.
    return validate_config(config)


def save_config(path, config):
    validate_config(config)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    stored = deepcopy(config)
    for key in ("server_url", "token", "token_protected", "ca_file"):
        stored.pop(key, None)
    temporary.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)

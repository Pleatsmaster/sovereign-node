"""LIFE-0 configuration: paths and the three permitted surfaces. No more."""
from __future__ import annotations

import json
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULTS = {
    # GitHub um-lineage mirror, read-only. If github_repo is null, the sensor
    # discovers the authenticated login via GET /user and uses {login}/unified_machine.
    "github_repo": None,
    "github_repo_fallback_name": "unified_machine",
    "fact0_db": str(Path.home() / "workspace/namariel-live0/fact-0/data/fact0.db"),
    "inbox_dir": str(LIFE0_ROOT / "inbox"),
    "state_dir": str(LIFE0_ROOT / "state"),
    "dispatch_dir": str(LIFE0_ROOT / "dispatch" / "staged"),
    "config_path": str(LIFE0_ROOT / "config" / "life0_config.json"),
}


def load(path: str | Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    if path is None:
        path = Path(cfg["config_path"])
    else:
        path = Path(path)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"config must be a JSON object: {path}")
        for k, v in loaded.items():
            if k not in DEFAULTS:
                raise ValueError(f"unknown config key: {k}")
            cfg[k] = v
    return cfg


def write_default(path: str | Path | None = None) -> Path:
    """Write the default config file (operator act, not a pulse act)."""
    if path is None:
        path = Path(DEFAULTS["config_path"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {k: v for k, v in DEFAULTS.items() if k != "config_path"}
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path

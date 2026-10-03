"""LIFE-0 test scaffolding: no network, no API. GitHub is mocked with fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

LIFE0_ROOT = Path(__file__).resolve().parent.parent
FACT0_SRC = Path.home() / "workspace/namariel-live0/fact-0/src"
FROZEN_SRC = Path.home() / "workspace/namariel-live0-v0.13/src"

sys.path.insert(0, str(LIFE0_ROOT / "src"))
sys.path.insert(0, str(FACT0_SRC))
sys.path.insert(0, str(FROZEN_SRC))

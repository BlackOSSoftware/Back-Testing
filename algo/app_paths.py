from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Algo Control"
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PACKAGE_DIR)) / "web"
IS_PACKAGED = bool(getattr(sys, "frozen", False))


def runtime_dir() -> Path:
    configured = os.getenv("ALGO_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if not IS_PACKAGED:
        return PACKAGE_DIR
    local_app_data = os.getenv("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME


DATA_DIR = runtime_dir()
INSTANCE_DIR = DATA_DIR / "instance"
RESULTS_DIR = DATA_DIR / "results"
CACHE_DIR = DATA_DIR / "cache"
REPORT_DATA_FILE = DATA_DIR / "report_data.js"


def prepare_runtime() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)
    return DATA_DIR

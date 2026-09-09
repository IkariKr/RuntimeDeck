"""Configuration loader for RuntimeDeck."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from runtime_deck.models import AppConfig

logger = logging.getLogger("ai_dashboard")


def load_raw_config(config_path: Path) -> Dict[str, Any]:
    """Load raw dict from runtimes.json, returning empty structure on failure."""
    if not config_path.exists():
        return {"gpus": {}, "profiles": {}}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        return {"gpus": {}, "profiles": {}}


def load_app_config(config_path: Path) -> AppConfig:
    """Load typed AppConfig from runtimes.json."""
    raw = load_raw_config(config_path)
    try:
        return AppConfig.model_validate(raw)
    except Exception as e:
        logger.error(f"Config validation error for {config_path}: {e}")
        return AppConfig()

"""Configuration loader for RuntimeDeck."""

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict

from runtime_deck.models import AppConfig

logger = logging.getLogger("ai_dashboard")

_CONFIG_LOCK = threading.RLock()


def load_raw_config(config_path: Path) -> Dict[str, Any]:
    """Load raw dict from runtimes.json, returning empty structure on failure."""
    with _CONFIG_LOCK:
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


def save_raw_config(config_path: Path, config: Dict[str, Any]) -> None:
    """Atomically save raw config dict to runtimes.json with retry on Windows file lock conflicts."""
    with _CONFIG_LOCK:
        dir_path = config_path.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
                temp_name = tf.name
                json.dump(config, tf, ensure_ascii=False, indent=2)

            # On Windows, antivirus or parallel readers may briefly hold read handles.
            # Retry replacement with brief backoff.
            max_attempts = 10
            for attempt in range(max_attempts):
                try:
                    os.replace(temp_name, config_path)
                    temp_name = None
                    break
                except OSError as exc:
                    if attempt == max_attempts - 1:
                        raise exc
                    time.sleep(0.01 * (attempt + 1))
        except Exception as e:
            logger.error(f"Failed to save config file {config_path}: {e}")
            raise e
        finally:
            if temp_name:
                for _ in range(5):
                    try:
                        if os.path.exists(temp_name):
                            os.remove(temp_name)
                        break
                    except OSError:
                        time.sleep(0.02)


def delete_profile_config(config_path: Path, profile_id: str) -> Dict[str, Any]:
    """Atomically delete a profile entry from runtimes.json, returning updated config."""
    with _CONFIG_LOCK:
        config = load_raw_config(config_path)
        profiles = config.get("profiles", {})
        if not isinstance(profiles, dict) or profile_id not in profiles:
            raise KeyError(f"未找到 Profile: {profile_id}")
        del profiles[profile_id]
        config["profiles"] = profiles
        save_raw_config(config_path, config)
        return config



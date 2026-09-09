"""State manager with RLock and atomic persistence using os.replace."""

import json
import logging
import os
import platform
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("ai_dashboard")


def resolve_default_state_file(fallback_project_dir: Optional[Path] = None) -> Path:
    """
    Resolve state file path following priority:
    1. RUNTIMEDECK_STATE_FILE env var if set
    2. %LOCALAPPDATA%/RuntimeDeck/state.json (on Windows) or ~/.local/share/RuntimeDeck/state.json (POSIX)
    3. fallback_project_dir / state.json
    """
    env_path = os.environ.get("RUNTIMEDECK_STATE_FILE")
    if env_path and env_path.strip():
        return Path(env_path.strip()).resolve()

    if platform.system() == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data and local_app_data.strip():
            return (Path(local_app_data.strip()) / "RuntimeDeck" / "state.json").resolve()
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data and xdg_data.strip():
            return (Path(xdg_data.strip()) / "RuntimeDeck" / "state.json").resolve()
        home = os.environ.get("HOME")
        if home and home.strip():
            return (Path(home.strip()) / ".local" / "share" / "RuntimeDeck" / "state.json").resolve()

    if fallback_project_dir:
        return (fallback_project_dir / "state.json").resolve()

    return Path("state.json").resolve()


class StateManager:
    def __init__(self, state_file: Path, legacy_fallback_file: Optional[Path] = None):
        self.state_file = Path(state_file).resolve()
        self.legacy_fallback_file = Path(legacy_fallback_file).resolve() if legacy_fallback_file else None
        self.lock = threading.RLock()

    def load_state(self) -> Dict[str, Any]:
        """
        Load state data. If external state_file does not exist, check legacy_fallback_file
        for backward compatibility.
        """
        with self.lock:
            target_file: Optional[Path] = None
            if self.state_file.exists():
                target_file = self.state_file
            elif self.legacy_fallback_file and self.legacy_fallback_file.exists():
                target_file = self.legacy_fallback_file

            if target_file and target_file.exists():
                try:
                    with open(target_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            data.setdefault("instances", {})
                            # Compatibility check: normalize instances dict
                            if not isinstance(data["instances"], dict):
                                data["instances"] = {}
                            return data
                except Exception as e:
                    logger.error(f"读取 state.json 失败 ({target_file}): {e}")
            return {"instances": {}}

    def save_state_atomic(self, state: Dict[str, Any]) -> None:
        """Atomic state persistence via temporary file + os.replace."""
        with self.lock:
            dir_path = self.state_file.parent
            dir_path.mkdir(parents=True, exist_ok=True)
            # Create temp file in same directory so os.replace is an atomic rename across same filesystem
            with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
                json.dump(state, tf, ensure_ascii=False, indent=2)
                temp_name = tf.name
            try:
                os.replace(temp_name, self.state_file)
            except Exception as e:
                # Clean up temp file on failure
                try:
                    if os.path.exists(temp_name):
                        os.remove(temp_name)
                except Exception:
                    pass
                raise e

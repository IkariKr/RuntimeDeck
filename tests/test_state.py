"""Unit tests for StateManager atomic persistence and corrupt fallback."""

import json
import threading
from pathlib import Path
from unittest.mock import patch
import pytest

from runtime_deck.state import StateManager


def test_load_state_non_existent(tmp_path: Path):
    """When state file does not exist, return empty instances dict."""
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)
    data = manager.load_state()
    assert data == {"instances": {}}


def test_save_and_load_state_valid(tmp_path: Path):
    """Saving state atomically and loading it back."""
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)

    payload = {
        "instances": {
            "inst_12345": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 12345,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    }

    manager.save_state_atomic(payload)
    assert state_file.exists()

    loaded = manager.load_state()
    assert loaded == payload
    assert "inst_12345" in loaded["instances"]
    assert loaded["instances"]["inst_12345"]["pid"] == 12345


def test_load_state_corrupt_json_fallback(tmp_path: Path):
    """When state.json contains invalid/corrupted JSON, gracefully fall back to empty instances."""
    state_file = tmp_path / "state.json"
    # Write invalid / broken JSON
    state_file.write_text("{corrupt json payload without quotes: broken", encoding="utf-8")

    manager = StateManager(state_file)
    loaded = manager.load_state()
    assert loaded == {"instances": {}}


def test_load_state_non_dict_fallback(tmp_path: Path):
    """When state.json contains non-dict JSON (e.g. array or primitive), return empty instances."""
    state_file = tmp_path / "state.json"
    state_file.write_text('["not", "a", "dict"]', encoding="utf-8")

    manager = StateManager(state_file)
    loaded = manager.load_state()
    assert loaded == {"instances": {}}


def test_state_manager_thread_concurrency(tmp_path: Path):
    """Verify thread-safe atomic writes under concurrent access."""
    state_file = tmp_path / "state.json"
    manager = StateManager(state_file)
    manager.save_state_atomic({"instances": {}})

    num_threads = 10
    writes_per_thread = 20

    def worker(worker_id: int):
        for i in range(writes_per_thread):
            with manager.lock:
                st = manager.load_state()
                inst_id = f"inst_{worker_id}_{i}"
                st["instances"][inst_id] = {
                    "profile": "test",
                    "pid": worker_id * 1000 + i,
                    "create_time": 1000.0,
                }
                manager.save_state_atomic(st)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final_state = manager.load_state()
    assert len(final_state["instances"]) == num_threads * writes_per_thread


def test_resolve_default_state_file_env_priority(tmp_path: Path, monkeypatch):
    """RUNTIMEDECK_STATE_FILE env var takes highest priority."""
    env_file = tmp_path / "custom" / "env_state.json"
    monkeypatch.setenv("RUNTIMEDECK_STATE_FILE", str(env_file))
    from runtime_deck.state import resolve_default_state_file

    resolved = resolve_default_state_file(fallback_project_dir=tmp_path / "fallback")
    assert resolved == env_file.resolve()


def test_resolve_default_state_file_localappdata(tmp_path: Path, monkeypatch):
    """When RUNTIMEDECK_STATE_FILE is unset, LOCALAPPDATA is used on Windows."""
    monkeypatch.delenv("RUNTIMEDECK_STATE_FILE", raising=False)
    local_app_data = tmp_path / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    from runtime_deck.state import resolve_default_state_file

    with patch("platform.system", return_value="Windows"):
        resolved = resolve_default_state_file(fallback_project_dir=tmp_path / "fallback")
        expected = (local_app_data / "RuntimeDeck" / "state.json").resolve()
        assert resolved == expected


def test_resolve_default_state_file_fallback_project_dir(tmp_path: Path, monkeypatch):
    """Fallback project directory is used when env vars are unavailable."""
    monkeypatch.delenv("RUNTIMEDECK_STATE_FILE", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    from runtime_deck.state import resolve_default_state_file

    fallback = tmp_path / "fallback_dir"
    resolved = resolve_default_state_file(fallback_project_dir=fallback)
    assert resolved == (fallback / "state.json").resolve()


def test_state_manager_legacy_fallback_read_compatibility(tmp_path: Path):
    """StateManager seamlessly loads existing legacy state file if primary does not exist."""
    external_state_file = tmp_path / "external" / "state.json"
    legacy_file = tmp_path / "legacy" / "state.json"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)

    legacy_payload = {
        "instances": {
            "inst_999": {
                "profile": "qwen_160k",
                "name": "Legacy Qwen",
                "pid": 999,
                "create_time": 12345.0,
            }
        }
    }
    legacy_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # Primary does not exist yet; legacy fallback exists
    mgr = StateManager(external_state_file, legacy_fallback_file=legacy_file)
    loaded = mgr.load_state()
    assert "inst_999" in loaded["instances"]
    assert loaded["instances"]["inst_999"]["name"] == "Legacy Qwen"

    # Now save atomic to primary: must create external_state_file and leave legacy intact
    loaded["instances"]["inst_1000"] = {
        "profile": "new_profile",
        "pid": 1000,
        "create_time": 12346.0,
    }
    mgr.save_state_atomic(loaded)

    assert external_state_file.exists()
    reloaded_from_primary = mgr.load_state()
    assert "inst_1000" in reloaded_from_primary["instances"]

    # Legacy file was not overwritten
    old_legacy_content = json.loads(legacy_file.read_text(encoding="utf-8"))
    assert "inst_1000" not in old_legacy_content["instances"]


"""Tests for Profile configuration management and model metadata reporting."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import server
from runtime_deck.state import StateManager


@pytest.fixture
def profile_test_env(tmp_path: Path, monkeypatch):
    """Set up isolated configuration and state files."""
    config_file = tmp_path / "runtimes.json"
    state_file = tmp_path / "state.json"

    test_config = {
        "gpus": {
            "3080": {
                "name": "RTX 3080 20G",
                "uuid": "GPU-00000000-0000-0000-0000-000000000001",
            }
        },
        "profiles": {
            "test_qwen": {
                "name": "Qwen 27B Test",
                "default_gpu": "3080",
                "model_id": "qwen3.8-27b-test",
                "context_size": "160K",
                "kv_quant": "K Q8_0 / V Q4_0",
                "cwd": "C:\\Runtimes",
                "executable": "cmd.exe",
                "args": ["test.cmd"],
            },
            "test_bge": {
                "name": "BGE Small Test",
                "default_gpu": "3080",
                "model_id": "bge-small",
                "context_size": "2K",
                "kv_quant": "F16",
                "cwd": "C:\\Runtimes",
                "executable": "cmd.exe",
                "args": ["bge.cmd"],
            },
        },
    }
    config_file.write_text(json.dumps(test_config, indent=2), encoding="utf-8")
    state_file.write_text(json.dumps({"instances": {}}, indent=2), encoding="utf-8")

    state_mgr = StateManager(state_file)
    monkeypatch.setattr(server, "CONFIG_FILE", config_file)
    monkeypatch.setattr(server, "STATE_FILE", state_file)
    monkeypatch.setattr(server, "state_manager", state_mgr)

    return {
        "client": TestClient(server.app),
        "config_file": config_file,
        "state_file": state_file,
        "state_mgr": state_mgr,
    }


def test_delete_profile_success(profile_test_env):
    """Deleting an idle profile removes it from runtimes.json atomically."""
    client = profile_test_env["client"]
    config_file = profile_test_env["config_file"]

    # Initial check
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert "test_qwen" in resp.json()["profiles"]
    assert "test_bge" in resp.json()["profiles"]

    # Delete test_qwen
    del_resp = client.delete("/api/profiles/test_qwen")
    assert del_resp.status_code == 200
    data = del_resp.json()
    assert data["status"] == "success"
    assert data["profile_id"] == "test_qwen"

    # Verify config updated
    resp_after = client.get("/api/config")
    profiles_after = resp_after.json()["profiles"]
    assert "test_qwen" not in profiles_after
    assert "test_bge" in profiles_after

    # Verify persisted JSON file
    disk_config = json.loads(config_file.read_text(encoding="utf-8"))
    assert "test_qwen" not in disk_config["profiles"]
    assert "test_bge" in disk_config["profiles"]


def test_delete_profile_404(profile_test_env):
    """Deleting a non-existent profile returns 404."""
    client = profile_test_env["client"]
    resp = client.delete("/api/profiles/non_existent_profile")
    assert resp.status_code == 404
    assert "未找到 Profile" in resp.json()["detail"]


def test_delete_profile_blocked_when_instance_running(profile_test_env, monkeypatch):
    """Deleting a profile with an active running instance returns 409 Conflict."""
    client = profile_test_env["client"]
    state_mgr = profile_test_env["state_mgr"]

    # Seed state with an active instance for test_qwen
    state = state_mgr.load_state()
    state["instances"]["inst_12345"] = {
        "profile": "test_qwen",
        "name": "Qwen 27B Test",
        "pid": 12345,
        "create_time": 1000.0,
        "executable": "C:\\Runtimes\\test.exe",
        "gpu": "3080",
        "gpu_name": "RTX 3080 20G",
        "started_at": "2026-10-04T12:00:00",
    }
    state_mgr.save_state_atomic(state)

    # Mock process identity check to match (process is alive and verified)
    monkeypatch.setattr(server, "check_process_identity", lambda pid, ctime: ("match", MagicMock()))

    resp = client.delete("/api/profiles/test_qwen")
    assert resp.status_code == 409
    assert "正在运行" in resp.json()["detail"]

    # Verify profile was NOT removed
    config_resp = client.get("/api/config")
    assert "test_qwen" in config_resp.json()["profiles"]


def test_delete_profile_blocked_when_identity_check_errors(profile_test_env, monkeypatch):
    """Deleting a profile must be fail-closed (409) if identity check returns error."""
    client = profile_test_env["client"]
    state_mgr = profile_test_env["state_mgr"]

    # Seed state with an instance that triggers identity error
    state = state_mgr.load_state()
    state["instances"]["inst_55555"] = {
        "profile": "test_qwen",
        "name": "Qwen 27B Test",
        "pid": 55555,
        "create_time": 1000.0,
        "executable": "C:\\Runtimes\\test.exe",
        "gpu": "3080",
        "gpu_name": "RTX 3080 20G",
        "started_at": "2026-10-04T12:00:00",
    }
    state_mgr.save_state_atomic(state)

    # Mock process identity check to return 'error'
    monkeypatch.setattr(server, "check_process_identity", lambda pid, ctime: ("error", None))

    resp = client.delete("/api/profiles/test_qwen")
    assert resp.status_code == 409
    assert "状态检查异常" in resp.json()["detail"]

    # Ensure config was NOT deleted
    config_resp = client.get("/api/config")
    assert "test_qwen" in config_resp.json()["profiles"]


def test_status_reports_model_metadata(profile_test_env, monkeypatch):
    """Status endpoint reports model_id, context_size, kv_quant in instance view."""
    client = profile_test_env["client"]
    state_mgr = profile_test_env["state_mgr"]

    # Seed active instance in state
    state = state_mgr.load_state()
    state["instances"]["inst_99999"] = {
        "profile": "test_qwen",
        "name": "Qwen 27B Test",
        "pid": 99999,
        "create_time": 2000.0,
        "executable": "C:\\Runtimes\\test.exe",
        "gpu": "3080",
        "gpu_name": "RTX 3080 20G",
        "started_at": "2026-10-04T12:00:00",
        "model_id": "qwen3.8-27b-test",
        "context_size": "160K",
        "kv_quant": "K Q8_0 / V Q4_0",
    }
    state_mgr.save_state_atomic(state)

    # Mock process identity to match
    monkeypatch.setattr(server, "check_process_identity", lambda pid, ctime: ("match", MagicMock()))
    monkeypatch.setattr(server, "get_gpu_metrics", lambda: ([], []))

    resp = client.get("/api/status")
    assert resp.status_code == 200
    inst_view = resp.json()["instances"]["inst_99999"]
    assert inst_view["model_id"] == "qwen3.8-27b-test"
    assert inst_view["context_size"] == "160K"
    assert inst_view["kv_quant"] == "K Q8_0 / V Q4_0"


def test_status_handles_numeric_and_arbitrary_metadata_types(profile_test_env, monkeypatch):
    """Numeric context_size or integer model_id must not crash /api/status with 500."""
    client = profile_test_env["client"]
    state_mgr = profile_test_env["state_mgr"]

    # Seed instance where metadata fields are integers/floats
    state = state_mgr.load_state()
    state["instances"]["inst_77777"] = {
        "profile": "test_qwen",
        "name": "Qwen 27B Numeric Test",
        "pid": 77777,
        "create_time": 2000.0,
        "executable": "C:\\Runtimes\\test.exe",
        "gpu": "3080",
        "gpu_name": "RTX 3080 20G",
        "started_at": "2026-10-04T12:00:00",
        "model_id": 12345,
        "context_size": 131072,
        "kv_quant": 8,
    }
    state_mgr.save_state_atomic(state)

    monkeypatch.setattr(server, "check_process_identity", lambda pid, ctime: ("match", MagicMock()))
    monkeypatch.setattr(server, "get_gpu_metrics", lambda: ([], []))

    resp = client.get("/api/status")
    assert resp.status_code == 200
    inst_view = resp.json()["instances"]["inst_77777"]
    assert inst_view["model_id"] == "12345"
    assert inst_view["context_size"] == "131072"
    assert inst_view["kv_quant"] == "8"


def test_status_falls_back_to_profile_config_metadata(profile_test_env, monkeypatch):
    """If state instance lacks metadata (e.g. legacy state), status falls back to config."""
    client = profile_test_env["client"]
    state_mgr = profile_test_env["state_mgr"]

    # Legacy state instance without model metadata
    state = state_mgr.load_state()
    state["instances"]["inst_88888"] = {
        "profile": "test_bge",
        "name": "BGE Small Test",
        "pid": 88888,
        "create_time": 3000.0,
        "executable": "C:\\Runtimes\\test.exe",
        "gpu": "3080",
        "gpu_name": "RTX 3080 20G",
        "started_at": "2026-10-04T12:00:00",
    }
    state_mgr.save_state_atomic(state)

    monkeypatch.setattr(server, "check_process_identity", lambda pid, ctime: ("match", MagicMock()))
    monkeypatch.setattr(server, "get_gpu_metrics", lambda: ([], []))

    resp = client.get("/api/status")
    assert resp.status_code == 200
    inst_view = resp.json()["instances"]["inst_88888"]
    assert inst_view["model_id"] == "bge-small"
    assert inst_view["context_size"] == "2K"
    assert inst_view["kv_quant"] == "F16"

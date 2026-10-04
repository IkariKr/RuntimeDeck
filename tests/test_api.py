"""Integration tests for FastAPI endpoints, lifecycle safety, and strict read-only status."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import psutil
import pytest
from fastapi.testclient import TestClient

import server
from runtime_deck.state import StateManager


@pytest.fixture
def client_env(tmp_path: Path, monkeypatch):
    """Set up isolated configuration and state files in temporary directory."""
    temp_config_file = tmp_path / "runtimes.json"
    temp_state_file = tmp_path / "state.json"

    test_config = {
        "gpus": {
            "3080": {
                "name": "RTX 3080 20G",
                "uuid": "GPU-00000000-0000-0000-0000-000000000001",
            },
            "3070": {
                "name": "RTX 3070 8G",
                "uuid": "GPU-00000000-0000-0000-0000-000000000002",
            },
        },
        "profiles": {
            "qwen_160k": {
                "name": "Qwen3.8-27B IQ3_S (160K Main)",
                "default_gpu": "3080",
                "cwd": "G:\\App\\llama.cpp",
                "executable": "G:\\App\\llama.cpp\\llama-server.exe",
                "args": ["-m", "model.gguf", "-c", "163840"],
            },
            "bge_small": {
                "name": "BGE Embedding (Small)",
                "default_gpu": "3070",
                "cwd": "G:\\App\\llama.cpp",
                "executable": "G:\\App\\llama.cpp\\llama-server.exe",
                "args": ["-m", "bge.gguf"],
            },
        },
    }
    temp_config_file.write_text(json.dumps(test_config, indent=2), encoding="utf-8")
    temp_state_file.write_text(json.dumps({"instances": {}}, indent=2), encoding="utf-8")

    temp_state_mgr = StateManager(temp_state_file)

    monkeypatch.setattr(server, "CONFIG_FILE", temp_config_file)
    monkeypatch.setattr(server, "STATE_FILE", temp_state_file)
    monkeypatch.setattr(server, "state_manager", temp_state_mgr)
    monkeypatch.setattr(server, "find_port_listener", lambda port: None)

    test_client = TestClient(server.app)
    return {
        "client": test_client,
        "config_file": temp_config_file,
        "state_file": temp_state_file,
        "state_mgr": temp_state_mgr,
        "tmp_path": tmp_path,
    }


def test_root_serves_full_dashboard(client_env):
    """The root route serves the complete local dashboard, not an API placeholder."""
    client = client_env["client"]
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AI Runtime 显式遥控器" in resp.text
    assert "/static/app.js" in resp.text


def test_get_config(client_env):
    """GET /api/config returns configured GPUs and profiles."""
    client = client_env["client"]
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "gpus" in data
    assert "3080" in data["gpus"]
    assert "profiles" in data
    assert "qwen_160k" in data["profiles"]


def test_get_status_strict_read_only(client_env):
    """GET /api/status is strictly read-only and never modifies or writes to state.json."""
    client = client_env["client"]
    state_file = client_env["state_file"]
    state_mgr = client_env["state_mgr"]

    # Pre-populate state
    state_mgr.save_state_atomic({
        "instances": {
            "inst_111": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 111,
                "create_time": 1000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    initial_content = state_file.read_bytes()
    initial_mtime = state_file.stat().st_mtime_ns

    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.create_time.return_value = 1000.0

    mock_gpu_metrics = (
        [{"index": 0, "name": "RTX 3080", "uuid": "GPU-3080", "used_mb": 5000, "total_mb": 20480, "util_percent": 30}],
        [],
    )

    with patch("server.get_gpu_metrics", return_value=mock_gpu_metrics):
        with patch("server.check_process_identity", return_value=("match", mock_proc)):
            resp = client.get("/api/status")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["gpus"]) == 1
    assert "inst_111" in body["instances"]
    assert body["instances"]["inst_111"]["is_alive"] is True
    assert body["instances"]["inst_111"]["identity_status"] == "match"

    # Verify state file was NOT modified (read-only guarantee)
    after_content = state_file.read_bytes()
    after_mtime = state_file.stat().st_mtime_ns
    assert initial_content == after_content
    assert initial_mtime == after_mtime


def test_get_status_hides_non_live_instances(client_env):
    """GET /api/status only returns verifiably alive real services."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]
    state_mgr.save_state_atomic({"instances": {
        "dead": {"profile": "qwen_160k", "name": "dead", "pid": 222, "create_time": 2000.0, "executable": "llama-server.exe", "gpu": "3080", "gpu_name": "RTX 3080 20G", "started_at": "now"},
        "legacy": {"profile": "qwen_160k", "name": "legacy", "pid": 223, "create_time": 2000.0, "executable": "cmd.exe", "gpu": "3080", "gpu_name": "RTX 3080 20G", "started_at": "now"},
    }})
    with patch("server.get_gpu_metrics", return_value=([], [])), patch("server.check_process_identity", return_value=("not_found", None)):
        resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["instances"] == {}


def test_get_status_reports_identity_error(client_env):
    """GET /api/status omits identity errors from the live instance list."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_222": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 222,
                "create_time": 2000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    with patch("server.get_gpu_metrics", return_value=([], [])):
        with patch("server.check_process_identity", return_value=("error", None)):
            resp = client.get("/api/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "inst_222" not in data["instances"]
    assert data["errors"] == []


def test_start_instance_success(client_env):
    """POST /api/instances/start launches subprocess and saves state atomically."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    mock_popen = MagicMock()
    mock_popen.pid = 8888

    mock_psutil_proc = MagicMock(spec=psutil.Process)
    mock_psutil_proc.create_time.return_value = 1725805555.0

    with patch("subprocess.Popen", return_value=mock_popen) as mock_sub:
        with patch("psutil.Process", return_value=mock_psutil_proc):
            resp = client.post("/api/instances/start", json={"profile": "qwen_160k", "gpu": "3080"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["instance_id"] == "inst_8888"
    assert data["pid"] == 8888

    # Verify saved state
    current_state = state_mgr.load_state()
    assert "inst_8888" in current_state["instances"]
    inst = current_state["instances"]["inst_8888"]
    assert inst["pid"] == 8888
    assert inst["create_time"] == 1725805555.0
    assert inst["gpu"] == "3080"


def test_start_instance_wrapper_records_real_service_pid(client_env):
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]
    config_file = client_env["config_file"]
    config = json.loads(config_file.read_text(encoding="utf-8"))
    config["profiles"]["qwen_160k"].update({
        "executable": "cmd.exe",
        "args": ["/d", "/c", "call", "launcher.cmd"],
        "managed_process": {"executable": "llama-server.exe", "cmd_markers": ["models-preset", "qwen_preset.ini"], "process_role": "Coordinator Router (llama.cpp)"},
    })
    config_file.write_text(json.dumps(config), encoding="utf-8")
    launcher = MagicMock()
    launcher.pid = 8888
    launcher_proc = MagicMock(spec=psutil.Process)
    launcher_proc.create_time.return_value = 100.0
    real = MagicMock(spec=psutil.Process)
    real.pid = 8889
    real.create_time.return_value = 101.0
    real.exe.return_value = "G:\\App\\llama.cpp\\llama-server.exe"
    real.cmdline.return_value = ["llama-server.exe", "--models-preset", "qwen_preset.ini"]
    with patch("server.get_process_baseline_pids", return_value={1, 2}), patch("subprocess.Popen", return_value=launcher), patch(
        "psutil.Process", return_value=launcher_proc
    ), patch("server.discover_real_service", return_value=("found", real, None)):
        resp = client.post("/api/instances/start", json={"profile": "qwen_160k", "gpu": "3080"})
    assert resp.status_code == 200
    assert resp.json()["pid"] == 8889
    record = state_mgr.load_state()["instances"]["inst_8889"]
    assert record["pid"] == 8889
    assert record["launcher_pid"] == 8888
    assert record.get("command_line_redacted") == "llama-server.exe --models-preset qwen_preset.ini"
    assert "command_line" not in record


def test_start_instance_unknown_profile(client_env):
    """POST /api/instances/start with unknown profile returns 400."""
    client = client_env["client"]
    resp = client.post("/api/instances/start", json={"profile": "non_existent_profile"})
    assert resp.status_code == 400
    assert "未知的 Profile" in resp.json()["detail"]


def test_start_instance_unknown_gpu(client_env):
    """POST /api/instances/start with invalid GPU key returns 400."""
    client = client_env["client"]
    resp = client.post("/api/instances/start", json={"profile": "qwen_160k", "gpu": "9990"})
    assert resp.status_code == 400
    assert "未知的 GPU 设备" in resp.json()["detail"]


def test_start_instance_metadata_failure_triggers_orphan_cleanup(client_env):
    """POST /api/instances/start immediately terminates process tree if metadata capture fails."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    mock_popen = MagicMock()
    mock_popen.pid = 9999

    # Popen succeeds, but psutil.Process fails
    with patch("server.find_port_listener", return_value=None):
        with patch("subprocess.Popen", return_value=mock_popen):
            with patch("psutil.Process", side_effect=RuntimeError("Cannot inspect process metadata")):
                with patch("server.kill_process_tree") as mock_kill:
                    resp = client.post("/api/instances/start", json={"profile": "qwen_160k"})

    assert resp.status_code == 500
    assert "拉起子进程失败" in resp.json()["detail"]
    # Verify targeted orphan cleanup was called with PID
    mock_kill.assert_called_once_with(9999)

    # Verify no dangling instance was saved to state
    current_state = state_mgr.load_state()
    assert "inst_9999" not in current_state.get("instances", {})


def test_start_instance_port_conflict_returns_409(client_env):
    """POST /api/instances/start returns 409 with PortConflictDetail when target port is occupied."""
    client = client_env["client"]
    fake_listener = {
        "port": 8080,
        "pid": 5555,
        "name": "llama-server.exe",
        "create_time": 1725800000.0,
        "exe": "G:\\App\\llama.cpp\\llama-server.exe",
        "is_system": False,
    }

    with patch("server.find_port_listener", return_value=fake_listener):
        resp = client.post("/api/instances/start", json={"profile": "qwen_160k", "gpu": "3080"})

    assert resp.status_code == 409
    data = resp.json()["detail"]
    assert data["conflict"] == "port_in_use"
    assert data["port"] == 8080
    assert data["pid"] == 5555
    assert data["name"] == "llama-server.exe"
    assert data["can_force_clean"] is True


def test_start_instance_port_conflict_force_clean_success(client_env):
    """POST /api/instances/start with force_clean_port=True terminates occupier and starts."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    fake_listener = {
        "port": 8080,
        "pid": 5555,
        "name": "llama-server.exe",
        "create_time": 1725800000.0,
        "exe": "G:\\App\\llama.cpp\\llama-server.exe",
        "is_system": False,
    }

    mock_popen = MagicMock()
    mock_popen.pid = 8888
    mock_psutil_proc = MagicMock(spec=psutil.Process)
    mock_psutil_proc.create_time.return_value = 1725805555.0

    # First check finds listener, then cleanup is called, wait_port_free returns True
    with patch("server.find_port_listener", return_value=fake_listener), \
         patch("server.kill_process_tree") as mock_kill, \
         patch("server.wait_process_exit", return_value=True), \
         patch("server.wait_port_free", return_value=True), \
         patch("subprocess.Popen", return_value=mock_popen), \
         patch("psutil.Process", return_value=mock_psutil_proc):

        mock_kill.return_value.returncode = 0
        resp = client.post(
            "/api/instances/start",
            json={"profile": "qwen_160k", "gpu": "3080", "force_clean_port": True, "expected_port_pid": 5555},
        )

    assert resp.status_code == 200
    mock_kill.assert_called_once_with(5555)
    assert resp.json()["status"] == "success"
    assert resp.json()["instance_id"] == "inst_8888"


def test_start_instance_port_conflict_protected_system_pid_rejected(client_env):
    """POST /api/instances/start refuses to kill system process occupying port."""
    client = client_env["client"]
    fake_listener = {
        "port": 8080,
        "pid": 4,
        "name": "System",
        "create_time": 0.0,
        "exe": "",
        "is_system": True,
    }

    with patch("server.find_port_listener", return_value=fake_listener):
        resp = client.post(
            "/api/instances/start",
            json={"profile": "qwen_160k", "gpu": "3080", "force_clean_port": True},
        )

    assert resp.status_code == 403
    assert "无法清理系统或受保护进程" in resp.json()["detail"]


def test_stop_instance_unknown_returns_404(client_env):
    """POST /api/instances/{id}/stop with unknown instance returns 404."""
    client = client_env["client"]
    resp = client.post("/api/instances/inst_99999/stop")
    assert resp.status_code == 404
    assert "未找到该实例记录" in resp.json()["detail"]


def test_stop_instance_success(client_env):
    """POST /api/instances/{id}/stop successfully stops matched process and removes state."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_777": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 777,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    with patch("server.check_process_identity", return_value=("match", mock_proc)):
        with patch("server.kill_process_tree", return_value=MagicMock(returncode=0)) as mock_kill:
            with patch("server.wait_process_exit", return_value=True):
                resp = client.post("/api/instances/inst_777/stop")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["instance_id"] == "inst_777"
    assert data["exited"] is True
    mock_kill.assert_called_once_with(777)

    # Verify state record was deleted
    final_state = state_mgr.load_state()
    assert "inst_777" not in final_state["instances"]


def test_stop_instance_pid_reuse_mismatch_never_kills(client_env):
    """POST /api/instances/{id}/stop on PID reuse safely cleans stale record without killing."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_555": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 555,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    with patch("server.check_process_identity", return_value=("mismatch", mock_proc)):
        with patch("server.kill_process_tree") as mock_kill:
            resp = client.post("/api/instances/inst_555/stop")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stale_cleaned"
    assert data["instance_id"] == "inst_555"
    assert data["exited"] is True
    # Invariant: NEVER kill when mismatch
    mock_kill.assert_not_called()

    # Verify state record cleaned up
    final_state = state_mgr.load_state()
    assert "inst_555" not in final_state["instances"]


def test_stop_instance_not_found_cleans_stale_record(client_env):
    """POST /api/instances/{id}/stop on already dead process cleans stale state."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_444": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 444,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    with patch("server.check_process_identity", return_value=("not_found", None)):
        with patch("server.kill_process_tree") as mock_kill:
            resp = client.post("/api/instances/inst_444/stop")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "stale_cleaned"
    assert data["instance_id"] == "inst_444"
    assert data["exited"] is True
    mock_kill.assert_not_called()

    final_state = state_mgr.load_state()
    assert "inst_444" not in final_state["instances"]


def test_stop_instance_identity_error_fail_closed_retains_state(client_env):
    """POST /api/instances/{id}/stop fails closed (500) and preserves state when identity check fails."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_333": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 333,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    with patch("server.check_process_identity", return_value=("error", None)):
        with patch("server.kill_process_tree") as mock_kill:
            resp = client.post("/api/instances/inst_333/stop")

    assert resp.status_code == 500
    assert "无法确认实例 inst_333 的进程身份" in resp.json()["detail"]
    mock_kill.assert_not_called()

    # Invariant: State MUST be retained
    final_state = state_mgr.load_state()
    assert "inst_333" in final_state["instances"]


def test_stop_instance_taskkill_failure_retains_state_returns_500(client_env):
    """A failed targeted termination command must fail closed and retain state."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_667": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 667,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    failed_kill = MagicMock(returncode=1, stdout="", stderr="Access denied")
    with patch("server.check_process_identity", return_value=("match", mock_proc)):
        with patch("server.kill_process_tree", return_value=failed_kill) as mock_kill:
            resp = client.post("/api/instances/inst_667/stop")

    assert resp.status_code == 500
    assert "终止命令失败" in resp.json()["detail"]
    mock_kill.assert_called_once_with(667)
    assert "inst_667" in state_mgr.load_state()["instances"]


def test_stop_instance_legacy_wrapper_deletes_state_without_killing(client_env):
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]
    state_mgr.save_state_atomic({"instances": {"legacy": {
        "profile": "huihui-Qwen_27B-Q4K_120K", "name": "legacy", "pid": 901,
        "create_time": 10.0, "executable": "cmd.exe", "gpu": "3080",
        "gpu_name": "RTX 3080 20G", "started_at": "now"
    }}})
    with patch("server.kill_process_tree") as kill:
        resp = client.post("/api/instances/legacy/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stale_cleaned"
    kill.assert_not_called()
    assert "legacy" not in state_mgr.load_state()["instances"]


def test_stop_instance_target_then_launcher_and_persisted_real_metadata(client_env):
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]
    state_mgr.save_state_atomic({"instances": {"real": {
        "profile": "huihui-Qwen_27B-Q4K_120K", "name": "real", "pid": 902,
        "create_time": 10.0, "executable": "llama-server.exe", "gpu": "3080",
        "gpu_name": "RTX 3080 20G", "started_at": "now", "process_role": "Coordinator Router (llama.cpp)",
        "command_line": "llama-server.exe --models-preset qwen_preset.ini",
        "launcher_pid": 903, "launcher_create_time": 11.0, "launcher_executable": "cmd.exe"
    }}})
    target = MagicMock(spec=psutil.Process)
    launcher = MagicMock(spec=psutil.Process)
    calls = []
    def identity(pid, _ctime):
        calls.append(pid)
        return ("match", target if pid == 902 else launcher)
    with patch("server.check_process_identity", side_effect=identity), patch(
        "server.kill_process_tree", side_effect=lambda pid: (calls.append(f"kill:{pid}") or MagicMock(returncode=0))
    ), patch("server.wait_process_exit", return_value=True):
        resp = client.post("/api/instances/real/stop")
    assert resp.status_code == 200
    assert calls.index("kill:902") < calls.index("kill:903")
    assert "real" not in state_mgr.load_state()["instances"]


def test_stop_instance_launcher_failure_retains_state(client_env):
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]
    state_mgr.save_state_atomic({"instances": {"real-fail": {
        "profile": "qwen_160k", "name": "real-fail", "pid": 904,
        "create_time": 10.0, "executable": "llama-server.exe", "gpu": "3080",
        "gpu_name": "RTX 3080 20G", "started_at": "now", "launcher_pid": 905,
        "launcher_create_time": 11.0, "launcher_executable": "cmd.exe"
    }}})
    target = MagicMock(spec=psutil.Process)
    launcher = MagicMock(spec=psutil.Process)
    def identity(pid, _ctime):
        return ("match", target if pid == 904 else launcher)
    def terminate(pid):
        return MagicMock(returncode=0 if pid == 904 else 1, stdout="", stderr="launcher access denied")
    with patch("server.check_process_identity", side_effect=identity), patch("server.kill_process_tree", side_effect=terminate), patch(
        "server.wait_process_exit", return_value=True
    ):
        resp = client.post("/api/instances/real-fail/stop")
    assert resp.status_code == 500
    assert "real-fail" in state_mgr.load_state()["instances"]


def test_stop_instance_exit_timeout_retains_state_returns_409(client_env):
    """POST /api/instances/{id}/stop returns 409 and retains state if process does not exit."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_666": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 666,
                "create_time": 1725800000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    with patch("server.check_process_identity", return_value=("match", mock_proc)):
        with patch("server.kill_process_tree", return_value=MagicMock(returncode=0)):
            # wait_process_exit returns False (process refused to die)
            with patch("server.wait_process_exit", return_value=False):
                resp = client.post("/api/instances/inst_666/stop")

    assert resp.status_code == 409
    assert "在终止请求后仍未确认退出" in resp.json()["detail"]

    # Invariant: State MUST NOT be deleted to prevent untracked orphan processes!
    final_state = state_mgr.load_state()
    assert "inst_666" in final_state["instances"]


# ==========================================
# Security, Redaction, and Safe Field Tests
# ==========================================

def test_redact_command_line_args_and_secrets():
    """Verify redaction of sensitive flags, tokens, Bearer, and KEY=value patterns."""
    from runtime_deck.security import redact_command_line

    # 1. --api-key and --api_key with = and space
    raw1 = "llama-server.exe --api-key secret123 --port 8080"
    assert redact_command_line(raw1) == "llama-server.exe --api-key [REDACTED] --port 8080"

    raw2 = "python main.py --api_key=my_secret_token --other flag"
    assert redact_command_line(raw2) == "python main.py --api_key=[REDACTED] --other flag"

    # 2. --token, --hf-token, --auth, --password, --passwd, --secret
    raw3 = "app.exe --token=token_value --hf-token huggingface_placeholder --auth auth_value --password=password_value --passwd passwd_value --secret secret_value"
    redacted3 = redact_command_line(raw3)
    assert "--token=[REDACTED]" in redacted3
    assert "--hf-token [REDACTED]" in redacted3
    assert "--auth [REDACTED]" in redacted3
    assert "--password=[REDACTED]" in redacted3
    assert "--passwd [REDACTED]" in redacted3
    assert "--secret [REDACTED]" in redacted3
    assert "token_value" not in redacted3
    assert "huggingface_placeholder" not in redacted3

    # 3. Authorization: Bearer and Bearer
    raw4 = "curl -H 'Authorization: Bearer my_secret_jwt' http://127.0.0.1:8080"
    redacted4 = redact_command_line(raw4)
    assert "Authorization: Bearer [REDACTED]" in redacted4
    assert "my_secret_jwt" not in redacted4

    raw5 = "Bearer eyJhbGciOi..."
    assert redact_command_line(raw5) == "Bearer [REDACTED]"

    # 4. KEY=value and TOKEN=value env-style
    raw6 = "OPENAI_API_KEY=fake-project-key-123456 HF_TOKEN=fake-hf-token-98765 SECRET_KEY=topsecret"
    redacted6 = redact_command_line(raw6)
    assert "OPENAI_API_KEY=[REDACTED]" in redacted6
    assert "HF_TOKEN=[REDACTED]" in redacted6
    assert "SECRET_KEY=[REDACTED]" in redacted6
    assert "fake-project-key-123456" not in redacted6


def test_safe_error_detail_sanitizes_and_truncates():
    """safe_error_detail masks secrets and limits string length."""
    from runtime_deck.security import safe_error_detail

    err = "Execution failed on --api-key super_secret_val with detail: " + ("x" * 300)
    safe = safe_error_detail(err, max_length=100)
    assert "super_secret_val" not in safe
    assert "[REDACTED]" in safe
    assert len(safe) <= 104  # 100 + "..."


def test_status_and_state_never_expose_raw_command_line_or_secrets(client_env):
    """GET /api/status never returns unredacted command_line or credentials in payload."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    # Pre-populate state containing sensitive args in legacy command_line or command_line_redacted
    state_mgr.save_state_atomic({
        "instances": {
            "inst_sensitive": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 777,
                "create_time": 1000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
                "command_line": "llama-server.exe --api-key super_secret_key_12345 --port 8080",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.create_time.return_value = 1000.0

    with patch("server.get_gpu_metrics", return_value=([], [])):
        with patch("server.check_process_identity", return_value=("match", mock_proc)):
            resp = client.get("/api/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "inst_sensitive" in data["instances"]
    inst_view = data["instances"]["inst_sensitive"]

    # Invariant: raw command_line must NOT be in the API view
    assert "command_line" not in inst_view
    # Public status must not return even the redacted command line.
    assert "command_line_redacted" not in inst_view
    assert "super_secret_key_12345" not in resp.text


def test_start_instance_stores_redacted_command_line_without_secret(client_env):
    """POST /api/instances/start strips sensitive keys before writing to state."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    mock_proc = MagicMock()
    mock_proc.pid = 888
    mock_proc.args = ["C:\\Runtimes\\llama.cpp\\llama-server.exe", "--api-key", "example_secret_token", "--port", "8080"]

    p_proc = MagicMock(spec=psutil.Process)
    p_proc.create_time.return_value = 5000.0

    with patch("server.get_process_baseline_pids", return_value={1, 2}), \
         patch("subprocess.Popen", return_value=mock_proc), \
         patch("psutil.Process", return_value=p_proc):
        resp = client.post("/api/instances/start", json={"profile": "qwen_160k", "gpu": "3080"})

    assert resp.status_code == 200
    saved_state = state_mgr.load_state()
    inst = saved_state["instances"]["inst_888"]
    assert "command_line" not in inst
    assert "command_line_redacted" in inst
    assert "example_secret_token" not in inst["command_line_redacted"]
    assert "[REDACTED]" in inst["command_line_redacted"]


# ==========================================
# Stable /api/v1 Endpoints Contract Tests
# ==========================================

def test_v1_profiles_returns_safe_fields_only(client_env):
    """GET /api/v1/profiles returns only safe metadata (no PID, cmd, executable, or secrets)."""
    client = client_env["client"]
    resp = client.get("/api/v1/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert "profiles" in data
    profiles = data["profiles"]
    assert len(profiles) >= 2

    for p in profiles:
        assert "profile_id" in p
        assert "name" in p
        assert "runtime_kind" in p
        assert "default_gpu" in p
        assert p["runtime_kind"] in ("llama_cpp", "comfyui", "unknown")
        # Ensure unsafe fields are never leaked
        assert "executable" not in p
        assert "args" not in p
        assert "cwd" not in p
        assert "command_line" not in p
        assert "pid" not in p


def test_v1_profile_status_stopped_when_no_instance(client_env):
    """GET /api/v1/profiles/{id}/status returns stopped when not running."""
    client = client_env["client"]
    resp = client.get("/api/v1/profiles/qwen_160k/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == "qwen_160k"
    assert data["runtime_kind"] == "llama_cpp"
    assert data["state"] == "stopped"
    assert data["endpoint"] == "http://127.0.0.1:163840" or "http://127.0.0.1" in data["endpoint"]
    assert data["health"] == "unknown"
    assert data["gpu"] == "3080"
    # Never return PID / command_line / launcher
    assert "pid" not in data
    assert "command_line" not in data
    assert "launcher_pid" not in data


def test_v1_profile_status_running_and_safe_fields(client_env):
    """GET /api/v1/profiles/{id}/status returns running when identity matches."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_123": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 123,
                "create_time": 1000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    with patch("server.check_process_identity", return_value=("match", mock_proc)):
        resp = client.get("/api/v1/profiles/qwen_160k/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "running"
    assert data["runtime_kind"] == "llama_cpp"
    assert data["gpu"] == "3080"
    assert "pid" not in data
    assert "command_line" not in data


def test_v1_profile_status_identity_mismatch_and_error(client_env):
    """GET /api/v1/profiles/{id}/status maps mismatch to stopped and error to error."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_123": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 123,
                "create_time": 1000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    # Mismatch (PID reused) -> maps to stopped
    with patch("server.check_process_identity", return_value=("mismatch", MagicMock())):
        resp = client.get("/api/v1/profiles/qwen_160k/status")
    assert resp.status_code == 200
    assert resp.json()["state"] == "stopped"

    # Error (permission error etc) -> maps to error
    with patch("server.check_process_identity", return_value=("error", None)):
        resp = client.get("/api/v1/profiles/qwen_160k/status")
    assert resp.status_code == 200
    assert resp.json()["state"] == "error"
    assert resp.json()["health"] == "error"


def test_v1_start_profile_success_never_returns_pid_or_launcher(client_env):
    """POST /api/v1/profiles/{id}/start starts instance and returns V1ProfileStatusResponse."""
    client = client_env["client"]

    mock_proc = MagicMock()
    mock_proc.pid = 999
    mock_proc.args = ["G:\\App\\llama.cpp\\llama-server.exe", "--port", "8080"]
    p_proc = MagicMock(spec=psutil.Process)
    p_proc.create_time.return_value = 1000.0

    with patch("server.get_process_baseline_pids", return_value={1}), \
         patch("subprocess.Popen", return_value=mock_proc), \
         patch("psutil.Process", return_value=p_proc), \
         patch("server.check_process_identity", return_value=("match", p_proc)):
        resp = client.post("/api/v1/profiles/qwen_160k/start", json={"gpu": "3080"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == "qwen_160k"
    assert data["state"] == "running"
    assert data["runtime_kind"] == "llama_cpp"
    assert data["gpu"] == "3080"
    assert "pid" not in data
    assert "launcher_pid" not in data
    assert "command_line" not in data


def test_v1_stop_profile_success_never_returns_pid_or_launcher(client_env):
    """POST /api/v1/profiles/{id}/stop stops instance and returns safe status."""
    client = client_env["client"]
    state_mgr = client_env["state_mgr"]

    state_mgr.save_state_atomic({
        "instances": {
            "inst_888": {
                "profile": "qwen_160k",
                "name": "Qwen3.8-27B",
                "pid": 888,
                "create_time": 1000.0,
                "executable": "llama-server.exe",
                "gpu": "3080",
                "gpu_name": "RTX 3080 20G",
                "started_at": "2026-09-08T12:00:00",
            }
        }
    })

    mock_proc = MagicMock(spec=psutil.Process)
    with patch("server.check_process_identity", return_value=("match", mock_proc)), \
         patch("server.kill_process_tree", return_value=MagicMock(returncode=0)), \
         patch("server.wait_process_exit", return_value=True):
        resp = client.post("/api/v1/profiles/qwen_160k/stop")

    assert resp.status_code == 200
    data = resp.json()
    assert data["profile_id"] == "qwen_160k"
    assert data["state"] == "stopped"
    assert "pid" not in data
    assert "launcher_pid" not in data
    assert "command_line" not in data


def test_v1_stop_unknown_profile_returns_404(client_env):
    """POST /api/v1/profiles/{id}/stop for unknown profile returns 404."""
    client = client_env["client"]
    resp = client.post("/api/v1/profiles/non_existent_profile/stop")
    assert resp.status_code == 404
    assert "未找到 Profile" in resp.json()["detail"]


def test_list_gpu_processes_returns_sanitized_snapshot(client_env):
    client = client_env["client"]
    process_rows = [{
        "gpu_index": 0,
        "gpu_uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "pid": 123,
        "name": "python.exe",
        "executable": "python.exe",
        "used_mb": 4096,
        "create_time": 100.0,
    }]
    with patch("server.get_gpu_metrics", return_value=([{
        "index": 0,
        "name": "RTX 3080",
        "uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "used_mb": 4096,
        "total_mb": 20480,
        "util_percent": 10,
    }], [])), patch("server.get_gpu_processes", return_value=(process_rows, [])):
        resp = client.get("/api/gpus/0/processes")

    assert resp.status_code == 200
    body = resp.json()
    assert body["gpu_index"] == 0
    assert body["processes"][0]["pid"] == 123
    assert "command_line" not in body["processes"][0]
    assert "environment" not in body["processes"][0]


def test_list_gpu_processes_unknown_gpu_returns_404(client_env):
    client = client_env["client"]
    with patch("server.get_gpu_metrics", return_value=([], [])):
        resp = client.get("/api/gpus/9/processes")
    assert resp.status_code == 404
    assert "未找到 GPU 9" in resp.json()["detail"]


def test_kill_gpu_process_rechecks_gpu_and_identity(client_env):
    client = client_env["client"]
    process_row = {
        "gpu_index": 0,
        "gpu_uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "pid": 123,
        "name": "python.exe",
        "executable": "python.exe",
        "used_mb": 4096,
        "create_time": 100.0,
    }
    process = MagicMock(spec=psutil.Process)
    with patch("server.get_gpu_metrics", return_value=([{
        "index": 0,
        "name": "RTX 3080",
        "uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "used_mb": 4096,
        "total_mb": 20480,
        "util_percent": 10,
    }], [])), patch("server.get_gpu_processes", return_value=([process_row], [])), patch(
        "server.check_process_identity", return_value=("match", process)
    ), patch("server.kill_process_tree", return_value=MagicMock(returncode=0)) as kill, patch(
        "server.wait_process_exit", return_value=True
    ):
        resp = client.post("/api/gpus/0/processes/123/kill", json={"create_time": 100.0})

    assert resp.status_code == 200
    assert resp.json()["exited"] is True
    kill.assert_called_once_with(123)


def test_kill_gpu_process_rejects_pid_that_left_gpu(client_env):
    client = client_env["client"]
    with patch("server.get_gpu_metrics", return_value=([{
        "index": 0,
        "name": "RTX 3080",
        "uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "used_mb": 0,
        "total_mb": 20480,
        "util_percent": 0,
    }], [])), patch("server.get_gpu_processes", return_value=([], [])), patch(
        "server.kill_process_tree"
    ) as kill:
        resp = client.post("/api/gpus/0/processes/123/kill", json={"create_time": 100.0})

    assert resp.status_code == 409
    assert "不再占用该 GPU" in resp.json()["detail"]
    kill.assert_not_called()


def test_kill_gpu_process_rejects_system_pid(client_env):
    client = client_env["client"]
    process_row = {
        "gpu_index": 0,
        "gpu_uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "pid": 4,
        "name": "System",
        "executable": "[Insufficient Permissions]",
        "used_mb": None,
        "create_time": 0.0,
        "can_kill": False,
        "kill_reason": "系统进程不可结束",
    }
    with patch("server.get_gpu_metrics", return_value=([{
        "index": 0,
        "name": "RTX 3080",
        "uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "used_mb": 0,
        "total_mb": 20480,
        "util_percent": 0,
    }], [])), patch("server.get_gpu_processes", return_value=([process_row], [])), patch(
        "server.kill_process_tree"
    ) as kill:
        resp = client.post("/api/gpus/0/processes/4/kill", json={"create_time": 0.0})

    assert resp.status_code == 403
    assert "系统进程不可结束" in resp.json()["detail"]
    kill.assert_not_called()


def test_kill_gpu_process_rejects_pid_reuse(client_env):
    client = client_env["client"]
    process_row = {
        "gpu_index": 0,
        "gpu_uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "pid": 123,
        "name": "python.exe",
        "executable": "python.exe",
        "used_mb": 4096,
        "create_time": 200.0,
    }
    with patch("server.get_gpu_metrics", return_value=([{
        "index": 0,
        "name": "RTX 3080",
        "uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "used_mb": 4096,
        "total_mb": 20480,
        "util_percent": 10,
    }], [])), patch("server.get_gpu_processes", return_value=([process_row], [])), patch(
        "server.check_process_identity", return_value=("mismatch", MagicMock(spec=psutil.Process))
    ), patch("server.kill_process_tree") as kill:
        resp = client.post("/api/gpus/0/processes/123/kill", json={"create_time": 100.0})

    assert resp.status_code == 409
    assert "PID 身份已变化" in resp.json()["detail"]
    kill.assert_not_called()



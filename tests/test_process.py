"""Unit tests for process identity verification and safe process management."""

import subprocess
from unittest.mock import MagicMock, patch
import psutil
import pytest

from runtime_deck.process import (
    check_process_identity,
    discover_real_service,
    get_service_spec_for_profile,
    is_legacy_wrapper_state,
    is_matching_real_service,
    kill_process_tree,
    wait_process_exit,
)


def test_check_process_identity_match():
    """Identity matches when PID exists and create_time is within 0.5s tolerance."""
    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.create_time.return_value = 1725801600.2

    with patch("psutil.Process", return_value=mock_proc):
        status, proc = check_process_identity(pid=12345, expected_create_time=1725801600.0)

    assert status == "match"
    assert proc is mock_proc


def test_check_process_identity_mismatch_pid_reuse():
    """Identity mismatch detected when PID exists but create_time differs (PID reuse defense)."""
    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.create_time.return_value = 1725809999.0

    with patch("psutil.Process", return_value=mock_proc):
        status, proc = check_process_identity(pid=12345, expected_create_time=1725801600.0)

    assert status == "mismatch"
    assert proc is mock_proc


def test_check_process_identity_not_found():
    """Process not found when target PID does not exist."""
    with patch("psutil.Process", side_effect=psutil.NoSuchProcess(pid=99999)):
        status, proc = check_process_identity(pid=99999, expected_create_time=1725801600.0)

    assert status == "not_found"
    assert proc is None


def test_check_process_identity_error():
    """Fail-closed on access denied or unexpected error during inspection."""
    with patch("psutil.Process", side_effect=psutil.AccessDenied(pid=1)):
        status, proc = check_process_identity(pid=1, expected_create_time=1725801600.0)

    assert status == "error"
    assert proc is None


def test_get_service_spec_for_profile_uses_explicit_markers():
    spec = get_service_spec_for_profile(
        "custom",
        explicit_spec={"executable": "llama-server.exe", "cmd_markers": ["model.gguf"]},
    )
    assert "llama-server.exe" in spec["exe_names"]
    assert spec["cmd_markers"] == ["model.gguf"]


def test_is_matching_real_service_qwen_and_comfy():
    qwen = MagicMock(spec=psutil.Process)
    qwen.name.return_value = "llama-server.exe"
    qwen.cmdline.return_value = ["llama-server.exe", "--models-preset", "qwen_preset.ini"]
    assert is_matching_real_service(qwen, ["llama-server.exe"], ["models-preset", "qwen_preset.ini"]) == (True, None)

    comfy = MagicMock(spec=psutil.Process)
    comfy.name.return_value = "python.exe"
    comfy.cmdline.return_value = ["python.exe", "ComfyUI\\main.py"]
    assert is_matching_real_service(comfy, ["python.exe"], ["ComfyUI", "main.py"]) == (True, None)


def test_discover_real_service_excludes_baseline_and_requires_unique_candidate():
    launcher = MagicMock()
    launcher.children.return_value = []
    candidate = MagicMock(spec=psutil.Process)
    candidate.pid = 222
    candidate.name.return_value = "llama-server.exe"
    candidate.cmdline.return_value = ["llama-server.exe", "--models-preset", "qwen_preset.ini"]
    candidate.create_time.return_value = 12.5
    with patch("runtime_deck.process.psutil.Process", side_effect=lambda pid: launcher if pid == 100 else candidate), patch(
        "runtime_deck.process.psutil.pids", side_effect=[[100, 222]]
    ):
        status, proc, detail = discover_real_service({111}, 100, ["llama-server.exe"], ["models-preset", "qwen_preset.ini"], timeout=0.01, poll_interval=0)
    assert status == "found"
    assert proc is candidate


def test_discover_real_service_ambiguous_fails_closed():
    launcher = MagicMock()
    launcher.children.return_value = []
    candidates = {}
    for pid in (222, 223):
        p = MagicMock(spec=psutil.Process)
        p.pid = pid
        p.name.return_value = "llama-server.exe"
        p.cmdline.return_value = ["llama-server.exe", "--models-preset", "qwen_preset.ini"]
        candidates[pid] = p
    def proc_for(pid):
        return launcher if pid == 100 else candidates[pid]
    with patch("runtime_deck.process.psutil.Process", side_effect=proc_for), patch(
        "runtime_deck.process.psutil.pids", return_value=[100, 222, 223]
    ):
        status, proc, detail = discover_real_service(set(), 100, ["llama-server.exe"], ["models-preset", "qwen_preset.ini"], timeout=0.01, poll_interval=0)
    assert status == "ambiguous"
    assert proc is None


def test_is_legacy_wrapper_state_refuses_wrapper_identity():
    assert is_legacy_wrapper_state({"pid": 10, "executable": "cmd.exe"}) is True
    assert is_legacy_wrapper_state({"pid": 10, "executable": "llama-server.exe", "launcher_pid": 11}) is False


def test_kill_process_tree_windows_command():
    """Verify targeted taskkill invocation with /PID <pid> /T /F without /IM."""
    mock_res = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("runtime_deck.process.IS_WINDOWS", True):
        with patch("subprocess.run", return_value=mock_res) as mock_run:
            res = kill_process_tree(pid=54321)
            mock_run.assert_called_once_with(
                ["taskkill", "/PID", "54321", "/T", "/F"],
                capture_output=True,
                text=True,
            )
            assert res.returncode == 0


def test_wait_process_exit_immediate_exit():
    """Process exits cleanly on first attempt."""
    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.is_running.return_value = False

    exited = wait_process_exit(mock_proc, max_attempts=3, interval=0.01)
    assert exited is True


def test_wait_process_exit_no_such_process():
    """Process raises NoSuchProcess indicating it has terminated."""
    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.is_running.side_effect = psutil.NoSuchProcess(pid=123)

    exited = wait_process_exit(mock_proc, max_attempts=3, interval=0.01)
    assert exited is True


def test_wait_process_exit_timeout_still_alive():
    """Process remains alive after all attempts; wait_process_exit returns False."""
    mock_proc = MagicMock(spec=psutil.Process)
    mock_proc.is_running.return_value = True

    exited = wait_process_exit(mock_proc, max_attempts=3, interval=0.01)
    assert exited is False


def test_wait_process_exit_none():
    """When proc is None, immediately returns True."""
    assert wait_process_exit(None) is True

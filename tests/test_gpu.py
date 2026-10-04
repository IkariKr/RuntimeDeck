"""Unit tests for GPU metrics parsing and resilient error handling."""

import subprocess
from unittest.mock import MagicMock, patch
import pytest

from runtime_deck.gpu import get_gpu_metrics, get_gpu_processes


def test_get_gpu_metrics_success():
    """Verify standard multi-GPU nvidia-smi CSV output parsing."""
    sample_output = (
        "0, NVIDIA GeForce RTX 3080, GPU-00000000-0000-0000-0000-000000000001, 14250, 20480, 85\n"
        "1, NVIDIA GeForce RTX 3070, GPU-00000000-0000-0000-0000-000000000002, 820, 8192, 5\n"
    )

    with patch("subprocess.check_output", return_value=sample_output):
        gpus, errors = get_gpu_metrics()

    assert errors == []
    assert len(gpus) == 2

    gpu0 = gpus[0]
    assert gpu0["index"] == 0
    assert gpu0["name"] == "NVIDIA GeForce RTX 3080"
    assert gpu0["uuid"] == "GPU-00000000-0000-0000-0000-000000000001"
    assert gpu0["used_mb"] == 14250
    assert gpu0["total_mb"] == 20480
    assert gpu0["util_percent"] == 85

    gpu1 = gpus[1]
    assert gpu1["index"] == 1
    assert gpu1["name"] == "NVIDIA GeForce RTX 3070"
    assert gpu1["uuid"] == "GPU-00000000-0000-0000-0000-000000000002"
    assert gpu1["used_mb"] == 820
    assert gpu1["total_mb"] == 8192
    assert gpu1["util_percent"] == 5


def test_get_gpu_metrics_file_not_found():
    """Verify graceful handling when nvidia-smi executable is missing from PATH."""
    with patch("subprocess.check_output", side_effect=FileNotFoundError("nvidia-smi not found")):
        gpus, errors = get_gpu_metrics()

    assert gpus == []
    assert len(errors) == 1
    assert "nvidia-smi" in errors[0]
    assert "未找到 nvidia-smi 命令" in errors[0]


def test_get_gpu_metrics_called_process_error():
    """Verify non-zero returncode handling (driver communication error, etc.)."""
    err = subprocess.CalledProcessError(
        returncode=999,
        cmd=["nvidia-smi"],
        output="NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."
    )
    with patch("subprocess.check_output", side_effect=err):
        gpus, errors = get_gpu_metrics()

    assert gpus == []
    assert len(errors) == 1
    assert "999" in errors[0]
    assert "couldn't communicate with the NVIDIA driver" in errors[0]


def test_get_gpu_metrics_generic_exception():
    """Verify general unexpected exception handling."""
    with patch("subprocess.check_output", side_effect=RuntimeError("Unexpected OS error")):
        gpus, errors = get_gpu_metrics()

    assert gpus == []
    assert len(errors) == 1
    assert "Unexpected OS error" in errors[0]


def test_get_gpu_metrics_malformed_lines():
    """Verify that empty lines or lines with missing columns are skipped safely."""
    sample_output = (
        "\n"
        "invalid, line, short\n"
        "0, RTX 3080, GPU-UUID-1, 1000, 20000, 10\n"
        "\n"
    )
    with patch("subprocess.check_output", return_value=sample_output):
        gpus, errors = get_gpu_metrics()

    assert errors == []
    assert len(gpus) == 1
    assert gpus[0]["index"] == 0
    assert gpus[0]["uuid"] == "GPU-UUID-1"


def test_get_gpu_processes_filters_uuid_and_sorts_by_memory():
    sample_output = (
        "GPU-OTHER, 20, other.exe, 900\n"
        "GPU-TARGET, 42, python.exe, 2048\n"
        "GPU-TARGET, 7, llama-server.exe, 512\n"
    )
    process = MagicMock()
    process.name.return_value = "python.exe"
    process.exe.return_value = "C:\\AI\\python.exe"
    process.create_time.return_value = 123.5
    with patch("subprocess.check_output", return_value=sample_output), patch(
        "runtime_deck.gpu.psutil.Process", return_value=process
    ):
        processes, errors = get_gpu_processes(1, "GPU-TARGET")

    assert errors == []
    assert [item["pid"] for item in processes] == [42, 7]
    assert all(item["gpu_uuid"] == "GPU-TARGET" for item in processes)
    assert processes[0]["used_mb"] == 2048
    assert processes[0]["create_time"] == 123.5
    assert processes[0]["can_kill"] is True


def test_get_gpu_processes_handles_driver_error():
    error = subprocess.CalledProcessError(returncode=1, cmd=["nvidia-smi"], output="Access denied")
    with patch("subprocess.check_output", side_effect=error):
        processes, errors = get_gpu_processes(0, "GPU-0")

    assert processes == []
    assert errors
    assert "退出码 1" in errors[0]

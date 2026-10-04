"""Tests for runtime_deck.ports module."""

import socket
import pytest
from unittest.mock import MagicMock, patch

from runtime_deck.ports import (
    resolve_profile_port,
    is_port_listening,
    find_port_listener,
    wait_port_free,
)


def test_resolve_profile_port_explicit():
    profile = {"port": 8080, "name": "test"}
    assert resolve_profile_port(profile) == 8080

    profile_managed = {"managed_process": {"port": 8094}}
    assert resolve_profile_port(profile_managed) == 8094


def test_resolve_profile_port_args():
    profile = {"args": ["--port=8088"]}
    assert resolve_profile_port(profile) == 8088

    profile2 = {"args": ["--host", "0.0.0.0", "-p", "8089"]}
    assert resolve_profile_port(profile2) == 8089


def test_resolve_profile_port_markers():
    profile = {
        "managed_process": {
            "cmd_markers": ["llama-server.exe", "--port", "8095"]
        }
    }
    assert resolve_profile_port(profile) == 8095

    profile2 = {
        "managed_process": {
            "cmd_markers": ["llama-server.exe", "8096"]
        }
    }
    assert resolve_profile_port(profile2) == 8096


def test_resolve_profile_port_heuristics():
    profile_comfy = {"name": "comfyui_server", "executable": "cmd.exe", "args": ["run_comfy.bat"]}
    assert resolve_profile_port(profile_comfy) == 8188

    profile_llama = {"name": "qwen_test", "executable": "llama-server.exe", "args": []}
    assert resolve_profile_port(profile_llama) == 8080

    profile_other = {"name": "custom", "executable": "custom.exe", "args": []}
    assert resolve_profile_port(profile_other) is None


def test_is_port_listening():
    # Bind a temporary local socket to test listening check
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert is_port_listening(port) is True
    finally:
        sock.close()

    assert is_port_listening(port) is False


def test_find_port_listener_system_pid():
    mock_conn = MagicMock()
    mock_conn.status = "LISTEN"
    mock_conn.laddr.port = 445
    mock_conn.pid = 4

    with patch("psutil.net_connections", return_value=[mock_conn]):
        info = find_port_listener(445)
        assert info is not None
        assert info["port"] == 445
        assert info["pid"] == 4
        assert info["is_system"] is True
        assert info["name"] == "System"


def test_find_port_listener_normal_process():
    mock_conn = MagicMock()
    mock_conn.status = "LISTEN"
    mock_conn.laddr.port = 8080
    mock_conn.pid = 5678

    mock_proc = MagicMock()
    mock_proc.name.return_value = "llama-server.exe"
    mock_proc.create_time.return_value = 1725800000.0
    mock_proc.exe.return_value = "G:\\App\\llama.cpp\\llama-server.exe"

    with patch("psutil.net_connections", return_value=[mock_conn]), \
         patch("psutil.Process", return_value=mock_proc):
        info = find_port_listener(8080)
        assert info is not None
        assert info["port"] == 8080
        assert info["pid"] == 5678
        assert info["name"] == "llama-server.exe"
        assert info["is_system"] is False
        assert info["create_time"] == 1725800000.0


def test_wait_port_free():
    with patch("runtime_deck.ports.is_port_listening", side_effect=[True, True, False]):
        assert wait_port_free(8080, timeout=1.0, interval=0.01) is True

    with patch("runtime_deck.ports.is_port_listening", return_value=True):
        assert wait_port_free(8080, timeout=0.1, interval=0.01) is False

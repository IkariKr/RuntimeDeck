"""API tests for the controlled Profile script endpoints."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

import server
from runtime_deck.state import StateManager


def _client(tmp_path: Path, monkeypatch, args):
    script = tmp_path / "private-start.cmd"
    script.write_text("@echo off\necho old\n", encoding="utf-8")
    config = {
        "gpus": {},
        "profiles": {"demo": {"name": "Demo", "cwd": str(tmp_path), "executable": "cmd.exe", "args": args}},
    }
    config_file = tmp_path / "runtimes.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(server, "CONFIG_FILE", config_file)
    monkeypatch.setattr(server, "state_manager", StateManager(state_file))
    return TestClient(server.app), script, config_file


def test_get_script_is_safe_and_reads_configured_file(tmp_path: Path, monkeypatch):
    script = tmp_path / "private-start.cmd"
    client, script, config_file = _client(tmp_path, monkeypatch, ["/d", "/c", "call", f'"{script}"'])

    response = client.get("/api/profiles/demo/script")
    assert response.status_code == 200
    body = response.json()
    assert body["profile_id"] == "demo"
    assert body["filename"] == script.name
    assert body["language"] == "batch"
    assert body["content"] == "@echo off\r\necho old\r\n"
    assert body["editable"] is True
    assert str(script) not in response.text
    assert "cwd" not in response.text
    assert config_file.read_text(encoding="utf-8") == json.dumps(
        {"gpus": {}, "profiles": {"demo": {"name": "Demo", "cwd": str(tmp_path), "executable": "cmd.exe", "args": ["/d", "/c", "call", f'"{script}"']}}},
        ensure_ascii=False,
    )


def test_put_script_updates_only_referenced_file_and_returns_revision(tmp_path: Path, monkeypatch):
    client, script, config_file = _client(tmp_path, monkeypatch, ["/d", "/c", "call", str(tmp_path / "private-start.cmd")])
    loaded = client.get("/api/profiles/demo/script").json()

    response = client.put(
        "/api/profiles/demo/script",
        json={"content": "@echo off\necho new\n", "expected_revision": loaded["revision"]},
    )
    assert response.status_code == 200
    assert response.json()["revision"] != loaded["revision"]
    assert script.read_text(encoding="utf-8") == "@echo off\necho new\n"
    assert "echo new" not in config_file.read_text(encoding="utf-8")


def test_put_script_rejects_stale_revision_without_overwrite(tmp_path: Path, monkeypatch):
    client, script, _ = _client(tmp_path, monkeypatch, ["private-start.cmd"])
    loaded = client.get("/api/profiles/demo/script").json()
    script.write_text("echo external\n", encoding="utf-8")

    response = client.put(
        "/api/profiles/demo/script",
        json={"content": "echo overwrite\n", "expected_revision": loaded["revision"]},
    )
    assert response.status_code == 409
    assert "current_revision" in response.json()["detail"]
    assert script.read_text(encoding="utf-8") == "echo external\n"


def test_script_api_handles_unknown_and_unconfigured_profiles(tmp_path: Path, monkeypatch):
    client, _, _ = _client(tmp_path, monkeypatch, ["--model", "model.gguf"])
    assert client.get("/api/profiles/missing/script").status_code == 404
    response = client.get("/api/profiles/demo/script")
    assert response.status_code == 200
    assert response.json() == {"profile_id": "demo", "filename": None, "language": None, "content": "", "revision": None, "editable": False}
    assert client.put("/api/profiles/demo/script", json={"content": "x", "expected_revision": "x"}).status_code == 400

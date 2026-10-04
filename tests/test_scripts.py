"""Unit tests for controlled Profile batch-script access."""

from pathlib import Path

import pytest

import runtime_deck.scripts as scripts_module
from runtime_deck.scripts import (
    ScriptConflictError,
    ScriptReferenceError,
    extract_script_references,
    read_script,
    resolve_script_path,
    save_script,
)


def test_extract_script_reference_supports_call_and_quotes(tmp_path: Path):
    script = tmp_path / "start service.cmd"
    profile = {"cwd": str(tmp_path), "args": ["/d", "/c", "call", f'"{script}"']}

    assert extract_script_references(profile["args"]) == [str(script)]
    assert resolve_script_path(profile) == script.resolve()


def test_extract_command_fragment_reports_only_script_token():
    assert extract_script_references(["cmd.exe /c call run.cmd"]) == ["run.cmd"]
    assert extract_script_references(["cmd.exe /c call 'run.cmd'"]) == ["run.cmd"]


def test_extract_direct_and_script_option_paths():
    assert extract_script_references(["run.cmd"]) == ["run.cmd"]
    assert extract_script_references(["--script=foo.cmd"]) == ["foo.cmd"]
    assert extract_script_references(["--script=\"foo.cmd\""]) == ["foo.cmd"]


def test_script_read_preserves_bom_and_newlines(tmp_path: Path):
    script = tmp_path / "run.bat"
    script.write_bytes(b"\xef\xbb\xbf@echo off\r\necho old\r\n")
    profile = {"cwd": str(tmp_path), "args": ["run.bat"]}

    document = read_script(profile)
    assert document is not None
    assert document.content == "@echo off\r\necho old\r\n"
    assert document.filename == "run.bat"
    assert document.language == "batch"

    saved = save_script(profile, "@echo off\necho new\n", document.revision)
    assert saved.content == "@echo off\r\necho new\r\n"
    assert script.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"echo new\r\n" in script.read_bytes()


def test_script_revision_conflict_keeps_external_content(tmp_path: Path):
    script = tmp_path / "run.cmd"
    script.write_text("echo one\n", encoding="utf-8")
    profile = {"cwd": str(tmp_path), "args": ["run.cmd"]}
    original = read_script(profile)
    assert original is not None

    script.write_text("echo external\n", encoding="utf-8")
    with pytest.raises(ScriptConflictError):
        save_script(profile, "echo overwrite\n", original.revision)
    assert script.read_text(encoding="utf-8") == "echo external\n"


def test_relative_script_parent_symlink_cannot_escape_cwd(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "run.cmd").write_text("echo outside\n", encoding="utf-8")
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ScriptReferenceError):
        resolve_script_path({"cwd": str(tmp_path), "args": ["linked/run.cmd"]})


def test_relative_script_dotdot_escape_is_rejected(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-dotdot"
    outside.mkdir()
    (outside / "run.cmd").write_text("echo outside\n", encoding="utf-8")
    with pytest.raises(ScriptReferenceError):
        resolve_script_path({"cwd": str(tmp_path), "args": ["../" + outside.name + "/run.cmd"]})


def test_save_rechecks_snapshot_before_replace(tmp_path: Path, monkeypatch):
    script = tmp_path / "run.cmd"
    script.write_text("echo one\n", encoding="utf-8")
    profile = {"cwd": str(tmp_path), "args": ["run.cmd"]}
    original = read_script(profile)
    assert original is not None

    original_verify = scripts_module._verify_snapshot

    def mutate_before_replace(path, snapshot, revision):
        script.write_text("echo external\n", encoding="utf-8")
        return original_verify(path, snapshot, revision)

    monkeypatch.setattr(scripts_module, "_verify_snapshot", mutate_before_replace)
    with pytest.raises(ScriptConflictError):
        save_script(profile, "echo overwrite\n", original.revision)
    assert script.read_text(encoding="utf-8") == "echo external\n"


def test_multiple_references_are_rejected_and_no_reference_is_safe(tmp_path: Path):
    with pytest.raises(ScriptReferenceError):
        resolve_script_path({"args": ["one.cmd", "two.bat"], "cwd": str(tmp_path)})
    assert resolve_script_path({"args": ["--model", "model.gguf"], "cwd": str(tmp_path)}) is None

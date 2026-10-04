"""Controlled access to batch scripts referenced by RuntimeDeck profiles.

Only script references already present in a profile's configured ``args`` are
accepted.  This module deliberately has no API for opening an arbitrary path
supplied by a caller.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


SCRIPT_EXTENSIONS = frozenset({".cmd", ".bat"})
MAX_SCRIPT_BYTES = 1024 * 1024


class ScriptError(Exception):
    """Base class for expected controlled-script failures."""


class ScriptReferenceError(ScriptError):
    """The profile does not contain exactly one usable script reference."""


class ScriptNotFoundError(ScriptError):
    """The configured script reference does not exist."""


class ScriptNotRegularFileError(ScriptError):
    """The configured script is not a regular, non-link file."""


class ScriptTooLargeError(ScriptError):
    """The script exceeds the configured size limit."""


class ScriptEncodingError(ScriptError):
    """The script cannot be decoded using a supported encoding."""


class ScriptConflictError(ScriptError):
    """The file changed since the caller loaded the supplied revision."""

    def __init__(self, current_revision: str):
        super().__init__("脚本文件已被外部修改")
        self.current_revision = current_revision


class ScriptSaveError(ScriptError):
    """The atomic replacement could not be completed."""


@dataclass(frozen=True)
class _FileSnapshot:
    """Identity and bytes digest captured for an optimistic save check."""

    identity: tuple[int, int, int, int, int]
    digest: str


@dataclass(frozen=True)
class ScriptDocument:
    """Decoded script data and metadata used to build the public response."""

    filename: str
    language: str
    content: str
    revision: str
    editable: bool = True
    # These fields are intentionally internal and are not returned by the API.
    path: Optional[Path] = None
    encoding: str = "utf-8"
    bom: bytes = b""
    newline: str = "\n"
    _snapshot: Optional[_FileSnapshot] = None


# A separate lock per resolved path allows unrelated profiles to save in
# parallel while serializing read/compare/write for the same file.
_SCRIPT_LOCKS: Dict[str, threading.RLock] = {}
_SCRIPT_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    # Windows paths are case-insensitive; normalize the lock key so aliases
    # for one configured file cannot be saved concurrently.
    key = os.path.normcase(str(path))
    with _SCRIPT_LOCKS_GUARD:
        lock = _SCRIPT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SCRIPT_LOCKS[key] = lock
        return lock


def _strip_shell_quotes(value: str) -> str:
    value = value.strip()
    # Batch arguments commonly arrive with one or more layers of quotes.
    while len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1].strip()
    return value


def _script_candidate(value: str) -> Optional[str]:
    """Return a script path from one argument, if the argument is a path."""
    candidate = _strip_shell_quotes(value)
    if not candidate:
        return None

    # Also accept forms such as --script=C:\\tools\\start.cmd.  The path
    # still originates in the profile argument; the prefix is not a path.
    if "=" in candidate:
        prefix, suffix = candidate.rsplit("=", 1)
        if prefix.startswith("-"):
            candidate = _strip_shell_quotes(suffix)

    # Quotes/punctuation around a shell token are not part of the path.
    candidate = candidate.strip("\"'\t\r\n,;")
    if Path(candidate).suffix.lower() in SCRIPT_EXTENSIONS:
        return candidate
    return None


# This is intentionally a small shell tokenizer.  It is used only to find
# references in configured arguments, not to execute them.
_SHELL_TOKEN_RE = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s&|;]+")
_SHELL_EXECUTABLES = frozenset({"cmd", "cmd.exe", "command.com"})
_SHELL_SWITCHES = frozenset({"/c", "/k", "/s"})


def _looks_like_command_fragment(value: str) -> bool:
    """Return whether ``value`` is a shell command rather than one path.

    A complete command passed as one profile argument used to also match the
    suffix check in ``_script_candidate``.  For example, the suffix of
    ``cmd.exe /c call run.cmd`` is ``.cmd``, so it was reported as a path in
    addition to the real ``run.cmd`` token.  Recognize the small set of batch
    launch forms here while continuing to allow unquoted paths containing
    spaces when they are supplied as one argument.
    """
    unquoted = _strip_shell_quotes(value)
    tokens = _SHELL_TOKEN_RE.findall(unquoted)
    if len(tokens) < 2:
        return False
    first = _strip_shell_quotes(tokens[0])
    first_name = re.split(r"[\\/]", first)[-1].casefold()
    if first_name in _SHELL_EXECUTABLES:
        return True
    # ``call script.cmd`` and ``/c call script.cmd`` are also commonly passed
    # as one command fragment without an explicit cmd.exe token.
    return first.casefold() in _SHELL_SWITCHES or first.casefold() == "call"


def extract_script_references(args: Iterable[Any]) -> list[str]:
    """Extract .cmd/.bat references from configured profile arguments.

    Supports separate ``cmd.exe /c call script.cmd`` arguments, quoted paths,
    and an argument containing a complete command fragment.  Non-script
    arguments are ignored.  Returned references preserve their configured
    spelling so relative paths can subsequently be resolved against ``cwd``.
    """
    references: list[str] = []
    seen: set[str] = set()
    for raw in args or []:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue

        candidates: list[str] = []
        # Do not treat the suffix of a whole command line as its filename.
        # Quoted/direct paths and --script=... continue to be handled here.
        is_command = _looks_like_command_fragment(text)
        if not is_command:
            direct = _script_candidate(text)
            if direct:
                # An unquoted path may itself contain spaces.  Once the whole
                # argument is recognized as a path, do not split it and report
                # its final word as a second reference.
                candidates.append(direct)

        if is_command or not candidates:
            for token in _SHELL_TOKEN_RE.findall(text):
                candidate = _script_candidate(token)
                if candidate:
                    candidates.append(candidate)

        for candidate in candidates:
            # Duplicate mentions of the same configured path are one logical
            # reference; two different paths remain ambiguous.
            key = candidate.casefold()
            if key not in seen:
                seen.add(key)
                references.append(candidate)
    return references


def _path_from_reference(reference: str, profile: Mapping[str, Any]) -> Path:
    path = Path(reference)
    if not path.is_absolute():
        cwd = profile.get("cwd")
        if cwd:
            path = Path(str(cwd)) / path
    # Use lexical normalization rather than Path.resolve(): resolve() follows
    # the final symlink, which would make it impossible to reject a configured
    # symlink before opening the target file.
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError):
        return path.absolute()


def _same_file(left: Path, right: Path) -> bool:
    """Compare two paths lexically without following links."""
    try:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))
    except (OSError, ValueError):
        return False


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return metadata that detects replacement and in-place file changes."""
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(getattr(info, "st_size", 0)),
        int(getattr(info, "st_mtime_ns", 0)),
        int(getattr(info, "st_ctime_ns", 0)),
    )


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether a path component is a symlink or Windows reparse point."""
    try:
        info = os.lstat(os.fspath(path))
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _absolute_path(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, ValueError):
        return path.absolute()


def _is_within(path: Path, parent: Path) -> bool:
    """Check containment using the host platform's path semantics."""
    try:
        child_value = os.path.normcase(os.path.abspath(os.fspath(path)))
        parent_value = os.path.normcase(os.path.abspath(os.fspath(parent)))
        return os.path.commonpath((child_value, parent_value)) == parent_value
    except (OSError, ValueError):
        # ValueError also covers paths on different Windows drives.
        return False


def _check_raw_relative_components(raw_path: Path, cwd: Path) -> None:
    """Reject links encountered before lexical ``..`` normalization."""
    # ``abspath`` normalizes ``link/../file`` before realpath can inspect it.
    # Walk the original components as well, so a link cannot use ``..`` to
    # escape before the final normalized path is checked.
    stack = [cwd]
    parts = raw_path.parts
    for index, component in enumerate(parts):
        if component in ("", "."):
            continue
        if component == "..":
            if len(stack) > 1:
                stack.pop()
            continue
        current = stack[-1] / component
        # The final file is checked separately by _checked_path so existing
        # callers retain ScriptNotRegularFileError for a linked target.
        if index != len(parts) - 1 and _is_link_or_reparse(current):
            raise ScriptReferenceError("脚本引用不能经过链接目录")
        stack.append(current)


def _ensure_referenced_file(path: Path, profile: Mapping[str, Any]) -> None:
    """Reject a relative reference that escapes cwd or traverses linked dirs."""
    # A profile may use a relative reference, which is intentionally scoped to
    # its configured cwd.  Absolute references remain valid as configured, but
    # must still be ordinary files and never be supplied by the API caller.
    reference = extract_script_references(profile.get("args") or [])
    if len(reference) != 1:
        return
    raw_path = Path(reference[0])
    if raw_path.is_absolute():
        return
    cwd_value = profile.get("cwd")
    if not cwd_value:
        return
    cwd = _absolute_path(Path(str(cwd_value)))
    _check_raw_relative_components(raw_path, cwd)

    # Check both lexical and resolved containment.  The former rejects ..;
    # the latter catches a symlink/junction whose target leaves cwd.
    if not _is_within(path, cwd):
        raise ScriptReferenceError("脚本引用必须位于 Profile 工作目录")
    resolved_cwd = Path(os.path.realpath(os.fspath(cwd)))
    resolved_path = Path(os.path.realpath(os.fspath(path)))
    if not _is_within(resolved_path, resolved_cwd):
        raise ScriptReferenceError("脚本引用必须位于 Profile 工作目录")

    # A resolved target can still be inside cwd while a linked parent was used
    # to reach it.  Reject every existing parent component between cwd and the
    # configured file.  st_file_attributes is needed for Windows junctions;
    # S_ISLNK covers POSIX and Windows symbolic links.
    parent = path.parent
    while not _same_file(parent, cwd):
        if not _is_within(parent, cwd) or _is_link_or_reparse(parent):
            raise ScriptReferenceError("脚本引用不能经过链接目录")
        next_parent = parent.parent
        if _same_file(next_parent, parent):
            break
        parent = next_parent


def resolve_script_path(profile: Mapping[str, Any]) -> Optional[Path]:
    """Resolve the sole .cmd/.bat reference configured by a profile.

    ``None`` means the profile has no script reference.  Multiple distinct
    references are rejected rather than guessed, and all other failures use
    path-free exception messages.
    """
    args = profile.get("args") or []
    if not isinstance(args, (list, tuple)):
        raise ScriptReferenceError("Profile 脚本参数格式无效")
    references = extract_script_references(args)
    if not references:
        return None
    if len(references) != 1:
        raise ScriptReferenceError("Profile 必须只引用一个 .cmd 或 .bat 脚本")
    path = _path_from_reference(references[0], profile)
    _ensure_referenced_file(path, profile)
    return path


def _checked_path(path: Path) -> os.stat_result:
    try:
        # Check the configured lexical target before any OS stat follows links.
        if _is_link_or_reparse(path):
            raise ScriptNotRegularFileError("脚本文件不是普通文件")
        if not path.exists():
            raise ScriptNotFoundError("脚本文件不存在")
        info = path.stat()
    except ScriptError:
        raise
    except (OSError, ValueError) as exc:
        raise ScriptNotFoundError("无法访问脚本文件") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ScriptNotRegularFileError("脚本文件不是普通文件")
    if path.suffix.lower() not in SCRIPT_EXTENSIONS:
        raise ScriptReferenceError("只允许 .cmd 或 .bat 脚本")
    if info.st_size > MAX_SCRIPT_BYTES:
        raise ScriptTooLargeError("脚本文件超过大小限制")
    return info


def _snapshot_for(path: Path, info: os.stat_result, data: bytes) -> _FileSnapshot:
    return _FileSnapshot(_file_identity(info), hashlib.sha256(data).hexdigest())


def _verify_snapshot(path: Path, snapshot: Optional[_FileSnapshot], expected_revision: str) -> None:
    """Verify bytes and identity immediately before os.replace.

    This closes the ordinary external-edit/replacement window under the
    per-path process lock.  It cannot prevent another process from changing
    the file in the tiny gap after this check and before ``os.replace`` on
    platforms without an atomic compare-and-swap rename; callers must still
    treat this as optimistic concurrency protection.
    """
    try:
        _info, data, current = _read_stable_bytes(path)
    except ScriptError:
        raise ScriptConflictError("")
    except OSError as exc:
        raise ScriptConflictError("") from exc
    if current.digest != expected_revision or snapshot is None or current != snapshot:
        raise ScriptConflictError(current.digest)


def _decode_script(data: bytes) -> tuple[str, str, bytes]:
    """Decode bytes while retaining the original BOM/encoding marker."""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8"), "utf-8", b"\xef\xbb\xbf"
        except UnicodeDecodeError as exc:
            raise ScriptEncodingError("脚本不是有效的 UTF-8 文件") from exc
    if data.startswith(b"\xff\xfe\x00\x00"):
        try:
            return data[4:].decode("utf-32-le"), "utf-32-le", b"\xff\xfe\x00\x00"
        except UnicodeDecodeError as exc:
            raise ScriptEncodingError("脚本编码无法读取") from exc
    if data.startswith(b"\x00\x00\xfe\xff"):
        try:
            return data[4:].decode("utf-32-be"), "utf-32-be", b"\x00\x00\xfe\xff"
        except UnicodeDecodeError as exc:
            raise ScriptEncodingError("脚本编码无法读取") from exc
    if data.startswith(b"\xff\xfe"):
        try:
            return data[2:].decode("utf-16-le"), "utf-16-le", b"\xff\xfe"
        except UnicodeDecodeError as exc:
            raise ScriptEncodingError("脚本编码无法读取") from exc
    if data.startswith(b"\xfe\xff"):
        try:
            return data[2:].decode("utf-16-be"), "utf-16-be", b"\xfe\xff"
        except UnicodeDecodeError as exc:
            raise ScriptEncodingError("脚本编码无法读取") from exc

    try:
        return data.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError as exc:
        raise ScriptEncodingError("脚本不是有效的 UTF-8 文件") from exc


def _newline_style(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    if "\n" in content:
        return "\n"
    if "\r" in content:
        return "\r"
    return "\n"


def _normalize_newlines(content: str, newline: str) -> str:
    # Collapse first so mixed client input cannot introduce a different style.
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", newline)


def _encode_script(content: str, encoding: str, bom: bytes, newline: str) -> bytes:
    normalized = _normalize_newlines(content, newline)
    try:
        encoded = normalized.encode(encoding)
    except UnicodeEncodeError as exc:
        raise ScriptEncodingError("脚本内容无法用原编码保存") from exc
    return bom + encoded


def _read_stable_bytes(path: Path) -> tuple[os.stat_result, bytes, _FileSnapshot]:
    """Read bytes and ensure the path identity stayed stable while reading."""
    for _ in range(2):
        info = _checked_path(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ScriptError("读取脚本文件失败") from exc
        # Check after reading too, so a concurrent growth cannot bypass the
        # limit observed by stat().
        if len(data) > MAX_SCRIPT_BYTES:
            raise ScriptTooLargeError("脚本文件超过大小限制")
        try:
            after = _checked_path(path)
        except ScriptError as exc:
            raise ScriptConflictError("") from exc
        if _file_identity(info) == _file_identity(after):
            return after, data, _snapshot_for(path, after, data)
    # The final read is deliberately not used for an optimistic save: it may
    # represent bytes from before or after an external writer's replacement.
    raise ScriptConflictError(hashlib.sha256(data).hexdigest())


def read_script_file(path: Path) -> ScriptDocument:
    """Read one already-resolved controlled script path."""
    path = Path(path)
    with _lock_for(path):
        _info, data, snapshot = _read_stable_bytes(path)
        content, encoding, bom = _decode_script(data)
        return ScriptDocument(
            filename=path.name,
            language="batch",
            content=content,
            revision=snapshot.digest,
            editable=True,
            path=path,
            encoding=encoding,
            bom=bom,
            newline=_newline_style(content),
            _snapshot=snapshot,
        )


def read_script(profile: Mapping[str, Any]) -> Optional[ScriptDocument]:
    """Read the script referenced by a profile, or ``None`` if none exists."""
    path = resolve_script_path(profile)
    if path is None:
        return None
    return read_script_file(path)


def _atomic_write(
    path: Path,
    data: bytes,
    mode: int,
    expected_snapshot: Optional[_FileSnapshot] = None,
    expected_revision: Optional[str] = None,
) -> None:
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as temp:
            temp_name = temp.name
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        try:
            os.chmod(temp_name, stat.S_IMODE(mode))
        except OSError:
            # chmod is best-effort on platforms where it is not meaningful.
            pass
        if expected_snapshot is not None and expected_revision is not None:
            # Keep this check inside the atomic-write helper, after the temp
            # file is durable and immediately before replacement.  There is no
            # portable compare-and-swap form of os.replace: another process can
            # still modify the path after this check and before the rename.
            _verify_snapshot(path, expected_snapshot, expected_revision)
        os.replace(temp_name, path)
        temp_name = None
    except ScriptConflictError:
        raise
    except OSError as exc:
        raise ScriptSaveError("保存脚本文件失败") from exc
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def save_script(
    profile: Mapping[str, Any], content: str, expected_revision: str
) -> ScriptDocument:
    """Atomically save a referenced script after checking its revision."""
    if not isinstance(content, str):
        raise ScriptSaveError("脚本内容格式无效")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise ScriptConflictError("")

    path = resolve_script_path(profile)
    if path is None:
        raise ScriptReferenceError("Profile 未配置可编辑脚本")

    with _lock_for(path):
        current = read_script_file(path)
        if current.revision != expected_revision:
            raise ScriptConflictError(current.revision)
        data = _encode_script(content, current.encoding, current.bom, current.newline)
        if len(data) > MAX_SCRIPT_BYTES:
            raise ScriptTooLargeError("脚本文件超过大小限制")
        mode = _checked_path(path).st_mode
        # The write helper re-checks both digest and metadata immediately
        # before replacement, after the durable temp file has been prepared.
        _atomic_write(path, data, mode, current._snapshot, expected_revision)
        # Re-read after replacement to make the returned revision represent
        # the exact bytes on disk.
        return read_script_file(path)


# Compatibility aliases for callers that prefer load/save terminology.
load_script = read_script
load_script_file = read_script_file
save_script_file = save_script
extract_script_path = resolve_script_path

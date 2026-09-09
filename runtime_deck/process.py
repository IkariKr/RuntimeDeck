"""Safe process management for Windows & Linux.
Strict PID+create_time identity checks, safe targeted termination, orphan cleanup on metadata failure.
"""

import logging
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import psutil

logger = logging.getLogger("ai_dashboard")

IS_WINDOWS = platform.system() == "Windows"

# Target real service executable names and markers per profile/binary pattern
PROFILE_SERVICE_SPECS = {
    "run_rtx3080_20g": {
        "exe_names": ["python.exe", "python"],
        "cmd_markers": ["ComfyUI", "main.py"],
    },
    "huihui-Qwen_27B-Q4K_120K": {
        "exe_names": ["llama-server.exe", "llama-server"],
        "cmd_markers": ["qwen_preset.ini", "8080"],
    },
    "Qwen_27B-GSQ_160K": {
        "exe_names": ["llama-server.exe", "llama-server"],
        "cmd_markers": ["Qwen3.8-27B-GSQ-RCO", "8080"],
    },
}

# Generic fallback specs by profile name substrings or commands if profile not exactly matched
GENERIC_SERVICE_SPECS = [
    {
        "match": lambda profile, cmd_str: "comfy" in profile.lower() or "comfy" in cmd_str.lower(),
        "exe_names": ["python.exe", "python"],
        "cmd_markers": ["main.py"],
    },
    {
        "match": lambda profile, cmd_str: "qwen" in profile.lower() or "llama" in profile.lower() or "llama" in cmd_str.lower(),
        "exe_names": ["llama-server.exe", "llama-server"],
        "cmd_markers": [],
    },
]


def get_service_spec_for_profile(
    profile_name: str,
    launcher_args: Optional[List[str]] = None,
    explicit_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Retrieve expected real-service executable names and command markers for a profile.
    Priority:
      1. explicit_spec (e.g. from config profile.managed_process)
      2. PROFILE_SERVICE_SPECS (known profiles)
      3. GENERIC_SERVICE_SPECS (name / args heuristic)
    """
    if explicit_spec:
        exe_names = explicit_spec.get("exe_names") or []
        cmd_markers = explicit_spec.get("cmd_markers") or []
        if not exe_names and explicit_spec.get("executable"):
            exe_names = [str(explicit_spec["executable"])]
        if exe_names:
            return {"exe_names": list(exe_names), "cmd_markers": list(cmd_markers)}

    if profile_name in PROFILE_SERVICE_SPECS:
        return PROFILE_SERVICE_SPECS[profile_name]

    args_str = " ".join(launcher_args or [])
    for spec in GENERIC_SERVICE_SPECS:
        if spec["match"](profile_name, args_str):
            return {
                "exe_names": spec["exe_names"],
                "cmd_markers": spec["cmd_markers"],
            }

    return {"exe_names": [], "cmd_markers": []}


def get_process_baseline_pids() -> Set[int]:
    """
    Capture current system PIDs snapshot before launching wrapper process.
    Fails closed: raises RuntimeError on failure so callers cannot proceed
    with an empty baseline and accidentally take over existing processes.
    """
    try:
        pids = set(psutil.pids())
        if not pids:
            raise RuntimeError("系统返回空 PID 列表")
        return pids
    except Exception as e:
        logger.error(f"获取系统 PID 基线异常: {e}")
        raise RuntimeError(f"获取系统 PID 基线失败: {e}") from e


def is_matching_real_service(
    proc: psutil.Process,
    expected_exe_names: List[str],
    cmd_markers: List[str],
) -> Tuple[bool, Optional[str]]:
    """
    Check if a process matches real service executable name and all command markers.
    Returns (matches, error_reason_if_failed_metadata).
    """
    try:
        # Check executable name (psutil proc.name())
        name = proc.name().lower()
        matched_name = False
        for expected in expected_exe_names:
            if name == expected.lower():
                matched_name = True
                break
        if not matched_name:
            return False, None

        # Check command markers
        if cmd_markers:
            try:
                cmdline = proc.cmdline()
                cmdline_str = " ".join(cmdline).lower()
                for marker in cmd_markers:
                    if marker.lower() not in cmdline_str:
                        return False, None
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                return False, "AccessDenied or NoSuchProcess reading cmdline"

        return True, None
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False, None
    except psutil.AccessDenied:
        return False, "AccessDenied"
    except Exception as e:
        return False, str(e)


def discover_real_service(
    baseline_pids: Set[int],
    launcher_pid: int,
    expected_exe_names: List[str],
    cmd_markers: List[str],
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> Tuple[str, Optional[psutil.Process], Optional[str]]:
    """
    Safely discover the real backend service process spawned under or alongside launcher.
    Requirements:
      1. PID must NOT be in baseline_pids (strictly prevents taking over pre-existing processes).
      2. Executable name must match expected_exe_names (case-insensitive).
      3. All command markers must appear in cmdline.
      4. Unique candidate only: if 0 candidate -> "none", if >1 candidates -> "ambiguous".
      5. Any unresolvable inspection error -> "error".

    Returns:
        (status, proc, detail)
        status: "found" | "none" | "ambiguous" | "error"
    """
    if not expected_exe_names:
        return "error", None, "未指定目标可执行文件名称匹配规则"

    start_time = time.time()
    while time.time() - start_time < timeout:
        candidates: List[psutil.Process] = []
        errors: List[str] = []

        # We first check descendants of launcher_pid if launcher is still running
        launcher_children: Set[int] = set()
        try:
            l_proc = psutil.Process(launcher_pid)
            for c in l_proc.children(recursive=True):
                launcher_children.add(c.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        # Scan new processes that were not in baseline_pids
        try:
            current_pids = set(psutil.pids())
        except Exception as e:
            return "error", None, f"枚举系统进程失败: {e}"

        new_pids = [p for p in current_pids if p not in baseline_pids and p != launcher_pid]

        # Prioritize launcher descendants if available, but search all new_pids to avoid missing detached processes
        for p in new_pids:
            try:
                proc = psutil.Process(p)
                matches, err = is_matching_real_service(proc, expected_exe_names, cmd_markers)
                if err:
                    # Note metadata failure
                    errors.append(f"PID {p}: {err}")
                elif matches:
                    candidates.append(proc)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                errors.append(f"PID {p}: AccessDenied")
            except Exception as e:
                errors.append(f"PID {p}: {e}")

        if errors:
            # Any uncertainty about a process that could be the target is fail-closed.
            return "error", None, f"进程元数据探测失败: {'; '.join(errors[:3])}"
        if len(candidates) == 1:
            return "found", candidates[0], None
        if len(candidates) > 1:
            # Ambiguous: more than 1 new candidate matched!
            cand_pids = [c.pid for c in candidates]
            return "ambiguous", None, f"发现多个符合条件的候选进程: {cand_pids}"

        time.sleep(poll_interval)


    if errors:
        return "error", None, f"进程元数据探测失败: {'; '.join(errors[:3])}"
    return "none", None, f"超时 {timeout}s 未发现符合条件的真实服务进程"


def is_legacy_wrapper_state(inst_record: Dict[str, Any]) -> bool:
    """
    Determine whether an instance state record is a legacy wrapper cmd.exe record.
    Legacy wrapper records have executable == 'cmd.exe' and no launcher_pid recorded,
    or executable ends with 'cmd.exe' without launcher fields.
    """
    exe = str(inst_record.get("executable", "")).lower()
    has_launcher = bool(inst_record.get("launcher_pid"))
    if not has_launcher and (exe == "cmd.exe" or exe.endswith("\\cmd.exe") or exe.endswith("/cmd.exe")):
        return True
    return False


def check_process_identity(pid: int, expected_create_time: float) -> Tuple[str, Optional[psutil.Process]]:
    """
    Check managed process identity with PID + create_time.
    Returns:
        ("match", psutil.Process)    - Identity matches within 0.5s tolerance
        ("mismatch", psutil.Process) - Process exists but create_time does not match (PID reuse)
        ("not_found", None)         - Process does not exist (already exited)
        ("error", None)             - Access denied or unexpected error during inspection
    """
    try:
        proc = psutil.Process(pid)
        actual_create_time = proc.create_time()
        # Allow 0.5s tolerance for platform timestamp precision/drift
        if abs(actual_create_time - expected_create_time) < 0.5:
            return "match", proc
        logger.warning(
            f"PID {pid} 已被其他进程复用: 记录时间 {expected_create_time}, 当前进程时间 {actual_create_time}"
        )
        return "mismatch", proc
    except psutil.NoSuchProcess:
        return "not_found", None
    except Exception as e:
        logger.error(f"检查进程 {pid} 身份异常: {e}")
        return "error", None


def kill_process_tree(pid: int) -> subprocess.CompletedProcess:
    """
    Safely kill process tree by PID only.
    On Windows: uses `taskkill /PID <pid> /T /F` (strictly no image name /IM killing).
    On Linux/POSIX: terminates child processes recursively via psutil or SIGKILL.
    """
    logger.info(f"Targeted process tree termination for PID: {pid}")
    if IS_WINDOWS:
        return subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True
        )
    else:
        # Cross-platform / Linux test fallback
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
            return subprocess.CompletedProcess(args=["kill", str(pid)], returncode=0, stdout="", stderr="")
        except psutil.NoSuchProcess:
            return subprocess.CompletedProcess(args=["kill", str(pid)], returncode=0, stdout="", stderr="")
        except Exception as e:
            return subprocess.CompletedProcess(args=["kill", str(pid)], returncode=1, stdout="", stderr=str(e))


def wait_process_exit(proc: Optional[psutil.Process], max_attempts: int = 10, interval: float = 0.5) -> bool:
    """
    Verify process has actually exited after kill signal.
    Checks up to max_attempts * interval seconds (default 5.0s).
    """
    if proc is None:
        return True
    for _ in range(max_attempts):
        try:
            if not proc.is_running():
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(interval)
    return False

"""GPU metrics and process collection via nvidia-smi."""

import locale
import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger("ai_dashboard")


def get_gpu_metrics(command_override: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Collect GPU hardware metrics using nvidia-smi.
    Returns:
        (gpus, errors)
        gpus: list of dicts with index, name, uuid, used_mb, total_mb, util_percent
        errors: list of error message strings (empty if successful)
    """
    errors: List[str] = []
    gpus: List[Dict[str, Any]] = []

    cmd = command_override or [
        "nvidia-smi",
        "--query-gpu=index,name,uuid,memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits"
    ]

    try:
        res = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip().splitlines()
        for line in res:
            line_str = line.strip()
            if not line_str:
                continue
            parts = [x.strip() for x in line_str.split(",")]
            if len(parts) < 6:
                continue
            idx, name, uuid, used, total, util = parts[:6]
            gpus.append({
                "index": int(idx),
                "name": name,
                "uuid": uuid,
                "used_mb": int(used),
                "total_mb": int(total),
                "util_percent": int(util)
            })
    except FileNotFoundError:
        err_msg = "采集 nvidia-smi 失败: 未找到 nvidia-smi 命令，请确认 NVIDIA 驱动已正确安装并加入 PATH"
        logger.warning(err_msg)
        errors.append(err_msg)
    except subprocess.CalledProcessError as e:
        err_msg = f"采集 nvidia-smi 失败 (退出码 {e.returncode}): {e.output.strip() if e.output else str(e)}"
        logger.warning(err_msg)
        errors.append(err_msg)
    except Exception as e:
        err_msg = f"采集 nvidia-smi 失败: {e}"
        logger.warning(err_msg)
        errors.append(err_msg)

    return gpus, errors


def _parse_optional_int(value: str) -> Optional[int]:
    """Parse nvidia-smi numeric fields while tolerating unavailable values."""
    normalized = value.strip().lower()
    if not normalized or normalized in {"n/a", "[n/a]", "not supported", "[not supported]", "-"}:
        return None
    match = re.search(r"-?\d+", normalized)
    return int(match.group(0)) if match else None


def _process_metadata(pid: int, fallback_name: str) -> Dict[str, Any]:
    """Read non-sensitive process metadata, degrading when access is denied."""
    metadata: Dict[str, Any] = {
        "name": fallback_name or "未知进程",
        "executable": fallback_name or "未知进程",
        "create_time": None,
    }
    try:
        process = psutil.Process(pid)
        try:
            metadata["name"] = process.name() or metadata["name"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
        try:
            executable = process.exe()
            metadata["executable"] = os.path.basename(executable) or metadata["executable"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
        try:
            metadata["create_time"] = float(process.create_time())
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
    except Exception as exc:
        logger.debug("读取 GPU 进程 %s 元数据失败: %s", pid, exc)
    return metadata


def get_gpu_processes(
    gpu_index: int,
    gpu_uuid: Optional[str] = None,
    command_override: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return the current compute processes associated with one physical GPU."""
    errors: List[str] = []
    processes: List[Dict[str, Any]] = []
    cmd = command_override or [
        "nvidia-smi",
        "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]

    try:
        raw_output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        output = (
            raw_output.decode(locale.getpreferredencoding(False), errors="replace")
            if isinstance(raw_output, bytes)
            else str(raw_output)
        )
        for line in output.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            parts = [item.strip() for item in line_str.split(",", 3)]
            if len(parts) < 4:
                continue
            row_uuid, pid_value, process_name, memory_value = parts
            if gpu_uuid and row_uuid.lower() != gpu_uuid.lower():
                continue
            pid = _parse_optional_int(pid_value)
            if pid is None or pid <= 0:
                continue
            metadata = _process_metadata(pid, process_name)
            processes.append({
                "gpu_index": gpu_index,
                "gpu_uuid": row_uuid,
                "pid": pid,
                "name": metadata["name"],
                "executable": metadata["executable"],
                "used_mb": _parse_optional_int(memory_value),
                "create_time": metadata["create_time"],
                "can_kill": pid > 4 and metadata["create_time"] is not None,
                "kill_reason": (
                    "系统进程不可结束" if pid <= 4
                    else "无法确认进程身份" if metadata["create_time"] is None
                    else None
                ),
            })
    except FileNotFoundError:
        errors.append("查询 GPU 进程失败: 未找到 nvidia-smi 命令")
    except subprocess.CalledProcessError as exc:
        detail = exc.output.strip() if exc.output else str(exc)
        errors.append(f"查询 GPU 进程失败 (退出码 {exc.returncode}): {detail}")
    except Exception as exc:
        errors.append(f"查询 GPU 进程失败: {exc}")

    processes.sort(key=lambda item: (-(item["used_mb"] if item["used_mb"] is not None else -1), item["pid"]))
    return processes, errors

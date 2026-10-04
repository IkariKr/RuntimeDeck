"""Port resolution, conflict detection, and listener inspection for RuntimeDeck."""

import logging
import re
import socket
import time
from typing import Any, Dict, Optional

import psutil

logger = logging.getLogger("ai_dashboard")

SYSTEM_PIDS = {0, 4}


def resolve_profile_port(profile: Dict[str, Any]) -> Optional[int]:
    """
    Resolve target TCP port for a given profile.
    Priority:
      1. profile.port (explicit top-level)
      2. profile.managed_process.port (explicit managed spec)
      3. launcher args (--port=X, --port X, -p X)
      4. managed_process.cmd_markers (--port X or numeric token)
      5. Runtime kind heuristics (llama.cpp -> 8080, ComfyUI -> 8188)
    """
    if not isinstance(profile, dict):
        return None

    # 1. Explicit top-level port
    explicit_port = profile.get("port")
    if explicit_port is not None:
        try:
            p = int(explicit_port)
            if 1 <= p <= 65535:
                return p
        except (ValueError, TypeError, OverflowError):
            pass

    # 2. Explicit managed_process.port
    managed = profile.get("managed_process")
    if isinstance(managed, dict):
        m_port = managed.get("port")
        if m_port is not None:
            try:
                p = int(m_port)
                if 1 <= p <= 65535:
                    return p
            except (ValueError, TypeError, OverflowError):
                pass

    # 3. Scan launcher args
    args = profile.get("args") or []
    if isinstance(args, list):
        for idx, arg in enumerate(args):
            arg_str = str(arg)
            m = re.match(r"^--(?:port|p)=(\d+)$", arg_str, re.IGNORECASE)
            if m:
                return int(m.group(1))
            if arg_str.lower() in ("--port", "-p") and idx + 1 < len(args):
                next_val = str(args[idx + 1])
                if next_val.isdigit():
                    return int(next_val)

    # 4. Scan managed_process markers
    if isinstance(managed, dict):
        markers = managed.get("cmd_markers") or managed.get("markers") or []
        if isinstance(markers, list):
            for idx, marker in enumerate(markers):
                m_str = str(marker)
                if m_str.lower() in ("--port", "-p") and idx + 1 < len(markers):
                    next_val = str(markers[idx + 1])
                    if next_val.isdigit() and 1000 <= int(next_val) <= 65535:
                        return int(next_val)
                elif m_str.isdigit() and 1000 <= int(m_str) <= 65535:
                    return int(m_str)

    # 5. Fallback heuristics based on name/command
    name_str = f"{profile.get('name', '')} {profile.get('executable', '')} {' '.join(str(a) for a in args)}".lower()
    if "comfy" in name_str:
        return 8188
    if "llama" in name_str or "qwen" in name_str:
        return 8080

    return None


def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """Check whether a TCP port is currently accepting connections."""
    if not (1 <= port <= 65535):
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        res = sock.connect_ex((host, port))
        return res == 0
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def find_port_listener(port: int) -> Optional[Dict[str, Any]]:
    """
    Find which process is currently listening on the specified TCP port.
    Returns None if not listening, or a dict with:
      {
        "port": port,
        "pid": Optional[int],
        "name": str,
        "create_time": Optional[float],
        "exe": str,
        "is_system": bool
      }
    """
    if not (1 <= port <= 65535):
        return None

    target_pid: Optional[int] = None
    try:
        connections = psutil.net_connections(kind="inet")
        for c in connections:
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                target_pid = c.pid
                break
    except Exception as e:
        logger.warning(f"获取系统网络连接表失败: {e}")

    # If psutil couldn't locate PID from connection table, test raw socket connectivity
    if target_pid is None:
        if is_port_listening(port):
            return {
                "port": port,
                "pid": None,
                "name": "未知进程",
                "create_time": None,
                "exe": "",
                "is_system": False,
            }
        return None

    if target_pid in SYSTEM_PIDS:
        return {
            "port": port,
            "pid": target_pid,
            "name": "System",
            "create_time": 0.0,
            "exe": "",
            "is_system": True,
        }

    try:
        proc = psutil.Process(target_pid)
        raw_name = proc.name()
        proc_name = str(raw_name) if not isinstance(raw_name, str) else raw_name
        raw_ctime = proc.create_time()
        try:
            proc_ctime = float(raw_ctime) if raw_ctime is not None else None
        except (ValueError, TypeError):
            proc_ctime = None
        try:
            raw_exe = proc.exe()
            proc_exe = str(raw_exe) if not isinstance(raw_exe, str) else raw_exe
        except Exception:
            proc_exe = ""

        # Protected system process heuristic
        is_sys = target_pid <= 4 or proc_name.lower() in ("system", "system idle process", "csrss.exe", "svchost.exe", "services.exe", "smss.exe")
        return {
            "port": port,
            "pid": int(target_pid),
            "name": proc_name,
            "create_time": proc_ctime,
            "exe": proc_exe,
            "is_system": is_sys,
        }
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None
    except psutil.AccessDenied:
        return {
            "port": port,
            "pid": target_pid,
            "name": "受保护/无权限进程",
            "create_time": None,
            "exe": "",
            "is_system": True,
        }
    except Exception as e:
        return {
            "port": port,
            "pid": target_pid,
            "name": "未知进程",
            "create_time": None,
            "exe": "",
            "is_system": False,
            "error": str(e),
        }


def wait_port_free(port: int, host: str = "127.0.0.1", timeout: float = 3.0, interval: float = 0.2) -> bool:
    """Wait until a TCP port is released and no longer listening."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_port_listening(port, host=host, timeout=0.2):
            return True
        time.sleep(interval)
    return not is_port_listening(port, host=host, timeout=0.2)

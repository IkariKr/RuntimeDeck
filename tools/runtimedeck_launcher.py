"""RuntimeDeck 后台启动器（供 Windows 计划任务调用）。

职责：
  1. 切换到项目根目录，保证 runtimes.json / static / logs 相对路径正确。
  2. 轮转日志，避免长年运行把日志文件撑爆。
  3. 用 CREATE_NO_WINDOW 拉起 server.py，把 stdout/stderr 落盘 —— 这样由
     pythonw.exe 调起时既不弹黑窗口，也不会丢掉 uvicorn 输出。
  4. 若 127.0.0.1:8000 已被占用（例如手工又启动了一次），直接以 0 退出，
     避免计划任务在 RestartOnFailure 下反复重试刷屏。

用法（计划任务里直接跑 pythonw.exe）：
    pythonw.exe "D:\\App\\01_Ai\\CodeX\\RuntimeDeck\\tools\\runtimedeck_launcher.py"
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER_SCRIPT = PROJECT_ROOT / "server.py"
LOG_DIR = PROJECT_ROOT / "logs"

HOST = "127.0.0.1"
PORT = 8000

STDOUT_LOG = LOG_DIR / "launcher-server.out.log"
STDERR_LOG = LOG_DIR / "launcher-server.err.log"
ROTATE_BYTES = 10 * 1024 * 1024  # 单文件超过 10MB 就轮转一份 .prev


def _log(msg: str) -> None:
    """启动器自己的存活记录，写进独立的 launcher.log，方便事后排查。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_DIR / "launcher.log", "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [INFO] {msg}\n")
    except OSError:
        pass


def _rotate(path: pathlib.Path) -> None:
    try:
        if path.exists() and path.stat().st_size > ROTATE_BYTES:
            prev = path.with_suffix(path.suffix + ".prev")
            prev.unlink(missing_ok=True)
            path.replace(prev)
    except OSError:
        pass


def _port_in_use(host: str, port: int) -> bool:
    """探测端口是否已有服务在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def _is_runtimedeck(host: str, port: int) -> bool:
    """确认占用端口的是 RuntimeDeck 本尊，而不是别的程序抢了 8000。"""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/config", timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and "profiles" in payload and "gpus" in payload


def main() -> int:
    os.chdir(PROJECT_ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not SERVER_SCRIPT.exists():
        _log(f"找不到服务入口: {SERVER_SCRIPT}")
        return 2

    if _port_in_use(HOST, PORT):
        if _is_runtimedeck(HOST, PORT):
            _log(f"{HOST}:{PORT} 已由 RuntimeDeck 占用，跳过本次启动（不重复拉起）。")
            return 0
        _log(f"{HOST}:{PORT} 被其它程序占用且不是 RuntimeDeck，本次启动中止。")
        return 3

    interpreter = pathlib.Path(sys.executable)
    if interpreter.name.lower() == "pythonw.exe":
        candidate = interpreter.with_name("python.exe")
        if candidate.exists():
            interpreter = candidate

    _log(f"启动 server.py，解释器={interpreter}")

    for target in (STDOUT_LOG, STDERR_LOG):
        _rotate(target)

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW  # 无控制台窗口

    with open(STDOUT_LOG, "ab", buffering=0) as out, open(STDERR_LOG, "ab", buffering=0) as err:
        process = subprocess.Popen(
            [str(interpreter), str(SERVER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _log(f"server.py 已拉起，PID={process.pid}")
        code = process.wait()

    _log(f"server.py 已退出，退出码={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())

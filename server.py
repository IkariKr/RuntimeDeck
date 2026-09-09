import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from runtime_deck.config import load_raw_config
from runtime_deck.gpu import get_gpu_metrics
from runtime_deck.models import (
    InstanceView,
    RuntimeKind,
    StartRequest,
    StartResponse,
    StatusResponse,
    StopResponse,
    V1ProfileStatusResponse,
    V1ProfileSummary,
    V1ProfilesResponse,
    V1StartRequest,
)
from runtime_deck.process import (
    IS_WINDOWS,
    check_process_identity,
    discover_real_service,
    get_process_baseline_pids,
    get_service_spec_for_profile,
    is_legacy_wrapper_state,
    kill_process_tree,
    wait_process_exit,
)
from runtime_deck.security import redact_command_line, safe_error_detail
from runtime_deck.state import StateManager, resolve_default_state_file

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ai_dashboard")

app = FastAPI(title="AI Runtime Dashboard")


@app.middleware("http")
async def add_anti_indexing_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    content_type = response.headers.get("content-type", "")
    if path == "/" or path == "/robots.txt" or path.startswith("/api") or "text/html" in content_type:
        response.headers["X-Robots-Tag"] = "noindex, noarchive, nosnippet"
        response.headers["Cache-Control"] = "no-store"
    return response

ROOT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = ROOT_DIR / "runtimes.json"
LEGACY_STATE_FILE = ROOT_DIR / "state.json"
STATE_FILE = resolve_default_state_file(fallback_project_dir=ROOT_DIR)
STATIC_DIR = ROOT_DIR / "static"
INDEX_FILE = ROOT_DIR / "index.html"

# Thread-safe atomic state manager with legacy fallback
state_manager = StateManager(STATE_FILE, legacy_fallback_file=LEGACY_STATE_FILE)

# WebUI static assets if static dir exists
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_current_config() -> Dict[str, Any]:
    return load_raw_config(CONFIG_FILE)


def infer_runtime_kind(profile_cfg: Dict[str, Any]) -> RuntimeKind:
    """Infer runtime_kind ('llama_cpp', 'comfyui', or 'unknown') from profile config."""
    mp = profile_cfg.get("managed_process") or {}
    role = str(mp.get("role") or mp.get("process_role") or "").lower()
    exe = str(mp.get("executable") or profile_cfg.get("executable") or "").lower()
    exe_names = [str(n).lower() for n in mp.get("exe_names") or []]
    cmd_markers = [str(m).lower() for m in mp.get("cmd_markers") or []]
    args_str = " ".join([str(a) for a in profile_cfg.get("args") or []]).lower()

    if "llama" in role or "llama-server" in exe or any("llama" in n for n in exe_names):
        return "llama_cpp"
    if "comfy" in role or "comfy" in exe or "comfy" in args_str or any("comfy" in m for m in cmd_markers):
        return "comfyui"

    # Secondary check on exe / args
    if "llama" in args_str:
        return "llama_cpp"
    if "python" in exe and ("main.py" in args_str or any("main.py" in m for m in cmd_markers)):
        return "comfyui"

    return "unknown"


def extract_endpoint_from_profile(profile_cfg: Dict[str, Any]) -> Optional[str]:
    """
    Extract host/port from profile configuration arguments.
    Always normalizes host to 127.0.0.1 for the external API contract.
    """
    args = profile_cfg.get("args") or []
    port: Optional[str] = None

    # Common flags: --port 8080, --port=8080, -p 8080, --listen 8188
    for i, arg in enumerate(args):
        arg_str = str(arg)
        if arg_str.startswith("--port="):
            port = arg_str.split("=", 1)[1]
        elif arg_str in ("--port", "-p") and i + 1 < len(args):
            port = str(args[i + 1])

    # If not explicitly in args, check managed_process cmd_markers or known defaults
    mp = profile_cfg.get("managed_process") or {}
    cmd_markers = mp.get("cmd_markers") or []
    if not port:
        for m in cmd_markers:
            if str(m).isdigit() and len(str(m)) in (4, 5):
                port = str(m)
                break

    kind = infer_runtime_kind(profile_cfg)
    if not port:
        if kind == "llama_cpp":
            port = "8080"
        elif kind == "comfyui":
            port = "8188"

    if port:
        return f"http://127.0.0.1:{port}"
    return None


def get_profile_live_status(profile_id: str) -> V1ProfileStatusResponse:
    """Internal helper to get sanitized stable status for a single profile."""
    cfg = get_current_config()
    profiles = cfg.get("profiles", {})
    if profile_id not in profiles:
        raise HTTPException(status_code=404, detail=safe_error_detail(f"未找到 Profile: {profile_id}"))

    p = profiles[profile_id]
    kind = infer_runtime_kind(p)
    endpoint = extract_endpoint_from_profile(p)
    default_gpu = p.get("default_gpu")

    state = state_manager.load_state()
    instances = state.get("instances", {})

    matching_inst_key = None
    matching_inst = None
    for k, inst in instances.items():
        if inst.get("profile") == profile_id:
            matching_inst_key = k
            matching_inst = inst
            break

    if not matching_inst:
        return V1ProfileStatusResponse(
            profile_id=profile_id,
            runtime_kind=kind,
            state="stopped",
            endpoint=endpoint,
            health="unknown",
            gpu=default_gpu,
        )

    # Legacy wrapper records are not treated as running
    if is_legacy_wrapper_state(matching_inst):
        return V1ProfileStatusResponse(
            profile_id=profile_id,
            runtime_kind=kind,
            state="stopped",
            endpoint=endpoint,
            health="unknown",
            gpu=matching_inst.get("gpu") or default_gpu,
        )

    identity_status, _ = check_process_identity(
        matching_inst["pid"], matching_inst.get("create_time", 0)
    )

    if identity_status == "match":
        return V1ProfileStatusResponse(
            profile_id=profile_id,
            runtime_kind=kind,
            state="running",
            endpoint=endpoint,
            health="unknown",
            gpu=matching_inst.get("gpu") or default_gpu,
        )
    elif identity_status in ("mismatch", "not_found"):
        return V1ProfileStatusResponse(
            profile_id=profile_id,
            runtime_kind=kind,
            state="stopped",
            endpoint=endpoint,
            health="unknown",
            gpu=default_gpu,
        )
    else:  # "error"
        return V1ProfileStatusResponse(
            profile_id=profile_id,
            runtime_kind=kind,
            state="error",
            endpoint=endpoint,
            health="error",
            gpu=default_gpu,
        )


# Robots.txt to prevent any crawler indexing
@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


# Root serves the complete local dashboard, with a small API-only fallback.
@app.get("/", response_class=HTMLResponse)
def index():
    dashboard_index = INDEX_FILE if INDEX_FILE.is_file() else STATIC_DIR / "index.html"
    if dashboard_index.is_file():
        return FileResponse(dashboard_index)
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html>
<head><title>AI Runtime Dashboard</title></head>
<body style="font-family: sans-serif; background: #0b0f19; color: #f3f4f6; padding: 2rem;">
  <h2>AI Runtime Dashboard API</h2>
  <p>Status: operational</p>
  <p>Endpoints: <a href="/api/status" style="color: #60a5fa;">/api/status</a>, <a href="/api/config" style="color: #60a5fa;">/api/config</a></p>
</body>
</html>""",
        status_code=200,
    )


# 1. Hardware and running instance query (strictly read-only, no persistence write)
@app.get("/api/status", response_model=StatusResponse)
def get_status():
    state = state_manager.load_state()
    gpus, errors = get_gpu_metrics()

    safe_errors = [safe_error_detail(e) for e in errors]

    view_instances: Dict[str, InstanceView] = {}
    for inst_id, inst in state.get("instances", {}).items():
        # The dashboard lists only verifiably alive real service processes.
        # Legacy wrapper-only and stale records remain hidden until explicitly cleaned.
        if is_legacy_wrapper_state(inst):
            continue

        identity_status, _ = check_process_identity(inst["pid"], inst.get("create_time", 0))
        if identity_status != "match":
            continue

        # Explicit construction - no raw or redacted command line in the public API.
        view_instances[inst_id] = InstanceView(
            profile=inst["profile"],
            name=inst.get("name", inst["profile"]),
            pid=inst["pid"],
            create_time=inst.get("create_time", 0),
            executable=inst.get("executable", ""),
            gpu=inst.get("gpu", ""),
            gpu_name=inst.get("gpu_name", ""),
            started_at=inst.get("started_at", ""),
            process_role=inst.get("process_role"),
            launcher_pid=inst.get("launcher_pid"),
            launcher_create_time=inst.get("launcher_create_time"),
            launcher_executable=inst.get("launcher_executable"),
            is_alive=True,
            identity_status="match",
            is_legacy=False,
        )

    return {
        "gpus": gpus,
        "instances": view_instances,
        "errors": safe_errors,
    }


# 2. Query configuration presets (read-only)
@app.get("/api/config")
def get_config():
    return get_current_config()


# Core internal start logic shared by legacy and v1 APIs
def _internal_start_profile(profile_name: str, target_gpu: Optional[str] = None) -> Tuple[str, int]:
    cfg = get_current_config()
    profiles = cfg.get("profiles", {})
    if profile_name not in profiles:
        raise HTTPException(status_code=400, detail="未知的 Profile 预设")

    p = profiles[profile_name]
    target_gpu_key = target_gpu or p.get("default_gpu", "3080")
    gpus_map = cfg.get("gpus", {})
    gpu_info = gpus_map.get(target_gpu_key)
    if not gpu_info:
        raise HTTPException(status_code=400, detail=safe_error_detail(f"未知的 GPU 设备: {target_gpu_key}"))

    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = gpu_info.get("uuid", "")

    cmd = [p["executable"]] + p.get("args", [])
    cwd = p.get("cwd")

    # Capture system PID baseline right before launch (fail closed if capture fails)
    try:
        baseline_pids = get_process_baseline_pids()
    except Exception as e:
        logger.error(f"启动前获取 PID 基线失败: {safe_error_detail(str(e))}")
        raise HTTPException(status_code=500, detail=safe_error_detail(f"启动前获取系统进程基线失败: {e}"))

    proc = None
    try:
        kwargs: Dict[str, Any] = {
            "cwd": cwd,
            "env": env,
        }
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE

        proc = subprocess.Popen(cmd, **kwargs)

        # Retrieve launcher process identity metadata
        p_proc = psutil.Process(proc.pid)
        launcher_create_time = p_proc.create_time()
        launcher_exe = p["executable"]
    except Exception as e:
        # Popen succeeded but identity metadata capture failed: clean up immediately to prevent untracked orphans
        if proc is not None:
            try:
                kill_process_tree(proc.pid)
            except Exception as kill_err:
                logger.error(f"清理孤儿进程 PID {proc.pid} 失败: {safe_error_detail(str(kill_err))}")
        logger.error(f"启动实例 {profile_name} 失败: {safe_error_detail(str(e))}")
        raise HTTPException(status_code=500, detail=safe_error_detail(f"拉起子进程失败: {e}"))

    launcher_exe_lower = os.path.basename(launcher_exe).lower()
    is_wrapper = launcher_exe_lower in ["cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe"]

    spec = get_service_spec_for_profile(
        profile_name,
        launcher_args=p.get("args", []),
        explicit_spec=p.get("managed_process"),
    )
    target_exe_names = spec["exe_names"]
    target_markers = spec["cmd_markers"]

    real_pid: int
    real_create_time: float
    real_exe_path: str
    redacted_cmdline: str

    if is_wrapper:
        disc_status, disc_proc, disc_err = discover_real_service(
            baseline_pids=baseline_pids,
            launcher_pid=proc.pid,
            expected_exe_names=target_exe_names,
            cmd_markers=target_markers,
            timeout=10.0,
            poll_interval=0.5,
        )

        if disc_status != "found" or disc_proc is None:
            logger.error(f"未能唯一定位真实服务进程 ({disc_status}): {safe_error_detail(disc_err)}")
            try:
                kill_process_tree(proc.pid)
            except Exception as kill_err:
                logger.error(f"清理启动包装进程树 PID {proc.pid} 失败: {safe_error_detail(str(kill_err))}")
            raise HTTPException(
                status_code=500,
                detail=safe_error_detail(f"启动服务失败：未能唯一定位真实服务进程 ({disc_status}): {disc_err or '无详细信息'}"),
            )

        try:
            real_pid = disc_proc.pid
            real_create_time = disc_proc.create_time()
            try:
                real_exe_path = disc_proc.exe()
            except Exception:
                real_exe_path = disc_proc.name()
            try:
                raw_command_line = " ".join(disc_proc.cmdline())
                redacted_cmdline = redact_command_line(raw_command_line)
            except Exception as cmd_err:
                raise RuntimeError(f"无法读取真实服务命令行: {cmd_err}") from cmd_err
        except Exception as meta_err:
            logger.error(f"获取真实服务进程元数据异常: {safe_error_detail(str(meta_err))}")
            try:
                kill_process_tree(proc.pid)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=safe_error_detail(f"获取真实服务进程元数据失败: {meta_err}"))
    else:
        real_pid = proc.pid
        real_create_time = launcher_create_time
        real_exe_path = launcher_exe
        try:
            raw_command_line = " ".join(proc.args if isinstance(proc.args, list) else [str(proc.args)])
            redacted_cmdline = redact_command_line(raw_command_line)
        except Exception:
            redacted_cmdline = ""

    inst_id = f"inst_{real_pid}"
    with state_manager.lock:
        state = state_manager.load_state()
        # State record NEVER saves raw command_line, only redacted
        state.setdefault("instances", {})[inst_id] = {
            "profile": profile_name,
            "name": p.get("name", profile_name),
            "pid": real_pid,
            "create_time": real_create_time,
            "executable": real_exe_path,
            "gpu": target_gpu_key,
            "gpu_name": gpu_info.get("name", target_gpu_key),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "process_role": p.get("managed_process", {}).get("process_role") or p.get("managed_process", {}).get("role"),
            "command_line_redacted": redacted_cmdline,
            "launcher_pid": proc.pid if is_wrapper else None,
            "launcher_create_time": launcher_create_time if is_wrapper else None,
            "launcher_executable": launcher_exe if is_wrapper else None,
        }
        state_manager.save_state_atomic(state)

    logger.info(f"已显式拉起实例: {inst_id} (PID: {real_pid}) 绑定显卡 {target_gpu_key}")
    return inst_id, real_pid


# 3. Explicitly launch an instance (Legacy route)
@app.post("/api/instances/start", response_model=StartResponse)
def start_instance(req: StartRequest):
    inst_id, real_pid = _internal_start_profile(req.profile, req.gpu)
    return StartResponse(status="success", instance_id=inst_id, pid=real_pid)


# Core internal stop logic shared by legacy and v1 APIs
def _internal_stop_instance_by_id(instance_id: str) -> StopResponse:
    with state_manager.lock:
        state = state_manager.load_state()
        instances = state.get("instances", {})
        if instance_id not in instances:
            raise HTTPException(status_code=404, detail="未找到该实例记录")

        inst = instances[instance_id]

        # Check if legacy wrapper-only record
        if is_legacy_wrapper_state(inst):
            logger.warning(
                f"拒绝将旧版本包装记录作为真实服务终止: 实例 {instance_id}, PID {inst['pid']} 为 cmd.exe"
            )
            del instances[instance_id]
            state_manager.save_state_atomic(state)
            return StopResponse(
                status="stale_cleaned",
                instance_id=instance_id,
                exited=True,
                message="旧包装记录已清理；未终止未知的后台服务，请重新启动实例以建立真实服务 PID",
            )

        pid = inst["pid"]
        create_time = inst.get("create_time", 0)

        # 1. Check actual target process identity
        identity_status, proc = check_process_identity(pid, create_time)

        if identity_status == "error":
            raise HTTPException(
                status_code=500,
                detail=safe_error_detail(f"无法确认实例 {instance_id} 的进程身份，已保留状态记录"),
            )

        if identity_status == "mismatch":
            logger.warning(
                f"防误杀触发：实例 {instance_id} 的 PID {pid} 已被其他进程复用，仅清理过期状态"
            )
            del instances[instance_id]
            state_manager.save_state_atomic(state)
            return StopResponse(
                status="stale_cleaned",
                instance_id=instance_id,
                exited=True,
                message="检测到 PID 已被其他程序复用，为防误杀已跳过终止操作，并清理了过期记录",
            )

        if identity_status == "not_found":
            l_pid = inst.get("launcher_pid")
            l_ctime = inst.get("launcher_create_time")
            if l_pid and l_ctime:
                l_status, l_proc = check_process_identity(l_pid, l_ctime)
                if l_status == "error":
                    raise HTTPException(status_code=500, detail=safe_error_detail(f"无法确认启动器 PID {l_pid} 身份，状态记录已保留"))
                if l_status == "match":
                    launcher_result = kill_process_tree(l_pid)
                    if launcher_result.returncode != 0:
                        detail = launcher_result.stderr.strip() or launcher_result.stdout.strip() or f"退出码 {launcher_result.returncode}"
                        raise HTTPException(status_code=500, detail=safe_error_detail(f"启动器 PID {l_pid} 终止失败，状态记录已保留: {detail}"))
                    if not wait_process_exit(l_proc, max_attempts=4, interval=0.25):
                        raise HTTPException(status_code=409, detail=safe_error_detail(f"启动器 PID {l_pid} 未确认退出，状态记录已保留"))

            del instances[instance_id]
            state_manager.save_state_atomic(state)
            return StopResponse(
                status="stale_cleaned",
                instance_id=instance_id,
                exited=True,
                message="受管进程已提前退出，已清理过期状态记录",
            )

        # Identity status is "match": targeted process termination of actual target first
        logger.info(f"执行真实服务进程终止: 实例 {instance_id}, PID {pid}")
        res = kill_process_tree(pid)
        if res.returncode != 0:
            detail = res.stderr.strip() or res.stdout.strip() or f"退出码 {res.returncode}"
            logger.error(f"终止实例 {instance_id} 失败: {safe_error_detail(detail)}")
            raise HTTPException(
                status_code=500,
                detail=safe_error_detail(f"实例 {instance_id} 的终止命令失败，状态记录已保留: {detail}"),
            )

        exited_cleanly = wait_process_exit(proc, max_attempts=10, interval=0.5)

        if not exited_cleanly:
            raise HTTPException(
                status_code=409,
                detail=safe_error_detail(f"实例 {instance_id} 在终止请求后仍未确认退出，状态记录已保留"),
            )

        l_pid = inst.get("launcher_pid")
        l_ctime = inst.get("launcher_create_time")
        if l_pid and l_ctime:
            l_status, l_proc = check_process_identity(l_pid, l_ctime)
            if l_status == "error":
                raise HTTPException(status_code=500, detail=safe_error_detail(f"无法确认启动器 PID {l_pid} 身份，状态记录已保留"))
            if l_status == "match":
                logger.info(f"清理启动器包装进程 PID {l_pid}")
                launcher_result = kill_process_tree(l_pid)
                if launcher_result.returncode != 0:
                    detail = launcher_result.stderr.strip() or launcher_result.stdout.strip() or f"退出码 {launcher_result.returncode}"
                    raise HTTPException(status_code=500, detail=safe_error_detail(f"启动器 PID {l_pid} 终止失败，状态记录已保留: {detail}"))
                if not wait_process_exit(l_proc, max_attempts=4, interval=0.25):
                    raise HTTPException(status_code=409, detail=safe_error_detail(f"启动器 PID {l_pid} 未确认退出，状态记录已保留"))

        del instances[instance_id]
        state_manager.save_state_atomic(state)

    return StopResponse(
        status="success",
        instance_id=instance_id,
        exited=True,
        message=f"实例 {inst.get('name', instance_id)} 已停止并清理状态记录",
    )


# 4. Explicitly stop a managed instance (Legacy route)
@app.post("/api/instances/{instance_id}/stop", response_model=StopResponse)
def stop_instance(instance_id: str):
    return _internal_stop_instance_by_id(instance_id)


# ==========================================
# Stable /api/v1 Routes (Explicit Safe Contract)
# ==========================================

@app.get("/api/v1/profiles", response_model=V1ProfilesResponse)
def v1_list_profiles():
    """List available profiles with safe metadata only."""
    cfg = get_current_config()
    profiles_data: List[V1ProfileSummary] = []
    for profile_id, p in cfg.get("profiles", {}).items():
        kind = infer_runtime_kind(p)
        profiles_data.append(
            V1ProfileSummary(
                profile_id=profile_id,
                name=p.get("name", profile_id),
                runtime_kind=kind,
                default_gpu=p.get("default_gpu"),
            )
        )
    return V1ProfilesResponse(profiles=profiles_data)


@app.get("/api/v1/profiles/{profile_id}/status", response_model=V1ProfileStatusResponse)
def v1_get_profile_status(profile_id: str):
    """Get stable, safe status for a specific profile."""
    return get_profile_live_status(profile_id)


@app.post("/api/v1/profiles/{profile_id}/start", response_model=V1ProfileStatusResponse)
def v1_start_profile(profile_id: str, req: Optional[V1StartRequest] = None):
    """Start profile and return safe status."""
    target_gpu = req.gpu if req else None
    _internal_start_profile(profile_id, target_gpu)
    return get_profile_live_status(profile_id)


@app.post("/api/v1/profiles/{profile_id}/stop", response_model=V1ProfileStatusResponse)
def v1_stop_profile(profile_id: str):
    """Stop profile and return safe status."""
    cfg = get_current_config()
    if profile_id not in cfg.get("profiles", {}):
        raise HTTPException(status_code=404, detail=safe_error_detail(f"未找到 Profile: {profile_id}"))

    state = state_manager.load_state()
    instances = state.get("instances", {})

    target_inst_id = None
    for k, inst in instances.items():
        if inst.get("profile") == profile_id:
            target_inst_id = k
            break

    if target_inst_id:
        _internal_stop_instance_by_id(target_inst_id)

    return get_profile_live_status(profile_id)


if __name__ == "__main__":
    import uvicorn

    # Default secure binding on 127.0.0.1:8000
    uvicorn.run(app, host="127.0.0.1", port=8000)

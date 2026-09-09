"""GPU metrics collection via nvidia-smi with explicit error reporting."""

import logging
import subprocess
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("ai_dashboard")


def get_gpu_metrics(command_override: List[str] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
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

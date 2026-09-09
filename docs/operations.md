# RuntimeDeck 运维与操作指南 (Operations Manual)

## 一、 服务启动与运行

### 1. 运行环境要求
* Python 3.10+ (已在 Python 3.11 环境充分验证)
* 依赖安装：
  ```bash
  pip install -r requirements.txt
  ```
  *(包含 `fastapi`, `uvicorn`, `psutil`)*
* 硬件与系统：Windows 10/11 64-bit，已安装 NVIDIA 显卡驱动并可执行 `nvidia-smi`。

### 2. 启动服务命令
* **标准命令**：
  ```bash
  python server.py
  ```
* **或使用 Uvicorn 直接启动**：
  ```bash
  uvicorn server:app --host 127.0.0.1 --port 8000
  ```
* 启动成功后，浏览器访问：`http://127.0.0.1:8000` 即可进入本地控制面板。

---

## 二、 API-First 规范操作手册

AI Agent 或自动化脚本对本地模型进行管控时，必须遵循 **API-First** 原则，禁止直接调用操作系统裸命令。

### 1. 查询显卡与运行中实例
* **请求**：
  ```bash
  curl -s http://127.0.0.1:8000/api/status
  ```
* **特性**：严格只读，不写磁盘。
* **响应示例**：
  ```json
  {
    "gpus": [
      {
        "index": 0,
        "name": "NVIDIA GeForce RTX 3080",
        "uuid": "GPU-00000000-0000-0000-0000-000000000001",
        "used_mb": 14250,
        "total_mb": 20480,
        "util_percent": 85
      },
      {
        "index": 1,
        "name": "NVIDIA GeForce RTX 3070",
        "uuid": "GPU-00000000-0000-0000-0000-000000000002",
        "used_mb": 820,
        "total_mb": 8192,
        "util_percent": 5
      }
    ],
    "instances": {
      "inst_28412": {
        "profile": "qwen_160k",
        "name": "Qwen3.8-27B IQ3_S (160K Main)",
        "pid": 28412,
        "create_time": 1725801600.123,
        "executable": "C:\\Runtimes\\llama.cpp\\llama-server.exe",
        "gpu": "3080",
        "gpu_name": "RTX 3080 20G",
        "started_at": "2026-09-08T17:15:00",
        "is_alive": true,
        "identity_status": "match",
        "is_legacy": false,
        "launcher_pid": 14208,
        "launcher_create_time": 1725801598.050,
        "launcher_executable": "cmd.exe"
      }
    },
    "errors": []
  }
  ```

* **真实服务 PID 不变量**：受管实例的主 `pid` 与 `executable` 严格反映真实工作进程（如 `llama-server.exe` 或 `python.exe`），外层启动器作为 `launcher_*` 辅助记录保存。
* **旧版本包装进程处理**：仅记录 `cmd.exe` 的旧记录不会出现在实例列表或错误区；显式 Stop 时只删除该旧 state，不终止未知后台服务。重新启动后才会建立真实服务 PID 记录。

### 2. 查询配置预设
* **请求**：
  ```bash
  curl -s http://127.0.0.1:8000/api/config
  ```
* **响应**：返回 `runtimes.json` 中配置的所有 GPU 定义及 Profile 列表。

### 3. 显式启动模型实例
* **请求**：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/instances/start \
    -H "Content-Type: application/json" \
    -d '{"profile": "qwen_160k", "gpu": "3080"}'
  ```
* **响应**：
  ```json
  {
    "status": "success",
    "instance_id": "inst_28412",
    "pid": 28412
  }
  ```
* **常见错误**：
  * Profile 不存在：返回 HTTP 400，`detail: "未知的 Profile 预设"`
  * GPU 不存在：返回 HTTP 400，`detail: "未知的 GPU 设备: ..."`
  * 拉起失败（如可执行文件缺失或元数据捕获失败）：返回 HTTP 500。若进程已短暂创建但元数据失败，系统已自动回收孤儿进程。

### 4. 显式停止指定实例 (Legacy 端点)
* **请求**：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/instances/inst_28412/stop
  ```
* **成功响应**：
  ```json
  {
    "status": "success",
    "instance_id": "inst_28412",
    "exited": true,
    "message": "实例 Qwen3.8-27B IQ3_S (160K Main) 已停止并清理状态记录"
  }
  ```
* **进程已提前退出、旧包装记录或 PID 复用响应 (200 OK stale_cleaned)**：
  ```json
  {
    "status": "stale_cleaned",
    "instance_id": "inst_28412",
    "exited": true,
    "message": "检测到 PID 已被其他程序复用，为防误杀已跳过终止操作，并清理了过期记录"
  }
  ```
  *(对 legacy wrapper cmd.exe 记录，返回 `stale_cleaned` 并清理 state，绝不盲杀未知后台进程)*
* **进程未确认退出错误（HTTP 409）**：
  ```json
  {
    "detail": "实例 inst_28412 在终止请求后仍未确认退出，状态记录已保留"
  }
  ```
  *(此时实例记录仍然安全保存在 `state.json` 中，绝不会被删除丢弃)*

---

## 三、 稳定版本化 Profile API (`/api/v1`)

针对外部自动化系统（如 Dreamina、NAS 调度器、Agent 编排流程）提供脱敏、稳定、版本化的 Profile 控制面 API。所有 `/api/v1` 端点永不暴露物理 PID、启动器 wrapper 信息、raw command_line 或环境变量密钥。

### 1. 查询所有可用 Profiles (`GET /api/v1/profiles`)
* **请求**：
  ```bash
  curl -s http://127.0.0.1:8000/api/v1/profiles
  ```
* **响应示例**：
  ```json
  {
    "profiles": [
      {
        "profile_id": "qwen_160k",
        "name": "Qwen3.8-27B IQ3_S (160K Main)",
        "runtime_kind": "llama_cpp",
        "default_gpu": "3080"
      },
      {
        "profile_id": "comfyui_default",
        "name": "ComfyUI Standard",
        "runtime_kind": "comfyui",
        "default_gpu": "3080"
      }
    ]
  }
  ```

### 2. 查询单个 Profile 运行状态 (`GET /api/v1/profiles/{profile_id}/status`)
* **请求**：
  ```bash
  curl -s http://127.0.0.1:8000/api/v1/profiles/qwen_160k/status
  ```
* **响应示例 (运行中)**：
  ```json
  {
    "profile_id": "qwen_160k",
    "runtime_kind": "llama_cpp",
    "state": "running",
    "endpoint": "http://127.0.0.1:8080",
    "health": "unknown",
    "gpu": "3080"
  }
  ```
* **状态值与语义**：
  - `state`: `"running"` (真实进程身份核验匹配), `"stopped"` (未启动、已退出或已清理), `"stale"`, `"error"` (检查权限异常)。
  - `endpoint`: 安全归一化为 `http://127.0.0.1:<port>`，绝不向外部稳定契约扩散局域网或公网绑定。
  - `health`: `"ready"`, `"unknown"`, 或 `"error"`。

### 3. 启动指定 Profile (`POST /api/v1/profiles/{profile_id}/start`)
* **请求**：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/v1/profiles/qwen_160k/start \
    -H "Content-Type: application/json" \
    -d '{"gpu": "3080"}'
  ```
* **响应**：直接返回该 Profile 的最新 `V1ProfileStatusResponse`（绝不返回 PID、launcher 或未脱敏命令行）。

### 4. 停止指定 Profile (`POST /api/v1/profiles/{profile_id}/stop`)
* **请求**：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/v1/profiles/qwen_160k/stop
  ```
* **响应**：执行严格身份核对与安全停止流程后，返回最新 `V1ProfileStatusResponse`（`state: "stopped"`）。若 Profile 不存在则返回 404。

---

## 四、 状态文件存储与路径优先级

为避免将运行态持久化文件写入代码仓库造成脏代码树，RuntimeDeck 采用分层路径解析策略：

1. **最高优先级**：`RUNTIMEDECK_STATE_FILE` 环境变量指定的绝对路径。
2. **默认外部存储**：
   - Windows 环境：`%LOCALAPPDATA%\RuntimeDeck\state.json`
   - Linux/POSIX 环境：`$XDG_DATA_HOME/RuntimeDeck/state.json` 或 `~/.local/share/RuntimeDeck/state.json`
3. **只读向后兼容回退**：若外部状态文件尚不存在，而项目根目录下存在旧 `state.json`，系统自动以只读方式兼容加载旧状态记录；后续原子写盘将统一写入上述外部目标路径，旧文件不会被破坏。

---

## 五、 配置维护指南 (`runtimes.json`)

系统配置遵循极简主义（KISS 原则），采用静态 JSON 维护：

```json
{
  "gpus": {
    "3080": {
      "name": "RTX 3080 20G",
      "uuid": "GPU-00000000-0000-0000-0000-000000000001"
    },
    "3070": {
      "name": "RTX 3070 8G",
      "uuid": "GPU-00000000-0000-0000-0000-000000000002"
    }
  },
  "profiles": {
    "qwen_160k": {
      "name": "Qwen3.8-27B IQ3_S (160K Main)",
      "default_gpu": "3080",
      "cwd": "C:\\Runtimes\\llama.cpp",
      "executable": "C:\\Runtimes\\llama.cpp\\llama-server.exe",
      "args": [
        "-m", "C:\\Models\\Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf",
        "-c", "163840",
        "--host", "0.0.0.0",
        "--port", "8080"
      ]
    }
  }
}
```

* **添加新 Profile**：在 `profiles` 下添加对应键名，指定 `name`, `default_gpu`, `cwd`, `executable` 与参数数组 `args`。修改文件后无需重启服务，刷新前端或下次调用即可生效。

---

## 六、 常见异常排查与运维对策

### 1. `nvidia-smi` 采集失败
* **现象**：`/api/status` 返回的 `gpus` 为空，`errors` 中包含 `"采集 nvidia-smi 失败: 未找到 nvidia-smi 命令"`。
* **原因**：NVIDIA 驱动未安装，或系统环境变量 `PATH` 中缺少 `C:\Windows\System32`。
* **处理**：验证终端中 `nvidia-smi` 是否可正常执行。

### 2. 终止实例失败（HTTP 500 / 409）
* **HTTP 500**：定向 `taskkill /PID <pid> /T /F` 返回非零结果，表示系统没有确认终止成功；RuntimeDeck 不删除 state，并在错误详情中保留命令输出。
* **HTTP 409**：终止命令返回成功，但 5 秒内仍无法确认进程退出；RuntimeDeck 同样保留 state。
* **保障与处理**：两种错误都不会制造脱管实例。管理员可打开 Windows 任务管理器核查该 PID，确认原因后重试 stop 接口。


### 3. PID 复用告警 (`mismatch`)
* **现象**：调用 stop 接口返回 `status: "stale_cleaned"`，日志显示 `"防误杀触发：实例 inst_xxxx 的 PID xxxx 已被其他进程复用，仅清理过期状态"`。
* **说明**：原模型进程早已退出，Windows 将该 PID 分配给系统其他应用程序。RuntimeDeck 成功防御误杀，并自动清理了过期记录。

### 4. 损坏的 `state.json`
* **现象**：手动编辑 `state.json` 导致 JSON 语法错误。
* **保障**：RuntimeDeck 会记录日志并自动回退为 `{"instances": {}}`，不会崩溃退出。下次保存实例时会自动通过临时文件原子重写标准格式。

---

## 七、 安全与敏感信息脱敏铁律

1. **默认绑定**：默认绑定为 `127.0.0.1:8000`（仅限本机访问）。
2. **严禁公网暴露**：
   * Dashboard 控制 API 具备操作系统进程启动与终止特权，**严禁将 8000 端口映射到公网或置于公网 DMZ 中**。
3. **跨设备受信任访问**：
   * 若需通过手机或 NAS 访问，可将其绑定在受信内网 IP（如 `192.168.1.x:8000`）或虚拟局域网（如 Tailscale / WireGuard），并通过局域网防火墙隔离外部访问。
4. **命令行敏感参数自动脱敏**：
   * 系统自动对 `--api-key`、`--token`、`--hf-token`、`--password`、`--secret`、`Authorization: Bearer [REDACTED]` 以及 `KEY=value` 等敏感凭据执行 `[REDACTED]` 替换。
   * 状态持久化文件与 API 响应中严禁持久化或回显明文密钥与原始命令行。
   * 错误信息与输出详情经 `safe_error_detail` 统一截断与脱敏（上限 240 字符）。

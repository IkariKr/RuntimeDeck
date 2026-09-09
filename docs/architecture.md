# RuntimeDeck 系统架构设计 (Architecture)

## 一、 系统定位与设计哲学

RuntimeDeck 是面向个人 AI 工作站（搭载 NVIDIA RTX 3080 20G 主力计算卡与 RTX 3070 8G 辅助卡）的**本地模型运行态仪表盘与显式遥控器（Dashboard & Explicit Launcher）**。

### 核心哲学：显式优于隐式 (Explicit over Implicit)
1. **纯显式操作**：坚决不引入任何后台自动抢占、自动隐式互斥或根据显存自动强杀进程的策略逻辑。所有模型与服务的启动和停止必须由用户或上层控制流程显式发起。
2. **控制面与数据面解耦**：
   * **数据面（Data Plane）**：推理客户端直接连接后端推理引擎的原生监听端口（如 `llama-server` 的 `:8080`，`ComfyUI` 的 `:8188`），获得原生低延迟、高并发和 Server-Sent Events (SSE) 流式传输，不经过任何网关代理或反向代理。
   * **控制面（Control Plane）**：RuntimeDeck 独立运行在 `:8000` 端口，仅负责生命周期管理（显式 Start/Stop）、配置预设读取以及硬件与实例状态监控。

```text
                     【数据面 (原生直连，零损耗)】
                      NAS CLIProxyAPI / 客户端
                             │             │
                    HTTP :8080             │ HTTP :8188
                             │             │
                             ▼             ▼
                      llama.cpp         ComfyUI
                      (LLM 引擎)       (绘图/视频)
                             ▲             ▲
                             │             │
  ═══════════════════════════╪═════════════╪═══════════════════════════
                             │             │ 本地原生进程操作 (启动/指定PID关闭)
                     【控制面 (WebUI / REST API)】
                     AI Runtime Dashboard (:8000)
                             ▲
                             │ HTTP / 浏览器 (受信任网络)
             用户手机 / 电脑 / 自动化脚本 (显式发起 Start / Stop)
```

---

## 二、 系统组件结构

RuntimeDeck 采用模块化 Python 包架构，结构如下：

```text
RuntimeDeck/
├── server.py                  # FastAPI 应用入口与 4 个核心 API 端点
├── runtime_deck/
│   ├── __init__.py            # 包导出与元数据
│   ├── models.py              # Pydantic 类型定义与契约模式
│   ├── config.py              # runtimes.json 配置文件解析与验证
│   ├── gpu.py                 # nvidia-smi 硬件指标采集与异常隔离
│   ├── process.py             # 进程身份核对、定向树状终止与退出验证
│   └── state.py               # 线程安全状态管理与原子持久化替换
├── static/                    # 本地化离线 WebUI 依赖
│   ├── alpine.min.js          # 本地 Alpine.js 运行时
│   ├── tailwindcss.js         # 本地 Tailwind CSS 运行时
│   ├── app.js                 # 仪表盘前端交互逻辑
│   ├── styles.css             # 基础暗色主题样式
│   └── index.html             # 控制面板单页应用
├── runtimes.json              # 显卡与模型运行预设配置
├── state.json                 # 当前受管实例运行态持久化记录
├── requirements.txt           # 基础运行环境依赖 (FastAPI, Uvicorn, psutil)
├── docs/                      # 架构设计与运维文档
└── tests/                     # 自动化测试套件
```

---

## 三、 核心关键机制

### 1. 状态管理与原子写盘 (`runtime_deck/state.py`)
* **线程锁保障**：通过 `threading.RLock` 确保跨协程与多线程对 `state.json` 的互斥操作。
* **文件系统原子替换**：状态写入时，先在目标文件同目录下创建 `tempfile.NamedTemporaryFile`，完成 JSON dump 后，通过 `os.replace` 在单一文件系统内执行原子重命名替换，防止因系统断电或进程中断产生半写入损坏文件。
* **损坏优雅回退**：若 `state.json` 发生格式破损（如非合法 JSON 或异常结构），系统记录错误日志并自动回退为 `{"instances": {}}`，避免服务崩溃中断。

### 2. 真实服务进程发现与生命周期解耦 (`runtime_deck/process.py`)
在实际 Windows 部署中，模型通常通过包装脚本（`.cmd`/`.bat`，经由 `cmd.exe`）拉起，而实际占用 GPU 显存的是 `llama-server.exe` 或 Python 运行环境（ComfyUI 的 `python.exe`）。若仅仅管控包装器 `cmd.exe`，当脚本提前退出或生成独立子进程时，真实服务可能脱管失控。

* **受管主身份转变**：系统将真实运行的后端服务进程（如 `llama-server.exe` 或 `python.exe`）作为受管实例的主 PID 与主身份；外层启动器作为 `launcher_pid` 辅助跟踪。
* **启动前 PID 基线快照**：在执行 `Popen` 启动包装脚本前，捕获操作系统所有已有 PID（`get_process_baseline_pids()`）。发现逻辑严格要求真实服务 PID 必须不在基线列表中，彻底禁止误接管或抢占此前已启动的同名进程。
* **精确命令标记匹配**：
  * Qwen Profile：匹配 `llama-server.exe` 且包含 `--port 8080` 等特征标记。
  * ComfyUI Profile：匹配 `python.exe` 且命令行参数包含 `ComfyUI` 与 `main.py`。
* **歧义与元数据异常 Fail-Closed**：若启动后发现 0 个候选、发现超过 1 个候选（歧义）或在探测命令行与创建时间时发生权限异常，系统绝不猜测盲选，直接触发 Fail-Closed，立即对刚拉起的包装进程树执行定向终止，防止孤儿留存。
* **先杀目标，后清启动器**：停止实例时，首先对真实服务 PID 执行核验与树状终止，确认退出后再核验并清理 `launcher_pid`。若真实服务未确认退出（409），绝不清理启动器或删除 state。
* **旧版本包装进程隐藏与清理**：历史遗留仅记录 `cmd.exe` 的 state 不进入 live 实例视图；显式停止时只删除旧 state，不盲杀未知后台服务。重新启动后才建立真实服务 PID 记录。

### 3. 进程身份标识与 PID 复用防御 (`runtime_deck/process.py`)
在 Windows 与长期运行的操作系统中，PID 在进程退出后可能被系统重新分配给完全无关的新进程。如果仅凭 `PID` 进行终止，极可能误杀系统关键服务或其他业务应用。

* **复合主键**：RuntimeDeck 在进程启动时捕获其真实的操作系统创建时间戳，以 `(PID, create_time)` 构成不可伪造的进程身份复合主键（允许 0.5s 系统时间抖动容限）。
* **四态核对模型** (`check_process_identity`)：
  1. `match`：PID 存在且创建时间一致。确认是受管实例，**唯有此状态允许执行进程终止**。
  2. `mismatch`：PID 存在但创建时间不一致（PID 复用）。**绝对不调用终止命令**，仅安全清理过期的运行记录，并返回 `status = "stale_cleaned"`。
  3. `not_found`：PID 已不存在（受管进程已正常自退出）。跳过终止命令，安全清理旧记录，返回 `status = "stale_cleaned"`。
  4. `error`：进程检查时遭遇权限不足或其他未知系统异常。采取 **Fail-Closed** 策略：不终止进程、不删除状态，向调用方抛出 HTTP 500 并在 `/api/status` 中如实汇报。

### 4. 定向树状终止与孤儿进程回收
* **严格禁止全局模糊误杀**：系统内严禁执行 `taskkill /IM python.exe` 或 `taskkill /IM llama-server.exe`。终止操作仅支持按确定 PID 进行：`taskkill /PID <pid> /T /F`。
* **拉起元数据异常时孤儿回收**：在 `POST /api/instances/start` 中，若子进程已成功拉起，但后续获取进程身份元数据（如 `psutil.Process(pid).create_time()`）失败，系统立即对该刚拉起的进程树执行定向终止，彻底防止留下脱管孤儿进程。
* **终止失败与退出确认保留状态**：在 `POST /api/instances/{id}/stop` 中，若定向终止命令返回非零结果，系统返回 HTTP 500 并保留 state；若命令成功但 `wait_process_exit` 在默认 5s 内仍无法确认退出，则返回 HTTP 409，同样保留 state，防止将存活的受管进程变为脱管孤儿。

### 5. 状态查询严格只读
* `GET /api/status` 设计为**严格只读**操作。其内部仅执行硬件 `nvidia-smi` 查询与进程元数据探测，绝不触发写盘操作（不修改或重新保存 `state.json`），完全规避轮询造成的磁盘 I/O 抖动与竞争。

### 6. 敏感信息防泄漏与安全沙箱 (`runtime_deck/security.py`)
* **敏感命令行自动脱敏**：系统在拉起或记录进程时，通过 `redact_command_line` 正则自动拦截并替换 `--api-key`、`--token`、`--hf-token`、`--auth`、`--password`、`Authorization: Bearer [REDACTED]` 及 `KEY=value` 等敏感认证材料为 `[REDACTED]`。
* **状态存储与 API 响应收口**：`state.json` 绝不持久化明文命令或原始密钥；`/api/status` 显式构造响应模型，不回传 `command_line`，彻底防止前端内存泄露。
* **错误诊断安全截断**：所有返回至前端或 API 错误体的 subprocess 输出、stderr 及详情统一经 `safe_error_detail` 脱敏，并限制在 240 字符以内。

### 7. 稳定版本化 Profile API 契约 (`/api/v1`)
* 提供面向外部客户端（如自动化编排器、外部调度系统）的稳定端点：
  - `GET /api/v1/profiles`
  - `GET /api/v1/profiles/{profile_id}/status`
  - `POST /api/v1/profiles/{profile_id}/start`
  - `POST /api/v1/profiles/{profile_id}/stop`
* **契约极简与安全**：仅暴露业务级字段（`profile_id`, `runtime_kind`, `state`, `endpoint`, `health`, `gpu`），严格抹除物理 PID、底层 executable 路径、launcher 关系与命令行。
* **Endpoint 规范化**：对外统一暴露 `http://127.0.0.1:<port>`，不泄露局域网/公网网络绑定细节。

### 8. 状态持久化外置与多层路径优先级
* 采用 `resolve_default_state_file` 解析持久化路径：
  1. `RUNTIMEDECK_STATE_FILE` 环境变量指定的绝对路径。
  2. Windows `%LOCALAPPDATA%\RuntimeDeck\state.json`（默认外置路径，不在源码目录中）。
  3. 项目目录 `state.json`（仅做开发兼容或旧状态文件只读读取回退）。

### 9. WebUI 离线闭环
* WebUI 所需的 Tailwind CSS、Alpine.js 及页面资源均置于 `static/` 目录中，FastAPI 直接提供本地静态服务。
* 严禁加载任何公网 CDN 资源（如 cdnjs, unpkg, jsdelivr, google fonts），即使在断开外网的纯内网离线工作站也能完整运作。

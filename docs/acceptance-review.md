# RuntimeDeck 验收与规格对照审查 (Acceptance Review)

## 一、 验收范围与设计原则对照

根据项目主方案《本地模型运维 - AI Runtime显式管理方案.md》与《本地AI Runtime Dashboard设计与实现.md》，系统核心验收项与对照结果如下：

| 验收编号 | 规格需求 | 实现文件 / 机制 | 测试覆盖 | 验收结论 |
| :--- | :--- | :--- | :--- | :--- |
| **AC-01** | **显式生命周期管理**：系统绝不包含任何自动抢占、自动隐式互斥或后台自动杀旧起新逻辑。 | `server.py`<br>`runtime_deck/process.py` | `test_api.py`<br>`test_process.py` | **PASS** |
| **AC-02** | **状态原子持久化与锁保护**：使用 `threading.RLock` 与 `os.replace` 原子替换，规避并发写坏。 | `runtime_deck/state.py` (`StateManager`) | `test_state.py::test_save_and_load_state_valid`<br>`test_state.py::test_state_manager_thread_concurrency` | **PASS** |
| **AC-03** | **状态损坏优雅回退**：`state.json` 损坏或非字典格式时不崩溃，安全回退至 `{"instances": {}}`。 | `runtime_deck/state.py` (`load_state`) | `test_state.py::test_load_state_corrupt_json_fallback`<br>`test_state.py::test_load_state_non_dict_fallback` | **PASS** |
| **AC-04** | **GPU 指标解析与弹性容错**：正确解析 multi-GPU CSV；驱动或命令缺失时将异常隔离在 `errors` 列表中。 | `runtime_deck/gpu.py` (`get_gpu_metrics`) | `test_gpu.py::test_get_gpu_metrics_success`<br>`test_gpu.py::test_get_gpu_metrics_file_not_found`<br>`test_gpu.py::test_get_gpu_metrics_called_process_error` | **PASS** |
| **AC-05** | **进程身份四态模型**：基于 `(pid, create_time)` 区分 `match`、`mismatch`、`not_found`、`error` 四种状态。 | `runtime_deck/process.py` (`check_process_identity`) | `test_process.py::test_check_process_identity_match`<br>`test_process.py::test_check_process_identity_mismatch_pid_reuse`<br>`test_process.py::test_check_process_identity_not_found`<br>`test_process.py::test_check_process_identity_error` | **PASS** |
| **AC-06** | **PID 复用不误杀**：检测到 PID 复用 (`mismatch`) 时，严禁调用终止命令，仅清理过期记录。 | `server.py` (`stop_instance`) | `test_api.py::test_stop_instance_pid_reuse_mismatch_never_kills` | **PASS** |
| **AC-07** | **拉起元数据异常时定向孤儿回收**：Popen 成功但元数据捕获失败时，立即定向终止该进程树，防止未受管孤儿。 | `server.py` (`start_instance`) | `test_api.py::test_start_instance_metadata_failure_triggers_orphan_cleanup` | **PASS** |
| **AC-08** | **终止失败或未确认退出保留状态**：定向终止命令失败时返回 500；命令成功但进程在超时内未确认退出时返回 409；两种情况都保留 `state.json` 记录。 | `server.py` (`stop_instance`) | `test_api.py::test_stop_instance_taskkill_failure_retains_state_returns_500`<br>`test_api.py::test_stop_instance_exit_timeout_retains_state_returns_409` | **PASS** |
| **AC-09** | **检查异常 Fail-Closed (HTTP 500)**：进程身份检查发生异常时，不杀进程且不删状态，报错 500。 | `server.py` (`stop_instance`) | `test_api.py::test_stop_instance_identity_error_fail_closed_retains_state` | **PASS** |
| **AC-10** | **状态查询严格只读**：`GET /api/status` 不触发任何磁盘写操作与 state 变更。 | `server.py` (`get_status`) | `test_api.py::test_get_status_strict_read_only` | **PASS** |
| **AC-11** | **输入验证与错误语义**：非法 Profile 返回 400，非法 GPU 返回 400，未找到实例返回 404。 | `server.py` | `test_api.py::test_start_instance_unknown_profile`<br>`test_api.py::test_start_instance_unknown_gpu`<br>`test_api.py::test_stop_instance_unknown_returns_404` | **PASS** |
| **AC-12** | **严禁全局模糊强杀**：严禁 `taskkill /IM ...`，仅允许定向按 PID 树状终止 (`/PID <pid> /T /F`)。 | `runtime_deck/process.py` (`kill_process_tree`) | `test_process.py::test_kill_process_tree_windows_command` | **PASS** |
| **AC-13** | **离线 UI 闭环**：前端静态依赖（Tailwind, Alpine.js）本地化托管，无任何公网 CDN 引用。 | `static/` 目录 | `test_static_offline.py::test_required_static_files_exist`<br>`test_static_offline.py::test_no_external_cdn_urls_in_static_assets` | **PASS** |
| **AC-14** | **多分辨率响应式适配**：覆盖 320/375/768 宽度无水平溢出，GPU 卡片自适应折叠，表单与实例控制按钮可用，宽屏居中限宽阅读。 | `static/index.html`<br>`static/styles.css` | `test_responsive.py::TestResponsiveViewportAndOverflow`<br>`test_responsive.py::TestGpuCardResponsiveCollapse`<br>`test_responsive.py::TestLaunchFormAndInstanceControlsUsability`<br>`test_responsive.py::TestWideScreenReadability` | **PASS** |
| **AC-15** | **真实服务进程发现与匹配**：按 profile/命令规则精准识别 Qwen `llama-server.exe` 与 ComfyUI `python.exe` + `main.py`。 | `runtime_deck/process.py` (`is_matching_real_service`, `get_service_spec_for_profile`) | `test_process.py::test_get_service_spec_for_profile_uses_explicit_markers`<br>`test_process.py::test_is_matching_real_service_qwen_and_comfy` | **PASS** |
| **AC-16** | **启动基线排除与防抢占**：在启动前抓取系统 PID 基线，已有同名/同端口进程绝不识别、绝不接管。 | `runtime_deck/process.py` (`discover_real_service`, `get_process_baseline_pids`) | `test_process.py::test_discover_real_service_excludes_baseline_and_requires_unique_candidate` | **PASS** |
| **AC-17** | **发现歧义与元数据异常 Fail-Closed**：新候选为 0、候选 >1 或元数据探测异常时拒绝启动，并定向回收本次启动的 launcher 进程树。 | `runtime_deck/process.py`<br>`server.py` | `test_process.py::test_discover_real_service_ambiguous_fails_closed`<br>现有 API 启动失败/孤儿回收测试 | **PASS** |
| **AC-18** | **真实 PID 作为受管主身份**：使用脚本包装器启动时，API 与 `state.json` 的主 PID 记录为真实服务进程 PID，外层包装器作为辅助 `launcher_*` 元数据留存。 | `server.py` (`start_instance`) | `test_api.py::test_start_instance_wrapper_records_real_service_pid` | **PASS** |
| **AC-19** | **停止先真实服务后 Launcher**：停止实例时必须先终止并确认真实服务退出，随后才安全清理 launcher；真实服务退出超时 (409) 或 launcher 清理失败时保留 state。 | `server.py` (`stop_instance`) | `test_api.py::test_stop_instance_target_then_launcher_and_persisted_real_metadata`<br>`test_api.py::test_stop_instance_launcher_failure_retains_state`<br>`test_api.py::test_stop_instance_exit_timeout_retains_state_returns_409` | **PASS** |
| **AC-20** | **旧/已退出实例隐藏与清理**：状态接口只返回真实存活服务；旧 wrapper 记录不展示，显式清理时只删除 state、不盲杀未知进程；已退出和 PID 复用记录直接清理。 | `server.py`<br>`static/app.js` | `test_api.py::test_get_status_hides_non_live_instances`<br>`test_api.py::test_stop_instance_legacy_wrapper_deletes_state_without_killing` | **PASS** |
| **AC-21** | **敏感命令行参数脱敏**：`--api-key`、`--token`、`--hf-token`、`--password`、`Authorization: Bearer [REDACTED]`、`KEY=value` 等敏感凭据在状态文件、API 响应及错误信息中强制脱敏为 `[REDACTED]`。 | `runtime_deck/security.py`<br>`server.py` | `test_api.py::test_redact_command_line_args_and_secrets`<br>`test_api.py::test_safe_error_detail_sanitizes_and_truncates`<br>`test_api.py::test_status_and_state_never_expose_raw_command_line_or_secrets`<br>`test_api.py::test_start_instance_stores_redacted_command_line_without_secret` | **PASS** |
| **AC-22** | **运行态文件外置与路径优先级**：状态文件优先使用 `RUNTIMEDECK_STATE_FILE`，默认外置至 `%LOCALAPPDATA%\RuntimeDeck\state.json`，支持对旧根目录 `state.json` 的只读平滑回退。 | `runtime_deck/state.py` (`resolve_default_state_file`, `StateManager`) | `test_state.py::test_resolve_default_state_file_env_priority`<br>`test_state.py::test_resolve_default_state_file_localappdata`<br>`test_state.py::test_resolve_default_state_file_fallback_project_dir`<br>`test_state.py::test_state_manager_legacy_fallback_read_compatibility` | **PASS** |
| **AC-23** | **稳定版本化 `/api/v1` Profile 契约**：提供 `/api/v1/profiles` 与 `/api/v1/profiles/{id}/(status|start|stop)`，仅返回安全业务字段，绝对不返回物理 PID、launcher、真实命令或环境变量。 | `server.py`<br>`runtime_deck/models.py` | `test_api.py::test_v1_profiles_returns_safe_fields_only`<br>`test_api.py::test_v1_profile_status_stopped_when_no_instance`<br>`test_api.py::test_v1_profile_status_running_and_safe_fields`<br>`test_api.py::test_v1_profile_status_identity_mismatch_and_error`<br>`test_api.py::test_v1_start_profile_success_never_returns_pid_or_launcher`<br>`test_api.py::test_v1_stop_profile_success_never_returns_pid_or_launcher`<br>`test_api.py::test_v1_stop_unknown_profile_returns_404` | **PASS** |

---

## 二、 自动化测试执行记录

测试套件运行于本地 Python 3.11 环境：

```text
pytest -q RuntimeDeck/tests
96 passed in 4.13s

覆盖范围：核心 API 与状态安全、敏感参数脱敏与密钥隔离、外置持久化路径与向后兼容加载、稳定版本化 /api/v1 Profile 契约、真实 llama-server/ComfyUI PID 发现、启动基线排除、多候选 fail-closed、真实 PID 优先停止、launcher 清理失败保留 state、旧/已退出记录隐藏与 state 清理、响应式布局与离线资源。
```

---

## 三、 当前环境限制与边界说明 (Environment Limitations)

在开发与自动化测试过程中，遵循不伪造运行结果的原则，真实记录以下环境约束：

1. **测试隔离模式（Mocked System Calls）**：
   * 在 CI 及未安装多 GPU 的开发机器上，`test_gpu.py` 与 `test_api.py` 采用模拟 `nvidia-smi` 输出与 `psutil.Process` 时间戳，对各种正常、边界与异常分支进行确定性验证。
   * 当在缺乏 NVIDIA GPU 的主机上以真实模式请求 `GET /api/status` 时，系统不会伪造显卡指标，而是正确将 `"采集 nvidia-smi 失败: 未找到 nvidia-smi 命令"` 记录在 `errors` 列表中，符合弹性容错契约。
2. **跨平台与 Windows 专有语义**：
   * `kill_process_tree` 在 Windows 环境下调用原生 `taskkill /PID <pid> /T /F`，在 POSIX 环境下通过 psutil 递归子进程发送 SIGKILL 进行跨平台兼容；单元测试通过针对 Windows 分支的 mock 验证确保绝对不使用 `/IM` 模糊杀进程。
3. **真实子进程路径依赖**：
   * `runtimes.json` 中配置的可执行路径（如 `C:\Runtimes\llama.cpp\llama-server.exe`）依赖具体主机部署路径；单元测试通过隔离的 temporary config 对 API 进行验证，避免强依赖外部物理路径的存在。

4. **静态与源码级响应式适配验证（Responsive Resolution Acceptance）**：
   * 针对多分辨率（320px、375px、768px 及宽屏）验收要求，在无真实移动端浏览器驱动环境下，编写源码级与几何契约测试 `tests/test_responsive.py`（共 15 项测试，全部 PASS）：
     - **320px / 375px / 768px 无水平溢出**：验证 `viewport` 包含 `width=device-width, initial-scale=1.0`；全局采用 `box-sizing: border-box`；主容器为 `width: 100%` 配合 `max-width: 48rem`；基础 CSS 规则中无硬编码超过 320px 的破坏性固定宽度；几何预算计算验证 320/375/768 宽度均无任何溢出。
     - **GPU 卡片自适应折叠**：移动端（<640px）基础样式默认为单列（`grid-template-columns: 1fr`），>=640px 平板/桌面断点展开为双列（`repeat(2, 1fr)`）；显存进度条宽度自适应 100%。
     - **启动表单与实例控制可用性**：表单在移动端纵向堆叠（1fr），>=640px 展开为两列（`2fr 1fr`）；下拉框与启动按钮宽度 100%；实例行采用 flexbox 布局，关闭按钮具备明确光标指针与触控内边距。
     - **宽屏阅读舒适度**：主容器限定最大宽度 `48rem`（768px，对应 Tailwind `max-w-2xl`），外层 body 采用 flexbox 居中对齐，防止超宽屏幕上拉伸变形导致排版失真。

---

## 四、 主节点最终二次 Review

按批准计划重新对照实现、测试、浏览器行为和安全边界后，结论如下：

- **控制面边界**：未发现推理数据代理、自动抢占、显存仲裁或隐式切换逻辑。
- **状态安全**：`GET /api/status` 只读；状态写入受 `RLock` 保护并使用同目录临时文件与 `os.replace`。
- **进程安全**：停止只接受真实服务 `pid + create_time` 匹配；`mismatch`、`not_found` 不杀当前 PID并清理 state；`error`、终止命令失败、launcher 清理失败和退出确认超时均保留 state；旧 wrapper-only 记录只删除 state、不盲杀未知服务。
- **真实 PID 不变量**：Qwen 受管主 PID 是 `llama-server.exe`，ComfyUI 受管主 PID 是带 `ComfyUI/main.py` 命令标记的 `python.exe`；`cmd.exe` 只作为 `launcher_*` 辅助字段。
- **离线闭环**：根路径提供完整 Dashboard；静态资源通过同源 `/static/*` 加载；代码与资源扫描未发现公网 URL。
- **响应式适配**：完成静态多分辨率（320/375/768px 及宽屏）验收测试，覆盖防溢出、卡片折叠、表单操作可用性及宽屏限宽。
- **真实浏览器复核**：在 `320/375/768/1440px` viewport 下实测 `document.documentElement.scrollWidth === viewport width`；320/375px 为 GPU 单列、表单单列、实例停止按钮纵向全宽，768/1440px 为 GPU 双列、表单 `2fr 1fr`、实例横向布局。
- **实际证据**：`pytest -q RuntimeDeck/tests` 为 **96 passed**；Python 与 JavaScript 语法检查通过；旧 `/api/status` 与 `/api/v1` 实际响应均未返回命令行、PID、launcher 或凭据；外置 state 为空；反索引路由与响应头已验证；重启控制面未接管或终止现有 ComfyUI/模型服务。
- **安全与 API 稳定化**：敏感参数自动脱敏（`[REDACTED]`）、状态文件外置至 `%LOCALAPPDATA%`（支持 `RUNTIMEDECK_STATE_FILE` 与旧文件兼容读取）、稳定 `/api/v1` Profile API 契约（脱敏且不暴露 PID/launcher/命令）全量覆盖并通过测试。

最终结论：**RuntimeDeck 已按批准计划完成，满足当前规格与多分辨率响应式验收边界。**

# Code Review — Profile 删除 + 受管实例元数据上报

- **Review Scope**: `DELETE /api/profiles/{profile_id}`、`save_raw_config` / `delete_profile_config`、`ProfileConfig` / `InstanceRecord` / `InstanceView` 的 `model_id`/`context_size`/`kv_quant` 字段、`get_status()` 元数据注入、`_internal_start_profile()` 元数据持久化、前端删除按钮与实例清单元数据行、`tests/test_profile_config_api.py`
- **Reviewed revision**: `fb74107`（HEAD）工作区未提交改动
- **审查日期**: 2026-10-04
- **审查者**: Code Reviewer（专职审查，未修改代码）

---

## Findings

### [P1] `context_size` / `model_id` / `kv_quant` 为非字符串时，`/api/status` 整体 500，实例清单不再显示任何实例

`runtime_deck/models.py:95-97`、`runtime_deck/models.py:113-115`、`server.py:290-292`、`server.py:308-310`

`ProfileConfig`、`InstanceRecord`、`InstanceView` 将这三个字段声明为 `Optional[str]`。Pydantic v2 默认 **不** 对 `int`/`float` 做隐式字符串转换。`context_size` 语义上就是 token 数（例如 131072、160000），用户或管理脚本很自然会在 `runtimes.json` 里写成数字。

触发条件（已实测复现）：
1. 在 `runtimes.json` 的某个 profile 写入 `"context_size": 131072`（或 state 记录里带数字元数据）；
2. 启动该 profile（`_internal_start_profile` 原样把 `p.get("context_size")` 写进 state，见 `server.py:621-623`，不做任何归一化）；
3. 调用 `GET /api/status`。

实际影响：`InstanceView(...)` 构造抛出 `ValidationError`，而 `get_status()` 没有 try/except 保护，整个 `/api/status` 返回 HTTP 500。该接口是所有存活实例的唯一展示来源，因此 **一个 profile 的数值型元数据会导致实例清单整体不可用**（前端 `fetchStatus` 进入 catch，页面实例区/错误区异常）。这是现实条件下的重大功能回归，且直接违反 `docs/architecture.md` 中“状态接口如实汇报”的契约。

实测证据：
- 配置侧数字：`GET /api/status` → **500**
- state 记录侧数字：`GET /api/status` → **500**
- 现有测试全部使用字符串（`"160K"`、`"2K"`），故 5 个新测试无法覆盖此路径。

最小修复方向（任一）：
- 在 `get_status()` 注入处做 `str(...)` 归一化（`str(model_id) if model_id is not None else None`）；或
- 将模型字段改为 `Optional[Union[str, int, float]]` 并加 validator 归一化为字符串；或
- 至少让 `_internal_start_profile` 写入 state 前归一化。

---

### [P2] 删除接口对“存活但身份校验 error”的实例会误判为可删除，存在产生脱管实例的窗口

`server.py:723-731`

拦截逻辑仅在 `identity_status == "match"` 时返回 409：

```python
if inst.get("profile") == profile_id and not is_legacy_wrapper_state(inst):
    identity_status, _ = check_process_identity(inst.get("pid", 0), inst.get("create_time", 0))
    if identity_status == "match":
        raise HTTPException(status_code=409, ...)
```

当 `check_process_identity` 返回 `"error"`（权限不足 / 未知系统异常，`process.py:258-260`）时，循环不拦截，流程继续删除配置。而已实测复现：`identity=error` 时 `DELETE /api/profiles/p1` 返回 **200 并删除配置**。

实际影响：删除配置后，仍在运行的受管实例将失去 `profiles` 中的配置项，后续 `/api/status` 中该实例的元数据回退只能拿到空值；`get_profile_live_status()`（`server.py:168`）会因 profile 不存在而 404；用户无法再通过该 profile 名执行受管 stop，只能退回到按 PID 的 GPU 进程强杀，与本次变更“防止留下脱管孤儿”的目标相悖。项目其他安全路径（`process.py` 契约、`docs/architecture.md:91`）对 `error` 一律 Fail-Closed，此处与之不一致。

最小修复方向：把拦截条件改为 `if identity_status in ("match", "error")`（或在 error 时返回 409/503），与停止/状态路径的 Fail-Closed 语义保持一致。

---

### [P2] `save_raw_config` 在 Windows 上存在真实写失败：`os.replace` 目标文件被并发读取时抛 `WinError 5`（拒绝访问），且临时文件被遗留

`runtime_deck/config.py:47-58`

`save_raw_config` 使用 `tempfile.NamedTemporaryFile("w", dir=..., delete=False)`，Windows 下该文件在 `with` 块结束前保持打开，句柄关闭后执行 `os.replace`。实测在“并发读取 runtimes.json”场景下，`os.replace` 会以 `[WinError 5] 拒绝访问` 失败：

```
Failed to save config file ...: [WinError 5] 拒绝访问。: 'tmpXXXX' -> 'runtimes.json'
```

压力测试（1 个读线程 + 400 次写）结果：`write_failures=211`，即约一半写入在并发读取下失败（Windows 文件共享语义：读句柄未以 `FILE_SHARE_DELETE` 打开时会阻止目标被替换/删除）。

实际影响：
- 删除接口在该情形下走 `except Exception` 返回 500（映射正确），但并发读取期间删除配置会间歇性失败；
- 失败时虽进入 `except` 分支尝试清理临时文件，但 `except` 内 `os.remove` 同样可能因句柄竞争失败并被静默 `pass`，**临时文件遗留在 runtimes.json 同目录**（已实测：序列化失败路径遗留 `tmp6x2fjixz`）。

对比：`runtime_deck/state.py:82-94` 的 `save_state_atomic` 同样模式，但 state 写入被 `state_manager.lock` 串行化、读路径也持锁，冲突面小得多；而 `load_raw_config`（`config.py:23`）不持锁，`get_current_config()` 每次请求都读，因此 config 的读写竞争更频繁。

最小修复方向：写入前对目标加排他/重试，或改用“写临时文件 → 关闭 → 带重试的 `os.replace`”，或让读取方以 `FILE_SHARE_DELETE` 语义打开（例如一次性读取后立即关闭，避免长持有），并在失败时保证临时文件清理。

---

### [P3] 删除成功路径无法覆盖“并发重复删除 / 预检后被并发移除”的竞态测试缺口

`server.py:715-745`、`tests/test_profile_config_api.py:65-100`

`delete_profile_endpoint` 先 `get_current_config()` 做存在性预检（`server.py:715-718`），再 `delete_profile_config`（`config.py:63-71`）在锁内重新 `load_raw_config` 校验。锁内二次校验正确避免了 TOCTOU 误删，但并发双删时后到者会走 `KeyError → 404`。这本身可接受，但测试未覆盖：
- 两次并发 DELETE 同一 profile 的幂等性/返回码；
- 预检与 `delete_profile_config` 之间配置被移除的路径。

实测确认 `KeyError` 映射为 404、其他异常映射为 500，行为与实现一致，但无自动化测试锁定该契约。

最小修复方向：补充并发双删与“预检后移除”用例（P3，不阻断）。

---

### [P3] 前端 `deleteCurrentProfile` 的禁用态在删除后存在瞬时错误状态，且删除按钮未纳入 `focus-visible` 可访问性样式

`static/app.js:814-833`、`static/styles.css:672`、`static/styles.css:970-978`

- `deleteCurrentProfile` 在 `finally` 中无条件 `btnDelete.disabled = false`。删除成功后 `fetchConfig()` 已把按钮置为 disabled（无 profile 时，`app.js:1045-1048`），随后 `finally` 又把它重新启用，出现短暂“可点击但无目标”的窗口（点击只会弹“请先选择”）。虽无害，但状态不一致。
- `styles.css:970-978` 的 `:focus-visible` 列表包含 `btn-secondary`/`btn-save`，但新增的 `.btn-delete-profile` 未列入，键盘用户对该破坏性按钮无统一焦点环（仅有 hover 样式）。

最小修复方向：`finally` 中改为按当前 profile 数重算禁用态；把 `.btn-delete-profile:focus-visible` 加入焦点样式列表。

---

## Verification

实际执行的检查（全部在本机运行）：

| 检查 | 命令 | 结果 |
| :--- | :--- | :--- |
| 全量测试 | `python -m pytest -q` | **141 passed** |
| 目标测试 | `python -m pytest tests/test_profile_config_api.py -q` | **5 passed** |
| 相关回归 | `python -m pytest tests/test_api.py -q` | **45 passed** |
| 静态/响应式 | `pytest tests/test_script_editor_static.py tests/test_responsive.py tests/test_static_offline.py -q` | **25 passed** |
| 语法 | `python -m py_compile server.py runtime_deck/config.py runtime_deck/models.py` | OK |
| JS 语法 | `node --check static/app.js` | OK |
| diff 空白检查 | `git diff --check` | 仅 `config.py:72` 文件末尾多余空行（纯格式，不计 Finding） |

针对 Finding 的定向复现（均为实测，非推断）：

1. **P1**：`context_size=131072`（配置侧）→ `GET /api/status` = **500**；state 记录侧数字 → 同样 **500**。
2. **P2**：`check_process_identity` 返回 `("error", None)` 时 `DELETE /api/profiles/p1` = **200**（配置被删除）。
3. **P2**：`save_raw_config` 并发读取压力测试 → `write_failures=211`，报错为 `[WinError 5] 拒绝访问`；序列化失败路径遗留临时文件 `tmp6x2fjixz`。
4. **P3**：`delete_profile_config` 抛 `KeyError` → 404、抛其他异常 → 500，行为与实现一致。

未验证：真实 Windows 部署下 `cmd.exe` 包装脚本端到端删除流程、浏览器端删除按钮交互（未启动服务/浏览器做 GUI 验证）。

---

## Residual Risks

- 未在真实受管实例（llama-server / ComfyUI）运行状态下验证删除拦截与 409 文案的端到端行为，仅以 mock 身份校验覆盖。
- 未对前端 `deleteCurrentProfile` 做浏览器级验证（二次确认、禁用态、刷新后选中项重置仅在代码层审查）。
- `save_raw_config` 的 `os.replace` 竞争依赖 Windows 文件共享语义，实测在本地 Temp 目录复现；生产目录（含杀软/索引器句柄）下失败率可能不同，未做真实环境测量。
- 未评估多进程部署（多个 uvicorn worker）下 `threading.RLock` 无法跨进程保护 config 的并发写（当前实现为进程内锁，未验证是否单 worker 部署）。

---

## Gate

**FAIL**

阻断项：P1（数值型 `context_size`/`model_id`/`kv_quant` 导致 `/api/status` 整体 500，实例清单不可用）为现实条件下的重大功能回归，必须先修复并补充对应测试。

---

## Review Report

`D:\App\01_Ai\CodeX\RuntimeDeck\docs\review\2026-10-04-profile-delete-and-instance-metadata-fb74107.md`

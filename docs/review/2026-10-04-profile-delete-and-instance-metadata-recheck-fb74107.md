# Code Review（二次复查）— Profile 删除 + 受管实例元数据上报修复

- **Review Scope**: 针对上一轮 Review 的 P1/P2/P2/P3 修复进行二次复查：
  - `runtime_deck/models.py`：`ProfileConfig` / `InstanceRecord` / `InstanceView` 的 `model_id`/`context_size`/`kv_quant` 归一化（`_coerce_meta_str` + `@field_validator(mode="before")`）
  - `server.py`：`DELETE /api/profiles/{profile_id}` 的 Fail-Closed 拦截（`identity_status in ("match","error")`）
  - `runtime_deck/config.py`：`_CONFIG_LOCK` 读写互斥、`os.replace` 指数回退重试、`finally` 临时文件清理
  - `static/app.js`：`deleteCurrentProfile` 的 `finally` 禁用态重算
  - `static/styles.css`：`.btn-delete-profile:focus-visible`
  - `tests/test_profile_config_api.py`：新增测试
- **Reviewed revision**: `fb74107`（HEAD）+ 工作区未提交改动
- **审查日期**: 2026-10-04
- **审查者**: Code Reviewer（专职审查，未修改代码；仅写入本报告）

---

## 复查结论（逐项）

| 项 | 上一轮问题 | 复查结果 | 证据 |
| :--- | :--- | :--- | :--- |
| 1 | P1：数值型元数据导致 `/api/status` 500 | **已修复** | 见下 |
| 2 | P2：身份校验 error 时未 fail-closed | **已修复** | 见下 |
| 3 | P2：Windows 并发读写竞争 + 临时文件遗留 | **主要修复；残留 1 处不可达清理缺口** | 见 Finding P3-1 |
| 4 | P3：前端删除按钮禁用态 | **已修复** | `app.js:831-834` |
| 5 | P3：删除按钮 focus-visible | **已修复** | `styles.css:667-670` |
| 6 | 测试覆盖 | **已补齐，全量通过** | 143 passed |

### 1. 数值型元数据归一化（P1）

`runtime_deck/models.py:7-12` 新增 `_coerce_meta_str`，对 `None` 返回 `None`，其余 `str(v).strip()`，空串折叠为 `None`。三个模型类（`models.py:42-45`、`112-115`、`138-141`）均以 `mode="before"` 校验器接入，**在任何类型检查前**完成归一化。

实测 `_coerce_meta_str` 对 `None/int/float/bool/str/list/dict/nan/inf` 全部不抛异常。

端到端实测（`GET /api/status`）：
- state 记录侧数字（`model_id=12345, context_size=131072, kv_quant=8`）→ **200**，视图值 `"12345" / "131072" / "8"`；
- 配置侧数字且 state 缺失元数据（回退路径 `server.py:290-292`）→ **200**，`"131072" / "99"`；
- `ProfileConfig` 直接校验数字（`load_app_config`）→ 归一化为 `str`。

结论：P1 在现实触发条件下（配置/state 出现 int/float）已彻底消除 500。

### 2. 删除接口 Fail-Closed（P2）

`server.py:725` 拦截条件改为 `if identity_status in ("match", "error")`，错误文案含“正在运行或状态检查异常”。

实测四态矩阵（`DELETE /api/profiles/p1`）：

| identity_status | HTTP | 配置是否保留 |
| :--- | :--- | :--- |
| match | 409 | 保留 |
| error | 409 | 保留 |
| mismatch | 200 | 删除 |
| not_found | 200 | 删除 |

结论：`error` 已与 `match` 一致 Fail-Closed，满足安全契约。新增测试 `test_delete_profile_blocked_when_identity_check_errors` 覆盖。

### 3. config.py 并发与清理（P2）

- `_CONFIG_LOCK = threading.RLock()`（`config.py:16`）；`load_raw_config`（`config.py:21`）与 `save_raw_config`（`config.py:44`）均持锁，读写互斥。
- `save_raw_config`：`tempfile.NamedTemporaryFile(delete=False)` 写临时文件后关闭句柄，再以 `os.replace` 替换；失败按 `time.sleep(0.01*(attempt+1))` 指数回退重试至多 10 次（`config.py:55-64`）。
- `finally` 中带重试的 `os.remove` 清理（`config.py:68-76`）。

实测并发压力（1 读线程 + 400 写线程，本地 Temp 目录）：**write_failures=0**（上一轮同场景为 211），**无临时文件遗留**，最终 JSON 合法。`os.replace` 持续失败场景实测清理生效（`leftover=[]`）。

结论：并发竞争与 replace 失败清理已修复。残留清理缺口见 Finding P3-1。

### 4. 前端删除按钮禁用态（P3）

`static/app.js:831-834`：
```js
} finally {
  if (btnDelete) {
    btnDelete.disabled = !state.selectedProfile || Object.keys(state.config.profiles || {}).length === 0;
  }
}
```
删除成功后 `state.selectedProfile=''`（`app.js:824`）→ `disabled=true`，与 `renderConfigControls`（`app.js:1047-1050`）一致；失败时按当前状态重算。不再无条件置 `false`。结论：已修复。

### 5. focus-visible（P3）

`static/styles.css:667-670` 新增 `.btn-delete-profile:focus-visible { outline: 2px solid #ef4444; outline-offset: 2px; }`。结论：已修复。

### 6. 测试

`tests/test_profile_config_api.py` 含 7 个用例，含新增 `test_delete_profile_blocked_when_identity_check_errors`（134-162）与 `test_status_handles_numeric_and_arbitrary_metadata_types`（199-229）。`python -m pytest -q` → **143 passed**。

---

## Findings

### [P3] `save_raw_config` 序列化失败路径仍会遗留临时文件（`temp_name` 赋值时机过晚）

`runtime_deck/config.py:48-52`

```python
try:
    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
        json.dump(config, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name          # 仅在 dump 成功后赋值
```

若 `json.dump` 抛异常（如不可序列化对象），`temp_name` 仍为 `None`，`finally`（`config.py:69`）的 `if temp_name:` 不成立，**临时文件被遗留**。

实测复现：
```
save_raw_config(cfg, {'k': <non-serializable>}) → TypeError
leaked: ['tmposogzbme']
```

实际影响与可达性：本轮唯一的 `save_raw_config` 调用方是 `delete_profile_config`（`config.py:88`），其入参来自 `json.load`，**必然可序列化**，因此该路径在生产代码中当前不可达，属防御性缺口（与上一轮 P2“避免临时文件遗留”的目标部分未完全闭合）。

最小修复方向：先取 `temp_name = tf.name`，再 `json.dump`；或在 `except` 分支也记录并清理 `tf.name`。

---

### [P3] 同类归一化未覆盖 `InstanceView` 其余字符串字段，非字符串仍会导致 `/api/status` 500

`server.py:295-314`、`runtime_deck/models.py:118-141`

本轮仅对 `model_id`/`context_size`/`kv_quant` 归一化。`InstanceView` 的 `name`/`gpu`/`gpu_name`/`profile` 仍为 `str`，`create_time` 为 `float`，均直接取自 state/config 原始值，未做类型归一化。

实测（`GET /api/status`，`raise_server_exceptions=False`）：

| 注入的非字符串字段 | 结果 |
| :--- | :--- |
| `name: 123` | 500 |
| `gpu: 0` | 500 |
| `gpu_name: 7` | 500 |
| `profile: 99` | 500 |
| `create_time: "abc"` | 500 |
| `process_role: 123` | 500 |

触发条件：用户在 `runtimes.json` 或 state 记录中把上述字段写成数字（`name`/`gpu` 由 `_internal_start_profile` 从配置原样带入 state，见 `server.py:608-614`）。

实际影响：与 P1 同一失败模式（一个字段类型异常导致实例清单整体 500）。可达性弱于 P1——`name`/`gpu`/`gpu_name` 写成数字在现实中不自然，故严重度低于 P1。属同类根因未完全收敛，建议后续统一对 `InstanceView` 全部展示字段做 `str(...)` 归一化或在构造处兜底。

---

## Verification

实际执行的检查（均在本机运行）：

| 检查 | 命令 | 结果 |
| :--- | :--- | :--- |
| 全量测试 | `python -m pytest -q` | **143 passed** |
| 目标测试 | `python -m pytest tests/test_profile_config_api.py -q` | **7 passed** |
| 语法 | `python -m py_compile server.py runtime_deck/config.py runtime_deck/models.py` | OK |
| JS 语法 | `node --check static/app.js` | OK |
| diff 空白 | `git diff --check` | 仅 `config.py:90` 文件末尾多余空行（纯格式，不计 Finding） |

定向实测（非推断）：

1. P1 归一化：`_coerce_meta_str` 对 `None/int/float/bool/str/list/dict/nan/inf` 均不抛；state 侧数字 / 配置侧数字回退 → `/api/status` 均 **200**，值被转为字符串。
2. P2 Fail-Closed 四态矩阵：`match`/`error` → 409 且配置保留；`mismatch`/`not_found` → 200 且删除。
3. config 并发：1 读 + 400 写 → `write_failures=0`、无临时文件遗留、最终 JSON 合法（上一轮同场景 211 失败）。
4. config 清理：`os.replace` 持续失败 → 抛 `OSError` 且 `leftover=[]`；序列化失败 → 遗留 `tmposogzbme`（Finding P3-1）。
5. 其他字段类型探测：见 Finding P3-2 表格（均 500）。
6. 前端：`app.js:831-834` 与 `styles.css:667-670` 代码级确认。

未验证：真实 Windows 部署下（含杀软/索引器句柄）的 `os.replace` 竞争率；浏览器端删除按钮交互与 focus 环的 GUI 实测；多 uvicorn worker（跨进程）下的 config 写并发。

---

## Residual Risks

- `_CONFIG_LOCK` 为进程内锁，多 worker / 多进程部署下不提供跨进程保护（未验证部署形态）。
- `os.replace` 重试期间持有 `_CONFIG_LOCK`，最坏约 0.55s 阻塞并发读；属延迟影响，非正确性问题。
- 前端 `finally` 禁用态、focus-visible 仅代码级审查，无自动化/浏览器验证。
- Finding P3-2 的其余字段归一化缺口在“非自然输入”下才可达，未纳入本轮阻断。

---

## Gate

**PASS WITH NON-BLOCKING FINDINGS**

四项请求修复（P1 数值元数据 500、P2 删除 Fail-Closed、P2 config 并发与清理、P3 前端禁用态与 focus-visible）均经实测确认修复，全量 143 测试通过。剩余两项 P3 分别为“生产不可达的临时文件清理缺口”与“其余字段同类归一化未覆盖（触发不自然）”，均不阻断 Gate。

---

## Review Report

`D:\App\01_Ai\CodeX\RuntimeDeck\docs\review\2026-10-04-profile-delete-and-instance-metadata-recheck-fb74107.md`

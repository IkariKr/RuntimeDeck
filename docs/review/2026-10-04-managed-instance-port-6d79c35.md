# Code Review — 受管实例端口号信息（Managed Instance Port）

- **Review Scope**:
  - `runtime_deck/models.py`：新增 `_coerce_port`（类型/范围归一化）与 `ProfileConfig` / `InstanceRecord` / `InstanceView` 的 `port: Optional[int]` + `@field_validator("port", mode="before")`
  - `server.py`：`get_status()` 注入 `port = inst.get("port") or resolve_profile_port(prof_meta)`；`_internal_start_profile()` 状态记录写入 `"port": resolve_profile_port(p)`
  - `static/app.js`：`renderInstances` 渲染 `端口:` 元数据
  - `tests/test_profile_config_api.py`：`test_status_reports_model_metadata` 新增 `inst_view["port"] == 8080`
- **Reviewed revision**: `6d79c35`（HEAD）+ 工作区未提交改动
- **审查日期**: 2026-10-04
- **审查者**: Code Reviewer（专职审查，未修改代码；仅写入本报告）

---

## Findings

### [P3] `_coerce_port` 未捕获 `OverflowError`，`inf` 端口会击穿防御并导致 `/api/status` 500

`runtime_deck/models.py:15-23`、`server.py:293`

```python
def _coerce_port(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        p = int(v)
        return p if 1 <= p <= 65535 else None
    except (ValueError, TypeError):
        return None
```

`int(float('inf'))` 抛出的是 `OverflowError`，不在 `except (ValueError, TypeError)` 覆盖范围内，异常会向外逃逸。该函数被声明为“对端口类型及 1~65535 范围进行合法性归一化与防御”，但 `inf` / `-inf` 恰恰未被防御。

实测（本机）：

```
_coerce_port(float('inf'))  -> OverflowError: cannot convert float infinity to integer
_coerce_port(float('-inf')) -> OverflowError
```

`json.loads` 会把 `Infinity` / `1e999` 解析为 `float('inf')`，因此 `runtimes.json` 或 state 记录中 `"port": 1e999` 是可达输入。

新增的 `server.py:293` 调用把该异常引入只读状态接口：

```
GET /api/status（state 侧 port=1e999）-> HTTP 500
GET /api/status（config 侧 port=1e999）-> HTTP 500
```

其中 config 侧为本轮**新引入的回归面**：变更前 `get_status()` 不调用 `resolve_profile_port`（`resolve_profile_port` 同样只 `except (ValueError, TypeError)`，`ports.py:31-37` 对 `inf` 同样抛 `OverflowError`），该输入下 `/api/status` 原为 200。现在一个畸形 port 会让整个实例清单接口 500。

实际影响：仪表盘状态接口整体失败（而非仅该实例），属可用性影响。触发条件需配置/状态中出现 `Infinity`/`1e999` 这类非标准端口值，现实可达性低，故定级 P3。

最小修复方向：`_coerce_port` 的 `except` 增加 `OverflowError`（或统一捕获 `Exception`）；`resolve_profile_port` 的两处 `int()` 同样处理。例如：

```python
except (ValueError, TypeError, OverflowError):
    return None
```

### [P3] `or` 短路发生在类型归一化之前，非法 state 端口会掩盖有效的 profile 端口

`server.py:293`、`runtime_deck/models.py:161-164`

```python
port = inst.get("port") or resolve_profile_port(prof_meta)
```

`inst.get("port")` 的原始值先参与真值判断，`InstanceView` 的 `normalize_port` 在**其后**才做范围裁剪。因此当 state 中的 port 为越界真值（如 `99999`）时，短路选中该越界值，随后被 `_coerce_port` 归一化为 `None`，而不会回退到 profile 的有效端口。

实测：

```
config port=8080, state port=99999 -> 视图 port = None（应为 8080）
config port=8080, state port=0     -> 视图 port = 8080（0 为假值，走了回退）
```

实际影响：前端显示“未知”，而配置里其实有可用端口，展示与实际不一致。触发条件为 state 记录出现越界端口；正常启动路径写入的 `resolve_profile_port(p)` 已保证 1~65535，故现实可达性低。定级 P3。

最小修复方向：先归一化再回退，例如 `port = _coerce_port(inst.get("port")) or resolve_profile_port(prof_meta)`（或将 `_coerce_port` 提到 `server` 侧调用）。

### [P3] 新增归一化与状态持久化缺少直接测试覆盖

`tests/test_profile_config_api.py:197`

本轮仅新增一处断言 `inst_view["port"] == 8080`（走的是 profile 元数据回退路径）。以下新行为无任何测试：

- `_coerce_port` / `normalize_port` 对 `None` / 字符串数字 / 越界 / 非数字 / `inf` 的边界行为（`grep _coerce_port tests/` 无命中）；
- `_internal_start_profile` 向 state 写入 `"port"` 的持久化（`tests/test_api.py` 启动用例未断言 `record["port"]`）；
- `port` 在 state 与 config 同时存在时的优先级。

属真实覆盖缺口，定级 P3，不阻断 Gate。

---

## Verification

实际执行的检查（均在本机运行）：

| 检查 | 命令 | 结果 |
| :--- | :--- | :--- |
| 全量测试 | `python -m pytest -q` | **143 passed** |
| 语法 | `python -m py_compile server.py runtime_deck/models.py` | OK |
| JS 语法 | `node --check static/app.js` | OK |
| diff 空白 | `git diff --check` | 仅 CRLF 提示，无空白错误 |
| 前端类名 | `grep font-mono/meta-value static/styles.css` | 两处均存在（`styles.css:465,470`） |

定向实测（非推断）：

1. `_coerce_port` 探针：`None/int/str/float/nan/list/dict/bool` 均安全；`inf` / `-inf` → `OverflowError`（Finding P3-1）。
2. `/api/status` 回退路径：`config=8080,state=None → 8080`；`config=None,state=None → 8080`（llama 启发式）；`config=8080,state="8080" → 8080`；`config=8096,state=8080 → 8080`（state 优先）。
3. `/api/status` 畸形输入：state 侧与 config 侧 `port=1e999` → 均 **500**（Finding P3-1）。
4. 越界 state 端口：`config=8080,state=99999 → None`（Finding P3-2）。
5. 已删除 profile 的实例：`profiles={}, state.port=8080 → 8080`（state 兜底生效，未因 profile 缺失 500）。
6. 契约兼容：`/api/status` 为附加字段，非 `/api/v1` 稳定契约；`test_safety_release.py` 仅对 `/api/v1` 做严格字段白名单（`test_safety_release.py:286,299`），新增 `port` 不触碰该契约；无测试断言实例字段全集，未发现契约破坏。

未验证：真实浏览器下“端口”行的渲染与响应式换行（仅代码级确认类名存在）；真实 Windows 部署中 `resolve_profile_port` 读配置的并发时序；多 worker 部署形态。

---

## Residual Risks

- `_coerce_port` 与 `resolve_profile_port` 对 `inf` 的 `OverflowError` 属同一根因，本轮仅新增了 `get_status()` 这一只读暴露面，未收敛根因；若其他调用方传入 `inf` 仍会异常。
- 前端 `inst.port || profileCfg.port || '未知'` 的回退与后端已做的回退重复，属冗余（无害），但两侧回退优先级需保持一致，当前一致。
- 本轮未做浏览器 GUI 实测，端口行的视觉换行/对齐未验证。

---

## Gate

**PASS WITH NON-BLOCKING FINDINGS**

功能主路径正确：`_internal_start_profile` 持久化端口、`get_status` 回退注入、前端渲染均按预期工作；全量 143 测试通过；`/api/status` 仅新增可选字段，未破坏稳定 `/api/v1` 契约，无回归（正常输入下）。三项 P3 分别为 `inf` 未防御（现实可达性低）、非法 state 端口掩盖有效 profile 端口、以及测试覆盖缺口，均不阻断 Gate。

---

## Review Report

`D:\App\01_Ai\CodeX\RuntimeDeck\docs\review\2026-10-04-managed-instance-port-6d79c35.md`

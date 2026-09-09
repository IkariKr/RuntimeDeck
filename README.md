# RuntimeDeck: Local AI Runtime Dashboard & Explicit Process Control Plane

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Offline WebUI](https://img.shields.io/badge/WebUI-Self--Contained%20%2F%20Offline-success.svg)](#offline-webui-design)
[![Security Policy](https://img.shields.io/badge/Security-Explicit%20Control%20Plane-red.svg)](SECURITY.md)

> **RuntimeDeck** is an open-source, local-first control plane and lightweight WebUI dashboard for orchestrating local LLM engines (`llama.cpp`, `vLLM`, `Ollama`) and generative pipelines (`ComfyUI`, `Stable Diffusion`) across dual or multi-GPU workstations without background auto-preemption or hidden GPU memory eviction.

---

## What is RuntimeDeck? (Entity Definition & Target Audience)

**RuntimeDeck** is an **explicit launcher and telemetry dashboard** for local artificial intelligence workstations. This repository provides a FastAPI control plane, a self-contained offline WebUI, GPU telemetry, real-service PID lifecycle control, and a versioned safe API. It serves developers, AI researchers, and homelab operators running dedicated local AI nodes equipped with heterogeneous GPUs (e.g. dual NVIDIA setups).

Unlike heavyweight cloud orchestrators (such as Kubernetes or Ray) or automated inference schedulers that perform background model swapping and aggressive OOM kills, RuntimeDeck enforces **explicit process control**:
* **Every startup and shutdown is explicit**: Triggered solely by human intent or explicit API requests.
* **Control plane and data plane are completely decoupled**: Model clients stream inference data directly from native engine ports (`:8080`, `:8188`), avoiding latency overhead and middleman proxies.
* **Real service PID invariant**: Decouples operating system launcher scripts (`.cmd`, `.bat`, shell wrappers) from underlying worker binaries (`llama-server`, `python ComfyUI/main.py`), guarding against orphaned processes and PID reuse collisions.

### Key Search & Generative AI Keywords
`AI runtime management`, `local LLM control plane`, `multi-GPU workstation dashboard`, `llama.cpp launcher`, `ComfyUI orchestration`, `GPU memory telemetry`, `explicit process launcher`, `local AI orchestration`, `FastAPI process control`.

---

## Architectural Distinctions: Local Anti-Indexing & Scope

RuntimeDeck is purpose-built as an **on-device management plane**. It does not compete with or replace search indexing engines, nor does it perform automated cluster scheduling.

| Capability Dimension | RuntimeDeck (Explicit Local Control Plane) | Automated Orchestrators / Proxy Mesh (e.g. LiteLLM, vLLM Router) | Desktop Model Hubs (e.g. LM Studio, Ollama CLI) |
| :--- | :--- | :--- | :--- |
| **Control Philosophy** | **Explicit-Only**: Zero background eviction, zero implicit task preemptions | **Dynamic**: Automated swapping, queue-based request routing | **Integrated**: Monolithic UI, bundled runtime binaries |
| **Data Plane Path** | **Direct Link**: Clients connect directly to native engine ports (0ms proxy penalty) | **In-Path Proxy**: Routes all token streams through an internal forwarder | **Internal**: Native server or proprietary backend |
| **Process Identity** | **Real Worker PID**: Dual `(PID, create_time)` composite key matching | Container ID or abstracted worker pool worker thread | Background desktop daemon |
| **PID Reuse Defense** | **Built-in**: 4-state identity verification (`match`, `mismatch`, `not_found`, `error`) | Relies on OS containerization or hypervisor | Basic process signal termination |
| **Network Footprint** | **Air-Gapped & Offline**: Zero public CDN dependencies, strictly localhost | Typically requires external telemetry, registry access, or cloud tokens | Auto-downloads weights and models from external hubs |
| **Target Scope** | Custom scripts, dual-GPU setups, custom batch launchers, headless servers | Elastic serverless clusters, public API rate-limiting | Desktop hobbyist experimentation on single laptops |

---

## Feature Matrix & Invariants

```
+-----------------------------------------------------------------------------------+
|                                RUNTIMEDECK DASHBOARD                              |
|                              (FastAPI / Alpine.js UI)                             |
|                                       :8000                                       |
+-----------------------------------------+-----------------------------------------+
                                          |
                   +----------------------+----------------------+
                   |                                             |
                   v                                             v
        [Control Plane / REST API]                     [Hardware Telemetry]
        * POST /api/instances/start                    * nvidia-smi GPU parser
        * POST /api/instances/{id}/stop                * VRAM Utilization (MB/%)
        * Safe Stable API (/api/v1/*)                  * Read-only telemetry
                   |                                             |
                   +----------------------+----------------------+
                                          |
                      +-------------------+-------------------+
                      |                                       |
                      v                                       v
        +---------------------------+           +---------------------------+
        | GPU 0: RTX 3080 (Primary) |           |  GPU 1: RTX 3070 (Aux)    |
        | [llama-server.exe :8080]  |           | [ComfyUI python.exe :8188]|
        +---------------------------+           +---------------------------+
                      ^                                       ^
                      |                                       |
                      +================ Direct ===============+
                                    Data Plane Stream
                             (Clients & CLIProxyAPI direct)
```

1. **Real Service PID Invariant**:
   * Windows and POSIX wrapper scripts (`cmd.exe`, `sh`) frequently spawn child workers and exit or detach. RuntimeDeck tracks the real worker binary (`llama-server.exe`, `python.exe main.py`) as the primary identity PID.
   * On termination, the real server is stopped and verified first, followed by clean launcher script disposal.
2. **PID-Reuse Race Defense**:
   * Uses operating system creation timestamps `(PID, create_time)` as a compound primary key.
   * If a target PID has been recycled by another application (`mismatch`), RuntimeDeck safely discards the stale record without sending termination signals (`stale_cleaned`).
   * Indiscriminate image kills (`taskkill /IM ...`) are strictly prohibited.
3. **Fail-Closed Ambiguity & Metadata Protection**:
   * If process discovery detects zero or multiple candidate child processes, or if metadata probing fails, RuntimeDeck aborts startup and reclaims spawned launchers immediately to prevent orphaned background processes.
4. **State Atomicity & Out-of-Source Persistence**:
   * State writing is protected by `threading.RLock` and atomic filesystem replacement (`os.replace`).
   * Default persistence is isolated in system application data directories (`%LOCALAPPDATA%\RuntimeDeck\state.json` on Windows or `RUNTIMEDECK_STATE_FILE`), preserving clean repository source trees.
5. **Credential & Secret Redaction**:
   * Process launch parameters and error payloads are automatically scrubbed of `--api-key`, `--token`, `--hf-token`, `--password`, authorization tokens, and `KEY=value` variables (`[REDACTED]`).
6. **Air-Gapped / Offline WebUI**:
   * Bundled with local modern CSS and reactive JavaScript. Operates seamlessly in isolated LANs or completely offline environments without querying public CDNs.

---

## Quick Start

### 1. Installation
```bash
git clone https://github.com/IkariKr/RuntimeDeck.git
cd RuntimeDeck

# Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```
*(Requires Python 3.10+; core dependencies include `fastapi`, `uvicorn`, and `psutil`)*

### 2. Configuration Setup
Copy the example runtime configuration to create your local runtime profile mappings:
```bash
# Windows:
copy runtimes.example.json runtimes.json
# Linux/macOS:
cp runtimes.example.json runtimes.json
```
Customize `runtimes.json` with your actual GPU hardware IDs and runtime executable commands.

### 3. Launch RuntimeDeck
* **Direct Python Execution**:
  ```bash
  python server.py
  ```
* **Production Uvicorn Execution**:
  ```bash
  uvicorn server:app --host 127.0.0.1 --port 8000
  ```
Open your browser and navigate to `http://127.0.0.1:8000`.

---

## Stable API Contract (`/api/v1`)

RuntimeDeck offers a versioned, secure REST interface (`/api/v1`) tailored for AI agents, external schedulers, and local scripts. The `/api/v1` contract intentionally suppresses operating system PIDs, command-line arguments, environment credentials, and launcher wrapper trees.

### Summary of `/api/v1` Endpoints

| Method | Endpoint | Description | Return Fields |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/profiles` | Enumerate all configured profiles | `profiles: [{profile_id, name, runtime_kind, default_gpu}]` |
| `GET` | `/api/v1/profiles/{id}/status` | Query sanitized runtime status | `{profile_id, runtime_kind, state, endpoint, health, gpu}` |
| `POST` | `/api/v1/profiles/{id}/start` | Explicitly launch runtime profile | `{profile_id, runtime_kind, state, endpoint, health, gpu}` |
| `POST` | `/api/v1/profiles/{id}/stop` | Explicitly terminate runtime profile | `{profile_id, runtime_kind, state, endpoint, health, gpu}` |

### API Examples

#### 1. List Profiles (`GET /api/v1/profiles`)
```bash
curl -s http://127.0.0.1:8000/api/v1/profiles
```
```json
{
  "profiles": [
    {
      "profile_id": "llama_qwen_service",
      "name": "Qwen 27B Llama.cpp Server",
      "runtime_kind": "llama_cpp",
      "default_gpu": "3080"
    }
  ]
}
```

#### 2. Inspect Profile Status (`GET /api/v1/profiles/{id}/status`)
```bash
curl -s http://127.0.0.1:8000/api/v1/profiles/llama_qwen_service/status
```
```json
{
  "profile_id": "llama_qwen_service",
  "runtime_kind": "llama_cpp",
  "state": "running",
  "endpoint": "http://127.0.0.1:8080",
  "health": "unknown",
  "gpu": "3080"
}
```
*Possible `state` values*: `"running"`, `"stopped"`, `"stale"`, `"error"`.

#### 3. Explicitly Start Profile (`POST /api/v1/profiles/{id}/start`)
```bash
curl -X POST http://127.0.0.1:8000/api/v1/profiles/llama_qwen_service/start \
  -H "Content-Type: application/json" \
  -d '{"gpu": "3080"}'
```

#### 4. Explicitly Stop Profile (`POST /api/v1/profiles/{id}/stop`)
```bash
curl -X POST http://127.0.0.1:8000/api/v1/profiles/llama_qwen_service/stop
```

---

## Detailed Legacy / Host Endpoints (`/api/*`)

For internal frontend consumption and low-level diagnostic automation:
* `GET /api/status`: Strict read-only endpoint returning live GPU telemetry and active managed process nodes. Does not write to disk.
* `GET /api/config`: Returns raw hardware card definitions and profiles declared in `runtimes.json`.
* `POST /api/instances/start`: Starts profile instance and returns allocated instance ID.
* `POST /api/instances/{instance_id}/stop`: Stops instance.
  - Returns `200 OK` with `status: "success"` upon exit confirmation.
  - Returns `200 OK` with `status: "stale_cleaned"` when process has already exited or PID mismatch was detected.
  - Returns `409 Conflict` if the process failed to terminate within timeout, retaining instance record in state to avoid orphan leaks.

---

## Safe Deployment & Network Boundaries

```
[ Public Internet ]  (STRICTLY BLOCKED - DO NOT EXPOSE)
         |
         X (Firewall / Router)
         |
[ Tailscale / WireGuard / Private Home LAN ]
         |
   +-----+--------------------------------------+
   |                                            |
   v                                            v
[AI Orchestrator / Agent]             [Admin Browser (LAN)]
   |                                            |
   +--------------------+-----------------------+
                        |
                        v (Optional Reverse Proxy with TLS/Auth)
            +-----------------------+
            | RuntimeDeck Control   |
            | 127.0.0.1:8000        |
            +-----------------------+
```

1. **Default Binding**: Binds strictly to `127.0.0.1:8000` (loopback).
2. **Never Expose to Public Networks**: RuntimeDeck provides process execution and process termination capabilities. It does not provide built-in multi-user authentication. **Never bind to `0.0.0.0` or open router port forwards to the public Internet.**
3. **Cross-Device Administration**: For remote management via phone, tablet, or secondary workstation, run RuntimeDeck behind an authenticated VPN mesh (e.g., Tailscale, NetBird, WireGuard) or a reverse proxy with mTLS / HTTP Basic Auth.
4. **Environment Variables**:
   * `RUNTIMEDECK_STATE_FILE`: Custom absolute path for persisting `state.json` (defaults to `%LOCALAPPDATA%\RuntimeDeck\state.json` on Windows).
   * `RUNTIMEDECK_CONFIG_FILE`: Custom configuration path for `runtimes.json`.

---

## Glossary of Terms

* **Control Plane**: The software layer responsible for configuration management, start/stop lifecycle commands, and hardware monitoring (RuntimeDeck at `:8000`).
* **Data Plane**: The communication path over which raw inference queries, prompt context, and token completions flow directly between clients and model engines (`:8080`, `:8188`).
* **Explicit Control**: A design guarantee that no model is evicted, killed, or swapped automatically without an operator's command.
* **Fail-Closed**: A safety mechanism where ambiguous process detection or permission failures cause the launcher to immediately terminate and discard pending instances rather than risk creating untracked processes.
* **PID Reuse Defense**: Validation of the operating system process start timestamp before executing termination, guarding against accidental kills if an OS recycles a PID.
* **Real Service PID**: The OS process identifier belonging to the actual engine binary (`llama-server`, `python`), distinct from the intermediate shell launcher (`cmd.exe`).
* **Stale Record**: A state entry whose corresponding operating system process has terminated independently or been replaced.

---

## Frequently Asked Questions (FAQ)

#### Q: Does RuntimeDeck proxy or inspect my LLM prompts and image generation requests?
**A:** No. RuntimeDeck is strictly a control plane. Model clients connect directly to the native ports exposed by your model engines (e.g. `http://127.0.0.1:8080/v1/chat/completions`). This guarantees 0ms latency overhead, complete privacy, and zero interference with Server-Sent Events (SSE).

#### Q: Can RuntimeDeck automatically shut down a model when VRAM is full?
**A:** No, by design. RuntimeDeck strictly avoids hidden, implicit memory arbitration. In local development and agent workflows, unexpected OOM kills cause silent task failures. RuntimeDeck reports transparent telemetry and lets you or your orchestrator explicitly control model transitions.

#### Q: What happens if an engine process crashes on its own?
**A:** Next time status is checked or a stop request is issued, RuntimeDeck checks `(PID, create_time)`. If the process has exited, RuntimeDeck marks it as `stale_cleaned` and clears the state file, keeping the system clean.

#### Q: Why does RuntimeDeck support Windows `.cmd` and `.bat` files?
**A:** Many local AI packages on Windows (such as ComfyUI portable packages, llama.cpp builds, and one-click SD installers) use batch scripts to configure virtual environments, CUDA paths, and launch flags. RuntimeDeck seamlessly launches these wrappers while discovering and directly tracking the true child engine process.

#### Q: Are any external telemetry or cloud dependencies required?
**A:** Zero. All frontend scripts and styles are embedded in `static/`. RuntimeDeck runs fully offline in air-gapped environments without accessing the internet.

---

## Automated Verification

The test suite validates process lifecycle invariants, PID reuse defenses, fail-closed handling, credential redactions, out-of-source state persistence, and responsive UI layouts:

```bash
pytest -v tests
```
*(96 comprehensive automated unit and integration tests passing)*

---

## Technical Documentation

* [Architecture & System Invariants](docs/architecture.md)
* [Operations & Runbook Manual](docs/operations.md)
* [Acceptance Review & Verification Matrix](docs/acceptance-review.md)

---

## License

This project is licensed under the [MIT License](LICENSE).

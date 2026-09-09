# Contributing to RuntimeDeck

Thank you for your interest in contributing to RuntimeDeck! RuntimeDeck is an explicit, local-first control plane for managing local AI runtimes (such as `llama.cpp`, `vLLM`, `Ollama`, and `ComfyUI`) across dual/multi-GPU setups.

---

## Code of Conduct

* Be respectful, collaborative, and constructive.
* Focus on correctness, reproducibility, process safety, and clear system boundaries.

---

## Architecture Principles to Respect

When contributing code or documentation, please keep our core invariants in mind:

1. **Explicit over Implicit**:
   * Never implement implicit process preemptions, hidden memory arbitration, or automatic killing of background services based on heuristic thresholds.
   * Actions must be explicitly triggered by human operators or explicit API calls.
2. **Real Service PID Invariant**:
   * Distinguish between startup wrappers (`cmd.exe`, `.bat`, shell scripts) and the underlying server process (`llama-server`, `python ComfyUI/main.py`). The real worker process PID must remain the primary identity key.
3. **PID Reuse Defense**:
   * Process termination must check `(PID, create_time)` composite identity. Never use wildcard image kills (`taskkill /IM` or indiscriminate `pkill`).
4. **Data Plane vs. Control Plane Separation**:
   * RuntimeDeck does not proxy LLM generation streams or inference payloads. Clients communicate directly with native runtime endpoints (`:8080`, `:8188`, etc.).
5. **Offline & Self-Contained**:
   * The WebUI and API must remain functional without internet access. Do not add runtime dependencies on public CDNs.
6. **Credential Privacy**:
   * Never expose raw command lines or unredacted tokens in API responses, logs, or error payloads.

---

## Development Setup

1. **Clone and create a virtual environment**:
   ```bash
   git clone https://github.com/your-org/RuntimeDeck.git
   cd RuntimeDeck
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-asyncio httpx
   ```

3. **Configure a local runtime template**:
   ```bash
   cp runtimes.example.json runtimes.json
   # Edit runtimes.json with your local GPU indices and test paths
   ```

4. **Run the test suite**:
   ```bash
   pytest -v
   ```

---

## Pull Request Guidelines

* **Tests**: Any change affecting process tracking, API schemas, or state persistence must include matching tests under `tests/`.
* **Sanitization**: Ensure no local absolute paths, private model storage locations, or hardware UUIDs are committed to repository files.
* **Documentation**: If you change configuration keys or API contracts, update both `README.md` and relevant files under `docs/`.

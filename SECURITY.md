# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| < 1.0   | :x:                |

---

## Threat Model & Security Boundaries

RuntimeDeck is an **explicit, local-first control plane** designed for AI workstations. Because RuntimeDeck manages operating system processes and hardware telemetry, security boundaries must be strictly observed:

1. **Localhost and Private Network Only**:
   * RuntimeDeck executes local processes specified in `runtimes.json`.
   * **Do not expose RuntimeDeck directly to the public internet** or bind to `0.0.0.0` without strict upstream reverse-proxy authentication (e.g. mTLS, WireGuard, Tailscale, or HTTP Basic Auth with TLS).
   * Default binding is strictly `127.0.0.1:8000`.

2. **No Public Multi-Tenant Isolation**:
   * Any client with HTTP access to the control plane can start and stop pre-configured runtime profiles. Access to RuntimeDeck must be treated with administrative trust.

3. **Credential & Secret Protection**:
   * RuntimeDeck implements automated in-memory redaction for process command lines and query parameters matching common secret patterns (`--api-key`, `--token`, `--hf-token`, `--password`, authorization headers, and `KEY=value`).
   * The `/api/status` and `/api/v1/*` endpoints intentionally suppress raw command lines and physical process arguments.
   * Do not commit real tokens, model licensing keys, or private filesystem structures to public version control. Use environment variables or local ignored files.

4. **Process Safety Invariants**:
   * RuntimeDeck uses dual `(PID, create_time)` composite identity keys to prevent PID-reuse race conditions.
   * Wildcard process kills (`taskkill /IM ...` or `killall`) are forbidden by design; terminations target only explicitly matched process trees.

---

## Reporting a Vulnerability

If you discover a security vulnerability or credential leakage risk in RuntimeDeck, please report it responsibly:

* **Email**: Send details to `security@runtimedeck.local` (or file a confidential GitHub Security Advisory via your repository host).
* Please include:
  - Description of the issue and potential impact
  - Minimal reproducible example or curl commands
  - Affected operating system and environment details
* We ask that you do not open public GitHub issues for unresolved critical vulnerabilities.
* You will receive an initial response acknowledging receipt within 48 hours, followed by patch validation and release coordination.

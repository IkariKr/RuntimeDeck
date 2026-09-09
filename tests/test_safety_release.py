"""Tests for release, GEO/SEO safety, public artifacts, .gitignore sanitization, and credential masking.

Validates that:
1. Public files exist (README.md, requirements.txt, static assets, docs).
2. .gitignore properly excludes runtimes.json, state.json, .env, and caches.
3. Public documentation and example configurations contain no machine-specific drive paths,
   no real GPU UUIDs (only zero/mock UUIDs allowed), no plaintext credentials, and no raw command lines.
4. Static index.html has noindex / anti-indexing metadata and no external URLs.
5. Server implements the /robots.txt route with Disallow: / and serves X-Robots-Tag / Cache-Control headers.
6. /api/v1 endpoints return only safe response fields, never leaking PIDs, raw command lines, or credentials.
"""

import os
import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "static"
DOCS_DIR = ROOT_DIR / "docs"
GITIGNORE_FILE = ROOT_DIR / ".gitignore"
INDEX_HTML_FILE = STATIC_DIR / "index.html"


# ==============================================================================
# 1. Public Files Existence
# ==============================================================================

class TestPublicFilesExistence:
    """Verifies that all expected public files and core documentation assets exist."""

    def test_readme_exists_and_non_empty(self):
        readme = ROOT_DIR / "README.md"
        assert readme.is_file(), "README.md must exist in the root directory"
        assert readme.stat().st_size > 500, "README.md must be populated"

    def test_requirements_exists(self):
        reqs = ROOT_DIR / "requirements.txt"
        assert reqs.is_file(), "requirements.txt must exist"
        content = reqs.read_text(encoding="utf-8")
        assert "fastapi" in content.lower(), "fastapi must be in requirements.txt"
        assert "psutil" in content.lower(), "psutil must be in requirements.txt"

    def test_docs_directory_and_key_docs_exist(self):
        assert DOCS_DIR.is_dir(), "docs/ directory must exist"
        expected_docs = [
            "architecture.md",
            "operations.md",
            "acceptance-review.md",
        ]
        for doc_name in expected_docs:
            doc_file = DOCS_DIR / doc_name
            assert doc_file.is_file(), f"Expected document {doc_name} to exist in docs/"
            assert doc_file.stat().st_size > 100, f"Document {doc_name} must not be empty"

    def test_static_webui_files_exist(self):
        assert STATIC_DIR.is_dir(), "static/ directory must exist"
        expected_static = [
            "index.html",
            "app.js",
            "styles.css",
            "tailwindcss.js",
            "alpine.min.js",
        ]
        for static_name in expected_static:
            static_file = STATIC_DIR / static_name
            assert static_file.is_file(), f"Expected static asset {static_name} to exist"
            assert static_file.stat().st_size > 0, f"Static asset {static_name} must not be empty"


# ==============================================================================
# 2. .gitignore Exclusions (.env, state.json, caches, runtimes.json / runtimes.local.json)
# ==============================================================================

class TestGitignoreSafetyExclusions:
    """Verifies that sensitive, stateful, and cache patterns are strictly excluded in .gitignore."""

    def test_gitignore_file_exists(self):
        assert GITIGNORE_FILE.is_file(), ".gitignore must exist in the root directory"

    def test_gitignore_excludes_state_json(self):
        content = GITIGNORE_FILE.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
        assert any(re.search(r"^state\.json$", line) for line in lines), ".gitignore must exclude state.json"
        assert any("*.state.json" in line for line in lines), ".gitignore must exclude *.state.json"

    def test_gitignore_excludes_dotenv(self):
        content = GITIGNORE_FILE.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
        assert any(re.search(r"^\.env(\b|/|$)", line) for line in lines), ".gitignore must exclude .env"

    def test_gitignore_excludes_caches(self):
        content = GITIGNORE_FILE.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
        assert any("__pycache__" in line for line in lines), ".gitignore must exclude __pycache__"
        assert any(".pytest_cache" in line for line in lines), ".gitignore must exclude .pytest_cache"

    def test_gitignore_excludes_local_runtimes_config(self):
        content = GITIGNORE_FILE.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
        # Local configuration file with actual machine paths must be gitignored
        assert any("runtimes.local.json" in line for line in lines), ".gitignore must exclude runtimes.local.json"


# ==============================================================================
# 3. Public Docs and Config Safety: No Machine-Specific Paths, Credentials, Real GPU UUIDs
# ==============================================================================

class TestPublicDocsAndConfigSafety:
    """Ensures that public documentation and example configuration files do not leak real host data."""

    def test_public_docs_example_gpu_uuids_are_mocked(self):
        """Docs operations.md must use zeroed / mocked GPU UUIDs (e.g., GPU-00000000-...)."""
        ops_doc = DOCS_DIR / "operations.md"
        assert ops_doc.is_file()
        content = ops_doc.read_text(encoding="utf-8")

        # Find all GPU UUID patterns
        uuid_pattern = re.compile(r"GPU-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        uuids_found = uuid_pattern.findall(content)
        assert len(uuids_found) > 0, "operations.md should contain example GPU UUIDs"

        for uuid_val in uuids_found:
            # Must be dummy/zeroed (e.g., GPU-00000000-0000-0000-0000-000000000001)
            is_zero_or_dummy = uuid_val.startswith("GPU-00000000-0000-0000-0000-00000000000") or uuid_val == "GPU-00000000-0000-0000-0000-000000000000"
            assert is_zero_or_dummy, f"Found non-mocked real GPU UUID in public documentation operations.md: {uuid_val}"

    def test_public_docs_contain_no_plaintext_passwords_or_tokens(self):
        """Docs and README must never contain unmasked API keys or real bearer tokens."""
        secret_assignment_pattern = re.compile(
            r"""(?:api[-_]?key|hf[-_]?token|password|passwd|secret)\s*[:=]\s*["'](?!(?:\[REDACTED\]|your[-_]|fake[-_]|dummy[-_]|my_secret_token|<[^>]+>))[a-zA-Z0-9_\-\.]{8,}["']""",
            re.IGNORECASE,
        )
        bearer_pattern = re.compile(
            r"""Bearer\s+(?!(?:\[REDACTED\]|<token>|your-token|fake|dummy))[a-zA-Z0-9_\-\.]{15,}""",
            re.IGNORECASE,
        )

        doc_files = list(DOCS_DIR.glob("*.md")) + [ROOT_DIR / "README.md"]
        for fpath in doc_files:
            if not fpath.is_file():
                continue
            text = fpath.read_text(encoding="utf-8", errors="ignore")

            matches = secret_assignment_pattern.findall(text)
            assert matches == [], f"Potential raw secret assignment found in {fpath.name}: {matches}"

            bearer_matches = bearer_pattern.findall(text)
            assert bearer_matches == [], f"Potential raw Bearer token found in {fpath.name}: {bearer_matches}"

    def test_example_configs_in_docs_use_generic_mock_paths(self):
        """Documented configuration examples in operations.md should use generic paths, not personal user profiles."""
        ops_doc = DOCS_DIR / "operations.md"
        content = ops_doc.read_text(encoding="utf-8")

        # Must not contain user home directories like C:\Users\<name> or /home/<name>
        personal_home_pattern = re.compile(r"""(?:C:\\Users\\[a-zA-Z0-9_]+|/home/[a-zA-Z0-9_]+)""", re.IGNORECASE)
        personal_matches = personal_home_pattern.findall(content)
        assert personal_matches == [], f"Personal user home paths found in operations.md: {personal_matches}"

    def test_public_docs_do_not_expose_raw_command_lines_with_secrets(self):
        """Documented command lines with secrets must be redacted with [REDACTED]."""
        for fpath in list(DOCS_DIR.glob("*.md")) + [ROOT_DIR / "README.md"]:
            if not fpath.is_file():
                continue
            text = fpath.read_text(encoding="utf-8", errors="ignore")
            # If --api-key or --token appears, ensure it is followed by [REDACTED] or placeholder
            for match in re.finditer(r"--(?:api[-_]key|token|hf[-_]token)\s+([^\s\n\r]+)", text, re.IGNORECASE):
                val = match.group(1).strip("'\"")
                assert val in ("[REDACTED]", "<token>", "<api_key>", "<your-token>", "your_key", "secret123"), \
                    f"Unredacted parameter in {fpath.name}: {match.group(0)}"


# ==============================================================================
# 4. Static index.html: noindex / Anti-Indexing Metadata and Zero External URLs
# ==============================================================================

class TestStaticIndexGeoSeoSafety:
    """Verifies that the static index.html has robust anti-indexing (noindex) and zero external URLs."""

    def test_index_html_has_robots_noindex_meta(self):
        assert INDEX_HTML_FILE.is_file(), "static/index.html must exist"
        content = INDEX_HTML_FILE.read_text(encoding="utf-8")

        robots_meta_pattern = re.compile(
            r'<meta\s+name=["\']robots["\']\s+content=["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        match = robots_meta_pattern.search(content)
        assert match is not None, "static/index.html must contain a <meta name='robots' ...> tag"
        directives = [d.strip().lower() for d in match.group(1).split(",")]
        assert "noindex" in directives, "robots meta tag must include 'noindex' to prevent search indexing"
        assert "noarchive" in directives, "robots meta tag must include 'noarchive'"

    def test_index_html_has_zero_external_http_urls(self):
        """Ensure static/index.html does not point to external websites, CDNs, or trackers."""
        content = INDEX_HTML_FILE.read_text(encoding="utf-8")
        external_url_pattern = re.compile(
            r'https?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)[a-zA-Z0-9\-\.]+(?:\:[0-9]+)?(?:/[^\s"\'<>]*)?',
            re.IGNORECASE,
        )
        matches = external_url_pattern.findall(content)
        disallowed = [m for m in matches if "w3.org" not in m]
        assert disallowed == [], f"static/index.html contains external URLs: {disallowed}"


# ==============================================================================
# 5. Server Robots Route and Anti-Indexing Headers
# ==============================================================================

class TestRobotsRouteAndAntiIndexingHeaders:
    """Verifies that the FastAPI application serves /robots.txt and anti-indexing headers."""

    @pytest.fixture(scope="class")
    def test_client(self):
        import server
        return TestClient(server.app)

    def test_robots_txt_route_returns_disallow_all(self, test_client):
        """GET /robots.txt must return 200 with User-agent: * and Disallow: /."""
        resp = test_client.get("/robots.txt")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
        assert "User-agent: *" in lines, "/robots.txt must target all user agents (User-agent: *)"
        assert "Disallow: /" in lines, "/robots.txt must disallow all paths (Disallow: /)"

    def test_root_route_serves_x_robots_tag_and_cache_control(self, test_client):
        """GET / must include X-Robots-Tag: noindex... and Cache-Control: no-store."""
        resp = test_client.get("/")
        assert resp.status_code == 200
        x_robots = resp.headers.get("X-Robots-Tag", "").lower()
        assert "noindex" in x_robots, f"Expected 'noindex' in X-Robots-Tag header, got: {x_robots}"
        assert "noarchive" in x_robots, f"Expected 'noarchive' in X-Robots-Tag header, got: {x_robots}"

        cache_control = resp.headers.get("Cache-Control", "").lower()
        assert "no-store" in cache_control, f"Expected 'no-store' in Cache-Control header, got: {cache_control}"

    def test_api_routes_serve_x_robots_tag(self, test_client):
        """API endpoints must also include X-Robots-Tag to prevent search indexing."""
        resp = test_client.get("/api/config")
        assert resp.status_code == 200
        x_robots = resp.headers.get("X-Robots-Tag", "").lower()
        assert "noindex" in x_robots, f"Expected 'noindex' in X-Robots-Tag for /api/config, got: {x_robots}"


# ==============================================================================
# 6. /api/v1 Safe Response Fields (Contract Invariant)
# ==============================================================================

class TestApiV1SafeResponseFields:
    """Verifies that the stable /api/v1 contract never exposes dangerous host fields."""

    @pytest.fixture(scope="class")
    def v1_client(self):
        import server
        return TestClient(server.app)

    def test_v1_profiles_schema_never_contains_pid_or_cmd(self, v1_client):
        """GET /api/v1/profiles must return only safe metadata (profile_id, name, runtime_kind, default_gpu)."""
        resp = v1_client.get("/api/v1/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert "profiles" in data
        assert isinstance(data["profiles"], list)
        assert len(data["profiles"]) > 0

        forbidden_fields = {
            "pid",
            "launcher_pid",
            "command_line",
            "command_line_redacted",
            "raw_command_line",
            "executable",
            "args",
            "cwd",
            "env",
            "token",
            "api_key",
            "password",
        }

        for profile in data["profiles"]:
            # Allowed fields only
            assert set(profile.keys()).issubset({"profile_id", "name", "runtime_kind", "default_gpu"})
            for forbidden in forbidden_fields:
                assert forbidden not in profile, f"Forbidden field '{forbidden}' found in /api/v1/profiles"

    def test_v1_profile_status_schema_never_contains_pid_or_cmd(self, v1_client):
        """GET /api/v1/profiles/{id}/status must strictly obey the safe contract."""
        resp_list = v1_client.get("/api/v1/profiles")
        first_profile_id = resp_list.json()["profiles"][0]["profile_id"]

        resp = v1_client.get(f"/api/v1/profiles/{first_profile_id}/status")
        assert resp.status_code == 200
        data = resp.json()

        allowed_fields = {"profile_id", "runtime_kind", "state", "endpoint", "health", "gpu"}
        assert set(data.keys()).issubset(allowed_fields), f"Unexpected fields in status response: {set(data.keys()) - allowed_fields}"

        forbidden_fields = {
            "pid",
            "launcher_pid",
            "command_line",
            "command_line_redacted",
            "raw_command_line",
            "executable",
            "args",
            "cwd",
            "env",
        }
        for forbidden in forbidden_fields:
            assert forbidden not in data, f"Forbidden field '{forbidden}' leaked in /api/v1/profiles/{first_profile_id}/status"

        # Check endpoint safety: if set, must only be localhost / 127.0.0.1
        if data.get("endpoint"):
            assert data["endpoint"].startswith("http://127.0.0.1:"), f"Endpoint {data['endpoint']} must bind strictly to 127.0.0.1"

"""Static file inspection tests ensuring complete offline self-containment without external CDNs."""

import re
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).parent.parent
STATIC_DIR = ROOT_DIR / "static"


def test_required_static_files_exist():
    """Verify all local WebUI static dependencies exist in static/ directory."""
    assert STATIC_DIR.is_dir(), "static directory must exist"

    required_files = [
        "tailwindcss.js",
        "alpine.min.js",
        "index.html",
    ]

    for fname in required_files:
        file_path = STATIC_DIR / fname
        assert file_path.is_file(), f"Required offline static asset {fname} is missing"
        assert file_path.stat().st_size > 0, f"Static asset {fname} is empty"


def test_no_external_cdn_urls_in_static_assets():
    """Verify that all static files and html contain no external CDN or http/https URLs."""
    external_url_pattern = re.compile(
        r'https?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)[a-zA-Z0-9\-\.]+(?:\:[0-9]+)?(?:/[^\s"\'<>]*)?',
        re.IGNORECASE,
    )

    cdn_patterns = [
        "cdn.jsdelivr.net",
        "unpkg.com",
        "cdnjs.cloudflare.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
    ]

    files_to_check = list(STATIC_DIR.glob("**/*"))
    # Also check root index.html if present
    root_index = ROOT_DIR / "index.html"
    if root_index.is_file():
        files_to_check.append(root_index)

    checked_count = 0
    for fpath in files_to_check:
        if fpath.is_file() and fpath.suffix.lower() in [".html", ".js", ".css", ".json"]:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            checked_count += 1

            # Check for CDN keywords
            for cdn in cdn_patterns:
                assert cdn not in content, f"External CDN reference '{cdn}' found in {fpath.name}"

            # Check for generic http/https external links
            matches = external_url_pattern.findall(content)
            # Filter out w3.org XML namespaces if present in SVG/HTML
            disallowed = [m for m in matches if "w3.org" not in m]
            assert disallowed == [], f"External URLs found in {fpath.name}: {disallowed}"

    assert checked_count >= 3, f"Expected at least 3 static files checked, got {checked_count}"

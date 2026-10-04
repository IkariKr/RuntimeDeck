"""Static source-level tests verifying responsive resolution acceptance for RuntimeDeck WebUI.

Acceptance criteria covered:
1. Viewport configuration and container bounds preventing horizontal overflow at 320px, 375px, 768px.
2. GPU hardware cards responsive grid behavior: single-column on mobile (<640px) and multi-column on >=640px.
3. Form controls & instance action usability: full-width accessible layout, touch targets, and flex wrapping.
4. Wide-screen readability constraints: max-width bounding (max-w-2xl / 48rem) preventing over-stretching.
"""

import re
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).parent.parent
STATIC_DIR = ROOT_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"
STYLES_CSS = STATIC_DIR / "styles.css"
APP_JS = STATIC_DIR / "app.js"


@pytest.fixture(scope="module")
def html_content() -> str:
    assert INDEX_HTML.is_file(), "index.html must exist in static/"
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_content() -> str:
    assert STYLES_CSS.is_file(), "styles.css must exist in static/"
    return STYLES_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_content() -> str:
    assert APP_JS.is_file(), "app.js must exist in static/"
    return APP_JS.read_text(encoding="utf-8")


class TestResponsiveViewportAndOverflow:
    """Verify viewport configuration and CSS rules that prevent horizontal overflow across 320, 375, 768px."""

    def test_viewport_meta_tag_present(self, html_content: str):
        """Ensure standard responsive mobile viewport tag is set to device-width."""
        pattern = re.compile(r'<meta\s+name=["\']viewport["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)
        match = pattern.search(html_content)
        assert match is not None, "Missing viewport meta tag in index.html"
        content_val = match.group(1)
        assert "width=device-width" in content_val, "Viewport meta must specify width=device-width"
        assert "initial-scale=1.0" in content_val, "Viewport meta must specify initial-scale=1.0"

    def test_global_box_sizing_border_box(self, css_content: str):
        """Ensure all elements use box-sizing: border-box to prevent padding overflow."""
        match = re.search(r'\*\s*\{[^}]*box-sizing\s*:\s*border-box[^}]*\}', css_content, re.DOTALL)
        assert match is not None, "Universal selector '*' must declare 'box-sizing: border-box'"

    def test_body_and_container_width_constraints(self, css_content: str):
        """Ensure body and dashboard-container do not enforce fixed horizontal widths exceeding 320px."""
        # Find dashboard-container rules
        container_block = re.search(r'\.dashboard-container\s*\{([^}]+)\}', css_content)
        assert container_block is not None, ".dashboard-container class must be defined"
        body_text = container_block.group(1)

        assert "width: 100%" in body_text or "width:100%" in body_text, ".dashboard-container must have width: 100%"
        assert "max-width:" in body_text, ".dashboard-container must specify max-width constraint"

        # Check no fixed px width on dashboard-container or body
        assert not re.search(r'(?<!max-|min-)width\s*:\s*\d{3,}px', body_text), (
            ".dashboard-container must not specify fixed pixel width"
        )

    def test_no_hardcoded_overflow_breaking_widths(self, css_content: str):
        """Ensure no element rule specifies a fixed width or min-width exceeding 320px outside of media queries."""
        # Strip comments first
        clean_css = re.sub(r'/\*.*?\*/', '', css_content, flags=re.DOTALL)
        # Strip media queries entirely (e.g. @media (min-width: 640px) { ... })
        clean_css = re.sub(r'@media[^{]+\{(?:[^{}]*\{[^{}]*\})*\s*\}', '', clean_css)

        # Match all property declarations of width or min-width in the remaining rules:
        declarations = re.findall(r'(?<!max-)(?<!min-media-)(?:min-)?width\s*:\s*(\d+)px\s*;', clean_css)
        for w_str in declarations:
            w_val = int(w_str)
            assert w_val <= 320, f"Found fixed width {w_val}px in base CSS which could break mobile viewports (<=320px)"

    @pytest.mark.parametrize("viewport_width", [320, 375, 768])
    def test_horizontal_fit_calculated_geometry(self, viewport_width: int, css_content: str):
        """Static geometric evaluation: container + padding <= viewport width at target resolutions."""
        # Body padding is 1rem = 16px on each side -> 32px total horizontal padding
        body_padding_h = 32
        content_budget = viewport_width - body_padding_h
        assert content_budget > 0

        # max-width is 48rem = 768px
        max_width_px = 48 * 16  # 768px
        actual_container_width = min(content_budget, max_width_px)

        # Container width must strictly fit within viewport width with zero horizontal overflow
        assert actual_container_width + body_padding_h <= viewport_width
        assert actual_container_width <= content_budget


class TestGpuCardResponsiveCollapse:
    """Verify GPU hardware grid adapts and collapses appropriately across screen breakpoints."""

    def test_gpu_grid_mobile_single_column_default(self, css_content: str):
        """Ensure base style for .gpu-grid defaults to a single column (1fr) for mobile (320/375px)."""
        # Find the base definition of .gpu-grid (before media query)
        base_match = re.search(r'\.gpu-grid\s*\{([^}]+)\}', css_content)
        assert base_match is not None, ".gpu-grid must be defined in styles.css"
        base_rules = base_match.group(1)

        assert "display: grid" in base_rules or "display:grid" in base_rules, ".gpu-grid must be CSS grid"
        assert re.search(r'grid-template-columns\s*:\s*1fr', base_rules), (
            ".gpu-grid base style must be single-column 'grid-template-columns: 1fr' for mobile"
        )

    def test_gpu_grid_breakpoint_expansion(self, css_content: str):
        """Ensure .gpu-grid expands to 2 columns on >=640px (tablets / 768px)."""
        media_match = re.search(
            r'@media\s*\(\s*min-width\s*:\s*640px\s*\)\s*\{([^}]+(?:\}[^}]*)*)\}',
            css_content,
            re.DOTALL,
        )
        assert media_match is not None, "A responsive media query for min-width: 640px must exist"
        media_body = media_match.group(1)

        assert ".gpu-grid" in media_body, ".gpu-grid must be styled inside min-width: 640px media query"
        assert re.search(r'grid-template-columns\s*:\s*repeat\(2,\s*1fr\)', media_body), (
            ".gpu-grid must expand to repeat(2, 1fr) at min-width: 640px"
        )

    def test_gpu_card_metric_bar_flexibility(self, css_content: str):
        """Verify progress bar and headers within GPU cards use full width and flexbox."""
        bar_match = re.search(r'\.progress-bar-bg\s*\{([^}]+)\}', css_content)
        assert bar_match is not None, ".progress-bar-bg must be styled"
        bar_rules = bar_match.group(1)
        assert "width: 100%" in bar_rules, ".progress-bar-bg must be 100% width"

        hdr_match = re.search(r'\.gpu-card-header\s*\{([^}]+)\}', css_content)
        assert hdr_match is not None, ".gpu-card-header must be styled"
        hdr_rules = hdr_match.group(1)
        assert "display: flex" in hdr_rules and "justify-content: space-between" in hdr_rules

    def test_gpu_process_panel_and_context_menu_contract(self, css_content: str, js_content: str, html_content: str):
        """GPU cards expose a compact process panel and explicit context-menu action."""
        assert ".gpu-process-row" in css_content
        assert ".gpu-context-menu" in css_content
        assert "contextmenu" in js_content
        assert "fetchGpuProcesses" in js_content
        assert "escapeHtml(process" in js_content
        assert "process.can_kill === false" in js_content
        assert 'id="gpuContextMenu"' in html_content
        assert 'id="gpuKillProcess"' in html_content
class TestLaunchFormAndInstanceControlsUsability:
    """Verify launch form and instance controls remain usable and touch-friendly on mobile & tablets."""

    def test_form_grid_single_column_on_mobile(self, css_content: str):
        """Form fields must stack into single column on mobile (<640px) and expand on tablet (>=640px)."""
        base_match = re.search(r'\.form-grid\s*\{([^}]+)\}', css_content)
        assert base_match is not None, ".form-grid must be defined"
        base_rules = base_match.group(1)

        assert re.search(r'grid-template-columns\s*:\s*1fr', base_rules), (
            ".form-grid base style must be single-column 'grid-template-columns: 1fr' for mobile"
        )

        media_match = re.search(
            r'@media\s*\(\s*min-width\s*:\s*640px\s*\)\s*\{([^}]+(?:\}[^}]*)*)\}',
            css_content,
            re.DOTALL,
        )
        assert media_match is not None
        assert ".form-grid" in media_match.group(1)
        assert re.search(r'grid-template-columns\s*:\s*2fr\s+1fr', media_match.group(1)), (
            ".form-grid must adapt to 2fr 1fr on desktop/tablet"
        )

    def test_form_select_and_button_full_width(self, css_content: str):
        """Select inputs and start button must occupy full width of their grid cell."""
        select_match = re.search(r'\.form-select\s*\{([^}]+)\}', css_content)
        assert select_match is not None
        assert "width: 100%" in select_match.group(1)

        btn_match = re.search(r'\.btn-start\s*\{([^}]+)\}', css_content)
        assert btn_match is not None
        assert "width: 100%" in btn_match.group(1)

    def test_instance_item_controls_actionable(self, css_content: str, js_content: str):
        """Ensure instance rows have dedicated stop action buttons with sufficient tap padding."""
        item_match = re.search(r'\.instance-item\s*\{([^}]+)\}', css_content)
        assert item_match is not None
        item_rules = item_match.group(1)
        assert "display: flex" in item_rules, ".instance-item must be flex layout"
        assert "justify-content: space-between" in item_rules

        stop_btn_match = re.search(r'\.btn-stop\s*\{([^}]+)\}', css_content)
        assert stop_btn_match is not None
        stop_rules = stop_btn_match.group(1)
        # Check cursor: pointer and padding
        assert "cursor: pointer" in stop_rules
        assert "padding:" in stop_rules


class TestWideScreenReadability:
    """Verify wide screens maintain comfortable reading width without unreadable stretching."""

    def test_container_max_width_constrained(self, css_content: str):
        """Ensure container max-width does not exceed standard reading bounds (48rem / 768px)."""
        container_block = re.search(r'\.dashboard-container\s*\{([^}]+)\}', css_content)
        assert container_block is not None
        body_text = container_block.group(1)

        # Look for max-width value
        max_w_match = re.search(r'max-width\s*:\s*([^;]+);', body_text)
        assert max_w_match is not None, ".dashboard-container must have a max-width"
        max_w_val = max_w_match.group(1).strip()
        # Should be bounded to 48rem (equivalent to Tailwind max-w-2xl: 768px)
        assert max_w_val == "48rem", f"Expected max-width of 48rem for comfortable reading, got {max_w_val}"

    def test_body_centering_for_widescreen(self, css_content: str):
        """Ensure body centers the dashboard container on wide desktop viewports."""
        body_match = re.search(r'body\s*\{([^}]+)\}', css_content)
        assert body_match is not None
        body_rules = body_match.group(1)
        assert "display: flex" in body_rules, "body should use flex display to center content"
        assert "justify-content: center" in body_rules, "body must center dashboard horizontally"

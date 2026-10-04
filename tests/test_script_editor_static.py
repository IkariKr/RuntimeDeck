"""Static acceptance tests for the offline Profile script editor."""

import re
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).parent.parent
STATIC_DIR = ROOT_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
STYLES_CSS = STATIC_DIR / "styles.css"


@pytest.fixture(scope="module")
def html_content() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_content() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_content() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def test_editor_dom_is_below_start_button(html_content: str):
    """The editor is in the launch panel and follows the explicit start button."""
    start_pos = html_content.index('id="btnStart"')
    editor_pos = html_content.index('id="scriptEditor"')
    assert start_pos < editor_pos
    assert 'id="scriptTextarea"' in html_content
    assert 'id="scriptHighlight"' in html_content
    assert 'id="scriptHighlightCode"' in html_content
    assert 'id="btnSaveScript"' in html_content
    assert 'id="btnReloadScript"' in html_content
    assert 'id="scriptDirtyBadge"' in html_content
    assert 'id="scriptReadOnlyBadge"' in html_content
    assert 'id="scriptStatus"' in html_content
    assert 'role="listbox"' in html_content
    assert 'aria-hidden="true"' in html_content
    assert 'spellcheck="false"' in html_content


def test_editor_uses_accessible_associations(html_content: str):
    """Labels and live regions provide a usable non-visual editor experience."""
    assert re.search(r'<label[^>]+for="scriptTextarea"', html_content)
    assert 'aria-describedby="scriptHelp scriptStatus"' in html_content
    assert 'role="status"' in html_content
    assert 'aria-live="polite"' in html_content
    assert 'aria-label="脚本补全建议"' in html_content


def test_script_load_save_contract_and_revision_handling(js_content: str):
    """The client reads and atomically updates the controlled script endpoint."""
    assert "`/api/profiles/${encodeURIComponent(profileKey)}/script`" in js_content
    assert "method: 'PUT'" in js_content
    assert "content: requestContent" in js_content
    assert "expected_revision: requestRevision" in js_content
    assert "body.current_revision" in js_content
    assert "e.status === 409" in js_content
    assert "AbortController" in js_content
    assert "loadSequence" in js_content
    assert "saveSequence" in js_content
    assert "state.script.value !== requestContent" in js_content
    assert "textarea.value !== state.script.value" in js_content
    assert "editable" in js_content
    assert "脚本已保存" in js_content
    assert "脚本保存失败" in js_content


def test_script_drafts_and_profile_switch_confirmation(js_content: str):
    """Dirty drafts survive profile switches only after explicit confirmation."""
    assert "savedValue" in js_content
    assert "drafts: {}" in js_content
    assert "isScriptDirty" in js_content
    assert "saveScriptDraft" in js_content
    assert "window.confirm('当前 Profile 有未保存的脚本修改" in js_content
    assert "state.selectedProfile = newProfileKey" in js_content
    assert "loadProfileScript(newProfileKey)" in js_content
    assert "beforeunload" in js_content


def test_batch_highlighting_and_completion_are_local(js_content: str):
    """Syntax highlighting and prefix/Ctrl+Space completion require no external library."""
    assert "function highlightBatch" in js_content
    assert "syntax-keyword" in js_content
    assert "syntax-comment" in js_content
    assert "syntax-variable" in js_content
    assert "BATCH_COMPLETIONS" in js_content
    assert "showScriptCompletions" in js_content
    assert "currentScriptPrefix" in js_content
    assert "e.key === ' ' || e.code === 'Space'" in js_content
    assert "e.ctrlKey || e.metaKey" in js_content
    for key_name in ("ArrowDown", "ArrowUp", "Enter", "Escape", "Tab"):
        assert key_name in js_content
    assert "insertScriptCompletion" in js_content
    assert "role=\"option\"" in js_content
    assert "/static/" not in js_content
    assert "http://" not in js_content
    assert "https://" not in js_content


def test_script_search_dom_and_keyboard_contract(html_content: str, js_content: str):
    """Search UI is local, accessible, and uses native textarea selection for navigation."""
    assert 'id="scriptSearchBar"' in html_content
    assert 'id="scriptSearchInput"' in html_content
    assert 'id="btnScriptSearch"' in html_content
    assert 'id="btnCloseScriptSearch"' in html_content
    assert 'aria-describedby="scriptSearchStatus"' in html_content
    assert 'role="status"' in html_content
    assert 'mouseenter' in js_content
    assert 'showScriptSearch' in js_content
    assert "toLowerCase() === 'f'" in js_content
    assert 'collectScriptSearchMatches' in js_content
    assert 'source.indexOf(query, offset)' in js_content
    assert 'textarea.setSelectionRange(match.start, match.end)' in js_content
    assert 'searchScript(e.shiftKey ? -1 : 1)' in js_content
    assert "e.key === 'Escape'" in js_content
    assert 'syncScriptScroll()' in js_content
    assert 'invalidateScriptSearch()' in js_content


def test_editor_styles_cover_dark_theme_responsive_and_focus(css_content: str):
    """Editor styles remain dark, responsive, keyboard-focusable, and touch-friendly."""
    for selector in (
        ".script-editor",
        ".script-editor-shell",
        ".script-textarea",
        ".script-highlight",
        ".script-completion-list",
        ".btn-save",
        ".btn-secondary",
    ):
        assert selector in css_content
    assert "background: #020617" in css_content
    assert "position: absolute" in css_content
    assert "pointer-events: none" in css_content
    assert "color: transparent" in css_content
    assert ".script-highlight {\n  color: var(--text-main);" in css_content
    assert "-webkit-text-fill-color: transparent" in css_content
    assert "white-space: pre-wrap" in css_content
    assert "overflow-x: hidden" in css_content
    assert "overflow-wrap: anywhere" in css_content
    assert "z-index: 1" in css_content
    assert "z-index: 2" in css_content
    assert "focus-within" in css_content
    assert ".script-textarea:focus-visible" in css_content
    assert "min-height: 2.25rem" in css_content
    assert "@media (max-width: 480px)" in css_content
    assert "@media (max-width: 360px)" in css_content
    assert "prefers-reduced-motion" in css_content

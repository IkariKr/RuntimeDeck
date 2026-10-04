/**
 * AI Runtime Dashboard - Offline WebUI Application
 * Pure vanilla JS interaction controller:
 * - 3-second polling
 * - GPU hardware cards rendering
 * - Managed instances with alive / stale status badges
 * - Profile and GPU selection controls with default_gpu auto-linkage
 * - Profile management script editor with local Batch highlighting/completion
 * - Dirty draft tracking, save confirmation, revision-aware PUT handling
 * - Explicit start with error handling
 * - Confirm stop with stale_cleaned notifications
 * - Error alerts
 * - Standalone capability
 */

(function () {
  const BATCH_COMPLETIONS = [
    { label: 'call', insert: 'call ', detail: '调用另一个 Batch 脚本' },
    { label: 'cd', insert: 'cd ', detail: '切换工作目录' },
    { label: 'echo', insert: 'echo ', detail: '输出提示信息' },
    { label: 'if', insert: 'if ', detail: '条件判断' },
    { label: 'set', insert: 'set ', detail: '设置环境变量' },
    { label: 'setlocal', insert: 'setlocal', detail: '开启局部环境' },
    { label: 'start', insert: 'start ', detail: '启动进程' },
    { label: 'title', insert: 'title ', detail: '设置窗口标题' },
    { label: 'endlocal', insert: 'endlocal', detail: '结束局部环境' },
    { label: 'exit', insert: 'exit ', detail: '退出脚本' }
  ];
  const state = {
    status: { gpus: [], instances: {}, errors: [] },
    config: { gpus: {}, profiles: {} },
    selectedProfile: '',
    selectedGpu: '',
    pollTimer: null,
    isStarting: false,
    isStopping: false,
    gpuProcesses: {},
    gpuProcessLoading: {},
    gpuProcessErrors: {},
    expandedGpu: null,
    gpuProcessRequestId: 0,
    gpuContextMenu: { open: false, gpuIndex: null, process: null, x: 0, y: 0 },
    gpuKillPending: false,
    script: {
      profile: '',
      value: '',
      savedValue: '',
      revision: null,
      loading: false,
      saving: false,
      editable: false,
      loadSequence: 0,
      saveSequence: 0,
      loadController: null,
      savingProfile: '',
      editGeneration: 0,
      latestSaveByProfile: {},
      drafts: {},
      completions: [],
      completionIndex: 0,
      searchVisible: false,
      searchQuery: '',
      searchMatches: [],
      searchIndex: -1,
      searchHideTimer: null,
      suppressSearchFocus: false
    }
  };

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  async function requestJson(url, options = {}) {
    const res = await fetch(url, options);
    const text = await res.text();
    let body = {};
    if (text) {
      try {
        body = JSON.parse(text);
      } catch (_) {
        body = { detail: text };
      }
    }
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      if (typeof body.detail === 'string') {
        msg = body.detail;
      } else if (body.detail && typeof body.detail.message === 'string') {
        msg = body.detail.message;
      } else if (typeof body.message === 'string') {
        msg = body.message;
      }
      const error = new Error(msg);
      error.status = res.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  function scriptEndpoint(profileKey) {
    return `/api/profiles/${encodeURIComponent(profileKey)}/script`;
  }

  function scriptResponseValue(body, profileKey) {
    const profile = (state.config.profiles || {})[profileKey] || {};
    return String(body.content ?? body.script ?? body.value ?? profile.script ?? '');
  }

  function scriptResponseRevision(body, profileKey) {
    const profile = (state.config.profiles || {})[profileKey] || {};
    const revision = body.revision ?? body.current_revision ?? body.version ?? profile.revision ?? null;
    return revision === null || revision === undefined ? null : revision;
  }

  function scriptResponseEditable(body) {
    return body && body.editable === true;
  }

  function isScriptDirty() {
    return Boolean(state.script.profile) && state.script.value !== state.script.savedValue;
  }

  function saveScriptDraft() {
    if (!state.script.profile) return;
    state.script.drafts[state.script.profile] = {
      value: state.script.value,
      savedValue: state.script.savedValue,
      revision: state.script.revision
    };
  }

  function clearScriptDraft(profileKey) {
    if (profileKey) delete state.script.drafts[profileKey];
  }

  function setScriptStatus(message, kind) {
    const status = document.getElementById('scriptStatus');
    if (!status) return;
    status.textContent = message;
    status.classList.remove('is-error', 'is-success', 'is-loading');
    if (kind) status.classList.add(`is-${kind}`);
  }

  function updateScriptEditorState() {
    const textarea = document.getElementById('scriptTextarea');
    const saveButton = document.getElementById('btnSaveScript');
    const reloadButton = document.getElementById('btnReloadScript');
    const badge = document.getElementById('scriptDirtyBadge');
    const readonlyBadge = document.getElementById('scriptReadOnlyBadge');
    const dirty = isScriptDirty();
    const editable = Boolean(state.script.editable);

    if (textarea) {
      if (textarea.value !== state.script.value) textarea.value = state.script.value;
      textarea.readOnly = !editable;
      textarea.setAttribute('aria-readonly', String(!editable));
      textarea.setAttribute('aria-label', state.script.profile
        ? `${state.script.profile} Profile Batch 启动脚本`
        : 'Profile Batch 启动脚本');
    }
    if (saveButton) {
      saveButton.disabled = !state.script.profile || !editable || !dirty || state.script.loading || state.script.saving;
      saveButton.textContent = state.script.saving ? '保存中…' : '保存脚本';
    }
    if (reloadButton) reloadButton.disabled = !state.script.profile || state.script.loading || state.script.saving;
    if (badge) {
      badge.hidden = !dirty;
      badge.textContent = dirty ? '未保存' : '已保存';
      badge.classList.toggle('is-clean', !dirty);
    }
    if (readonlyBadge) readonlyBadge.hidden = !state.script.profile || editable;
    renderScriptHighlight();
  }

  function highlightBatch(source) {
    const lines = String(source || '').split('\n');
    return lines.map((line) => {
      const leading = line.match(/^\s*/)[0];
      const rest = line.slice(leading.length);
      if (/^(?:rem(?:\s|$)|::)/i.test(rest)) {
        return `${escapeHtml(leading)}<span class="syntax-comment">${escapeHtml(rest)}</span>`;
      }

      const commentAt = rest.search(/(?:^|[&|])\s*(?:::|rem(?:\s|$))/i);
      const codePart = commentAt >= 0 ? rest.slice(0, commentAt) : rest;
      const commentPart = commentAt >= 0 ? rest.slice(commentAt) : '';
      let html = escapeHtml(codePart);
      html = html.replace(/(%~?[0-9A-Za-z_*?]+%|%%?[A-Za-z_][A-Za-z0-9_]*|%[0-9]+)/g,
        '<span class="syntax-variable">$1</span>');
      html = html.replace(/(^|[&|]\s*)(@?(?:call|cd|chcp|cls|copy|del|dir|echo|else|endlocal|exit|for|goto|if|pause|popd|pushd|set|setlocal|start|title|where))(?=\s|$)/gi,
        '$1<span class="syntax-keyword">$2</span>');
      if (commentPart) html += `<span class="syntax-comment">${escapeHtml(commentPart)}</span>`;
      return `${escapeHtml(leading)}${html}`;
    }).join('\n');
  }

  function renderScriptHighlight() {
    const code = document.getElementById('scriptHighlightCode');
    if (!code) return;
    code.innerHTML = `${highlightBatch(state.script.value)}\n`;
    syncScriptScroll();
  }

  function syncScriptScroll() {
    const textarea = document.getElementById('scriptTextarea');
    const highlight = document.getElementById('scriptHighlight');
    if (!textarea || !highlight) return;
    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
  }

  function currentScriptPrefix() {
    const textarea = document.getElementById('scriptTextarea');
    if (!textarea) return null;
    const beforeCursor = state.script.value.slice(0, textarea.selectionStart);
    const line = beforeCursor.slice(beforeCursor.lastIndexOf('\n') + 1);
    const match = line.match(/^\s*[@]?([A-Za-z]*)$/);
    return match ? match[1] : null;
  }

  function setScriptSearchStatus(message, kind) {
    const status = document.getElementById('scriptSearchStatus');
    if (!status) return;
    status.textContent = message;
    status.classList.remove('is-error', 'is-success');
    if (kind) status.classList.add(`is-${kind}`);
  }

  function resetScriptSearch(clearQuery = false) {
    state.script.searchMatches = [];
    state.script.searchIndex = -1;
    if (clearQuery) {
      state.script.searchQuery = '';
      const input = document.getElementById('scriptSearchInput');
      if (input) input.value = '';
    }
    setScriptSearchStatus('输入文本后点击确定');
  }

  function invalidateScriptSearch() {
    state.script.searchQuery = '';
    state.script.searchMatches = [];
    state.script.searchIndex = -1;
    if (state.script.searchVisible) setScriptSearchStatus('输入文本后点击确定');
  }

  function scheduleHideScriptSearch() {
    if (state.script.searchHideTimer) clearTimeout(state.script.searchHideTimer);
    state.script.searchHideTimer = setTimeout(() => {
      const editor = document.getElementById('scriptEditor');
      const activeElement = document.activeElement;
      if (editor && !editor.matches(':hover') && !editor.contains(activeElement)) hideScriptSearch(false);
      state.script.searchHideTimer = null;
    }, 160);
  }

  function showScriptSearch(focusInput = true) {
    const bar = document.getElementById('scriptSearchBar');
    const input = document.getElementById('scriptSearchInput');
    if (!bar || !input) return;
    if (state.script.searchHideTimer) clearTimeout(state.script.searchHideTimer);
    state.script.searchVisible = true;
    bar.hidden = false;
    if (focusInput) {
      input.focus();
      input.select();
    }
  }

  function hideScriptSearch(restoreEditorFocus = true) {
    const bar = document.getElementById('scriptSearchBar');
    const textarea = document.getElementById('scriptTextarea');
    state.script.searchVisible = false;
    state.script.suppressSearchFocus = true;
    if (bar) bar.hidden = true;
    if (state.script.searchHideTimer) {
      clearTimeout(state.script.searchHideTimer);
      state.script.searchHideTimer = null;
    }
    if (restoreEditorFocus && textarea && !textarea.readOnly) textarea.focus();
  }

  function collectScriptSearchMatches(query) {
    const matches = [];
    if (!query) return matches;
    const source = state.script.value;
    let offset = 0;
    while (offset <= source.length - query.length) {
      const start = source.indexOf(query, offset);
      if (start === -1) break;
      matches.push({ start, end: start + query.length });
      offset = start + Math.max(query.length, 1);
    }
    return matches;
  }

  function selectScriptSearchMatch(index) {
    const textarea = document.getElementById('scriptTextarea');
    const match = state.script.searchMatches[index];
    if (!textarea || !match) return false;
    state.script.searchIndex = index;
    textarea.focus();
    textarea.setSelectionRange(match.start, match.end);
    syncScriptScroll();
    setScriptSearchStatus(`第 ${index + 1}/${state.script.searchMatches.length} 个匹配`, 'success');
    return true;
  }

  function searchScript(direction = 1) {
    const input = document.getElementById('scriptSearchInput');
    if (!input) return false;
    const query = input.value;
    const previousQuery = state.script.searchQuery;
    state.script.searchQuery = query;
    hideScriptCompletions();
    if (!query) {
      resetScriptSearch();
      setScriptSearchStatus('请输入搜索内容', 'error');
      input.focus();
      return false;
    }
    const queryChanged = query !== previousQuery;
    if (queryChanged) {
      state.script.searchMatches = collectScriptSearchMatches(query);
      state.script.searchIndex = direction > 0 ? -1 : state.script.searchMatches.length;
    }
    if (state.script.searchMatches.length === 0) {
      setScriptSearchStatus('未找到匹配', 'error');
      input.focus();
      return false;
    }
    const nextIndex = (state.script.searchIndex + direction + state.script.searchMatches.length)
      % state.script.searchMatches.length;
    return selectScriptSearchMatch(nextIndex);
  }

  function hideScriptCompletions() {
    const list = document.getElementById('scriptCompletionList');
    if (list) list.hidden = true;
    state.script.completions = [];
    state.script.completionIndex = 0;
  }

  function renderScriptCompletions() {
    const list = document.getElementById('scriptCompletionList');
    if (!list) return;
    list.innerHTML = state.script.completions.map((item, index) => `
      <li role="option" aria-selected="${index === state.script.completionIndex ? 'true' : 'false'}" class="script-completion-item${index === state.script.completionIndex ? ' is-active' : ''}" data-completion-index="${index}">
        <strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span>
      </li>`).join('');
    list.hidden = state.script.completions.length === 0;
  }

  function showScriptCompletions(prefix) {
    if (prefix === null || prefix === undefined) {
      hideScriptCompletions();
      return;
    }
    const normalized = String(prefix).toLowerCase();
    state.script.completions = BATCH_COMPLETIONS.filter((item) => !normalized || item.label.startsWith(normalized));
    state.script.completionIndex = 0;
    renderScriptCompletions();
  }

  function insertScriptCompletion(index) {
    const textarea = document.getElementById('scriptTextarea');
    const item = state.script.completions[index];
    if (!textarea || !item) return false;
    const cursor = textarea.selectionStart;
    const before = state.script.value.slice(0, cursor);
    const lineStart = before.lastIndexOf('\n') + 1;
    const linePrefix = before.slice(lineStart);
    const commandMatch = linePrefix.match(/^(\s*@?)([A-Za-z]*)$/);
    if (!commandMatch) return false;
    const tokenStart = lineStart + commandMatch[1].length;
    const replacement = item.insert;
    state.script.value = `${state.script.value.slice(0, tokenStart)}${replacement}${state.script.value.slice(cursor)}`;
    textarea.value = state.script.value;
    const newCursor = tokenStart + replacement.length;
    textarea.setSelectionRange(newCursor, newCursor);
    saveScriptDraft();
    updateScriptEditorState();
    hideScriptCompletions();
    return true;
  }

  function isCurrentScriptLoad(requestId, profileKey) {
    return requestId === state.script.loadSequence && state.script.profile === profileKey;
  }

  async function loadProfileScript(profileKey, options = {}) {
    if (state.script.loadController) state.script.loadController.abort();
    const requestId = ++state.script.loadSequence;

    if (!profileKey) {
      state.script.profile = '';
      state.script.value = '';
      state.script.savedValue = '';
      state.script.revision = null;
      state.script.editable = false;
      state.script.loading = false;
      state.script.saving = false;
      state.script.savingProfile = '';
      state.script.loadController = null;
      updateScriptEditorState();
      setScriptStatus('没有可加载的 Profile');
      return false;
    }

    const draft = state.script.drafts[profileKey];
    const controller = new AbortController();
    state.script.loadController = controller;
    state.script.profile = profileKey;
    invalidateScriptSearch();
    state.script.value = draft && !options.force ? draft.value : '';
    state.script.savedValue = draft && !options.force ? (draft.savedValue ?? '') : '';
    state.script.revision = draft && !options.force ? (draft.revision ?? null) : null;
    state.script.editable = false;
    state.script.loading = true;
    state.script.saving = false;
    state.script.savingProfile = '';
    state.script.conflict = false;
    updateScriptEditorState();
    setScriptStatus(`正在加载 ${profileKey} 脚本…`, 'loading');

    try {
      const body = await requestJson(scriptEndpoint(profileKey), {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        signal: controller.signal
      });
      if (!isCurrentScriptLoad(requestId, profileKey)) return false;

      const serverValue = scriptResponseValue(body, profileKey);
      const serverRevision = scriptResponseRevision(body, profileKey);
      state.script.editable = scriptResponseEditable(body);
      if (draft && !options.force) {
        state.script.value = draft.value;
        state.script.savedValue = draft.savedValue ?? serverValue;
        state.script.revision = draft.revision ?? serverRevision;
      } else {
        state.script.value = serverValue;
        state.script.savedValue = serverValue;
        state.script.revision = serverRevision;
        clearScriptDraft(profileKey);
      }
      state.script.loading = false;
      state.script.loadController = null;
      updateScriptEditorState();
      if (!state.script.editable) {
        setScriptStatus('当前脚本为只读状态，无法保存', 'success');
      } else {
        setScriptStatus(isScriptDirty() ? '已恢复本地未保存草稿' : `已加载 ${profileKey} 脚本`, isScriptDirty() ? 'loading' : 'success');
      }
      return true;
    } catch (e) {
      if (!isCurrentScriptLoad(requestId, profileKey)) return false;
      state.script.loading = false;
      state.script.loadController = null;
      updateScriptEditorState();
      if (e.name === 'AbortError') return false;
      setScriptStatus(`脚本加载失败：${e.message}`, 'error');
      return false;
    }
  }

  function updateSavedScriptDraft(profileKey, requestContent, savedValue, revision) {
    const draft = state.script.drafts[profileKey];
    if (!draft) return;
    draft.savedValue = savedValue;
    draft.revision = revision;
    state.script.drafts[profileKey] = draft;
    if (draft.value === requestContent && draft.value === savedValue) clearScriptDraft(profileKey);
  }

  async function saveProfileScript() {
    if (state.script.saving || state.script.loading || !state.script.profile || !state.script.editable || !isScriptDirty()) return false;
    const profileKey = state.script.profile;
    const requestContent = state.script.value;
    const requestRevision = state.script.revision;
    const requestId = ++state.script.saveSequence;
    state.script.saving = true;
    state.script.savingProfile = profileKey;
    state.script.latestSaveByProfile[profileKey] = requestId;
    updateScriptEditorState();
    setScriptStatus('正在保存脚本…', 'loading');
    try {
      const body = await requestJson(scriptEndpoint(profileKey), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: requestContent, expected_revision: requestRevision })
      });
      const savedValue = scriptResponseValue(body, profileKey);
      const savedRevision = scriptResponseRevision(body, profileKey);
      updateSavedScriptDraft(profileKey, requestContent, savedValue, savedRevision);

      const isCurrentSave = state.script.profile === profileKey
        && state.script.savingProfile === profileKey
        && state.script.saveSequence === requestId;
      if (isCurrentSave) {
        const hasNewEdits = state.script.value !== requestContent;
        state.script.savedValue = savedValue;
        state.script.revision = savedRevision;
        state.script.editable = scriptResponseEditable(body);
        if (!hasNewEdits) state.script.value = savedValue;
        state.script.saving = false;
        state.script.savingProfile = '';
        state.script.conflict = false;
        if (!hasNewEdits) clearScriptDraft(profileKey);
        else saveScriptDraft();
        updateScriptEditorState();
        setScriptStatus(hasNewEdits ? '脚本已保存，保留保存后的新修改' : '脚本已保存', 'success');
      }
      return true;
    } catch (e) {
      const isCurrentSave = state.script.profile === profileKey
        && state.script.savingProfile === profileKey
        && state.script.saveSequence === requestId;
      if (!isCurrentSave) return false;
      state.script.saving = false;
      state.script.savingProfile = '';
      if (e.status === 409) {
        state.script.conflict = true;
        const detail = e.body && e.body.detail;
        const latestRevision = (e.body && (e.body.current_revision ?? e.body.revision ?? e.body.version))
          || (detail && (detail.current_revision ?? detail.revision));
        if (latestRevision !== undefined && latestRevision !== null) state.script.serverRevision = latestRevision;
        setScriptStatus('保存冲突：服务器脚本已更新，请重新加载后再保存。', 'error');
      } else {
        setScriptStatus(`脚本保存失败：${e.message}`, 'error');
      }
      updateScriptEditorState();
      return false;
    }
  }

  function confirmDiscardScriptDraft() {
    if (!isScriptDirty()) return true;
    return window.confirm('当前 Profile 有未保存的脚本修改，确定切换并保留草稿吗？');
  }

  function captureScriptDraftBeforeUnload(event) {
    if (!isScriptDirty()) return;
    saveScriptDraft();
    event.preventDefault();
    event.returnValue = '';
  }

  async function onProfileChange(newProfileKey) {
    const previousProfile = state.selectedProfile;
    if (previousProfile && previousProfile !== newProfileKey && !confirmDiscardScriptDraft()) {
      const profileSelect = document.getElementById('profileSelect');
      if (profileSelect) profileSelect.value = previousProfile;
      return false;
    }
    if (previousProfile && previousProfile !== newProfileKey) saveScriptDraft();
    resetScriptSearch(true);
    state.selectedProfile = newProfileKey;
    applyProfileDefaultGpu();
    renderConfigControls();
    await loadProfileScript(newProfileKey);
    return true;
  }

  async function fetchConfig() {
    try {
      state.config = await requestJson('/api/config');
      const profileKeys = Object.keys(state.config.profiles || {});
      if (profileKeys.length > 0 && (!state.selectedProfile || !state.config.profiles[state.selectedProfile])) {
        state.selectedProfile = profileKeys[0];
      } else if (profileKeys.length === 0) {
        state.selectedProfile = '';
      }
      applyProfileDefaultGpu();
      renderConfigControls();
      await loadProfileScript(state.selectedProfile);
    } catch (e) {
      console.error('获取配置失败', e);
      renderErrors([`获取配置失败: ${e.message}`]);
    }
  }

  function applyProfileDefaultGpu() {
    const profiles = state.config.profiles || {};
    const gpus = state.config.gpus || {};
    const currentProfile = profiles[state.selectedProfile];

    if (currentProfile && currentProfile.default_gpu && gpus[currentProfile.default_gpu]) {
      state.selectedGpu = currentProfile.default_gpu;
      return;
    }

    const gpuKeys = Object.keys(gpus);
    if (gpuKeys.length > 0) {
      state.selectedGpu = gpuKeys[0];
    } else {
      state.selectedGpu = '';
    }
  }

  async function fetchStatus() {
    try {
      state.status = await requestJson('/api/status');
      render();
      if (state.expandedGpu !== null) await fetchGpuProcesses(state.expandedGpu, { silent: true });
    } catch (e) {
      console.error('获取状态失败', e);
      renderErrors([`获取状态失败: ${e.message}`]);
    }
  }

  function gpuProcessEndpoint(gpuIndex) {
    return `/api/gpus/${encodeURIComponent(gpuIndex)}/processes`;
  }

  async function fetchGpuProcesses(gpuIndex, options = {}) {
    const requestId = ++state.gpuProcessRequestId;
    if (!options.silent) {
      state.gpuProcessLoading[gpuIndex] = true;
      state.gpuProcessErrors[gpuIndex] = '';
      renderGpus();
    }
    try {
      const result = await requestJson(gpuProcessEndpoint(gpuIndex));
      if (requestId !== state.gpuProcessRequestId || state.expandedGpu !== gpuIndex) return;
      state.gpuProcesses[gpuIndex] = result.processes || [];
      state.gpuProcessErrors[gpuIndex] = (result.errors || []).join('；');
    } catch (e) {
      if (requestId !== state.gpuProcessRequestId || state.expandedGpu !== gpuIndex) return;
      state.gpuProcesses[gpuIndex] = [];
      state.gpuProcessErrors[gpuIndex] = e.message || '读取 GPU 进程失败';
    } finally {
      if (requestId === state.gpuProcessRequestId) {
        state.gpuProcessLoading[gpuIndex] = false;
        renderGpus();
      }
    }
  }

  function closeGpuContextMenu() {
    state.gpuContextMenu = { open: false, gpuIndex: null, process: null, x: 0, y: 0 };
    const menu = document.getElementById('gpuContextMenu');
    if (menu) menu.hidden = true;
  }

  function openGpuContextMenu(event, gpuIndex, process) {
    event.preventDefault();
    event.stopPropagation();
    const menu = document.getElementById('gpuContextMenu');
    if (!menu) return;
    const menuWidth = 132;
    const menuHeight = 42;
    const x = Math.max(8, Math.min(event.clientX, window.innerWidth - menuWidth - 8));
    const y = Math.max(8, Math.min(event.clientY, window.innerHeight - menuHeight - 8));
    state.gpuContextMenu = { open: true, gpuIndex, process, x, y };
    const killButton = document.getElementById('gpuKillProcess');
    if (killButton) {
      killButton.disabled = process.can_kill === false;
      killButton.textContent = process.can_kill === false ? (process.kill_reason || '不可结束') : '强制结束';
    }
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.hidden = false;
  }

  async function killSelectedGpuProcess() {
    const target = state.gpuContextMenu.process;
    const gpuIndex = state.gpuContextMenu.gpuIndex;
    if (!target || gpuIndex === null || state.gpuKillPending) return;
    const label = target.name || target.executable || '目标进程';
    if (!window.confirm(`确认强制结束 ${label} (PID ${target.pid})？`)) {
      closeGpuContextMenu();
      return;
    }
    state.gpuKillPending = true;
    const button = document.getElementById('gpuKillProcess');
    if (button) {
      button.disabled = true;
      button.textContent = '结束中…';
    }
    try {
      await requestJson(`/api/gpus/${encodeURIComponent(gpuIndex)}/processes/${encodeURIComponent(target.pid)}/kill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ create_time: target.create_time })
      });
      closeGpuContextMenu();
      await fetchStatus();
    } catch (e) {
      state.gpuProcessErrors[gpuIndex] = e.message || '强制结束失败';
      closeGpuContextMenu();
      renderGpus();
    } finally {
      state.gpuKillPending = false;
    }
  }

  function toggleGpuProcesses(gpuIndex) {
    closeGpuContextMenu();
    if (state.expandedGpu === gpuIndex) {
      state.expandedGpu = null;
      state.gpuProcessRequestId += 1;
      renderGpus();
      return;
    }
    state.expandedGpu = gpuIndex;
    state.gpuProcesses[gpuIndex] = state.gpuProcesses[gpuIndex] || [];
    fetchGpuProcesses(gpuIndex);
  }

  function onGpuChange(newGpuKey) {
    state.selectedGpu = newGpuKey;
  }

  async function startInstance(options = {}) {
    if (state.isStarting && !options.isRetry) return;
    if (!state.selectedProfile) {
      alert('请先选择需要启动的 Profile 预设');
      return;
    }
    state.isStarting = true;
    updateStartButtonState();
    try {
      const payload = {
        profile: state.selectedProfile,
        gpu: state.selectedGpu || undefined,
        force_clean_port: Boolean(options.force_clean_port),
        expected_port_pid: options.expected_port_pid !== undefined ? options.expected_port_pid : undefined
      };
      await requestJson('/api/instances/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      await fetchStatus();
    } catch (e) {
      const detail = e.body?.detail;
      if (e.status === 409 && detail && detail.conflict === 'port_in_use') {
        const port = detail.port;
        const pid = detail.pid;
        const name = detail.name || '未知进程';
        const canClean = detail.can_force_clean !== false && pid !== null && pid !== undefined;

        if (canClean) {
          const confirmMsg = `【检测到端口冲突】\n` +
            `目标端口 ${port} 已被外部进程占用：\n` +
            `• 进程名: ${name}\n` +
            `• 进程 PID: ${pid}\n\n` +
            `是否由 RuntimeDeck 终止该占用进程并重新拉起服务？`;
          if (window.confirm(confirmMsg)) {
            // User explicitly confirmed: retry with force_clean_port
            return await startInstance({
              isRetry: true,
              force_clean_port: true,
              expected_port_pid: pid
            });
          }
          return;
        } else {
          alert(`【端口占用警告】\n目标端口 ${port} 已被系统或受保护进程占用 (${name})，无法自动清理，请更换端口或手动释放。`);
          return;
        }
      }
      alert('启动失败: ' + e.message);
    } finally {
      state.isStarting = false;
      updateStartButtonState();
    }
  }

  async function stopInstance(id, name) {
    if (state.isStopping) return;
    const confirmed = window.confirm(`确认关闭实例: ${name}?`);
    if (!confirmed) return;

    state.isStopping = true;
    try {
      const result = await requestJson(`/api/instances/${encodeURIComponent(id)}/stop`, {
        method: 'POST'
      });
      if (result.status === 'stale_cleaned') {
        alert(result.message || '实例状态已过期，已安全清理记录');
      }
      await fetchStatus();
    } catch (e) {
      alert('停止失败: ' + e.message);
    } finally {
      state.isStopping = false;
    }
  }

  async function deleteCurrentProfile() {
    const profileKey = state.selectedProfile;
    if (!profileKey) {
      alert('请先选择需要删除的 Profile 预设');
      return;
    }
    const profileCfg = (state.config.profiles && state.config.profiles[profileKey]) || null;
    const profileName = (profileCfg && profileCfg.name) || profileKey;

    const confirmed = window.confirm(
      `确定要删除 Profile 配置项「${profileName}」吗？\n\n此操作将从 runtimes.json 中移除该配置，但会保留磁盘上的脚本文件。`
    );
    if (!confirmed) return;

    const btnDelete = document.getElementById('btnDeleteProfile');
    if (btnDelete) btnDelete.disabled = true;

    try {
      const result = await requestJson(`/api/profiles/${encodeURIComponent(profileKey)}`, {
        method: 'DELETE'
      });
      alert(result.message || `已成功删除 Profile「${profileName}」配置项`);
      delete state.script.drafts[profileKey];
      delete state.script.latestSaveByProfile[profileKey];
      state.selectedProfile = '';
      await fetchConfig();
      await fetchStatus();
    } catch (e) {
      const detail = e.body?.detail;
      const message = typeof detail === 'string' ? detail : (detail?.message || e.message);
      alert('删除失败: ' + message);
    } finally {
      if (btnDelete) {
        btnDelete.disabled = !state.selectedProfile || Object.keys(state.config.profiles || {}).length === 0;
      }
    }
  }

  function renderErrors(overrideErrors) {
    const errorContainer = document.getElementById('errorContainer');
    if (!errorContainer) return;
    const errors = overrideErrors || state.status.errors || [];
    if (!errors || errors.length === 0) {
      errorContainer.innerHTML = '';
      errorContainer.style.display = 'none';
      return;
    }

    errorContainer.style.display = 'flex';
    errorContainer.innerHTML = errors
      .map(
        (err) => `
        <div class="error-item">
          <span>⚠️</span>
          <span>${escapeHtml(err)}</span>
        </div>`
      )
      .join('');
  }

  function renderGpuProcessPanel(gpu) {
    const gpuIndex = gpu.index;
    if (state.expandedGpu !== gpuIndex) return '';
    if (state.gpuProcessLoading[gpuIndex]) {
      return '<div class="gpu-process-panel"><div class="gpu-process-state">正在读取 GPU 进程…</div></div>';
    }
    const error = state.gpuProcessErrors[gpuIndex];
    const processes = state.gpuProcesses[gpuIndex] || [];
    const errorHtml = error ? `<div class="gpu-process-state is-error">${escapeHtml(error)}</div>` : '';
    const rows = processes.map((process) => `
      <div class="gpu-process-row" data-gpu-process-pid="${escapeHtml(process.pid)}" tabindex="0" aria-label="${escapeHtml(process.name || process.executable || 'GPU 进程')} PID ${escapeHtml(process.pid)}">
        <span class="gpu-process-name" title="${escapeHtml(process.executable || process.name || '')}">${escapeHtml(process.name || process.executable || '未知进程')}</span>
        <span class="gpu-process-meta">PID ${escapeHtml(process.pid)}</span>
        <span class="gpu-process-meta">${process.used_mb === null || process.used_mb === undefined ? '显存未知' : `${escapeHtml(process.used_mb)} MB`}</span>
      </div>`).join('');
    const emptyHtml = !error && processes.length === 0 ? '<div class="gpu-process-state">当前没有检测到 CUDA 计算进程</div>' : '';
    return `<div class="gpu-process-panel" id="gpu-processes-${escapeHtml(gpuIndex)}" role="region" aria-label="GPU ${escapeHtml(gpuIndex)} 进程列表">
      <div class="gpu-process-heading"><span>正在使用 GPU 的进程</span><span class="gpu-process-hint">右键进程可强制结束</span></div>
      ${errorHtml}${emptyHtml}<div class="gpu-process-list">${rows}</div>
    </div>`;
  }

  function renderGpus() {
    const gpuContainer = document.getElementById('gpuContainer');
    if (!gpuContainer) return;

    const gpus = state.status.gpus || [];
    if (gpus.length === 0) {
      gpuContainer.innerHTML = `
        <div class="gpu-card" style="grid-column: 1 / -1; text-align: center; color: var(--text-muted);">
          未检测到可用的 NVIDIA GPU 设备或驱动监控未就绪
        </div>`;
      return;
    }

    gpuContainer.innerHTML = gpus
      .map((gpu) => {
        const usedGb = (gpu.used_mb / 1024).toFixed(1);
        const totalGb = (gpu.total_mb / 1024).toFixed(1);
        const freeGb = Math.max(0, (gpu.total_mb - gpu.used_mb) / 1024).toFixed(1);
        const percent = gpu.total_mb > 0 ? Math.min(100, Math.round((gpu.used_mb / gpu.total_mb) * 100)) : 0;
        const expanded = state.expandedGpu === gpu.index;
        return `
        <div class="gpu-card ${expanded ? 'is-expanded' : ''}" data-gpu-index="${escapeHtml(gpu.index)}" tabindex="0" role="button" aria-expanded="${expanded}" aria-controls="gpu-processes-${escapeHtml(gpu.index)}">
          <div class="gpu-card-header">
            <span class="gpu-name">GPU ${escapeHtml(gpu.index)}: ${escapeHtml(gpu.name)}</span>
            <span class="gpu-mem">${usedGb}G / ${totalGb}G</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width: ${percent}%;"></div>
          </div>
          <div class="gpu-card-footer">
            <span>利用率: ${escapeHtml(gpu.util_percent)}%</span>
            <span>空闲: ${freeGb}G</span>
          </div>
          ${renderGpuProcessPanel(gpu)}
        </div>`;
      })
      .join('');

    gpuContainer.querySelectorAll('[data-gpu-index]').forEach((card) => {
      const gpuIndex = Number(card.getAttribute('data-gpu-index'));
      card.addEventListener('click', (event) => {
        if (event.target.closest('.gpu-process-row')) return;
        toggleGpuProcesses(gpuIndex);
      });
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggleGpuProcesses(gpuIndex);
        }
      });
      card.querySelectorAll('.gpu-process-row').forEach((row) => {
        const pid = Number(row.getAttribute('data-gpu-process-pid'));
        const process = (state.gpuProcesses[gpuIndex] || []).find((item) => item.pid === pid);
        if (!process) return;
        row.addEventListener('contextmenu', (event) => openGpuContextMenu(event, gpuIndex, process));
      });
    });
  }

  function renderInstances() {
    const instanceContainer = document.getElementById('instanceContainer');
    if (!instanceContainer) return;

    const instances = state.status.instances || {};
    const keys = Object.keys(instances);

    if (keys.length === 0) {
      instanceContainer.innerHTML = `
        <div class="empty-state">当前没有运行中的 AI 进程实例</div>`;
      return;
    }

    instanceContainer.innerHTML = `
      <div class="instance-list">
        ${keys
          .map((id) => {
            const inst = instances[id];
            const statusClass = 'alive';

            const profileCfg = (state.config.profiles && state.config.profiles[inst.profile]) || null;
            const managedCfg = (profileCfg && profileCfg.managed_process) || null;

            const processRole = inst.process_role || (managedCfg && (managedCfg.process_role || managedCfg.role)) || 'AI 运行时';
            const executable = inst.managed_executable || (managedCfg && managedCfg.executable) || inst.executable || '未知';
            const managedPid = inst.managed_pid || inst.pid;
            const launcherPid = inst.launcher_pid || (inst.managed_pid && inst.pid && inst.pid !== inst.managed_pid ? inst.pid : null);

            const modelId = inst.model_id || (profileCfg && profileCfg.model_id) || '未知';
            const contextSize = inst.context_size || (profileCfg && profileCfg.context_size) || '未知';
            const kvQuant = inst.kv_quant || (profileCfg && profileCfg.kv_quant) || '未知';
            const port = inst.port || (profileCfg && profileCfg.port) || '未知';

            let launcherPidHtml = '';
            if (launcherPid) {
              launcherPidHtml = `<span>启动器 PID: <span class="meta-value font-mono">${escapeHtml(launcherPid)}</span></span>`;
            }

            return `
            <div class="instance-item">
              <div class="instance-info">
                <div class="instance-title-row ${statusClass}">
                  <span class="status-dot ${statusClass}"></span>
                  <span class="instance-name">${escapeHtml(inst.name || id)}</span>
                </div>
                <div class="instance-meta">
                  <div class="instance-meta-row">
                    <span>GPU: <span class="meta-value">${escapeHtml(inst.gpu || '未知')}</span></span>
                    <span>端口: <span class="meta-value font-mono">${escapeHtml(port)}</span></span>
                    <span>进程角色: <span class="meta-value">${escapeHtml(processRole)}</span></span>
                  </div>
                  <div class="instance-meta-row">
                    <span>模型ID: <span class="meta-value font-mono">${escapeHtml(modelId)}</span></span>
                    <span>上下文大小: <span class="meta-value">${escapeHtml(contextSize)}</span></span>
                    <span>KV量化: <span class="meta-value">${escapeHtml(kvQuant)}</span></span>
                  </div>
                  <div class="instance-meta-row">
                    <span>可执行文件: <span class="meta-value font-mono">${escapeHtml(executable)}</span></span>
                  </div>
                  <div class="instance-meta-row">
                    <span>受管进程 PID: <span class="meta-value font-mono">${escapeHtml(managedPid)}</span></span>
                    ${launcherPidHtml}
                    <span>启动时间: <span class="meta-value">${escapeHtml(inst.started_at || '未知')}</span></span>
                  </div>
                </div>
              </div>
              <button class="btn-stop" data-id="${escapeHtml(id)}" data-name="${escapeHtml(inst.name || id)}">
                关闭实例
              </button>
            </div>`;
          })
          .join('')}
      </div>`;

    // Attach click listeners to stop buttons
    instanceContainer.querySelectorAll('.btn-stop').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        const name = btn.getAttribute('data-name');
        stopInstance(id, name);
      });
    });
  }

  function renderConfigControls() {
    const profileSelect = document.getElementById('profileSelect');
    const gpuSelect = document.getElementById('gpuSelect');
    if (!profileSelect || !gpuSelect) return;

    const profiles = state.config.profiles || {};
    const gpus = state.config.gpus || {};

    profileSelect.innerHTML = Object.keys(profiles)
      .map((key) => {
        const p = profiles[key];
        const selected = key === state.selectedProfile ? 'selected' : '';
        return `<option value="${escapeHtml(key)}" ${selected}>${escapeHtml(p.name || key)}</option>`;
      })
      .join('');

    gpuSelect.innerHTML = Object.keys(gpus)
      .map((key) => {
        const g = gpus[key];
        const selected = key === state.selectedGpu ? 'selected' : '';
        return `<option value="${escapeHtml(key)}" ${selected}>${escapeHtml(g.name || key)}</option>`;
      })
      .join('');

    const btnDeleteProfile = document.getElementById('btnDeleteProfile');
    if (btnDeleteProfile) {
      btnDeleteProfile.disabled = Object.keys(profiles).length === 0;
    }
  }

  function updateStartButtonState() {
    const btnStart = document.getElementById('btnStart');
    if (!btnStart) return;
    if (state.isStarting) {
      btnStart.setAttribute('disabled', 'true');
      btnStart.textContent = '⏳ 正在启动中...';
    } else {
      btnStart.removeAttribute('disabled');
      btnStart.textContent = '🚀 显式启动选定服务';
    }
  }

  function render() {
    renderErrors();
    renderGpus();
    renderInstances();
  }

  async function init() {
    const btnRefresh = document.getElementById('btnRefresh');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', async () => {
        btnRefresh.setAttribute('disabled', 'true');
        await fetchStatus();
        btnRefresh.removeAttribute('disabled');
      });
    }

    const profileSelect = document.getElementById('profileSelect');
    if (profileSelect) {
      profileSelect.addEventListener('change', (e) => {
        onProfileChange(e.target.value);
      });
    }

    const scriptTextarea = document.getElementById('scriptTextarea');
    if (scriptTextarea) {
      scriptTextarea.addEventListener('input', (e) => {
        state.script.value = e.target.value;
        invalidateScriptSearch();
        saveScriptDraft();
        updateScriptEditorState();
        setScriptStatus(isScriptDirty() ? '有未保存修改' : '脚本未修改', isScriptDirty() ? 'loading' : 'success');
      });
      scriptTextarea.addEventListener('scroll', syncScriptScroll);
      scriptTextarea.addEventListener('keydown', (e) => {
        if (e.key === 'Tab' && state.script.completions.length) {
          e.preventDefault();
          insertScriptCompletion(state.script.completionIndex);
          return;
        }
        if (e.key === 'Tab') {
          e.preventDefault();
          const start = e.target.selectionStart;
          const end = e.target.selectionEnd;
          state.script.value = `${state.script.value.slice(0, start)}  ${state.script.value.slice(end)}`;
          e.target.value = state.script.value;
          e.target.setSelectionRange(start + 2, start + 2);
          saveScriptDraft();
          updateScriptEditorState();
          return;
        }
        if (e.key === 'ArrowDown' && state.script.completions.length) {
          e.preventDefault();
          state.script.completionIndex = (state.script.completionIndex + 1) % state.script.completions.length;
          renderScriptCompletions();
          return;
        }
        if (e.key === 'ArrowUp' && state.script.completions.length) {
          e.preventDefault();
          state.script.completionIndex = (state.script.completionIndex - 1 + state.script.completions.length) % state.script.completions.length;
          renderScriptCompletions();
          return;
        }
        if (e.key === 'Enter' && state.script.completions.length) {
          e.preventDefault();
          insertScriptCompletion(state.script.completionIndex);
          return;
        }
        if (e.key === 'Escape') {
          hideScriptCompletions();
          return;
        }
        if ((e.key === ' ' || e.code === 'Space') && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          showScriptCompletions(currentScriptPrefix());
        }
      });
      scriptTextarea.addEventListener('keyup', (e) => {
        if (['Control', 'Shift', 'Alt', 'Meta', 'ArrowDown', 'ArrowUp', 'Enter', 'Escape', 'Tab'].includes(e.key)) return;
        if (!e.ctrlKey && !e.metaKey && !e.altKey) showScriptCompletions(currentScriptPrefix());
      });
    }

    const scriptEditor = document.getElementById('scriptEditor');
    const scriptSearchBar = document.getElementById('scriptSearchBar');
    const scriptSearchInput = document.getElementById('scriptSearchInput');
    const btnScriptSearch = document.getElementById('btnScriptSearch');
    const btnCloseScriptSearch = document.getElementById('btnCloseScriptSearch');
    if (scriptEditor) {
      scriptEditor.addEventListener('mouseenter', () => {
        state.script.suppressSearchFocus = false;
        showScriptSearch(false);
      });
      scriptEditor.addEventListener('mouseleave', scheduleHideScriptSearch);
      scriptEditor.addEventListener('focusin', () => {
        if (state.script.suppressSearchFocus) return;
        showScriptSearch(false);
      });
      scriptEditor.addEventListener('focusout', scheduleHideScriptSearch);
    }
    if (scriptSearchBar) {
      scriptSearchBar.addEventListener('mouseenter', () => showScriptSearch(false));
      scriptSearchBar.addEventListener('mouseleave', scheduleHideScriptSearch);
    }
    if (scriptSearchInput) {
      scriptSearchInput.addEventListener('input', () => {
        state.script.searchQuery = '';
        state.script.searchMatches = [];
        state.script.searchIndex = -1;
        setScriptSearchStatus(scriptSearchInput.value ? '输入文本后点击确定' : '请输入搜索内容');
      });
      scriptSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          searchScript(e.shiftKey ? -1 : 1);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          hideScriptSearch(true);
        }
      });
    }
    if (btnScriptSearch) btnScriptSearch.addEventListener('click', () => searchScript(1));
    if (btnCloseScriptSearch) btnCloseScriptSearch.addEventListener('click', () => hideScriptSearch(true));
    window.addEventListener('keydown', (e) => {
      if ((e.key || '').toLowerCase() === 'f' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        showScriptSearch(true);
      }
    });

    const completionList = document.getElementById('scriptCompletionList');
    if (completionList) {
      completionList.addEventListener('mousedown', (e) => {
        const item = e.target.closest('[data-completion-index]');
        if (!item) return;
        e.preventDefault();
        insertScriptCompletion(Number(item.getAttribute('data-completion-index')));
      });
    }

    const btnSaveScript = document.getElementById('btnSaveScript');
    if (btnSaveScript) btnSaveScript.addEventListener('click', saveProfileScript);
    const btnReloadScript = document.getElementById('btnReloadScript');
    if (btnReloadScript) btnReloadScript.addEventListener('click', () => loadProfileScript(state.script.profile, { force: true }));

    const gpuSelect = document.getElementById('gpuSelect');
    if (gpuSelect) {
      gpuSelect.addEventListener('change', (e) => {
        onGpuChange(e.target.value);
      });
    }

    const gpuKillProcess = document.getElementById('gpuKillProcess');
    if (gpuKillProcess) gpuKillProcess.addEventListener('click', killSelectedGpuProcess);
    document.addEventListener('pointerdown', (event) => {
      const menu = document.getElementById('gpuContextMenu');
      if (menu && !menu.hidden && !menu.contains(event.target)) closeGpuContextMenu();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeGpuContextMenu();
    });

    const btnStart = document.getElementById('btnStart');
    if (btnStart) {
      btnStart.addEventListener('click', () => {
        startInstance();
      });
    }

    const btnDeleteProfile = document.getElementById('btnDeleteProfile');
    if (btnDeleteProfile) {
      btnDeleteProfile.addEventListener('click', () => {
        deleteCurrentProfile();
      });
    }

    await fetchConfig();
    await fetchStatus();

    // 3-second polling
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(fetchStatus, 3000);
  }

    window.addEventListener('beforeunload', captureScriptDraftBeforeUnload);

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }


  // Export to window for debugging or Alpine compatibility
  window.RuntimeDashboard = {
    state,
    fetchStatus,
    fetchConfig,
    startInstance,
    stopInstance,
    deleteCurrentProfile,
    onProfileChange,
    loadProfileScript,
    saveProfileScript,
    highlightBatch,
    showScriptCompletions,
    render,
    renderInstances
  };
})();

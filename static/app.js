/**
 * AI Runtime Dashboard - Offline WebUI Application
 * Pure vanilla JS interaction controller:
 * - 3-second polling
 * - GPU hardware cards rendering
 * - Managed instances with alive / stale status badges
 * - Profile and GPU selection controls with default_gpu auto-linkage
 * - Explicit start with error handling
 * - Confirm stop with stale_cleaned notifications
 * - Error alerts
 * - Standalone capability
 */

(function () {
  const state = {
    status: { gpus: [], instances: {}, errors: [] },
    config: { gpus: {}, profiles: {} },
    selectedProfile: '',
    selectedGpu: '',
    pollTimer: null,
    isStarting: false,
    isStopping: false
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
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    return body;
  }

  async function fetchConfig() {
    try {
      state.config = await requestJson('/api/config');
      const profileKeys = Object.keys(state.config.profiles || {});
      if (profileKeys.length > 0 && !state.selectedProfile) {
        state.selectedProfile = profileKeys[0];
      }
      applyProfileDefaultGpu();
      renderConfigControls();
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
    } catch (e) {
      console.error('获取状态失败', e);
      renderErrors([`获取状态失败: ${e.message}`]);
    }
  }

  function onProfileChange(newProfileKey) {
    state.selectedProfile = newProfileKey;
    applyProfileDefaultGpu();
    renderConfigControls();
  }

  function onGpuChange(newGpuKey) {
    state.selectedGpu = newGpuKey;
  }

  async function startInstance() {
    if (state.isStarting) return;
    if (!state.selectedProfile) {
      alert('请先选择需要启动的 Profile 预设');
      return;
    }
    state.isStarting = true;
    updateStartButtonState();
    try {
      await requestJson('/api/instances/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile: state.selectedProfile,
          gpu: state.selectedGpu || undefined
        })
      });
      await fetchStatus();
    } catch (e) {
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

        return `
        <div class="gpu-card">
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
        </div>`;
      })
      .join('');
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
                    <span>进程角色: <span class="meta-value">${escapeHtml(processRole)}</span></span>
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

    const gpuSelect = document.getElementById('gpuSelect');
    if (gpuSelect) {
      gpuSelect.addEventListener('change', (e) => {
        onGpuChange(e.target.value);
      });
    }

    const btnStart = document.getElementById('btnStart');
    if (btnStart) {
      btnStart.addEventListener('click', () => {
        startInstance();
      });
    }

    await fetchConfig();
    await fetchStatus();

    // 3-second polling
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(fetchStatus, 3000);
  }

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
    onProfileChange,
    render,
    renderInstances
  };
})();

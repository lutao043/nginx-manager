/* app.js — nginx 管理端主逻辑 */
"use strict";

const App = (() => {
  let treeData = null;      // 配置树
  let currentFile = null;   // 当前编辑文件相对路径
  let statusTimer = null;   // 状态轮询
  let editing = false;      // 编辑器是否有未保存改动

  /* ---------- 初始化 ---------- */
  async function init() {
    bindEvents();
    try {
      const s = await api.settings();
      if (s.configured) {
        enterDashboard();
      } else {
        showWizard();
      }
    } catch (e) {
      showWizard();
      toast(e.message, "error");
    }
    startStatusPolling();
  }

  /* ---------- 视图切换 ---------- */
  function showWizard() {
    $("#wizard").hidden = false;
    $("#dashboard").hidden = true;
  }

  async function enterDashboard() {
    $("#wizard").hidden = true;
    $("#dashboard").hidden = false;
    refreshStatus();
    await Promise.all([loadTree(), loadBackups(), loadErrorLog()]);
    toast("已加载 nginx 配置", "success");
  }

  /* ---------- 事件绑定 ---------- */
  function bindEvents() {
    // 向导
    $("#btnWizSave").addEventListener("click", saveWizard);
    // 设置弹窗
    $("#btnSettings").addEventListener("click", openSettings);
    $("#btnSaveSettings").addEventListener("click", saveSettings);
    bindModalClose("#settingsModal");
    // 树刷新
    $("#btnRefreshTree").addEventListener("click", loadTree);
    // 状态操作
    $("#btnStart").addEventListener("click", () => doNginxAction("start", "启动 nginx 服务？"));
    $("#btnStop").addEventListener("click", () => doNginxAction("stop", "停止 nginx 服务？"));
    $("#btnReload").addEventListener("click", () => doNginxAction("reload", "重载 nginx 配置？"));
    $("#btnRestart").addEventListener("click", () => doNginxAction("restart", "重启 nginx 服务？"));
    $("#btnTest").addEventListener("click", runConfigTest);
    // 编辑器
    $("#btnSave").addEventListener("click", saveFile);
    $("#editor").addEventListener("input", () => {
      editing = true;
      $("#editorMeta").textContent = "● 有未保存的修改";
    });
    // 备份/日志
    $("#btnRefreshBackups").addEventListener("click", loadBackups);
    $("#btnRefreshLog").addEventListener("click", loadErrorLog);
  }

  /* ---------- 向导保存 ---------- */
  async function saveWizard() {
    const nginxPath = $("#wizNginxPath").value.trim();
    const confDir = $("#wizConfDir").value.trim();
    if (!nginxPath || !confDir) {
      showWizError("请填写完整路径");
      return;
    }
    try {
      await api.saveSettings(nginxPath, confDir);
      hideWizError();
      await enterDashboard();
    } catch (e) {
      showWizError(e.message);
    }
  }

  function showWizError(msg) {
    const el = $("#wizError");
    el.textContent = msg;
    el.hidden = false;
  }
  function hideWizError() { $("#wizError").hidden = true; }

  /* ---------- 设置弹窗 ---------- */
  async function openSettings() {
    try {
      const s = await api.settings();
      $("#setNginxPath").value = s.nginxPath || "";
      $("#setConfDir").value = s.confDir || "";
      $("#setError").hidden = true;
      lockBody();
      openModal("#settingsModal");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function saveSettings() {
    const nginxPath = $("#setNginxPath").value.trim();
    const confDir = $("#setConfDir").value.trim();
    if (!nginxPath || !confDir) {
      $("#setError").textContent = "请填写完整路径";
      $("#setError").hidden = false;
      return;
    }
    try {
      await api.saveSettings(nginxPath, confDir);
      closeModal("#settingsModal");
      unlockBody();
      toast("设置已保存", "success");
      // 配置可能变化，刷新整个面板
      await loadTree();
      refreshStatus();
      loadBackups();
      loadErrorLog();
    } catch (e) {
      $("#setError").textContent = e.message;
      $("#setError").hidden = false;
    }
  }

  /* ---------- 状态轮询 ---------- */
  function startStatusPolling() {
    refreshStatus();
    if (statusTimer) clearInterval(statusTimer);
    statusTimer = setInterval(refreshStatus, 10000);
  }

  async function refreshStatus() {
    try {
      const st = await api.status();
      const badge = $("#statusBadge");
      if (st.running) {
        badge.textContent = "运行中";
        badge.className = "badge badge-running";
      } else {
        badge.textContent = "已停止";
        badge.className = "badge badge-stopped";
      }
      $("#stRunning").textContent = st.running ? "运行中" : "已停止";
      $("#stVersion").textContent = st.version || "—";
      $("#stPid").textContent = st.pid || "—";
      $("#stConf").textContent = st.confPath || "—";
    } catch (e) {
      // 服务不可达时静默，保底显示
    }
  }

  /* ---------- 配置树 ---------- */
  async function loadTree() {
    try {
      const data = await api.configTree();
      treeData = data.tree || [];
      renderTree();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function renderTree() {
    const nav = $("#fileTree");
    nav.innerHTML = "";
    if (!treeData.length) {
      nav.innerHTML = '<div class="muted" style="padding:8px 12px">未找到配置文件</div>';
      return;
    }
    treeData.forEach((node) => nav.appendChild(renderTreeNode(node, 0)));
  }

  function renderTreeNode(node, depth) {
    const wrap = document.createElement("div");
    const isDir = node.isDir;
    const item = document.createElement("div");
    item.className = "tree-item" + (currentFile === node.path ? " active" : "");
    item.style.paddingLeft = (8 + depth * 14) + "px";

    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = isDir ? "▾" : "";
    const icon = document.createElement("span");
    icon.className = "icon";
    icon.textContent = isDir ? "📁" : "📄";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = node.name;

    item.appendChild(arrow);
    item.appendChild(icon);
    item.appendChild(name);
    wrap.appendChild(item);

    if (isDir && node.children && node.children.length) {
      const children = document.createElement("div");
      children.className = "tree-children";
      node.children.forEach((c) => children.appendChild(renderTreeNode(c, depth + 1)));
      wrap.appendChild(children);
    }

    if (!isDir) {
      item.addEventListener("click", () => openFile(node.path));
    }
    return wrap;
  }

  /* ---------- 文件编辑 ---------- */
  async function openFile(path) {
    if (editing) {
      const ok = await confirmDialog("当前文件有未保存的修改，放弃修改并切换文件？");
      if (!ok) return;
      editing = false;
    }
    currentFile = path;
    renderTree(); // 高亮
    try {
      const data = await api.readFile(path);
      $("#editor").value = data.content;
      $("#editorTitle").textContent = data.path;
      $("#editorPanel").hidden = false;
      $("#emptyPanel").hidden = true;
      $("#editorMeta").textContent = "";
      $("#saveWarning").hidden = true;
      editing = false;
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function saveFile() {
    if (!currentFile) return;
    const ok = await confirmDialog("保存将自动备份原文件，并执行 nginx -t 校验。确认保存？");
    if (!ok) return;
    const content = $("#editor").value;
    try {
      const res = await api.saveFile(currentFile, content, true);
      $("#editorMeta").textContent = "已保存，备份 " + (res.backupId || "");
      showTestResult(res.test);
      $("#saveWarning").hidden = true;
      editing = false;
      toast("配置已保存", "success");
      loadBackups();
    } catch (e) {
      if (e.status === 409 && e.payload && e.payload.saved) {
        // 已保存但校验失败
        $("#saveWarning").hidden = false;
        $("#saveWarning").innerHTML =
          "⚠ " + escapeHtml(e.message) +
          '<div class="actions"><button class="btn btn-mini" id="btnRollback">回滚到备份 ' + escapeHtml(e.payload.backupId || "") + "</button></div>";
        const rb = $("#btnRollback");
        if (rb) rb.addEventListener("click", () => rollbackFile(e.payload.backupId));
        showTestResult(e.payload.test);
        editing = false;
        toast("配置已保存，但校验失败", "error");
      } else {
        toast(e.message, "error");
      }
    }
  }

  async function rollbackFile(backupId) {
    if (!backupId) return;
    const ok = await confirmDialog("将当前配置回滚到备份 " + backupId + "？");
    if (!ok) return;
    try {
      await api.restoreBackup(backupId);
      toast("已回滚，正在重新加载…", "success");
      await loadTree();
      if (currentFile) openFile(currentFile);
      loadBackups();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function showTestResult(test) {
    const el = $("#testResult");
    if (!test) { el.hidden = true; return; }
    el.textContent = test.output || "";
    el.className = "test-result " + (test.ok ? "ok" : "fail");
    el.hidden = false;
  }

  /* ---------- 配置校验 ---------- */
  async function runConfigTest() {
    toast("正在执行 nginx -t …");
    try {
      const res = await api.testConfig();
      showTestResult(res);
      toast(res.ok ? "配置校验通过" : "配置校验失败", res.ok ? "success" : "error");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* ---------- nginx 操作 ---------- */
  async function doNginxAction(action, msg) {
    const ok = await confirmDialog(msg);
    if (!ok) return;
    try {
      const res = await api.nginxAction(action);
      toast(res.message || "操作成功", "success");
      refreshStatus();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* ---------- 备份 ---------- */
  async function loadBackups() {
    try {
      const data = await api.backups();
      const list = $("#backupList");
      const items = data.backups || [];
      if (!items.length) {
        list.innerHTML = '<p class="muted">暂无备份</p>';
        return;
      }
      list.innerHTML = "";
      items.forEach((b) => {
        const row = document.createElement("div");
        row.className = "backup-item";
        const meta = document.createElement("div");
        meta.innerHTML = '<span class="meta">' + escapeHtml(b.createdAt) + "</span>";
        const files = document.createElement("span");
        files.className = "files";
        files.title = (b.files || []).join("\n");
        files.textContent = (b.files || []).join(", ");
        const btn = document.createElement("button");
        btn.className = "btn btn-mini";
        btn.textContent = "回滚";
        btn.addEventListener("click", () => doRestore(b));
        row.appendChild(meta);
        row.appendChild(files);
        row.appendChild(btn);
        list.appendChild(row);
      });
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function doRestore(backup) {
    const ok = await confirmDialog(
      "回滚到备份 " + backup.id + "（" + backup.createdAt + "）？\n将覆盖当前配置并执行 nginx -t 校验。"
    );
    if (!ok) return;
    try {
      const res = await api.restoreBackup(backup.id);
      toast("回滚成功", "success");
      showTestResult(res.test);
      await loadTree();
      if (currentFile) openFile(currentFile);
      loadBackups();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* ---------- 错误日志 ---------- */
  async function loadErrorLog() {
    try {
      const data = await api.errorLog(200);
      $("#logPathLabel").textContent = data.logPath ? "📄 " + data.logPath : "";
      $("#errorLog").textContent = data.content || "（错误日志为空或文件不存在）";
    } catch (e) {
      toast(e.message, "error");
    }
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", App.init);

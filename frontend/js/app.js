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
    // 页签
    $("#tabConfig").addEventListener("click", () => switchTab("config"));
    $("#tabProxies").addEventListener("click", () => switchTab("proxies"));
    // 代理
    $("#btnRefreshProxies").addEventListener("click", loadProxies);
    $("#btnAddProxy").addEventListener("click", openAddProxy);
    $("#btnConfirmAddProxy").addEventListener("click", confirmAddProxy);
    $("#btnSaveTargets").addEventListener("click", saveTargets);
    // 目标地址池
    $("#btnAddPoolTarget").addEventListener("click", openAddPool);
    $("#btnConfirmAddPool").addEventListener("click", confirmAddPool);
    bindModalClose("#addProxyModal");
    bindModalClose("#editTargetsModal");
    bindModalClose("#addPoolModal");
  }

  /* ---------- 页签切换 ---------- */
  function switchTab(name) {
    const isConfig = name === "config";
    $("#tabConfig").classList.toggle("active", isConfig);
    $("#tabProxies").classList.toggle("active", !isConfig);
    $("#viewConfig").hidden = !isConfig;
    $("#viewProxies").hidden = isConfig;
    if (!isConfig) {
      loadPool();
      loadProxies();
    }
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

  /* ---------- 代理管理 ---------- */
  async function loadProxies() {
    try {
      const data = await api.proxies();
      renderProxyList(data.proxies || []);
    } catch (e) {
      $("#proxyList").innerHTML = '<p class="muted">加载失败：' + escapeHtml(e.message) + "</p>";
    }
  }

  /* 目标地址池（模块内状态，加载后供下拉合并使用） */
  let poolTargets = [];

  async function loadPool() {
    try {
      const data = await api.proxyPool();
      poolTargets = data.targets || [];
      renderPoolList();
    } catch (e) {
      poolTargets = [];
      $("#poolList").innerHTML = '<p class="muted">加载失败</p>';
    }
  }

  function renderPoolList() {
    const list = $("#poolList");
    if (!poolTargets.length) {
      list.innerHTML = '<p class="muted">暂无目标地址，点击「添加目标」创建</p>';
      return;
    }
    list.innerHTML = "";
    poolTargets.forEach((t) => {
      const item = document.createElement("span");
      item.className = "pool-item";
      const label = document.createElement("span");
      label.textContent = t;
      label.title = t;
      const del = document.createElement("button");
      del.className = "pool-del";
      del.type = "button";
      del.textContent = "×";
      del.title = "删除";
      del.addEventListener("click", () => doRemovePoolTarget(t));
      item.appendChild(label);
      item.appendChild(del);
      list.appendChild(item);
    });
  }

  async function doRemovePoolTarget(target) {
    const ok = await confirmDialog("从目标池删除 " + target + "？\n已写入各代理的备选不受影响。");
    if (!ok) return;
    try {
      await api.removePoolTarget(target);
      poolTargets = poolTargets.filter((t) => t !== target);
      renderPoolList();
      loadProxies(); // 重新渲染下拉（去掉已删目标）
      toast("已从池删除: " + target, "success");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function openAddPool() {
    $("#addPoolTarget").value = "";
    $("#addPoolError").hidden = true;
    lockBody();
    openModal("#addPoolModal");
  }

  async function confirmAddPool() {
    const target = $("#addPoolTarget").value.trim();
    if (!target) {
      $("#addPoolError").textContent = "目标地址必填";
      $("#addPoolError").hidden = false;
      return;
    }
    try {
      const res = await api.addPoolTarget(target);
      closeModal("#addPoolModal");
      unlockBody();
      poolTargets = res.targets || [];
      renderPoolList();
      loadProxies();
      toast("已添加目标: " + target, "success");
    } catch (e) {
      $("#addPoolError").textContent = e.message;
      $("#addPoolError").hidden = false;
    }
  }

  function showProxyTest(test) {
    const el = $("#proxyTestResult");
    if (!test) { el.hidden = true; return; }
    el.textContent = test.output || "";
    el.className = "test-result " + (test.ok ? "ok" : "fail");
    el.hidden = false;
  }

  function renderProxyList(proxies) {
    const list = $("#proxyList");
    if (!proxies.length) {
      list.innerHTML = '<p class="muted">暂无代理，点击「添加代理」创建</p>';
      return;
    }
    list.innerHTML = "";
    proxies.forEach((p) => {
      const item = document.createElement("div");
      item.className = "proxy-item";

      const head = document.createElement("div");
      head.className = "proxy-item-head";
      const path = document.createElement("span");
      path.className = "proxy-path";
      path.textContent = p.path;
      const active = document.createElement("span");
      active.className = "proxy-active";
      active.textContent = "当前 → " + p.active;
      head.appendChild(path);
      head.appendChild(active);

      const row = document.createElement("div");
      row.className = "proxy-item-row";
      const select = document.createElement("select");
      // 下拉选项 = 目标池地址 ∪ 该代理已有地址（去重，保持顺序）
      const merged = [];
      poolTargets.forEach((t) => { if (!merged.includes(t)) merged.push(t); });
      (p.targets || []).forEach((t) => { if (!merged.includes(t)) merged.push(t); });
      merged.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        const inProxy = (p.targets || []).includes(t);
        const inPool = poolTargets.includes(t);
        const tags = [];
        if (t === p.active) tags.push("当前");
        if (!inProxy && inPool) tags.push("池");
        opt.textContent = t + (tags.length ? "（" + tags.join("+") + "）" : "");
        if (t === p.active) opt.selected = true;
        select.appendChild(opt);
      });
      const btnSwitch = document.createElement("button");
      btnSwitch.className = "btn btn-primary btn-mini";
      btnSwitch.textContent = "切换";
      btnSwitch.addEventListener("click", () => doSwitchProxy(p, select.value));

      const btnEdit = document.createElement("button");
      btnEdit.className = "btn btn-mini";
      btnEdit.textContent = "编辑备选";
      btnEdit.addEventListener("click", () => openEditTargets(p));

      const btnDel = document.createElement("button");
      btnDel.className = "btn btn-mini";
      btnDel.textContent = "删除";
      btnDel.addEventListener("click", () => doRemoveProxy(p));

      const actions = document.createElement("div");
      actions.className = "proxy-item-actions";
      actions.appendChild(btnSwitch);
      actions.appendChild(btnEdit);
      actions.appendChild(btnDel);

      row.appendChild(select);
      row.appendChild(actions);

      item.appendChild(head);
      item.appendChild(row);
      list.appendChild(item);
    });
  }

  async function doSwitchProxy(p, target) {
    if (!target || target === p.active) return;
    const ok = await confirmDialog("将代理 " + p.path + " 切换到 " + target + "？\n将自动备份并校验配置。");
    if (!ok) return;
    try {
      const res = await api.switchProxy(p.path, target);
      showProxyTest(res.test);
      toast("已切换: " + p.path + " → " + target, "success");
      loadProxies();
      refreshStatus();
    } catch (e) {
      showProxyTest(e.payload && e.payload.test);
      toast(e.message, "error");
    }
  }

  async function doRemoveProxy(p) {
    const ok = await confirmDialog("删除代理 " + p.path + "？\n将移除整个 location 块并校验配置。");
    if (!ok) return;
    try {
      const res = await api.removeProxy(p.path);
      showProxyTest(res.test);
      toast("已删除代理: " + p.path, "success");
      loadProxies();
      refreshStatus();
    } catch (e) {
      showProxyTest(e.payload && e.payload.test);
      toast(e.message, "error");
    }
  }

  /* 添加代理弹窗 */
  function openAddProxy() {
    $("#addProxyPath").value = "";
    $("#addProxyTarget").value = "";
    $("#addProxyError").hidden = true;
    lockBody();
    openModal("#addProxyModal");
  }

  async function confirmAddProxy() {
    const path = $("#addProxyPath").value.trim();
    const target = $("#addProxyTarget").value.trim();
    if (!path || !target) {
      showAddProxyError("路径与目标地址均必填");
      return;
    }
    try {
      const res = await api.addProxy(path, target);
      closeModal("#addProxyModal");
      unlockBody();
      showProxyTest(res.test);
      toast("已添加代理: " + path, "success");
      loadProxies();
      refreshStatus();
    } catch (e) {
      showAddProxyError(e.message);
    }
  }

  function showAddProxyError(msg) {
    const el = $("#addProxyError");
    el.textContent = msg;
    el.hidden = false;
  }

  /* 编辑备选弹窗 */
  let editingProxy = null;

  function openEditTargets(p) {
    editingProxy = p;
    $("#editTargetsTitle").textContent = "编辑备选目标 — " + p.path;
    const list = $("#editTargetsList");
    list.innerHTML = "";
    (p.targets || []).forEach((t, i) => {
      list.appendChild(buildTargetRow(t, t === p.active, i === 0));
    });
    $("#editTargetsError").hidden = true;
    lockBody();
    openModal("#editTargetsModal");
  }

  function buildTargetRow(value, isActive, isFirst) {
    const row = document.createElement("div");
    row.className = "targets-edit-row";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "activeTarget";
    radio.checked = !!isActive;
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = "http://host:port/";
    const del = document.createElement("button");
    del.className = "btn-remove-row";
    del.type = "button";
    del.textContent = "×";
    del.title = "删除此行";
    del.addEventListener("click", () => row.remove());
    row.appendChild(radio);
    row.appendChild(input);
    row.appendChild(del);
    return row;
  }

  async function saveTargets() {
    if (!editingProxy) return;
    const rows = Array.from($$("#editTargetsList .targets-edit-row"));
    const targets = [];
    let activeIdx = 0;
    rows.forEach((row, i) => {
      const input = row.querySelector('input[type="text"]');
      const radio = row.querySelector('input[type="radio"]');
      const val = (input.value || "").trim();
      if (val && !targets.includes(val)) {
        targets.push(val);
        if (radio.checked) activeIdx = targets.indexOf(val);
      }
    });
    if (!targets.length) {
      $("#editTargetsError").textContent = "至少保留一个目标地址";
      $("#editTargetsError").hidden = false;
      return;
    }
    try {
      const res = await api.saveProxyTargets(editingProxy.path, targets);
      closeModal("#editTargetsModal");
      unlockBody();
      showProxyTest(res.test);
      toast("已更新备选目标", "success");
      loadProxies();
      refreshStatus();
    } catch (e) {
      $("#editTargetsError").textContent = e.message;
      $("#editTargetsError").hidden = false;
    }
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", App.init);

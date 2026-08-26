/* app.js — nginx 管理端主逻辑 */
"use strict";

const App = (() => {
  let treeData = null;      // 配置树
  let currentFile = null;   // 当前编辑文件相对路径
  let statusTimer = null;   // 状态轮询
  let editing = false;      // 编辑器是否有未保存改动
  let preview = false;      // 预览模式（未配置 nginx）

  /* ---------- 初始化 ---------- */
  async function init() {
    bindEvents();
    try {
      const s = await api.settings();
      preview = !!s.preview;
      if (s.configured || s.preview) {
        enterDashboard();
      } else {
        showWizard();
      }
    } catch (e) {
      showWizard();
      toast(e.message, "error");
    }
    startStatusPolling();
    // 首次入场动画播完后摘掉 first-load 标记，之后的搜索/切页签不再重放动画
    setTimeout(() => document.body.classList.remove("first-load"), 700);
  }

  /* 预览模式拦截：未配置 nginx 时，写操作给出明确提示而非底层 409 */
  function inPreviewGuard(actionName) {
    if (!preview) return false;
    toast("预览模式：请先在「设置」中配置 nginx 后再" + (actionName || "操作"), "error");
    return true;
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
    if (preview) {
      toast("预览模式：未配置 nginx，可浏览界面；点「设置」配置后可操作", "info");
    } else {
      toast("已加载 nginx 配置", "success");
    }
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
    // 代理搜索：150ms 防抖过滤，避免每敲一个字符全量重建列表（低端机掉帧源）
    const onProxySearchInput = (e) => {
      proxySearch = e.target.value;
      renderProxyList(filterProxies());
      $("#btnClearProxySearch").hidden = !proxySearch.trim();
      const cnt = document.getElementById("proxyCount");
      if (cnt) cnt.textContent = proxySearch ? `（${filterProxies().length}/${allProxies.length}）` : `（${allProxies.length}）`;
    };
    $("#proxySearch").addEventListener("input", debounce(onProxySearchInput, 150));
    $("#btnClearProxySearch").addEventListener("click", () => {
      proxySearch = "";
      $("#proxySearch").value = "";
      $("#btnClearProxySearch").hidden = true;
      renderProxyList(filterProxies());
      const cnt = document.getElementById("proxyCount");
      if (cnt) cnt.textContent = `（${allProxies.length}）`;
    });
    // 目标地址池
    $("#btnAddPoolTarget").addEventListener("click", openAddPool);
    $("#btnConfirmAddPool").addEventListener("click", () => {
      if (editingPoolItem) savePoolAlias();
      else confirmAddPool();
    });
    bindModalClose("#addProxyModal");
    bindModalClose("#editTargetsModal");
    bindModalClose("#addPoolModal", () => {
      // 关闭池弹窗时重置编辑态
      if (editingPoolItem) {
        editingPoolItem = null;
        $("#addPoolTarget").disabled = false;
        $("#addPoolModal .modal-head .panel-title").textContent = "添加目标地址";
        $("#btnConfirmAddPool").textContent = "添加";
      }
    });
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
  let currentSettingsPort = null; // 当前服务实际端口（用于判断是否变更）

  async function openSettings() {
    try {
      const s = await api.settings();
      currentSettingsPort = s.port || 8310;
      $("#setNginxPath").value = s.nginxPath || "";
      $("#setConfDir").value = s.confDir || "";
      $("#setBackupRetention").value = s.backupRetention != null ? s.backupRetention : 7;
      $("#setPort").value = s.port || 8310;
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
    const retentionRaw = $("#setBackupRetention").value.trim();
    const portRaw = $("#setPort").value.trim();
    if (!nginxPath || !confDir) {
      $("#setError").textContent = "请填写完整路径";
      $("#setError").hidden = false;
      return;
    }
    const retention = retentionRaw === "" ? null : parseInt(retentionRaw, 10);
    if (retention !== null && (isNaN(retention) || retention < 0 || retention > 100)) {
      $("#setError").textContent = "保留份数必须为 0~100 的整数";
      $("#setError").hidden = false;
      return;
    }
    const newPort = portRaw === "" ? null : parseInt(portRaw, 10);
    if (newPort !== null && (isNaN(newPort) || newPort < 1 || newPort > 65535)) {
      $("#setError").textContent = "端口必须为 1~65535 的整数";
      $("#setError").hidden = false;
      return;
    }
    // 端口变更 → 确认自动重启
    const portChanged = newPort !== null && newPort !== currentSettingsPort;
    if (portChanged) {
      const ok = await confirmDialog("端口将改为 " + newPort + "，服务会自动重启并打开新地址。确认？");
      if (!ok) return;
    }
    try {
      await api.saveSettings(nginxPath, confDir, retention, newPort);
      closeModal("#settingsModal");
      unlockBody();
      if (portChanged) {
        // 触发服务重启，等待新端口就绪后跳转
        toast("端口已更新，服务重启中…", "success");
        try { await api.restart(newPort); } catch (e) { /* 重启瞬间连接断开属正常 */ }
        const newUrl = "http://" + window.location.hostname + ":" + newPort + "/";
        const ready = await waitForServer(newUrl, 40);
        if (ready) {
          toast("服务已在新端口启动", "success");
          window.location.href = newUrl;
        } else {
          toast("服务重启中，请稍后手动访问 " + newUrl, "error");
        }
        return;
      }
      toast("设置已保存", "success");
      if (preview) {
        // 预览模式下配置 nginx 后，重载以退出预览、进入正常管理模式
        toast("配置已生效，正在重新加载…", "success");
        window.location.reload();
        return;
      }
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

  /* 轮询等待服务就绪（最多 maxSeconds 秒） */
  async function waitForServer(url, maxSeconds) {
    for (let i = 0; i < maxSeconds * 2; i++) {
      try {
        const r = await fetch(url + "api/status");
        if (r.ok) return true;
      } catch (e) { /* 未就绪，继续等 */ }
      await new Promise((res) => setTimeout(res, 500));
    }
    return false;
  }

  /* ---------- 状态轮询 ---------- */
  function startStatusPolling() {
    refreshStatus();
    if (statusTimer) clearInterval(statusTimer);
    statusTimer = setInterval(refreshStatus, 10000);
  }

  let lastStatusKey = null; // 上次状态 key，避免每 10s 无谓重写 DOM 重启动画

  async function refreshStatus() {
    try {
      const st = await api.status();
      const badge = $("#statusBadge");
      let key, text;
      if (preview) { key = "preview"; text = "预览模式"; }
      else if (st.running) { key = "running"; text = "运行中"; }
      else { key = "stopped"; text = "已停止"; }
      if (key !== lastStatusKey) {
        lastStatusKey = key;
        badge.textContent = text;
        badge.className = "badge badge-" + (key === "running" ? "running" : key === "stopped" ? "stopped" : "unknown");
        $("#stRunning").textContent = text;
      }
      $("#stVersion").textContent = st.version || "—";
      $("#stPid").textContent = st.pid || "—";
      $("#stConf").textContent = st.confPath || (preview ? "（预览模式）" : "—");
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
    if (inPreviewGuard("保存配置")) return;
    const choice = await confirmChoice("保存修改？备份仅在你显式确认时执行。", [
      { label: "保存并备份", value: "backup", primary: true },
      { label: "仅保存", value: "save" },
    ]);
    if (!choice) return;
    const doBackup = choice === "backup";
    const content = $("#editor").value;
    try {
      const res = await api.saveFile(currentFile, content, true, doBackup);
      $("#editorMeta").textContent = res.backedUp
        ? "已保存，备份 " + (res.backupId || "")
        : "已保存（未备份）";
      showTestResult(res.test);
      $("#saveWarning").hidden = true;
      editing = false;
      toast(res.backedUp ? "配置已保存并备份" : "配置已保存（未备份）", "success");
      loadBackups();
    } catch (e) {
      if (e.status === 409 && e.payload && e.payload.saved) {
        // 已保存但校验失败
        $("#saveWarning").hidden = false;
        const backupLabel = e.payload.backupId ? "回滚到备份 " + e.payload.backupId : "回滚到备份";
        $("#saveWarning").innerHTML =
          "⚠ " + escapeHtml(e.message) +
          '<div class="actions"><button class="btn btn-mini" id="btnRollback">' + escapeHtml(backupLabel) + "</button></div>";
        const rb = $("#btnRollback");
        if (rb && e.payload.backupId) rb.addEventListener("click", () => rollbackFile(e.payload.backupId));
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
    if (inPreviewGuard("校验配置")) return;
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
    if (inPreviewGuard("操作 nginx 服务")) return;
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
  function renderBackups(items, retention) {
    const list = $("#backupList");
    const tip = $("#backupRetentionTip");
    if (tip) tip.textContent = retention > 0 ? `自动保留最近 ${retention} 份（可在设置中调整）` : "未启用自动清理（可在设置中调整）";
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
      const actions = document.createElement("div");
      actions.className = "backup-actions";
      const btnRestore = document.createElement("button");
      btnRestore.className = "btn btn-mini";
      btnRestore.textContent = "回滚";
      btnRestore.addEventListener("click", () => doRestore(b));
      const btnDel = document.createElement("button");
      btnDel.className = "btn btn-mini btn-danger";
      btnDel.textContent = "删除";
      btnDel.addEventListener("click", () => doDeleteBackup(b));
      actions.appendChild(btnRestore);
      actions.appendChild(btnDel);
      row.appendChild(meta);
      row.appendChild(files);
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  async function loadBackups() {
    try {
      const data = await api.backups();
      renderBackups(data.backups || [], data.retention);
    } catch (e) {
      toast(e.message, "error");
    }
  }

  async function doDeleteBackup(backup) {
    const ok = await confirmDialog("删除备份 " + backup.id + "（" + backup.createdAt + "）？\n该操作不可恢复。");
    if (!ok) return;
    try {
      const res = await api.deleteBackup(backup.id);
      toast("已删除备份: " + backup.id, "success");
      renderBackups(res.backups || [], res.retention != null ? res.retention : 7);
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
  let allProxies = [];      // 全量代理列表（搜索过滤用）
  let proxySearch = "";     // 当前搜索关键词

  function filterProxies() {
    const kw = proxySearch.trim().toLowerCase();
    if (!kw) return allProxies;
    return allProxies.filter((p) => {
      if (p.path && p.path.toLowerCase().includes(kw)) return true;
      return (p.targets || []).some((t) => t.toLowerCase().includes(kw));
    });
  }

  async function loadProxies() {
    try {
      const data = await api.proxies();
      allProxies = data.proxies || [];
      renderProxyList(filterProxies());
      const cnt = document.getElementById("proxyCount");
      if (cnt) cnt.textContent = proxySearch ? `（${filterProxies().length}/${allProxies.length}）` : `（${allProxies.length}）`;
    } catch (e) {
      $("#proxyList").innerHTML = '<p class="muted">加载失败：' + escapeHtml(e.message) + "</p>";
    }
  }

  /* 目标地址池（模块内状态，加载后供下拉合并使用）
     条目结构: {target, alias} */
  let poolTargets = [];

  function poolTargetList() { return poolTargets.map((p) => p.target); }

  function poolAlias(target) {
    const item = poolTargets.find((p) => p.target === target);
    return item ? item.alias : "";
  }

  function poolLabel(target) {
    const alias = poolAlias(target);
    return alias ? alias + " (" + target + ")" : target;
  }

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
    poolTargets.forEach((item) => {
      const t = item.target;
      const chip = document.createElement("span");
      chip.className = "pool-item";
      const label = document.createElement("span");
      label.textContent = item.alias ? item.alias + " (" + t + ")" : t;
      label.title = t;
      const editBtn = document.createElement("button");
      editBtn.className = "pool-del";
      editBtn.type = "button";
      editBtn.textContent = "✎";
      editBtn.title = "编辑别名";
      editBtn.addEventListener("click", () => openEditPoolAlias(item));
      const del = document.createElement("button");
      del.className = "pool-del";
      del.type = "button";
      del.textContent = "×";
      del.title = "删除";
      del.addEventListener("click", () => doRemovePoolTarget(t));
      chip.appendChild(label);
      chip.appendChild(editBtn);
      chip.appendChild(del);
      list.appendChild(chip);
    });
  }

  async function doRemovePoolTarget(target) {
    const ok = await confirmDialog("从目标池删除 " + target + "？\n已写入各代理的备选不受影响。");
    if (!ok) return;
    try {
      await api.removePoolTarget(target);
      poolTargets = poolTargets.filter((p) => p.target !== target);
      renderPoolList();
      loadProxies(); // 重新渲染下拉（去掉已删目标）
      toast("已从池删除: " + target, "success");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function openAddPool() {
    $("#addPoolTarget").value = "";
    $("#addPoolAlias").value = "";
    $("#addPoolError").hidden = true;
    lockBody();
    openModal("#addPoolModal");
  }

  async function confirmAddPool() {
    const target = $("#addPoolTarget").value.trim();
    const alias = $("#addPoolAlias").value.trim();
    if (!target) {
      $("#addPoolError").textContent = "目标地址必填";
      $("#addPoolError").hidden = false;
      return;
    }
    try {
      const res = await api.addPoolTarget(target, alias);
      closeModal("#addPoolModal");
      unlockBody();
      poolTargets = res.targets || [];
      renderPoolList();
      loadProxies();
      toast(alias ? "已添加目标: " + alias : "已添加目标: " + target, "success");
    } catch (e) {
      $("#addPoolError").textContent = e.message;
      $("#addPoolError").hidden = false;
    }
  }

  /* 编辑池条目别名 */
  let editingPoolItem = null;

  function openEditPoolAlias(item) {
    editingPoolItem = item;
    $("#addPoolTarget").value = item.target;
    $("#addPoolTarget").disabled = true;
    $("#addPoolAlias").value = item.alias || "";
    $("#addPoolError").hidden = true;
    $("#addPoolModal .modal-head .panel-title").textContent = "编辑别名 — " + item.target;
    $("#btnConfirmAddPool").textContent = "保存";
    lockBody();
    openModal("#addPoolModal");
  }

  async function savePoolAlias() {
    if (!editingPoolItem) return;
    const alias = $("#addPoolAlias").value.trim();
    try {
      const res = await api.setPoolAlias(editingPoolItem.target, alias);
      closeModal("#addPoolModal");
      unlockBody();
      poolTargets = res.targets || [];
      renderPoolList();
      loadProxies();
      toast("别名已更新", "success");
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
      // 下拉选项 = 目标池地址 ∪ 该代理已有地址（去重，保持顺序）；池地址带别名显示
      const merged = [];
      poolTargets.forEach((pt) => { if (!merged.includes(pt.target)) merged.push(pt.target); });
      (p.targets || []).forEach((t) => { if (!merged.includes(t)) merged.push(t); });
      merged.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        const inProxy = (p.targets || []).includes(t);
        const inPool = poolTargetList().includes(t);
        const alias = poolAlias(t);
        const tags = [];
        if (t === p.active) tags.push("当前");
        if (!inProxy && inPool) tags.push("池");
        const display = alias ? alias + " (" + t + ")" : t;
        opt.textContent = display + (tags.length ? "（" + tags.join("+") + "）" : "");
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
    if (inPreviewGuard("切换代理")) return;
    const ok = await confirmDialog("将代理 " + p.path + " 切换到 " + target + "？\n将自动备份并校验配置。");
    if (!ok) return;
    try {
      const res = await api.switchProxy(p.path, target);
      showProxyTest(res.test);
      toast("已切换: " + p.path + " → " + target, "success");
      loadProxies();
      refreshStatus();
      // 切换成功后询问是否立即重载配置
      const reloadNow = await confirmDialog("配置已切换并校验通过。\n是否立即重载 nginx 配置？");
      if (!reloadNow) return;
      const reloadRes = await api.nginxAction("reload");
      if (reloadRes && reloadRes.ok) {
        toast("nginx 配置已重载", "success");
      } else {
        toast((reloadRes && reloadRes.message) || "重载失败", "error");
      }
      refreshStatus();
    } catch (e) {
      showProxyTest(e.payload && e.payload.test);
      toast(e.message, "error");
    }
  }

  async function doRemoveProxy(p) {
    if (inPreviewGuard("删除代理")) return;
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
    if (inPreviewGuard("添加代理")) return;
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
    if (inPreviewGuard("编辑备选目标")) return;
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

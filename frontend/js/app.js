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
    // 查看/更改当前管理的配置文件地址（打开设置弹窗并定位到配置目录输入框）
    $("#btnChangeConf").addEventListener("click", () => {
      openSettings();
      setTimeout(() => { const el = $("#setConfDir"); if (el) { el.focus(); el.select(); } }, 60);
    });
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
    // 目标地址池（弹窗入口在代理列表头部，代理再多无需滚动）
    $("#btnOpenPool").addEventListener("click", openPool);
    $("#btnAddPoolTarget").addEventListener("click", openAddPool);
    $("#btnConfirmAddPool").addEventListener("click", () => {
      if (editingPoolItem) savePoolAlias();
      else confirmAddPool();
    });
    bindModalClose("#addProxyModal");
    bindModalClose("#editTargetsModal");
    bindModalClose("#poolModal");
    bindModalClose("#addPoolModal", () => {
      // 关闭池弹窗时重置编辑态
      if (editingPoolItem) resetPoolModal();
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
  let currentDataDir = null;      // 当前 manager 数据目录（判断是否变更）

  async function openSettings() {
    try {
      const s = await api.settings();
      currentSettingsPort = s.port || 8310;
      currentDataDir = s.dataDir || null;
      $("#setNginxPath").value = s.nginxPath || "";
      $("#setConfDir").value = s.confDir || "";
      $("#setBackupRetention").value = s.backupRetention != null ? s.backupRetention : 7;
      $("#setPort").value = s.port || 8310;
      $("#setDataDir").value = s.dataDir || "";
      $("#setDataDir").disabled = !!s.dataDirLocked;
      $("#setDataDir").title = s.dataDirLocked ? "由 --data-dir 参数或 NGINX_MANAGER_DATA_DIR 环境变量指定，界面不可修改" : "";
      $("#settingsFileText").textContent = s.settingsFile || "—";
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
    const newDataDir = $("#setDataDir").value.trim();
    if (!nginxPath || !confDir) {
      $("#setError").textContent = "请填写完整路径";
      $("#setError").hidden = false;
      return;
    }
    if (!newDataDir) {
      $("#setError").textContent = "manager 数据目录不能为空";
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
    // 数据目录（manager 配置文件地址）变更 → 确认迁移 + 自动重启
    const dataDirChanged = !!currentDataDir && newDataDir !== currentDataDir;
    if (dataDirChanged) {
      const ok = await confirmDialog("manager 配置文件地址将改为：\n" + newDataDir + "\n\n现有设置与备份会迁移过去（旧目录保留），服务将自动重启。确认？");
      if (!ok) return;
    }
    try {
      const res = await api.saveSettings(nginxPath, confDir, retention, newPort, dataDirChanged ? newDataDir : null);
      closeModal("#settingsModal");
      unlockBody();
      const restarting = !!(res && res.restarting); // 后端在数据目录变更时自行重启
      const targetPort = (res && res.port) || newPort || currentSettingsPort;
      if (portChanged || restarting) {
        toast("服务重启中…", "success");
        if (portChanged && !restarting) {
          // 仅端口变更：沿用前端触发重启的旧流程
          try { await api.restart(targetPort); } catch (e) { /* 重启瞬间连接断开属正常 */ }
        }
        const newUrl = "http://" + window.location.hostname + ":" + targetPort + "/";
        const ready = await waitForServer(newUrl, 40);
        if (ready) {
          toast("服务已重启", "success");
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
      // 配置可能变化（含配置目录切换），刷新所有面板：配置树、状态、备份、日志、代理与地址池
      await loadTree();
      refreshStatus();
      loadBackups();
      loadErrorLog();
      loadProxies();
      loadPool();
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
      // 代理页顶部同步显示当前管理的配置文件完整路径
      const cp = $("#confPathText");
      if (cp) {
        cp.textContent = st.confPath || (preview ? "（预览模式，未配置）" : "—");
        cp.title = st.confPath || "";
      }
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

  /* 目标地址池（与 nginx.conf 合一：池 = 全部 proxy_pass 目标并集，读取自配置文件）
     条目结构: {target, alias}，alias 为 proxy_pass 行尾注释 */
  let poolTargets = [];

  /* 池去重口径（与后端 _pool_key 一致）：scheme/host 小写、省默认端口、路径去末尾 '/'，
     使 http://A:80/ 与 http://a 视为同一地址，避免等价写法重复展示/重复切换 */
  function targetKey(t) {
    const s = String(t || "").trim();
    const m = s.match(/^(https?):\/\/([^/?#]+)([^#]*)$/i);
    if (!m) return s.toLowerCase();
    const scheme = m[1].toLowerCase();
    let hostport = m[2].toLowerCase();
    const path = (m[3] || "").replace(/\/+$/, "");
    let host = hostport, port = "";
    const i = hostport.lastIndexOf(":");
    if (i >= 0) { host = hostport.slice(0, i); port = hostport.slice(i + 1); }
    if ((scheme === "http" && port === "80") || (scheme === "https" && port === "443")) port = "";
    hostport = port ? host + ":" + port : host;
    return scheme + "://" + hostport + path;
  }

  function poolTargetList() { return poolTargets.map((p) => p.target); }

  function poolAlias(target) {
    const k = targetKey(target);
    const item = poolTargets.find((p) => targetKey(p.target) === k);
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
    renderPoolCount();
  }

  function renderPoolCount() {
    const text = poolTargets.length ? `（${poolTargets.length}）` : "";
    ["#poolCount", "#poolCountModal"].forEach((sel) => {
      const el = $(sel);
      if (el) el.textContent = text;
    });
  }

  function openPool() {
    openModal("#poolModal");
    loadPool();
  }

  function renderPoolList() {
    const list = $("#poolList");
    if (!poolTargets.length) {
      list.innerHTML = '<p class="muted">暂无目标地址；地址池即配置文件中各代理的 proxy_pass 目标，可点击「添加目标」或添加代理创建</p>';
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
    if (inPreviewGuard("删除目标地址")) return;
    const ok = await confirmDialog("从配置文件中删除目标地址 " + target + "？\n将移除所有代理中该地址的备选行（处于激活状态时会被拒绝，需先切换）。");
    if (!ok) return;
    try {
      await api.removePoolTarget(target);
      toast("已删除目标: " + target, "success");
      loadPool();
      loadProxies();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  function resetPoolModal() {
    editingPoolItem = null;
    $("#addPoolTarget").disabled = false;
    $("#addPoolModal .modal-head .panel-title").textContent = "添加目标地址";
    $("#btnConfirmAddPool").textContent = "添加";
  }

  function openAddPool() {
    resetPoolModal();
    $("#addPoolTarget").value = "";
    $("#addPoolAlias").value = "";
    $("#addPoolError").hidden = true;
    lockBody();
    openModal("#addPoolModal");
  }

  async function confirmAddPool() {
    if (inPreviewGuard("添加目标地址")) return;
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
      loadPool();
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
    if (inPreviewGuard("编辑别名")) return;
    const alias = $("#addPoolAlias").value.trim();
    try {
      await api.setPoolAlias(editingPoolItem.target, alias);
      closeModal("#addPoolModal");
      unlockBody();
      resetPoolModal();
      loadPool();
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
      // 下拉选项读取自配置文件（池 = 全部 proxy_pass 目标并集 ∪ 该代理已有地址），
      // 按规范化 key 去重，等价写法（斜杠/大小写/默认端口）只展示一条
      const merged = [];
      const seenKeys = new Set();
      const pushTarget = (t, override) => {
        const k = targetKey(t);
        if (seenKeys.has(k)) {
          // 同 key 已存在（池地址在先）：代理自身写法优先展示（切换无需改配置）
          if (override) {
            const i = merged.findIndex((x) => targetKey(x) === k);
            if (i >= 0) merged[i] = t;
          }
          return;
        }
        seenKeys.add(k);
        merged.push(t);
      };
      poolTargets.forEach((pt) => pushTarget(pt.target));
      (p.targets || []).forEach((t) => { if (t === p.active) pushTarget(t, true); });
      (p.targets || []).forEach((t) => pushTarget(t, true));
      merged.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        const tk = targetKey(t);
        const inProxy = (p.targets || []).some((x) => targetKey(x) === tk);
        const inPool = poolTargetList().some((x) => targetKey(x) === tk);
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
      loadPool(); // 切换可能自动追加备选，配置文件已变化
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
      loadPool(); // 代理删除后其独有目标从池中消失
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
      loadPool(); // 新代理的激活目标进入地址池
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
      loadPool(); // 备选变化直接影响地址池
      refreshStatus();
    } catch (e) {
      $("#editTargetsError").textContent = e.message;
      $("#editTargetsError").hidden = false;
    }
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", App.init);

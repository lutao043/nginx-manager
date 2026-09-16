/* app.js — nginx 管理端主逻辑 */
"use strict";

const App = (() => {
  let treeData = null;      // 配置树
  let currentFile = null;   // 当前编辑文件相对路径
  let originalContent = ""; // 当前文件打开时的原文（保存前 diff 基准）
  let statusTimer = null;   // 状态轮询
  let editing = false;      // 编辑器是否有未保存改动
  let preview = false;      // 预览模式（未配置 nginx）
  let configDirty = false;  // 有已写入配置文件但尚未重载生效的变更（前端近似跟踪）

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

  /* ---------- 未重载变更跟踪 ----------
     任一写操作成功后标记；重载/重启成功后清除。仅本页近似跟踪：
     其他途径改动配置文件（外部手改）不感知。 */
  function updateReloadHint() {
    const show = configDirty && !preview;
    $("#reloadHint").hidden = !show;
    $("#btnQuickReload").hidden = !show;
  }
  function markConfigDirty() { configDirty = true; updateReloadHint(); }
  function clearConfigDirty() { configDirty = false; updateReloadHint(); }

  /* 状态栏一键重载：让已保存的修改立即生效 */
  async function quickReload() {
    if (inPreviewGuard("重载配置")) return;
    const btn = $("#btnQuickReload");
    btn.disabled = true;
    try {
      const res = await api.nginxAction("reload");
      clearConfigDirty();
      toast((res && res.message) || "nginx 配置已重载", "success");
      refreshStatus();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
    }
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
    updateReloadHint();
    refreshStatus();
    await Promise.all([loadTree(), loadBackups(), loadErrorLog(), loadAccessLog()]);
    if (preview) {
      toast("预览模式：未配置 nginx，可浏览界面；点「设置」配置后可操作", "info");
    } else {
      toast("已加载 nginx 配置", "success");
    }
  }

  /* ---------- 事件绑定 ---------- */
  /* 路径输入「浏览…」：调后端弹系统选择框（请求阻塞到用户关闭），选中后回填输入框 */
  function bindPathPicker(inputSel, btnSel, kind, title) {
    const btn = $(btnSel);
    const input = $(inputSel);
    if (!btn || !input) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await api.pickPath(kind, input.value.trim(), title);
        if (res && res.path) input.value = res.path; // 用户取消（null）不动原值
      } catch (e) {
        toast(e.message, "error");
      } finally {
        btn.disabled = false;
      }
    });
  }

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
    // 一键重载（「配置已修改未重载」提示旁）
    $("#btnQuickReload").addEventListener("click", quickReload);
    // 底部停靠页签：备份 / 错误日志 / 访问日志
    $("#dockTabBackups").addEventListener("click", () => switchDock("backups"));
    $("#dockTabErrorLog").addEventListener("click", () => switchDock("errorlog"));
    $("#dockTabAccessLog").addEventListener("click", () => switchDock("accesslog"));
    // 编辑器
    $("#btnSave").addEventListener("click", saveFile);
    $("#editor").addEventListener("input", () => {
      editing = true;
      updateCaret();
    });
    $("#editor").addEventListener("keyup", updateCaret);
    $("#editor").addEventListener("click", updateCaret);
    $("#editor").addEventListener("keydown", editorKeydown);
    // 有未保存修改时拦截页面刷新/关闭，防误触丢失
    window.addEventListener("beforeunload", (e) => {
      if (!editing) return;
      e.preventDefault();
      e.returnValue = "";
    });
    // 备份/日志
    $("#btnRefreshBackups").addEventListener("click", loadBackups);
    $("#btnRefreshLog").addEventListener("click", loadErrorLog);
    // 访问日志
    $("#btnRefreshAccessLog").addEventListener("click", loadAccessLog);
    $("#accessLogPath").addEventListener("change", loadAccessLog);
    $("#accessLogFilter").addEventListener("input", debounce(renderAccessLog, 150));
    // 负载均衡 upstream
    $("#btnRefreshUpstreams").addEventListener("click", loadUpstreams);
    $("#btnAddUpstream").addEventListener("click", () => openUpstreamModal(null));
    $("#btnSaveUpstream").addEventListener("click", saveUpstream);
    $("#btnUpstreamAddServer").addEventListener("click", () => {
      $("#upstreamServers").appendChild(buildUpstreamServerRow("", 1, false, false, []));
    });
    // 实时指标（stub_status）
    $("#btnMetricsEnable").addEventListener("click", enableMetricsFlow);
    // 保存前 diff 弹窗
    $("#btnSaveDiffCancel").addEventListener("click", closeSaveDiff);
    $("#btnSaveDiffPlain").addEventListener("click", () => doSaveFile(false));
    $("#btnSaveDiffBackup").addEventListener("click", () => doSaveFile(true));
    // 备份对比弹窗
    ["#diffA", "#diffB", "#diffPath"].forEach((sel) => $(sel).addEventListener("change", loadBackupDiff));
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
    // 路径输入：弹系统选择框代替手输（向导 + 设置）
    bindPathPicker("#wizNginxPath", "#btnWizPickNginx", "file", "请选择 nginx 可执行文件 (nginx.exe / nginx)");
    bindPathPicker("#wizConfDir", "#btnWizPickConfDir", "dir", "请选择 nginx 配置目录（含 nginx.conf 的目录）");
    bindPathPicker("#setNginxPath", "#btnSetPickNginx", "file", "请选择 nginx 可执行文件 (nginx.exe / nginx)");
    bindPathPicker("#setConfDir", "#btnSetPickConfDir", "dir", "请选择 nginx 配置目录（含 nginx.conf 的目录）");
    bindPathPicker("#setDataDir", "#btnSetPickDataDir", "dir", "请选择 manager 数据目录（settings.json 存放位置）");
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
    bindModalClose("#upstreamModal");
    bindModalClose("#saveDiffModal");
    bindModalClose("#backupDiffModal");
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
      loadUpstreams();
    }
  }

  /* ---------- 底部停靠页签（配置备份/错误日志/访问日志） ---------- */
  const DOCK_MAP = {
    backups:   { tab: "#dockTabBackups",    body: "#dockBackups" },
    errorlog:  { tab: "#dockTabErrorLog",   body: "#dockErrorLog" },
    accesslog: { tab: "#dockTabAccessLog",  body: "#dockAccessLog" },
  };

  function switchDock(name) {
    Object.entries(DOCK_MAP).forEach(([key, ref]) => {
      $(ref.tab).classList.toggle("active", key === name);
      $(ref.body).hidden = key !== name;
    });
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
      $("#btnSetPickDataDir").disabled = !!s.dataDirLocked;
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
      // 启停按钮随运行状态可用/禁用；restart 内部是先退再启，停止时也可用（等同启动）
      const running = !preview && !!st.running;
      $("#btnStart").disabled = preview || running;
      $("#btnStop").disabled = !running;
      $("#btnReload").disabled = !running;
      $("#btnRestart").disabled = preview;
      $("#btnTest").disabled = preview;
      // 代理页顶部同步显示当前管理的配置文件完整路径
      const cp = $("#confPathText");
      if (cp) {
        cp.textContent = st.confPath || (preview ? "（预览模式，未配置）" : "—");
        cp.title = st.confPath || "";
      }
      // 实时连接指标（stub_status）
      updateMetrics(st);
    } catch (e) {
      // 服务不可达时静默，保底显示
    }
  }

  /* ---------- 实时指标（stub_status） ---------- */
  let lastMetricsSample = null; // 上次采样 {ts, requests}，用于前端计算请求速率

  async function updateMetrics(st) {
    const connEl = $("#stConn"), reqEl = $("#stReq"), btn = $("#btnMetricsEnable");
    if (preview || !st.running) {
      connEl.textContent = "—";
      reqEl.textContent = "—";
      btn.hidden = true;
      lastMetricsSample = null;
      return;
    }
    try {
      const m = await api.metrics();
      if (m.available) {
        const mt = m.metrics || {};
        connEl.textContent = mt.active != null ? mt.active : "—";
        let reqText = mt.requests != null ? String(mt.requests) : "—";
        if (mt.requests != null && lastMetricsSample) {
          const dt = (Date.now() - lastMetricsSample.ts) / 1000;
          const dr = mt.requests - lastMetricsSample.requests;
          if (dt > 0 && dr >= 0) reqText += "（+" + (dr / dt).toFixed(1) + "/s）";
        }
        reqEl.textContent = reqText;
        lastMetricsSample = mt.requests != null ? { ts: Date.now(), requests: mt.requests } : null;
        btn.hidden = true;
      } else {
        connEl.textContent = "—";
        reqEl.textContent = "—";
        lastMetricsSample = null;
        // nginx 运行中但未配置 stub_status → 提供一键开启
        btn.hidden = m.reason !== "not_configured";
      }
    } catch (e) { /* 指标获取失败不影响状态栏 */ }
  }

  async function enableMetricsFlow() {
    if (inPreviewGuard("开启状态页")) return;
    const ok = await confirmDialog("将在 nginx.conf 最后一个 server 块写入 stub_status 状态页（/nginx_status，仅允许本机访问），自动备份并校验。确认？");
    if (!ok) return;
    try {
      const res = await api.enableMetrics();
      if (res.already) {
        toast("状态页已存在，无需重复开启", "info");
      } else {
        toast("状态页配置已写入并通过 nginx -t 校验", "success");
        const reloadNow = await confirmDialog("是否立即重载 nginx 使状态页生效？");
        if (reloadNow) {
          try {
            const r = await api.nginxAction("reload");
            clearConfigDirty();
            toast((r && r.message) || "nginx 配置已重载", "success");
          } catch (e) {
            markConfigDirty();
            toast(e.message, "error");
          }
        } else {
          markConfigDirty();
        }
      }
      refreshStatus();
    } catch (e) {
      toast(e.message, "error");
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

  /* ---------- 编辑器辅助：行列指示 / Tab 缩进 / Ctrl+S ---------- */

  /* 状态栏左侧元信息：未保存标记 + 当前行列号 */
  function updateCaret() {
    const ed = $("#editor");
    if ($("#editorPanel").hidden) return;
    const upto = ed.value.slice(0, ed.selectionStart);
    const lines = upto.split("\n");
    const caret = "行 " + lines.length + "，列 " + (lines[lines.length - 1].length + 1);
    $("#editorMeta").textContent = (editing ? "● 有未保存的修改 · " : "") + caret;
  }

  function editorKeydown(e) {
    // Ctrl/Cmd+S 保存（走保存前 diff 预览流程）
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      saveFile();
      return;
    }
    // Tab 缩进 / Shift+Tab 反缩进（默认行为是跳出编辑器，编辑配置时很反人类）
    if (e.key === "Tab") {
      e.preventDefault();
      editorIndent(e.shiftKey);
    }
  }

  /* Tab 缩进：光标处插入 4 空格；多行选区整体缩进/反缩进（空行跳过） */
  function editorIndent(outdent) {
    const ed = $("#editor");
    const unit = "    ";
    const start = ed.selectionStart, end = ed.selectionEnd;
    const value = ed.value;
    if (start === end || !value.slice(start, end).includes("\n")) {
      if (outdent) return;
      ed.value = value.slice(0, start) + unit + value.slice(end);
      ed.selectionStart = ed.selectionEnd = start + unit.length;
      editing = true;
      updateCaret();
      return;
    }
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    let lineEnd = value.indexOf("\n", end);
    if (lineEnd === -1) lineEnd = value.length;
    const block = value.slice(lineStart, lineEnd).split("\n");
    const changed = block.map((line) => {
      if (outdent) return line.replace(/^ {1,4}/, "");
      return line ? unit + line : line;
    });
    const joined = changed.join("\n");
    ed.value = value.slice(0, lineStart) + joined + value.slice(lineEnd);
    // 重新选中缩进后的整块，便于连续操作
    ed.selectionStart = lineStart;
    ed.selectionEnd = lineStart + joined.length;
    editing = true;
    updateCaret();
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
      originalContent = data.content;
      $("#editor").value = data.content;
      $("#editor").scrollTop = 0; // 打开新文件回到顶部，避免残留上次滚动位置
      $("#editorTitle").textContent = data.path;
      $("#editorPanel").hidden = false;
      $("#emptyPanel").hidden = true;
      editing = false;
      updateCaret();
      $("#saveWarning").hidden = true;
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* 保存入口：先本地 diff 预览（与打开时原文对比），无修改直接提示；
     有修改弹窗展示统一 diff，由用户选择「保存并备份 / 仅保存 / 取消」。 */
  async function saveFile() {
    if (!currentFile) return;
    if (inPreviewGuard("保存配置")) return;
    const content = $("#editor").value;
    if (content === originalContent) {
      toast("内容无修改", "info");
      return;
    }
    $("#saveDiffView").innerHTML = renderDiffHtml(lineDiff(originalContent, content));
    lockBody();
    openModal("#saveDiffModal");
  }

  function closeSaveDiff() {
    closeModal("#saveDiffModal");
    unlockBody();
  }

  async function doSaveFile(doBackup) {
    const content = $("#editor").value;
    try {
      const res = await api.saveFile(currentFile, content, true, doBackup);
      $("#editorMeta").textContent = res.backedUp
        ? "已保存，备份 " + (res.backupId || "")
        : "已保存（未备份）";
      showTestResult(res.test);
      $("#saveWarning").hidden = true;
      editing = false;
      originalContent = content;
      markConfigDirty(); // 已写入磁盘，重载后生效
      toast(res.backedUp ? "配置已保存并备份，重载后生效" : "配置已保存（未备份），重载后生效", "success");
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
        originalContent = content;
        $("#editorMeta").textContent = "已保存（校验失败）"; // 内容已落盘，不再显示「未保存」
        markConfigDirty();
        toast("配置已保存，但校验失败", "error");
      } else {
        toast(e.message, "error");
      }
    } finally {
      closeSaveDiff();
    }
  }

  async function rollbackFile(backupId) {
    if (!backupId) return;
    const ok = await confirmDialog("将当前配置回滚到备份 " + backupId + "？");
    if (!ok) return;
    try {
      await api.restoreBackup(backupId);
      markConfigDirty();
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
    appendJumpLink(el, test);
  }

  /* 校验失败且能解析出错误行时，提供「跳转到第 N 行」（文件匹配时）或错误位置提示 */
  function appendJumpLink(el, test) {
    const old = $("#btnJumpErr");
    if (old) old.remove();
    if (!test || test.ok || !test.errLine) return;
    const norm = (s) => String(s || "").replace(/\\/g, "/").toLowerCase();
    const sameFile = currentFile && test.errFile
      && norm(test.errFile).endsWith("/" + norm(currentFile));
    const btn = document.createElement("button");
    btn.className = "btn btn-mini";
    btn.id = "btnJumpErr";
    btn.style.marginTop = "8px";
    btn.textContent = sameFile
      ? "跳转到第 " + test.errLine + " 行"
      : "错误位于 " + (test.errFile || "?") + ":" + test.errLine;
    if (sameFile) btn.addEventListener("click", () => jumpToLine(test.errLine));
    el.appendChild(document.createElement("br"));
    el.appendChild(btn);
  }

  /* 编辑器定位到指定行：选中该行并滚动到可视区 */
  function jumpToLine(n) {
    const ed = $("#editor");
    const lines = ed.value.split("\n");
    let pos = 0;
    for (let i = 0; i < n - 1 && i < lines.length; i++) pos += lines[i].length + 1;
    ed.focus();
    ed.setSelectionRange(pos, pos + (lines[n - 1] || "").length);
    const lh = parseFloat(getComputedStyle(ed).lineHeight) || 22;
    ed.scrollTop = Math.max(0, (n - 8) * lh);
    $("#editorMeta").textContent = "● 已定位到第 " + n + " 行";
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
      // 重载/重启成功意味着磁盘配置已全部生效
      if (action === "reload" || action === "restart") clearConfigDirty();
      refreshStatus();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* ---------- 备份 ---------- */
  function renderBackups(items, retention) {
    const list = $("#backupList");
    const tip = $("#backupRetentionTip");
    const cnt = $("#backupCount");
    if (cnt) cnt.textContent = items.length ? "（" + items.length + "）" : "";
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
      const btnDiff = document.createElement("button");
      btnDiff.className = "btn btn-mini";
      btnDiff.textContent = "对比";
      btnDiff.title = "与其他版本对比差异";
      btnDiff.addEventListener("click", () => openBackupDiff(b.id));
      const btnRestore = document.createElement("button");
      btnRestore.className = "btn btn-mini";
      btnRestore.textContent = "回滚";
      btnRestore.addEventListener("click", () => doRestore(b));
      const btnDel = document.createElement("button");
      btnDel.className = "btn btn-mini btn-danger";
      btnDel.textContent = "删除";
      btnDel.addEventListener("click", () => doDeleteBackup(b));
      actions.appendChild(btnDiff);
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
      markConfigDirty();
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

  /* ---------- 访问日志（尾部 500 行，过滤为本地过滤） ---------- */
  let accessLogRaw = "";

  async function loadAccessLog() {
    const sel = $("#accessLogPath");
    if (preview) {
      accessLogRaw = "";
      $("#accessLog").textContent = "（预览模式：未配置 nginx，暂无访问日志）";
      $("#accessLogPathLabel").textContent = "";
      sel.hidden = true;
      return;
    }
    try {
      const data = await api.accessLog(500, sel.value || "");
      accessLogRaw = data.content || "";
      $("#accessLogPathLabel").textContent = data.logPath ? "📄 " + data.logPath : "";
      const paths = data.paths || [];
      if (paths.length) {
        sel.hidden = false;
        const cur = sel.value || data.logPath || "";
        sel.innerHTML = "";
        paths.forEach((p) => {
          const o = document.createElement("option");
          o.value = p;
          o.textContent = p;
          if (p === cur) o.selected = true;
          sel.appendChild(o);
        });
      } else {
        sel.hidden = true;
      }
      renderAccessLog();
    } catch (e) {
      accessLogRaw = "";
      $("#accessLog").textContent = "加载失败：" + e.message;
    }
  }

  function renderAccessLog() {
    const el = $("#accessLog");
    if (!accessLogRaw) {
      el.textContent = "（访问日志为空或文件不存在）";
      return;
    }
    const kw = $("#accessLogFilter").value.trim().toLowerCase();
    if (!kw) {
      el.textContent = accessLogRaw;
      return;
    }
    const lines = accessLogRaw.split("\n").filter((l) => l.toLowerCase().includes(kw));
    el.textContent = lines.length ? lines.join("\n") : "（无匹配行）";
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
    renderTargetOptions();
  }

  /* 目标地址输入候选（datalist）：地址池 ∪ upstream 名称 */
  function renderTargetOptions() {
    const dl = $("#proxyTargetOptions");
    if (!dl) return;
    dl.innerHTML = "";
    poolTargets.forEach((p) => {
      const o = document.createElement("option");
      o.value = p.target;
      if (p.alias) o.label = p.alias;
      dl.appendChild(o);
    });
    allUpstreams.forEach((u) => {
      const o = document.createElement("option");
      o.value = "http://" + u.name;
      o.label = "upstream · " + upstreamMethodLabel(u.method);
      dl.appendChild(o);
    });
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
    appendJumpLink(el, test);
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
      // 场景模板标签（非 standard 时展示）
      const TEMPLATE_LABELS = { websocket: "WebSocket", sse: "流式", upload: "上传" };
      if (p.template && p.template !== "standard" && TEMPLATE_LABELS[p.template]) {
        const tag = document.createElement("span");
        tag.className = "tag-template";
        tag.textContent = TEMPLATE_LABELS[p.template];
        head.appendChild(tag);
      }
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
    // 一次弹窗同时确认「切换」与「是否立即重载」，替代原先的连续两次确认
    const choice = await confirmChoice(
      "将代理 " + p.path + " 切换到 " + target + "？\n将自动备份并校验配置。",
      [
        { label: "切换并重载", value: "reload", primary: true },
        { label: "仅切换", value: "switch" },
      ]
    );
    if (!choice) return;
    try {
      const res = await api.switchProxy(p.path, target);
      showProxyTest(res.test);
      toast("已切换: " + p.path + " → " + target, "success");
      loadProxies();
      loadPool(); // 切换可能自动追加备选，配置文件已变化
      refreshStatus();
      if (choice === "reload") {
        try {
          const reloadRes = await api.nginxAction("reload");
          clearConfigDirty();
          toast((reloadRes && reloadRes.message) || "nginx 配置已重载", "success");
        } catch (e) {
          markConfigDirty();
          toast(e.message, "error");
        }
      } else {
        markConfigDirty(); // 仅切换未重载，提醒稍后生效
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
      markConfigDirty();
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
    $("#addProxyTemplate").value = "standard";
    $("#addProxyError").hidden = true;
    lockBody();
    openModal("#addProxyModal");
  }

  async function confirmAddProxy() {
    if (inPreviewGuard("添加代理")) return;
    const path = $("#addProxyPath").value.trim();
    const target = $("#addProxyTarget").value.trim();
    const template = $("#addProxyTemplate").value;
    if (!path || !target) {
      showAddProxyError("路径与目标地址均必填");
      return;
    }
    try {
      const res = await api.addProxy(path, target, template);
      closeModal("#addProxyModal");
      unlockBody();
      showProxyTest(res.test);
      markConfigDirty();
      toast("已添加代理: " + path + "，重载后生效", "success");
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
      markConfigDirty();
      toast("已更新备选目标，重载后生效", "success");
      loadProxies();
      loadPool(); // 备选变化直接影响地址池
      refreshStatus();
    } catch (e) {
      $("#editTargetsError").textContent = e.message;
      $("#editTargetsError").hidden = false;
    }
  }

  /* ---------- 负载均衡 upstream ---------- */
  let allUpstreams = [];
  let editingUpstream = null; // null = 新建

  function upstreamMethodLabel(m) {
    return m === "least_conn" ? "least_conn" : m === "ip_hash" ? "ip_hash" : "轮询";
  }

  async function loadUpstreams() {
    try {
      const data = await api.upstreams();
      allUpstreams = data.upstreams || [];
      $("#upstreamCount").textContent = allUpstreams.length ? "（" + allUpstreams.length + "）" : "";
      renderUpstreamList();
      renderTargetOptions();
    } catch (e) {
      $("#upstreamList").innerHTML = '<p class="muted">加载失败：' + escapeHtml(e.message) + "</p>";
    }
  }

  function renderUpstreamList() {
    const list = $("#upstreamList");
    if (!allUpstreams.length) {
      list.innerHTML = '<p class="muted">暂无 upstream；用于多台后端的负载均衡，代理目标填 http://名称 即可引用</p>';
      return;
    }
    list.innerHTML = "";
    allUpstreams.forEach((u) => {
      const item = document.createElement("div");
      item.className = "proxy-item";

      const head = document.createElement("div");
      head.className = "proxy-item-head";
      const name = document.createElement("span");
      name.className = "proxy-path";
      name.textContent = u.name;
      const right = document.createElement("span");
      right.className = "upstream-meta";
      const methodTag = document.createElement("span");
      methodTag.className = "tag-template";
      methodTag.textContent = upstreamMethodLabel(u.method);
      right.appendChild(methodTag);
      if (u.usedBy && u.usedBy.length) {
        const used = document.createElement("span");
        used.className = "muted small";
        used.textContent = " 被 " + u.usedBy.join(", ") + " 引用";
        right.appendChild(used);
      }
      head.appendChild(name);
      head.appendChild(right);

      const servers = document.createElement("div");
      servers.className = "upstream-servers";
      (u.servers || []).forEach((s) => {
        const row = document.createElement("div");
        row.className = "srv mono";
        const bits = [s.address];
        if (s.weight !== 1) bits.push("weight=" + s.weight);
        if (s.backup) bits.push("backup");
        if (s.down) bits.push("down");
        (s.extra || []).forEach((x) => bits.push(x));
        row.textContent = bits.join(" ");
        if (s.down) {
          row.style.opacity = ".5";
          row.title = "已下线";
        }
        servers.appendChild(row);
      });

      const row = document.createElement("div");
      row.className = "proxy-item-row";
      const actions = document.createElement("div");
      actions.className = "proxy-item-actions";
      const btnEdit = document.createElement("button");
      btnEdit.className = "btn btn-mini";
      btnEdit.textContent = "编辑";
      btnEdit.addEventListener("click", () => openUpstreamModal(u));
      const btnDel = document.createElement("button");
      btnDel.className = "btn btn-mini";
      btnDel.textContent = "删除";
      btnDel.addEventListener("click", () => doRemoveUpstream(u));
      actions.appendChild(btnEdit);
      actions.appendChild(btnDel);
      row.appendChild(actions);

      item.appendChild(head);
      item.appendChild(servers);
      item.appendChild(row);
      list.appendChild(item);
    });
  }

  function openUpstreamModal(u) {
    editingUpstream = u || null;
    $("#upstreamModalTitle").textContent = u ? "编辑 Upstream — " + u.name : "新建 Upstream";
    $("#upstreamName").value = u ? u.name : "";
    $("#upstreamName").disabled = !!u;
    $("#upstreamMethod").value = u ? (u.method || "round_robin") : "round_robin";
    const list = $("#upstreamServers");
    list.innerHTML = "";
    const servers = u && u.servers && u.servers.length
      ? u.servers
      : [{ address: "", weight: 1, backup: false, down: false, extra: [] }];
    servers.forEach((s) => list.appendChild(buildUpstreamServerRow(s.address, s.weight, s.backup, s.down, s.extra)));
    $("#upstreamError").hidden = true;
    lockBody();
    openModal("#upstreamModal");
  }

  function buildUpstreamServerRow(address, weight, backup, down, extra) {
    const row = document.createElement("div");
    row.className = "targets-edit-row upstream-server-row";
    if (extra && extra.length) row.dataset.extra = extra.join(" ");
    const input = document.createElement("input");
    input.type = "text";
    input.value = address || "";
    input.placeholder = "10.1.2.3:8080";
    const w = document.createElement("input");
    w.type = "number";
    w.min = "1";
    w.max = "100";
    w.value = weight || 1;
    w.title = "权重（1~100）";
    w.className = "up-weight";
    const mkCheck = (labelText, checked) => {
      const lab = document.createElement("label");
      lab.className = "up-check";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!checked;
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(labelText));
      return lab;
    };
    const del = document.createElement("button");
    del.className = "btn-remove-row";
    del.type = "button";
    del.textContent = "×";
    del.title = "删除此行";
    del.addEventListener("click", () => row.remove());
    row.appendChild(input);
    row.appendChild(w);
    row.appendChild(mkCheck("备机", backup));
    row.appendChild(mkCheck("下线", down));
    row.appendChild(del);
    return row;
  }

  function showUpstreamError(msg) {
    const el = $("#upstreamError");
    el.textContent = msg;
    el.hidden = false;
  }

  async function saveUpstream() {
    if (inPreviewGuard(editingUpstream ? "编辑 upstream" : "新建 upstream")) return;
    const name = $("#upstreamName").value.trim();
    const method = $("#upstreamMethod").value;
    if (!name) {
      showUpstreamError("名称必填");
      return;
    }
    const servers = [];
    $$("#upstreamServers .upstream-server-row").forEach((row) => {
      const addr = row.querySelector('input[type="text"]').value.trim();
      if (!addr) return; // 空行跳过
      const weight = parseInt(row.querySelector('input[type="number"]').value, 10) || 1;
      const checks = row.querySelectorAll('input[type="checkbox"]');
      servers.push({
        address: addr,
        weight: weight,
        backup: checks[0].checked,
        down: checks[1].checked,
        extra: (row.dataset.extra || "").split(/\s+/).filter(Boolean),
      });
    });
    if (!servers.length) {
      showUpstreamError("至少填写一台服务器");
      return;
    }
    try {
      const res = editingUpstream
        ? await api.updateUpstream(name, method, servers)
        : await api.addUpstream(name, method, servers);
      closeModal("#upstreamModal");
      unlockBody();
      showProxyTest(res.test);
      markConfigDirty();
      toast("upstream 已保存，重载后生效", "success");
      loadUpstreams();
    } catch (e) {
      showUpstreamError(e.message);
    }
  }

  async function doRemoveUpstream(u) {
    if (inPreviewGuard("删除 upstream")) return;
    const ok = await confirmDialog("删除 upstream " + u.name + "？\n将移除整个 upstream 块并校验配置（被代理引用时会被拒绝）。");
    if (!ok) return;
    try {
      const res = await api.removeUpstream(u.name);
      showProxyTest(res.test);
      markConfigDirty();
      toast("已删除 upstream: " + u.name + "，重载后生效", "success");
      loadUpstreams();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* ---------- 备份对比 ---------- */
  let diffBackups = [];

  function flattenTreeFiles(nodes, out) {
    out = out || [];
    (nodes || []).forEach((n) => {
      if (n.isDir) flattenTreeFiles(n.children, out);
      else out.push(n.path);
    });
    return out;
  }

  async function openBackupDiff(backupId) {
    let data;
    try {
      data = await api.backups();
    } catch (e) {
      toast(e.message, "error");
      return;
    }
    diffBackups = data.backups || [];
    const paths = new Set(flattenTreeFiles(treeData));
    diffBackups.forEach((b) => (b.files || []).forEach((f) => paths.add(f)));
    const pathSel = $("#diffPath");
    pathSel.innerHTML = "";
    Array.from(paths).sort().forEach((p) => {
      const o = document.createElement("option");
      o.value = p;
      o.textContent = p;
      pathSel.appendChild(o);
    });
    if (currentFile && paths.has(currentFile)) pathSel.value = currentFile;
    const fillVersion = (sel, def) => {
      sel.innerHTML = "";
      const cur = document.createElement("option");
      cur.value = "current";
      cur.textContent = "当前文件";
      sel.appendChild(cur);
      diffBackups.forEach((b) => {
        const o = document.createElement("option");
        o.value = b.id;
        o.textContent = b.createdAt + "（" + b.id + "）";
        sel.appendChild(o);
      });
      if (def) sel.value = def;
    };
    fillVersion($("#diffA"), "current");
    fillVersion($("#diffB"), backupId || (diffBackups[0] && diffBackups[0].id));
    lockBody();
    openModal("#backupDiffModal");
    loadBackupDiff();
  }

  async function loadBackupDiff() {
    const a = $("#diffA").value, b = $("#diffB").value, path = $("#diffPath").value;
    if (!a || !b || !path) return;
    const view = $("#backupDiffView");
    view.textContent = "加载中…";
    try {
      const res = await api.backupDiff(a, b, path);
      view.innerHTML = res.diff ? renderUnifiedDiffHtml(res.diff) : "（无差异）";
    } catch (e) {
      view.textContent = e.message;
    }
  }

  /* ---------- diff 工具（零依赖行级 diff） ---------- */

  /* 编辑器保存预览：对两段文本做行级 LCS diff，返回 [类型, 行] 序列，
     类型 ' ' 上下文 / '-' 删除 / '+' 新增 / 'hunk' 省略标记；上下文压缩为变更点前后各 2 行 */
  function lineDiff(oldText, newText) {
    const a = String(oldText == null ? "" : oldText).split("\n");
    const b = String(newText == null ? "" : newText).split("\n");
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) start++;
    let ea = a.length, eb = b.length;
    while (ea > start && eb > start && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
    const am = a.slice(start, ea), bm = b.slice(start, eb);
    const ops = [];
    if (am.length * bm.length > 1000000) {
      // 超大改动退化为整体替换，避免 LCS 平方开销
      am.forEach((l) => ops.push(["-", l]));
      bm.forEach((l) => ops.push(["+", l]));
    } else {
      const n = am.length, m = bm.length;
      const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
      for (let i = n - 1; i >= 0; i--) {
        for (let j = m - 1; j >= 0; j--) {
          dp[i][j] = am[i] === bm[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
      }
      let i = 0, j = 0;
      while (i < n && j < m) {
        if (am[i] === bm[j]) { ops.push([" ", am[i]]); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push(["-", am[i]]); i++; }
        else { ops.push(["+", bm[j]]); j++; }
      }
      while (i < n) ops.push(["-", am[i++]]);
      while (j < m) ops.push(["+", bm[j++]]);
    }
    const keep = new Array(ops.length).fill(false);
    ops.forEach((o, idx) => {
      if (o[0] !== " ") {
        for (let k = Math.max(0, idx - 2); k <= Math.min(ops.length - 1, idx + 2); k++) keep[k] = true;
      }
    });
    const out = [];
    let skipped = false;
    ops.forEach((o, idx) => {
      if (keep[idx]) {
        if (skipped) { out.push(["hunk", "⋯"]); skipped = false; }
        out.push(o);
      } else skipped = true;
    });
    return out;
  }

  function renderDiffHtml(ops) {
    return ops.map(([t, line]) => {
      const cls = t === "+" ? "dl-add" : t === "-" ? "dl-del" : t === "hunk" ? "dl-hunk" : "dl-ctx";
      const sign = t === "hunk" ? "" : t;
      return '<span class="' + cls + '">' + escapeHtml(sign + line) + "</span>";
    }).join("");
  }

  /* 后端 unified diff 文本上色（备份对比） */
  function renderUnifiedDiffHtml(text) {
    return String(text).split("\n").map((line) => {
      let cls = "dl-ctx";
      if (line.startsWith("+") && !line.startsWith("+++")) cls = "dl-add";
      else if (line.startsWith("-") && !line.startsWith("---")) cls = "dl-del";
      else if (line.startsWith("@@")) cls = "dl-hunk";
      return '<span class="' + cls + '">' + escapeHtml(line) + "</span>";
    }).join("");
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", App.init);

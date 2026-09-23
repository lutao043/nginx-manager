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
    // 不在这里预拉日志：默认停靠页签是「配置备份」，两个日志面板都被隐藏。
    // 切到日志页签时 switchDock → syncLogPolling 会立刻拉一次，内容同样即时可见。
    await Promise.all([loadTree(), loadBackups()]);
    Object.keys(logPanes).forEach((key) => logUpdateUI(logPanes[key]));   // 跟随按钮初态与状态一致
    syncLogPolling();
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
    // 更新历史（入口：顶栏版本号）
    $("#btnVersion").addEventListener("click", openChangelog);
    bindModalClose("#changelogModal");
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
      editing = $("#editor").value !== originalContent;
      historyRecord(true);
      renderGutter();
      highlightLine(0);   // 内容已变，上次的错误行定位失效
      updateCaret();
    });
    $("#editor").addEventListener("keyup", updateCaret);
    $("#editor").addEventListener("click", updateCaret);
    $("#editor").addEventListener("keydown", editorKeydown);
    // 行号栏与错误行高亮随编辑区滚动同步
    $("#editor").addEventListener("scroll", () => { syncGutterScroll(); updateLineHighlight(); });
    window.addEventListener("resize", () => { renderGutter(); updateLineHighlight(); });
    // 有未保存修改时拦截页面刷新/关闭，防误触丢失
    window.addEventListener("beforeunload", (e) => {
      if (!editing) return;
      e.preventDefault();
      e.returnValue = "";
    });
    // 备份/日志
    $("#btnRefreshBackups").addEventListener("click", loadBackups);
    // 错误日志：刷新（全量重载 + 回到跟随最新）、暂停/继续跟随、往上滚自动暂停
    $("#btnRefreshLog").addEventListener("click", () => logReload(logPanes.errorlog));
    $("#btnFollowErrorLog").addEventListener("click", () => logToggleFollow(logPanes.errorlog));
    logBindScroll(logPanes.errorlog);
    // 访问日志：切换文件=重载、过滤=本地实时过滤、自动滚动同上
    $("#btnRefreshAccessLog").addEventListener("click", () => logReload(logPanes.accesslog));
    $("#btnFollowAccessLog").addEventListener("click", () => logToggleFollow(logPanes.accesslog));
    $("#accessLogPath").addEventListener("change", () => logReload(logPanes.accesslog));
    $("#accessLogFilter").addEventListener("input", debounce(() => logRender(logPanes.accesslog), 150));
    logBindScroll(logPanes.accesslog);
    // 页面切到后台时停掉日志轮询（回来再续），不可见时空转没有意义。
    // 前台恢复时 syncLogPolling 会重新激活并立刻拉一次（回来即见最新，不用等下一个周期）。
    document.addEventListener("visibilitychange", () => syncLogPolling());
    // 负载均衡 upstream
    $("#btnRefreshUpstreams").addEventListener("click", loadUpstreams);
    $("#btnAddUpstream").addEventListener("click", () => openUpstreamModal(null));
    $("#btnSaveUpstream").addEventListener("click", saveUpstream);
    // upstream 搜索：与代理搜索同款 150ms 防抖过滤
    const onUpstreamSearchInput = (e) => {
      upstreamSearch = e.target.value;
      renderUpstreamList(filterUpstreams());
      $("#btnClearUpstreamSearch").hidden = !upstreamSearch.trim();
      updateUpstreamCount();
    };
    $("#upstreamSearch").addEventListener("input", debounce(onUpstreamSearchInput, 150));
    $("#btnClearUpstreamSearch").addEventListener("click", () => {
      upstreamSearch = "";
      $("#upstreamSearch").value = "";
      $("#btnClearUpstreamSearch").hidden = true;
      renderUpstreamList(filterUpstreams());
      updateUpstreamCount();
    });
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
    $("#tabUpstreams").addEventListener("click", () => switchTab("upstreams"));
    // 页签键盘导航：←/→/Home/End（roving tabindex，整组只占一个 Tab 停靠点）
    bindTablistKeys(".tabs", ["config", "proxies", "upstreams"], (n) => TAB_MAP[n].tab, switchTab);
    bindTablistKeys(".dock-tabs", ["backups", "errorlog", "accesslog"], (n) => DOCK_MAP[n].tab, switchDock);
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
  const TAB_MAP = {
    config:    { tab: "#tabConfig",    view: "#viewConfig" },
    proxies:   { tab: "#tabProxies",   view: "#viewProxies" },
    upstreams: { tab: "#tabUpstreams", view: "#viewUpstreams" },
  };

  async function switchTab(name) {
    Object.entries(TAB_MAP).forEach(([key, ref]) => {
      const active = key === name;
      const tab = $(ref.tab);
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;   // roving tabindex：整组只留一个 Tab 停靠点
      $(ref.view).hidden = !active;
    });
    // 日志面板在 #viewConfig 内：切走时整棵子树已隐藏，必须**立刻**停表。
    // 放在下面串行加载之前——否则那几百毫秒里隐藏面板还会继续轮询与渲染。
    syncLogPolling();
    // 按依赖顺序串行加载：代理列表的标签要用地址池别名、目标候选要用 upstream 名称，
    // 并发加载时先返回的那个会拿着旧状态渲染（别名/候选缺一块，要等下次刷新才对）
    if (name === "proxies") {
      await loadPool();
      await loadUpstreams(); // 添加代理的目标候选（datalist）依赖 upstream 名称，保持同步
      await loadProxies();
    } else if (name === "upstreams") {
      await loadUpstreams();
    }
  }

  /* 页签键盘导航：←/→ 在页签间移动并即时切换，Home/End 跳首尾（符合 ARIA tablist 习惯） */
  function bindTablistKeys(boxSel, names, tabSelOf, switchFn) {
    const box = $(boxSel);
    if (!box) return;
    box.addEventListener("keydown", (e) => {
      const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
      if (keys.indexOf(e.key) === -1) return;
      const current = names.findIndex((n) => $(tabSelOf(n)).getAttribute("aria-selected") === "true");
      const from = current < 0 ? 0 : current;
      let next = from;
      if (e.key === "ArrowLeft") next = (from - 1 + names.length) % names.length;
      else if (e.key === "ArrowRight") next = (from + 1) % names.length;
      else if (e.key === "Home") next = 0;
      else next = names.length - 1;
      e.preventDefault();
      switchFn(names[next]);
      $(tabSelOf(names[next])).focus();
    });
  }

  /* ---------- 底部停靠页签（配置备份/错误日志/访问日志） ---------- */
  const DOCK_MAP = {
    backups:   { tab: "#dockTabBackups",    body: "#dockBackups" },
    errorlog:  { tab: "#dockTabErrorLog",   body: "#dockErrorLog" },
    accesslog: { tab: "#dockTabAccessLog",  body: "#dockAccessLog" },
  };

  /* 当前停靠页签。日志轮询要同时看它和「配置文件页签是否可见」：
     日志面板在 #viewConfig 内，切到代理管理/负载均衡后整棵子树都是隐藏的。 */
  let activeDock = "backups";

  /* 日志轮询的唯一开关：可见（配置文件页签 + 页面在前台）且是当前停靠页签才轮询。
     状态未变时不重复调用 logActivate——它会在打开时立刻拉一次，重复调用就是白多一个请求。 */
  function syncLogPolling() {
    const on = !$("#dashboard").hidden && !$("#viewConfig").hidden && !document.hidden;
    Object.keys(logPanes).forEach((key) => {
      const want = on && key === activeDock;
      if (logPanes[key].active !== want) logActivate(logPanes[key], want);
    });
  }

  function switchDock(name) {
    activeDock = name;
    Object.entries(DOCK_MAP).forEach(([key, ref]) => {
      const active = key === name;
      const tab = $(ref.tab);
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      $(ref.body).hidden = !active;
    });
    // 只有当前可见的日志面板才轮询；切走即停，切回立刻拉一次
    syncLogPolling();
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
      // 只有该日志面板正在轮询时才重载；隐藏面板交给 syncLogPolling 在切回时按需拉取
      if (logPanes.errorlog.active) logReload(logPanes.errorlog);
      loadProxies();
      loadPool();
    } catch (e) {
      $("#setError").textContent = e.message;
      $("#setError").hidden = false;
    }
  }

  /* 轮询等待服务就绪（最多 maxSeconds 秒）。
     改端口/改数据目录后重启时，新地址与本页不同源 —— CORS 模式下 fetch 会直接抛错，
     永远等不到「就绪」而白等满 40 秒；no-cors 拿到的 opaque 响应读不到内容，但足以证明
     该端口已在监听（连接被拒时才抛错）。 */
  async function waitForServer(url, maxSeconds) {
    for (let i = 0; i < maxSeconds * 2; i++) {
      try {
        await fetch(url + "api/status", { mode: "no-cors", cache: "no-store" });
        return true;
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
      setText($("#stVersion"), st.version || "—");
      // 顶栏版本号：manager 自身版本（与上面 nginx 的「版本」是两件事）
      const verBtn = $("#btnVersion");
      if (verBtn && st.managerVersion) setText(verBtn, "v" + st.managerVersion);
      setText($("#stPid"), st.pid || "—");
      const confEl = $("#stConf");
      setText(confEl, st.confPath || (preview ? "（预览模式）" : "—"));
      setTitle(confEl, st.confPath || ""); // 状态条中路径会被省略号截断，悬停可看全路径
      // 启停按钮随运行状态可用/禁用；restart 内部是先退再启，停止时也可用（等同启动）
      const running = !preview && !!st.running;
      setDisabled($("#btnStart"), preview || running);
      setDisabled($("#btnStop"), !running);
      setDisabled($("#btnReload"), !running);
      setDisabled($("#btnRestart"), preview);
      setDisabled($("#btnTest"), preview);
      // 代理页顶部同步显示当前管理的配置文件完整路径
      const cp = $("#confPathText");
      if (cp) {
        setText(cp, st.confPath || (preview ? "（预览模式，未配置）" : "—"));
        setTitle(cp, st.confPath || "");
      }
      // 实时连接指标（stub_status）
      updateMetrics(st);
    } catch (e) {
      // 服务不可达时静默，保底显示
    }
  }

  /* ---------- 更新历史（顶栏版本号 → 发布说明） ---------- */
  let changelogData = null;   // 首次打开时拉取；版本历史是静态数据，缓存到本次会话结束

  async function openChangelog() {
    openModal("#changelogModal");
    if (changelogData) { renderChangelog(changelogData); return; }
    const box = $("#changelogList");
    box.innerHTML = '<p class="muted">加载中…</p>';
    try {
      changelogData = await api.changelog();
    } catch (e) {
      box.innerHTML = '<p class="muted">读取失败：' + escapeHtml(e.message) + "</p>";
      return;
    }
    renderChangelog(changelogData);
  }

  function renderChangelog(data) {
    const box = $("#changelogList");
    const cur = (data && data.version) || "";
    const releases = (data && data.releases) || [];
    $("#changelogCurrent").textContent = cur ? "v" + cur : "—";
    if (!releases.length) {
      box.innerHTML = '<p class="muted">未找到发布说明（release-notes/），当前环境无法展示更新历史；不影响其他功能。</p>';
      return;
    }
    const hasCur = releases.some(r => r.version === cur);
    box.innerHTML = releases.map((r, i) => {
      const isCur = !!cur && r.version === cur;
      // 默认展开当前版本；版本号还没写发布说明时（开发中）展开最新一版
      const open = isCur || (!hasCur && i === 0);
      const head = "v" + escapeHtml(r.version)
        + (isCur ? '<span class="release-cur">当前版本</span>' : "")
        + (r.title ? '<span class="release-title">' + escapeHtml(r.title) + "</span>" : "");
      const body = (r.sections || []).map(s =>
        '<div class="release-section">'
        + (s.title ? '<p class="release-section-title">' + escapeHtml(s.title) + "</p>" : "")
        + '<ul class="release-items">'
        + (s.items || []).map(t => "<li>" + inlineMarkup(t) + "</li>").join("")
        + "</ul></div>").join("");
      const link = r.link
        ? '<p class="release-link">完整变更对比：<span class="mono">' + escapeHtml(r.link) + "</span></p>" : "";
      return '<details class="release"' + (open ? " open" : "") + '><summary class="release-head">' + head
        + '</summary><div class="release-body">' + body + link + "</div></details>";
    }).join("");
  }

  /* 发布说明的行内标记：先转义再套白名单（**粗体** 与 `代码`）。
     文本来自仓库自身，但一律不得当 HTML 执行。 */
  function inlineMarkup(text) {
    return escapeHtml(text)
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  /* ---------- 实时指标（stub_status） ---------- */
  let lastMetricsSample = null; // 上次采样 {ts, requests}，用于前端计算请求速率

  async function updateMetrics(st) {
    const connEl = $("#stConn"), reqEl = $("#stReq"), btn = $("#btnMetricsEnable");
    if (preview || !st.running) {
      setText(connEl, "—");
      setText(reqEl, "—");
      setHidden(btn, true);
      lastMetricsSample = null;
      return;
    }
    try {
      const m = await api.metrics();
      if (m.available) {
        const mt = m.metrics || {};
        setText(connEl, mt.active != null ? mt.active : "—");
        let reqText = mt.requests != null ? String(mt.requests) : "—";
        if (mt.requests != null && lastMetricsSample) {
          const dt = (Date.now() - lastMetricsSample.ts) / 1000;
          const dr = mt.requests - lastMetricsSample.requests;
          if (dt > 0 && dr >= 0) reqText += "（+" + (dr / dt).toFixed(1) + "/s）";
        }
        setText(reqEl, reqText);
        lastMetricsSample = mt.requests != null ? { ts: Date.now(), requests: mt.requests } : null;
        setHidden(btn, true);
      } else {
        setText(connEl, "—");
        setText(reqEl, "—");
        lastMetricsSample = null;
        // nginx 运行中但未配置 stub_status → 提供一键开启
        setHidden(btn, m.reason !== "not_configured");
      }
    } catch (e) { /* 指标获取失败不影响状态栏 */ }
  }

  async function enableMetricsFlow() {
    if (inPreviewGuard("开启状态页")) return;
    const ok = await confirmDialog("将在 nginx.conf 最后一个 server 块写入 stub_status 状态页（/nginx_status，仅允许本机访问）；有实际改动时会先自动备份并校验，已存在同名 location 则不做改动。确认？");
    if (!ok) return;
    try {
      const res = await api.enableMetrics();
      if (res.already) {
        // 已存在同名 location 时后端不写入（再写一个同名 location 会让 nginx -t 失败），
        // 提示按事实说「未做改动」：这个分支既可能是状态页已在，也可能是同名 location
        // 存在但里面没有 stub_status，不能一口咬定「状态页已存在」。
        toast((res.stubPath || "/nginx_status") + " 已存在，未做改动", "info");
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

  /* 只挪高亮，不重建整棵树。

     openFile 原来为了换个高亮调用 renderTree()：每个节点要重建约 6 个元素 + 2~3 个监听器，
     而且顺手把用户手动折叠的目录状态一起丢了。这里只遍历已有节点改 class（不建任何元素）。 */
  function setTreeActive(path) {
    $("#fileTree").querySelectorAll(".tree-item").forEach((el) => {
      el.classList.toggle("active", !!path && el.dataset.path === path);
    });
  }

  /* 文件树图标：内联 SVG（复用 index.html 的 sprite，离线无外部资源） */
  const TREE_ICON_DIR = '<svg class="i"><use href="#i-folder"/></svg>';
  const TREE_ICON_FILE = '<svg class="i"><use href="#i-file"/></svg>';

  function renderTreeNode(node, depth) {
    const wrap = document.createElement("div");
    const isDir = node.isDir;
    const item = document.createElement("div");
    item.className = "tree-item" + (currentFile === node.path ? " active" : "");
    item.style.paddingLeft = (8 + depth * 14) + "px";
    item.dataset.path = node.path;  // 供 setTreeActive 定位，避免为了挪高亮重建整棵树

    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = isDir ? "▾" : "";
    const icon = document.createElement("span");
    icon.className = "icon";
    icon.innerHTML = isDir ? TREE_ICON_DIR : TREE_ICON_FILE; // 静态字符串，不含用户数据
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

    if (isDir) {
      // 目录可折叠：配置目录层级较深时，避免整棵树拉得很长
      item.title = "展开 / 折叠";
      item.setAttribute("aria-expanded", "true");
      item.addEventListener("click", toggleDir);
    } else {
      item.addEventListener("click", () => openFile(node.path));
    }

    function toggleDir() {
      const kids = wrap.querySelector(".tree-children");
      if (!kids) return;
      kids.hidden = !kids.hidden;
      arrow.textContent = kids.hidden ? "▸" : "▾";
      item.setAttribute("aria-expanded", String(!kids.hidden));
    }

    // 键盘可达：树项可聚焦，Enter/Space 激活，↑/↓ 在可见项间移动，目录 ←/→ 折叠展开
    // （ROADMAP G3 要求不碰鼠标走完「选文件 → 编辑 → 保存 → 回滚」）
    item.tabIndex = 0;
    item.setAttribute("role", "treeitem");
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        // 键盘激活时直接调目标函数：click() 会经 renderTree 重建 DOM，焦点随之丢失
        if (isDir) toggleDir();
        else openFile(node.path, true);
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        focusSiblingTreeItem(item, e.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (isDir && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
        e.preventDefault();
        const kids = wrap.querySelector(".tree-children");
        if (!kids) return;
        const wantOpen = e.key === "ArrowRight";
        if (kids.hidden === wantOpen) return;
        kids.hidden = !wantOpen;
        arrow.textContent = kids.hidden ? "▸" : "▾";
        item.setAttribute("aria-expanded", String(!kids.hidden));
      }
    });
    return wrap;
  }

  /* 在文件树的可见项之间移动焦点（↑/↓） */
  function focusSiblingTreeItem(item, delta) {
    const items = Array.from(document.querySelectorAll("#fileTree .tree-item"))
      .filter((el) => el.offsetParent !== null);
    const target = items[items.indexOf(item) + delta];
    if (target) target.focus();
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

  /* ---------- 编辑器行号栏 ---------- */
  const EDITOR_LINE_H = 20;    // 与 .editor / .editor-gutter 的 line-height 一致
  const EDITOR_PAD_TOP = 12;   // 与 .editor / .editor-gutter 的 padding-top 一致

  function renderGutter() {
    const ed = $("#editor"), gutter = $("#editorGutter");
    if (!ed || !gutter || $("#editorPanel").hidden) return;
    const lines = ed.value.split("\n").length;
    // 只在行数变化时重建内容（每次输入全量重写行号是低端机掉帧源）
    if (gutter.dataset.lines !== String(lines)) {
      gutter.dataset.lines = String(lines);
      let out = "";
      for (let i = 1; i <= lines; i++) out += i + "\n";
      gutter.textContent = out;
    }
    const digits = String(lines).length;
    if (gutter.dataset.digits !== String(digits)) {
      gutter.dataset.digits = String(digits);
      gutter.style.width = "calc(" + digits + "ch + 18px)";  // 宽度随行号位数伸缩
    }
    syncGutterScroll();
  }

  function syncGutterScroll() {
    const ed = $("#editor"), gutter = $("#editorGutter");
    if (ed && gutter) gutter.scrollTop = ed.scrollTop;
  }

  /* ---------- 校验错误行高亮（与「跳转到第 N 行」联动） ---------- */
  let highlightedLine = 0;   // 0 = 无高亮

  function highlightLine(n) {
    highlightedLine = n || 0;
    updateLineHighlight();
  }

  function updateLineHighlight() {
    const ed = $("#editor"), hl = $("#editorLineHl"), gutter = $("#editorGutter");
    if (!ed || !hl || !gutter) return;
    if (!highlightedLine || $("#editorPanel").hidden) { hl.hidden = true; return; }
    const top = EDITOR_PAD_TOP + (highlightedLine - 1) * EDITOR_LINE_H - ed.scrollTop;
    // 完全滚出可视区即收起（部分可见时仍显示）
    if (top + EDITOR_LINE_H <= 0 || top >= ed.clientHeight) { hl.hidden = true; return; }
    hl.hidden = false;
    hl.style.top = top + "px";
    hl.style.left = gutter.offsetWidth + "px";
  }

  /* ---------- 撤销 / 重做 ----------
     程序化写入 textarea.value（Tab 缩进、打开文件、格式化）会清空浏览器原生撤销栈，
     故自建快照历史；连续输入按时间窗合并成一步，撤销一次回到这段输入之前。 */
  const HISTORY_LIMIT = 300;
  const HISTORY_MERGE_MS = 600;

  let historyStack = [];
  let historyIndex = -1;
  let historyStamp = 0;

  function historyReset() {
    const ed = $("#editor");
    historyStack = [{ v: ed.value, ss: ed.selectionStart, se: ed.selectionEnd }];
    historyIndex = 0;
    historyStamp = 0;
  }

  function historyRecord(typing) {
    const ed = $("#editor");
    const cur = { v: ed.value, ss: ed.selectionStart, se: ed.selectionEnd };
    const top = historyStack[historyIndex];
    const now = Date.now();
    if (typing && top && historyIndex === historyStack.length - 1 && now - historyStamp < HISTORY_MERGE_MS) {
      historyStack[historyIndex] = cur;   // 合并连续输入
      historyStamp = now;
      return;
    }
    if (top && top.v === cur.v && top.ss === cur.ss && top.se === cur.se) return;  // 无变化不记步
    historyStack = historyStack.slice(0, historyIndex + 1);  // 新改动丢弃重做分支
    historyStack.push(cur);
    if (historyStack.length > HISTORY_LIMIT) historyStack.shift();
    historyIndex = historyStack.length - 1;
    historyStamp = now;
  }

  function historyApply(entry) {
    const ed = $("#editor");
    ed.value = entry.v;
    ed.setSelectionRange(entry.ss, entry.se);
    editing = ed.value !== originalContent;
    renderGutter();
    highlightLine(0);   // 撤销后行号已变，旧定位失效
    updateCaret();
  }

  function historyUndo() {
    if (historyIndex <= 0) return false;
    historyIndex -= 1;
    historyApply(historyStack[historyIndex]);
    return true;
  }

  function historyRedo() {
    if (historyIndex >= historyStack.length - 1) return false;
    historyIndex += 1;
    historyApply(historyStack[historyIndex]);
    return true;
  }

  function editorKeydown(e) {
    // Ctrl/Cmd+S 保存（走保存前 diff 预览流程）
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      saveFile();
      return;
    }
    const mod = e.ctrlKey || e.metaKey;
    // Ctrl/Cmd+Z 撤销、Ctrl/Cmd+Shift+Z 重做（macOS 习惯）
    if (mod && !e.altKey && (e.key === "z" || e.key === "Z")) {
      e.preventDefault();
      if (e.shiftKey) historyRedo(); else historyUndo();
      return;
    }
    // Ctrl+Y 重做（Windows 习惯）
    if (mod && !e.shiftKey && !e.altKey && (e.key === "y" || e.key === "Y")) {
      e.preventDefault();
      historyRedo();
      return;
    }
    // Tab 缩进 / Shift+Tab 反缩进（默认行为是跳出编辑器，编辑配置时很反人类）；
    // 代价是 Tab 出不去编辑器，故 Esc 把焦点交给下一个可聚焦控件（编辑器惯例）
    if (e.key === "Tab") {
      e.preventDefault();
      editorIndent(e.shiftKey);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      const ed = $("#editor");
      const all = focusableIn(document.body);
      const next = all[all.indexOf(ed) + 1];
      if (next) next.focus(); else ed.blur();
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
      editing = ed.value !== originalContent;
      historyRecord(false);
      renderGutter();
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
    editing = ed.value !== originalContent;
    historyRecord(false);
    renderGutter();
    updateCaret();
  }

  /* ---------- 文件编辑 ---------- */
  /* focusEditorFromKeyboard：由文件树的键盘激活传入（点击不需要抢焦点）
     force：跳过「放弃修改」确认并强制从磁盘重读（回滚/恢复后用，此时编辑器内容已过期）
     请求代次 openFileSeq：快速连点两个文件时，先发的响应可能后到，若不丢弃就会
     出现「编辑器显示 A、currentFile 是 B」——接下来保存会把 A 的内容写进 B。 */
  let openFileSeq = 0;

  async function openFile(path, focusEditorFromKeyboard, force) {
    if (editing && !force) {
      const ok = await confirmDialog("当前文件有未保存的修改，放弃修改并切换文件？");
      if (!ok) return;
      editing = false;
    }
    const seq = ++openFileSeq;
    currentFile = path;
    if (force) editing = false;
    setTreeActive(path); // 高亮（只改已有节点的 class，不重建树）
    try {
      const data = await api.readFile(path);
      if (seq !== openFileSeq) return; // 已被更晚的 openFile 取代：丢弃本次结果
      originalContent = data.content;
      $("#editor").value = data.content;
      $("#editor").scrollTop = 0; // 打开新文件回到顶部，避免残留上次滚动位置
      $("#editorTitle").textContent = data.path;
      $("#editorPanel").hidden = false;
      $("#emptyPanel").hidden = true;
      editing = false;
      historyReset();
      renderGutter();
      highlightLine(0);
      updateCaret();
      // 键盘选文件时直接把焦点送进编辑器，省去再按一次 Tab
      if (focusEditorFromKeyboard) $("#editor").focus();
      $("#saveWarning").hidden = true;
    } catch (e) {
      if (seq !== openFileSeq) return; // 旧请求的失败不该弹给用户（当前文件已切换）
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
    $("#btnSaveDiffBackup").focus();   // 键盘流程：打开即可回车「保存并备份」
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
        // 未启用备份时 backupId 为空：此时按钮若不给动作就是死按钮，
        // 改为回滚到最新一份备份（确认框里会写明具体是哪一份）；一份备份都没有就不显示按钮。
        let rollbackId = e.payload.backupId || null;
        if (!rollbackId) {
          try {
            const data = await api.backups();
            const items = (data && data.backups) || [];
            if (items.length) {
              const newest = items.slice().sort((a, b) => String(b.id).localeCompare(String(a.id)))[0];
              rollbackId = newest && newest.id;
            }
          } catch (_) { /* 取不到备份列表就不提供回滚入口 */ }
        }
        const backupLabel = rollbackId ? "回滚到 " + rollbackId : "";
        $("#saveWarning").innerHTML =
          "⚠ 配置已保存，但未通过 nginx -t 校验，尚未生效。" +
          (rollbackId
            ? ""
            : '<div class="muted">未启用备份，无法一键回滚：可在下方「备份」页签查看已有备份，或手工修正后重试。</div>') +
          (backupLabel
            ? '<div class="actions"><button class="btn btn-mini" id="btnRollback">' +
              escapeHtml(backupLabel) + "</button></div>"
            : "");
        const rb = $("#btnRollback");
        if (rb && rollbackId) rb.addEventListener("click", () => rollbackFile(rollbackId));
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
      const res = await api.restoreBackup(backupId);
      markConfigDirty();
      // 已回滚：失败的警告条与错误行定位同时失效，换成回滚后的校验结果
      $("#saveWarning").hidden = true;
      showTestResult(res && res.test);
      highlightLine(0);
      toast("已回滚，正在重新加载…", "success");
      await loadTree();
      if (currentFile) openFile(currentFile, false, true); // force：强制重读回滚后的磁盘内容
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

  /* 编辑器定位到指定行：选中该行、滚动到可视区（尽量居中）并高亮 —— 与 nginx -t 的 errLine 联动 */
  function jumpToLine(n) {
    const ed = $("#editor");
    const lines = ed.value.split("\n");
    let pos = 0;
    for (let i = 0; i < n - 1 && i < lines.length; i++) pos += lines[i].length + 1;
    ed.focus();
    ed.setSelectionRange(pos, pos + (lines[n - 1] || "").length);
    const lh = parseFloat(getComputedStyle(ed).lineHeight) || EDITOR_LINE_H;
    // 编辑区可能只有几行高（窄窗口），把目标行放到视口中间，行号栏与高亮才对得上
    const lineTop = EDITOR_PAD_TOP + (n - 1) * lh;
    ed.scrollTop = Math.max(0, lineTop - Math.max(0, (ed.clientHeight - lh) / 2));
    renderGutter();
    highlightLine(n);
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
      // force：编辑器内容已过期（磁盘被回滚），必须强制重读；
      // 否则「放弃修改？」确认里的取消会让编辑器留着旧内容，随后保存会静默覆盖刚回滚的配置
      if (currentFile) openFile(currentFile, false, true);
      loadBackups();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  /* ---------- 日志查看：实时跟随 + 可暂停 ----------
     后端按字节偏移增量下发（offset 只在完整行边界前进），前端只做「追加」：
     跟随时不重建 DOM，用户正在看的位置不会被顶掉。三种状态：
       - 跟随中：拉到新内容后贴底滚动；
       - 已暂停：点了暂停，或自己往上滚离底部（自动暂停）；新内容照常追加但不动滚动位置，
         工具栏提示累计新行数，点跟随按钮即恢复（跳回最新）；
       - 预览模式：无 nginx，只渲染一次占位文案，不轮询。
     缓冲区按行数封顶，面板长时间开着也不会把内存吃掉。 */
  const LOG_POLL_MS = 1000;      // 跟随轮询间隔
  const LOG_MAX_LINES = 5000;    // 前端缓冲上限（与后端单次返回上限一致）
  const LOG_BOTTOM_SLACK = 8;    // 距底部多少像素内算「贴底」

  const logPanes = {
    errorlog: {
      pre: "#errorLog", hint: "#errorLogHint", btn: "#btnFollowErrorLog",
      pathLabel: "#logPathLabel", empty: "（错误日志为空或文件不存在）",
      fetch: (since) => api.errorLog(200, since),
    },
    accesslog: {
      pre: "#accessLog", hint: "#accessLogHint", btn: "#btnFollowAccessLog",
      pathLabel: "#accessLogPathLabel", pathSelect: "#accessLogPath", filter: "#accessLogFilter",
      empty: "（访问日志为空或文件不存在）",
      fetch: (since) => api.accessLog(500, $("#accessLogPath").value || "", since),
    },
  };
  Object.keys(logPanes).forEach((key) => Object.assign(logPanes[key], {
    follow: true,      // 是否跟随（贴底自动滚动）
    offset: null,      // 下次增量读取的起始字节偏移；null = 尚未加载
    chunks: [],        // 缓冲分块账本 [{text, lines, node}]：追加/淘汰按块做，不整段重排
    lines: 0,          // 缓冲内累计换行数（随 chunks 同步维护，免得每次 split 全文）
    pending: 0,        // 暂停期间累计的新行数
    loading: false,
    timer: null,
    chain: null,       // hasMore 续取的自链定时器（停表时要一并清掉）
    uiKey: "",         // 上次渲染的工具栏状态：没变就不碰 DOM
    active: false,     // 是否应当轮询（配置文件页签可见 + 是当前停靠页签 + 页面在前台）
  }));

  function logAtBottom(el) { return el.scrollHeight - el.scrollTop - el.clientHeight <= LOG_BOTTOM_SLACK; }

  /* 访问日志的本地过滤关键词；无过滤时为空串，走「只追加」的快路径 */
  function logKeyword(st) { return st.filter ? $(st.filter).value.trim().toLowerCase() : ""; }

  function logCountNewlines(s) {
    let n = 0;
    let i = s.indexOf("\n");
    while (i >= 0) { n += 1; i = s.indexOf("\n", i + 1); }
    return n;
  }

  /* 缓冲按行封顶，超限从**头部整块**丢弃并摘掉对应 DOM 节点；返回丢弃的行数。

     原实现每次都 st.raw.split("\n") 再整段 textContent 替换：缓冲满之后每来一行都要把
     5000 行（数百 KB）重新解析并重排一次，而这正是「跟随中的日志」每秒的固定开销。 */
  function logTrim(st) {
    const CAP = LOG_MAX_LINES;
    let dropped = 0;
    while (st.chunks.length > 1 && st.lines - st.chunks[0].lines >= CAP) {
      const head = st.chunks.shift();
      dropped += head.lines;
      st.lines -= head.lines;
      if (head.node) head.node.remove();
    }
    // 头部块自身就超预算（单次下发就装满了）：只裁这一块的前若干行，其余内容不动
    const head = st.chunks[0];
    if (head && st.lines > CAP) {
      const drop = st.lines - CAP;
      const text = head.text;
      let idx = -1;
      for (let k = 0; k < drop; k++) {
        idx = text.indexOf("\n", idx + 1);
        if (idx < 0) break;
      }
      if (idx >= 0) {
        head.text = text.slice(idx + 1);
        head.lines -= drop;
        st.lines -= drop;
        dropped += drop;
        if (head.node) head.node.nodeValue = head.text;
      }
    }
    return dropped;
  }

  /* 从缓冲整体重建视图（首次加载 / 日志轮转 / 手动刷新 / 过滤启用）。
     过滤时 DOM 只呈现命中行，但缓冲仍保留全量，清空关键词即可回到完整内容。 */
  function logRender(st) {
    const el = $(st.pre);
    const keep = el.scrollTop;
    const kw = logKeyword(st);
    const raw = st.chunks.map((c) => c.text).join("");
    st.chunks = [];
    st.lines = 0;
    if (!raw) {
      el.textContent = st.empty;
    } else if (!kw) {
      el.textContent = raw;
      st.lines = logCountNewlines(raw);
      st.chunks = [{ text: raw, lines: st.lines, node: el.firstChild }];
    } else {
      const hit = raw.split("\n").filter((l) => l.toLowerCase().includes(kw));
      el.textContent = hit.length ? hit.join("\n") : "（无匹配行）";
      st.lines = logCountNewlines(raw);
      st.chunks = [{ text: raw, lines: st.lines, node: null }];  // DOM 是过滤视图，不绑节点
    }
    el.scrollTop = st.follow ? el.scrollHeight : Math.min(keep, el.scrollHeight);
  }

  /* 追加一段新内容。无过滤时只 AppendChild 一个文本节点（+ 必要的头部淘汰），
     不重建已有内容；过滤启用时才整体重建过滤视图。 */
  function logAppend(st, text) {
    const el = $(st.pre);
    const lines = logCountNewlines(text);
    if (logKeyword(st)) {
      st.chunks.push({ text: text, lines: lines, node: null });
      st.lines += lines;
      const dropped = logTrim(st);
      // 新增行没有命中、且淘汰没动到已展示的命中行时，视图不变，不必重建
      if (dropped > 0 || text.toLowerCase().includes(logKeyword(st))) logRender(st);
      return;
    }
    // 面板此刻若显示的是空态文案（日志为空/不存在，或轮转后为空），它不是 chunk、
    // 不会被淘汰逻辑带走，追加前必须先清掉：否则文案会粘在日志首行前面，出现
    // 「（错误日志为空或文件不存在）2026/09/23 ... 」这种自相矛盾的显示。
    if (!st.chunks.length && el.firstChild && el.textContent === st.empty) el.textContent = "";
    const node = document.createTextNode(text);
    el.appendChild(node);
    st.chunks.push({ text: text, lines: lines, node: node });
    st.lines += lines;
    logTrim(st);
    // 贴底滚动仍在同一任务里同步做（与原有行为一致）。曾试过合并到 requestAnimationFrame
    // 以省掉这次强制重排，但引入两个真实回归：追加后到下一帧之间面板尚未贴底，滚动锚定
    // 派发的 scroll 会被「往上滚自动暂停」误判成用户行为，跟随中的日志持续写入时把自己
    // 暂停掉；且标签页不可见时 rAF 不触发，排队标记会永久卡住、之后再也不贴底。
    if (st.follow) el.scrollTop = el.scrollHeight;
  }

  /* 并入一段新内容；replace=true 表示全量重置（首次加载 / 日志轮转 / 手动刷新） */
  function logMerge(st, text, replace) {
    if (replace) {
      st.chunks = [];
      st.lines = 0;
      if (text) {
        const lines = logCountNewlines(text);
        st.chunks = [{ text: text, lines: lines, node: null }];
        st.lines = lines;
      }
      logRender(st);
      return;
    }
    if (!text) return;
    logAppend(st, text);
  }

  function logUpdateUI(st) {
    const show = !st.follow && st.pending > 0;
    // 状态没变就不碰 DOM：这条路径原来每秒都在改图标/标题/提示文本
    const key = (st.follow ? "1" : "0") + "|" + (show ? st.pending : 0);
    if (key === st.uiKey) return;
    st.uiKey = key;
    const btn = $(st.btn);
    const icon = btn.querySelector("use");
    if (icon) icon.setAttribute("href", st.follow ? "#i-pause" : "#i-play");
    btn.setAttribute("aria-pressed", st.follow ? "true" : "false");
    btn.title = st.follow ? "暂停实时跟随（暂停后不再自动滚动）" : "继续实时跟随（跳到最新）";
    const hint = $(st.hint);
    hint.hidden = !show;
    hint.textContent = show ? "已暂停 · 新日志 " + st.pending + " 行" : "";
  }

  async function logPoll(st) {
    if (st.loading) return;
    st.loading = true;
    try {
      const data = await st.fetch(st.offset === null ? undefined : st.offset);
      const label = $(st.pathLabel);
      if (label) {
        const next = data.logPath ? "📄 " + data.logPath : "";
        if (label.textContent !== next) label.textContent = next;  // 路径没变就不写 DOM
      }
      if (st.pathSelect) logFillPathSelect(st, data);
      const text = data.content || "";
      // 后端只在完整行边界下发，故按换行数即可准确计新增行
      const added = logCountNewlines(text);
      logMerge(st, text, !!data.reset || st.offset === null);
      if (typeof data.offset === "number") st.offset = data.offset;
      if (added && !st.follow) st.pending += added;
      logUpdateUI(st);
      if (data.hasMore && st.active) {
        // 还有积压：立刻续取，不等下个周期。自链必须受「是否仍在轮询」约束——
        // 原来只清 timer，链式 setTimeout 管不到，切走之后还会多跑一轮。
        st.chain = setTimeout(() => { st.chain = null; if (st.active) logPoll(st); }, 0);
      }
    } catch (e) {
      logStop(st);        // 停表：否则每秒弹一次同样的错误
      toast(e.message, "error");
    } finally {
      st.loading = false;
    }
  }

  /* 候选日志文件下拉只在选项真的变化时重建，避免轮询打断用户正在做的选择 */
  function logFillPathSelect(st, data) {
    const sel = $(st.pathSelect);
    const paths = data.paths || [];
    if (!paths.length) { sel.hidden = true; return; }
    sel.hidden = false;
    const same = sel.options.length === paths.length
      && paths.every((p, i) => sel.options[i].value === p);
    if (same) return;
    const cur = sel.value || data.logPath || "";
    sel.innerHTML = "";
    paths.forEach((p) => {
      const o = document.createElement("option");
      o.value = p;
      o.textContent = p;
      if (p === cur) o.selected = true;
      sel.appendChild(o);
    });
  }

  function logStart(st) {
    if (st.timer || preview) return;   // 预览模式没有可跟随的内容，不轮询
    st.timer = setInterval(() => logPoll(st), LOG_POLL_MS);
  }

  function logStop(st) {
    if (st.timer) { clearInterval(st.timer); st.timer = null; }
    if (st.chain) { clearTimeout(st.chain); st.chain = null; }
  }

  /* 只有当前可见的停靠面板才轮询：切走即停，不在后台空转 */
  function logActivate(st, on) {
    st.active = !!on;
    if (on) { logStart(st); logPoll(st); }
    else logStop(st);
  }

  /* 全量重载并回到「跟随最新」（手动刷新、切换日志文件都走这里） */
  function logReload(st) {
    st.offset = null;
    st.pending = 0;
    st.follow = true;
    st.chunks = [];   // 丢弃缓冲账本：下一次轮询带 reset 会整体重建
    st.lines = 0;
    logPoll(st);
  }

  function logToggleFollow(st) {
    st.follow = !st.follow;
    if (st.follow) {
      st.pending = 0;
      const el = $(st.pre);
      el.scrollTop = el.scrollHeight;
    }
    logUpdateUI(st);
  }

  /* 往上滚 = 想停下来看：自动暂停。程序化贴底时仍在底部，不会误触发。
     反向（滚回底部自动恢复）刻意不做：暂停必须由用户自己解除，
     否则过滤/重渲染把内容变短时会静默恢复跟随，用户会以为「我暂停了它自己又滚起来」。 */
  function logBindScroll(st) {
    $(st.pre).addEventListener("scroll", () => {
      if (st.follow && !logAtBottom($(st.pre))) {
        st.follow = false;
        logUpdateUI(st);
      }
    });
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
      $("#poolList").innerHTML = '<p class="muted">加载失败：' + escapeHtml(e.message) + "</p>";
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
      editBtn.innerHTML = '<svg class="i"><use href="#i-pencil"/></svg>'; // 静态字符串，不含用户数据
      editBtn.title = "编辑别名";
      editBtn.addEventListener("click", () => openEditPoolAlias(item));
      const del = document.createElement("button");
      del.className = "pool-del";
      del.type = "button";
      del.innerHTML = '<svg class="i"><use href="#i-trash"/></svg>';
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

  /* 校验结果输出到指定视图的结果条（代理视图 / upstream 视图各自独立，互不遮挡） */
  function showTestResultIn(sel, test) {
    const el = $(sel);
    if (!test) { el.hidden = true; return; }
    el.textContent = test.output || "";
    el.className = "test-result " + (test.ok ? "ok" : "fail");
    el.hidden = false;
    appendJumpLink(el, test);
  }

  function showProxyTest(test) {
    showTestResultIn("#proxyTestResult", test);
  }

  function showUpstreamTest(test) {
    showTestResultIn("#upstreamTestResult", test);
  }

  function renderProxyList(proxies) {
    const list = $("#proxyList");
    if (!proxies.length) {
      list.innerHTML = '<p class="muted">暂无代理，点击「添加代理」创建</p>';
      return;
    }
    // 渲染期共享的规范化键：原实现每个候选都要 poolTargetList().some(...) 与 poolAlias(t)，
    // 两者内部又逐项跑 targetKey 的正则，规模一大就是 O(代理 × 候选 × 池条目) 次正则
    // （50 代理 × 10 候选 × 50 池条目约 5 万次）。这里一次算好、按字符串查表。
    const keyMemo = new Map();
    const keyOf = (t) => {
      let k = keyMemo.get(t);
      if (k === undefined) { k = targetKey(t); keyMemo.set(t, k); }
      return k;
    };
    const poolKeySet = new Set();
    const aliasByKey = new Map();   // 与 poolAlias 同口径：同 key 取池中首个条目的别名
    poolTargets.forEach((pt) => {
      const k = keyOf(pt.target);
      poolKeySet.add(k);
      if (!aliasByKey.has(k)) aliasByKey.set(k, pt.alias);
    });
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
      // 每个代理只用算一次的目标 key 集合（原实现每个候选都重新扫一遍 p.targets）
      const pTargets = p.targets || [];
      const pTargetKeys = new Set(pTargets.map(keyOf));
      // 按规范化 key 去重，等价写法（斜杠/大小写/默认端口）只展示一条
      const merged = [];
      const mergedIndex = new Map();   // key -> merged 下标（原来靠 findIndex 每次重跑正则）
      const pushTarget = (t, override) => {
        const k = keyOf(t);
        const i = mergedIndex.get(k);
        if (i !== undefined) {
          // 同 key 已存在（池地址在先）：代理自身写法优先展示（切换无需改配置）
          if (override) merged[i] = t;
          return;
        }
        mergedIndex.set(k, merged.length);
        merged.push(t);
      };
      poolTargets.forEach((pt) => pushTarget(pt.target));
      pTargets.forEach((t) => { if (t === p.active) pushTarget(t, true); });
      pTargets.forEach((t) => pushTarget(t, true));
      merged.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        const tk = keyOf(t);
        const inProxy = pTargetKeys.has(tk);
        const inPool = poolKeySet.has(tk);
        const alias = aliasByKey.get(tk) || "";
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
      btnDel.className = "btn btn-mini btn-danger"; // 破坏性操作与备份列表的「删除」保持一致
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
      // 状态刷新只留末尾这一次：它同时覆盖「仅切换」和「切换并重载」两种情况
      // （原来这里先刷一次、重载后再刷一次，每次点击两遍 /api/status + /api/metrics）
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
    (p.targets || []).forEach((t) => {
      list.appendChild(buildTargetRow(t, t === p.active));
    });
    $("#editTargetsError").hidden = true;
    lockBody();
    openModal("#editTargetsModal");
  }

  function buildTargetRow(value, isActive) {
    const row = document.createElement("div");
    row.className = "targets-edit-row";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "activeTarget";
    radio.checked = !!isActive;
    // 单选没有可见文字标签，补读屏名与悬停说明（保存后即成为配置文件里未注释的那行）
    radio.setAttribute("aria-label", "设为激活目标");
    radio.title = "设为激活目标（保存后为生效的那一行）";
    const input = document.createElement("input");
    input.type = "text";
    input.value = value;
    input.placeholder = "http://host:port/";
    input.setAttribute("aria-label", "目标地址");
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
    let active = "";
    rows.forEach((row) => {
      const input = row.querySelector('input[type="text"]');
      const radio = row.querySelector('input[type="radio"]');
      const val = (input.value || "").trim();
      if (val && !targets.includes(val)) {
        targets.push(val);
        // 单选按钮选中的那行即新的激活目标：必须随请求发给后端，
        // 否则「选中的目标」只在界面上生效，保存后配置里还是原来的激活行
        if (radio.checked) active = val;
      }
    });
    if (!targets.length) {
      $("#editTargetsError").textContent = "至少保留一个目标地址";
      $("#editTargetsError").hidden = false;
      return;
    }
    if (!active) active = targets[0];
    try {
      const res = await api.saveProxyTargets(editingProxy.path, targets, active);
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
  let allUpstreams = [];    // 全量 upstream 列表（搜索过滤用）
  let upstreamSearch = "";  // 当前搜索关键词
  let editingUpstream = null; // null = 新建

  function upstreamMethodLabel(m) {
    return m === "least_conn" ? "least_conn" : m === "ip_hash" ? "ip_hash" : "轮询";
  }

  /* 按名称 / 服务器地址 / 被引用代理过滤 */
  function filterUpstreams() {
    const kw = upstreamSearch.trim().toLowerCase();
    if (!kw) return allUpstreams;
    return allUpstreams.filter((u) => {
      if (u.name && u.name.toLowerCase().includes(kw)) return true;
      if ((u.servers || []).some((s) => s.address && s.address.toLowerCase().includes(kw))) return true;
      return (u.usedBy || []).some((p) => p.toLowerCase().includes(kw));
    });
  }

  /* 标题计数：搜索中显示「匹配/全量」，否则仅全量（空列表不显示） */
  function updateUpstreamCount() {
    const cnt = $("#upstreamCount");
    if (!cnt) return;
    if (!allUpstreams.length) { cnt.textContent = ""; return; }
    cnt.textContent = upstreamSearch.trim()
      ? `（${filterUpstreams().length}/${allUpstreams.length}）`
      : `（${allUpstreams.length}）`;
  }

  async function loadUpstreams() {
    try {
      const data = await api.upstreams();
      allUpstreams = data.upstreams || [];
      updateUpstreamCount();
      renderUpstreamList(filterUpstreams());
      renderTargetOptions();
    } catch (e) {
      $("#upstreamList").innerHTML = '<p class="muted">加载失败：' + escapeHtml(e.message) + "</p>";
    }
  }

  function renderUpstreamList(upstreams) {
    const list = $("#upstreamList");
    if (!allUpstreams.length) {
      list.innerHTML = '<p class="muted">暂无 upstream；用于多台后端的负载均衡，代理目标填 http://名称 即可引用</p>';
      return;
    }
    if (!upstreams.length) {
      list.innerHTML = '<p class="muted">无匹配 upstream，换个关键词试试</p>';
      return;
    }
    list.innerHTML = "";
    upstreams.forEach((u) => {
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
      btnDel.className = "btn btn-mini btn-danger";
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
      showUpstreamTest(res.test);
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
      showUpstreamTest(res.test);
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

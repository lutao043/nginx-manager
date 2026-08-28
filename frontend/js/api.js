/* api.js — 统一 API 请求封装
   支持两种访问形态：
   1) 直连 http://127.0.0.1:8310/            → API 走 /api/...
   2) 经 nginx 代理 http://host/nginx-manager/ → 自动带前缀 /nginx-manager/api/...
   通过当前页面路径自动判断（检测首段路径是否非 /api、非静态资源）。
*/
"use strict";

function detectBase() {
  const seg = (window.location.pathname || "/").split("/").filter(Boolean);
  // 首段若是纯文件名（含 .）或已知静态资源目录，视为直连模式（无前缀）
  if (seg.length > 0 && !seg[0].includes(".") && !["api", "css", "js"].includes(seg[0])) {
    return "/" + seg[0];
  }
  return "";
}

const api = {
  base: detectBase(),

  async _request(method, path, body) {
    const opts = { method, headers: {} };
    opts.headers["X-Requested-With"] = "XMLHttpRequest"; // 写操作 CSRF 防护（后端校验）
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(this.base + path, opts);
    } catch (e) {
      throw new Error("无法连接本地服务，请确认服务已启动");
    }
    let data = null;
    const text = await resp.text();
    if (text) {
      try { data = JSON.parse(text); } catch (_) { data = null; }
    }
    if (!resp.ok) {
      const err = new Error((data && data.error) || `请求失败 (${resp.status})`);
      err.status = resp.status;
      err.detail = (data && data.detail) || null;
      err.payload = data;
      throw err;
    }
    return data;
  },

  get(path) { return this._request("GET", path); },
  post(path, body) { return this._request("POST", path, body || {}); },
  put(path, body) { return this._request("PUT", path, body || {}); },

  // ---- 业务方法 ----
  status() { return this.get("/api/status"); },
  configTree() { return this.get("/api/config"); },
  readFile(path) { return this.get("/api/config/file?path=" + encodeURIComponent(path)); },
  saveFile(path, content, runTest, doBackup) { return this.put("/api/config/file", { path, content, runTest, doBackup }); },
  testConfig() { return this.post("/api/config/test"); },
  nginxAction(action) { return this.post("/api/nginx/" + action); },
  backups() { return this.get("/api/backups"); },
  restoreBackup(id) { return this.post("/api/backups/restore", { id }); },
  deleteBackup(id) { return this._request("DELETE", "/api/backups", { id }); },
  errorLog(lines) { return this.get("/api/logs/error?lines=" + (lines || 200)); },
  settings() { return this.get("/api/settings"); },
  saveSettings(nginxPath, confDir, backupRetention, port, dataDir) {
    const body = { nginxPath, confDir };
    if (backupRetention !== null && backupRetention !== undefined) body.backupRetention = backupRetention;
    if (port !== null && port !== undefined) body.port = port;
    if (dataDir) body.dataDir = dataDir; // manager 自身数据目录（变更后后端自动重启）
    return this.put("/api/settings", body);
  },
  restart(port) { return this.post("/api/restart", { port }); },

  // 弹系统选择框选文件/目录（后端阻塞到用户关闭对话框；取消时返回 {path: null}）
  pickPath(kind, initial, title) {
    const body = { kind };
    if (initial) body.initial = initial;
    if (title) body.title = title;
    return this.post("/api/pick-path", body);
  },

  // ---- 代理管理 ----
  proxies() { return this.get("/api/proxies"); },
  addProxy(path, target) { return this.post("/api/proxies", { path, target }); },
  switchProxy(path, target) { return this.put("/api/proxies/switch", { path, target }); },
  saveProxyTargets(path, targets) { return this.put("/api/proxies/targets", { path, targets }); },
  removeProxy(path) { return this._request("DELETE", "/api/proxies", { path }); },

  // ---- 目标地址池 ----
  proxyPool() { return this.get("/api/proxy-pool"); },
  addPoolTarget(target, alias) { return this.post("/api/proxy-pool", { target, alias }); },
  setPoolAlias(target, alias) { return this.put("/api/proxy-pool", { target, alias }); },
  removePoolTarget(target) { return this._request("DELETE", "/api/proxy-pool", { target }); },
};

/* 简易 XSS 转义：用户数据插入 innerHTML 前必须过此函数 */
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

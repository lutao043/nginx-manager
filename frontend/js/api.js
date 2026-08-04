/* api.js — 统一 API 请求封装 */
"use strict";

const api = {
  base: "",

  async _request(method, path, body) {
    const opts = { method, headers: {} };
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
  saveFile(path, content, runTest) { return this.put("/api/config/file", { path, content, runTest }); },
  testConfig() { return this.post("/api/config/test"); },
  nginxAction(action) { return this.post("/api/nginx/" + action); },
  backups() { return this.get("/api/backups"); },
  restoreBackup(id) { return this.post("/api/backups/restore", { id }); },
  errorLog(lines) { return this.get("/api/logs/error?lines=" + (lines || 200)); },
  settings() { return this.get("/api/settings"); },
  saveSettings(nginxPath, confDir) { return this.put("/api/settings", { nginxPath, confDir }); },
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

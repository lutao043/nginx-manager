# -*- coding: utf-8 -*-
"""server.py — nginx 轻量网页管理端后端服务（Python 标准库，零第三方依赖）

启动方式：
    python backend/server.py [--port 8080] [--nginx-path <exe>] [--conf-dir <dir>]

特性：
  - 仅监听 127.0.0.1
  - 端口 --port 指定，缺省随机空闲端口（启动时打印实际地址并尝试打开浏览器）
  - 首次使用（settings 未配置且未传参数）：弹系统文件选择对话框让用户指定
    nginx 程序路径与配置目录（tkinter，打包 exe 内可用）；无图形环境时可
    用 --nginx-path / --conf-dir 参数预置，或用 API 配置。
  - 运行时数据存用户数据目录（Windows %APPDATA%，macOS ~/Library/Application
    Support，Linux ~/.config），避免 onefile 解压临时目录不可写。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from nginxctl import NginxController, create_controller

APP_NAME = "nginx-manager"
IS_FROZEN = getattr(sys, "frozen", False)
# server.py 位于 backend/ 下，项目根为其父目录
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
# 前端资源目录：开发时读项目根 frontend/；打包后读 sys._MEIPASS/frontend/
FRONTEND_DIR = os.path.join(getattr(sys, "_MEIPASS", PROJECT_ROOT), "frontend")


# ---------- 用户数据目录 ----------

def user_data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP_NAME)


def ensure_data_dirs() -> dict:
    """确保数据目录结构存在，返回 {root, backups} 路径。"""
    root = user_data_dir()
    backups = os.path.join(root, "backups")
    os.makedirs(root, exist_ok=True)
    os.makedirs(backups, exist_ok=True)
    return {"root": root, "backups": backups}


# ---------- Settings ----------

class SettingsStore:
    """settings.json 持久化。文件：<user_data>/settings.json"""

    def __init__(self, root: str):
        self.path = os.path.join(root, "settings.json")
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    return d
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def configured(self) -> bool:
        return bool(self.data.get("nginxPath") and self.data.get("confDir"))


# ---------- 备份 ----------

def timestamp_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def make_backup(backups_dir: str, conf_dir: str, rel_path: str) -> str:
    """备份单个文件到 backups/<时间戳>/<相对路径>，返回备份 id。"""
    backup_id = timestamp_id()
    src = os.path.abspath(os.path.join(conf_dir, rel_path))
    dst = os.path.join(backups_dir, backup_id, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return backup_id


def list_backups(backups_dir: str) -> list:
    result = []
    if not os.path.isdir(backups_dir):
        return result
    for name in sorted(os.listdir(backups_dir), reverse=True):
        d = os.path.join(backups_dir, name)
        if not os.path.isdir(d) or not name.replace("_", "").isdigit():
            continue
        files = []
        for root, _dirs, fnames in os.walk(d):
            for fn in fnames:
                files.append(os.path.relpath(os.path.join(root, fn), d).replace("\\", "/"))
        files.sort()
        try:
            created = datetime.strptime(name, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            created = name
        result.append({"id": name, "createdAt": created, "files": files})
    return result


# ---------- 首次使用：文件选择对话框 ----------

def pick_nginx_via_dialog() -> dict:
    """弹系统对话框依次选择 nginx 程序与配置目录。返回 {nginxPath, confDir}。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()

    nginx_path = filedialog.askopenfilename(
        title="请选择 nginx 可执行文件 (nginx.exe / nginx)",
        filetypes=[
            ("nginx 可执行文件", "*.exe"),
            ("所有文件", "*.*"),
        ],
        parent=root,
    )
    if not nginx_path:
        root.destroy()
        return {}

    conf_dir = filedialog.askdirectory(
        title="请选择 nginx 配置目录（含 nginx.conf 的目录）",
        mustexist=True,
        parent=root,
    )
    root.destroy()
    if not conf_dir:
        return {}
    return {"nginxPath": nginx_path, "confDir": conf_dir}


# ---------- HTTP 处理 ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "nginx-manager/0.1"
    settings: SettingsStore = None  # type: ignore
    data_dirs: dict = {}
    controller: NginxController = None  # type: ignore

    # ---- 工具 ----

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, payload: dict) -> None:
        self._send_json(200, payload)

    def _err(self, status: int, error: str, detail: str = None) -> None:
        payload = {"error": error}
        if detail:
            payload["detail"] = detail
        self._send_json(status, payload)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            d = json.loads(raw.decode("utf-8"))
            return d if isinstance(d, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _safe_rel(self, raw: str) -> str | None:
        """校验并规范化相对路径；非法（空/绝对/穿越）返回 None。"""
        if not raw:
            return None
        raw = raw.replace("\\", "/")
        if raw.startswith("/") or ".." in raw.split("/"):
            return None
        rel = os.path.normpath(raw)
        if rel == "." or rel.startswith("..") or os.path.isabs(rel):
            return None
        return rel.replace("\\", "/")

    def _conf_abs(self, rel: str) -> str:
        return os.path.abspath(os.path.join(self.controller.conf_dir, rel))

    def _in_conf_dir(self, abs_path: str) -> bool:
        root = os.path.abspath(self.controller.conf_dir) + os.sep
        return abs_path.startswith(root)

    def _controller_or_error(self) -> bool:
        if self.controller is None:
            self._err(409, "尚未配置 nginx 路径，请先完成配置", "请通过设置页或 API 配置 nginxPath / confDir")
            return False
        return True

    def _require_controller(self):
        if self.controller is None:
            self._err(409, "尚未配置 nginx 路径，请先完成配置")
            return None
        return self.controller

    # ---- 静态文件 ----

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        if ".." in rel.split("/"):
            self._err(404, "Not Found")
            return
        fp = os.path.join(FRONTEND_DIR, rel)
        if not os.path.isfile(fp):
            self._err(404, "Not Found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        try:
            with open(fp, "rb") as f:
                body = f.read()
        except OSError:
            self._err(500, "读取静态资源失败")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._route_api_get(path, qs)
        else:
            self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._route_api_post(parsed.path)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._route_api_put(parsed.path)

    # ---- API: GET ----

    def _route_api_get(self, path: str, qs: dict) -> None:
        if path == "/api/status":
            self._api_status()
        elif path == "/api/config":
            self._api_config()
        elif path == "/api/config/file":
            self._api_config_file_get(qs)
        elif path == "/api/backups":
            self._api_backups()
        elif path == "/api/logs/error":
            self._api_logs_error(qs)
        elif path == "/api/settings":
            self._api_settings_get()
        else:
            self._err(404, "接口不存在")

    def _api_status(self) -> None:
        if self.controller is None:
            self._ok({
                "running": False, "version": None, "pid": None,
                "nginxPath": self.settings.get("nginxPath"),
                "confDir": self.settings.get("confDir"),
                "confPath": None, "confFileExists": False,
            })
            return
        info = self.controller.detect_process()
        conf_path = self.controller.main_conf_path()
        self._ok({
            "running": info.get("running", False),
            "version": info.get("version"),
            "pid": info.get("pid"),
            "nginxPath": self.controller.nginx_path,
            "confDir": self.controller.conf_dir,
            "confPath": conf_path,
            "confFileExists": os.path.isfile(conf_path),
        })

    def _api_config(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        tree, included = ctl.build_config_tree()
        self._ok({"tree": tree, "included": included})

    def _api_config_file_get(self, qs: dict) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        rel = self._safe_rel((qs.get("path") or [""])[0])
        if rel is None:
            self._err(400, "path 参数非法")
            return
        abs_path = self._conf_abs(rel)
        if not self._in_conf_dir(abs_path):
            self._err(403, "路径越出配置目录")
            return
        if not os.path.isfile(abs_path):
            self._err(404, "文件不存在")
            return
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            self._err(500, "读取文件失败")
            return
        self._ok({"path": rel, "content": content})

    def _api_backups(self) -> None:
        self._ok({"backups": list_backups(self.data_dirs["backups"])})

    def _api_logs_error(self, qs: dict) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        try:
            lines = int((qs.get("lines") or ["200"])[0])
        except ValueError:
            lines = 200
        log_path, content = ctl.read_error_log(lines)
        self._ok({"logPath": log_path, "content": content})

    def _api_settings_get(self) -> None:
        self._ok({
            "nginxPath": self.settings.get("nginxPath"),
            "confDir": self.settings.get("confDir"),
            "configured": self.settings.configured(),
        })

    # ---- API: POST ----

    def _route_api_post(self, path: str) -> None:
        if path == "/api/nginx/start":
            self._api_nginx_start()
        elif path == "/api/nginx/stop":
            self._api_nginx_stop()
        elif path == "/api/nginx/reload":
            self._api_nginx_reload()
        elif path == "/api/nginx/restart":
            self._api_nginx_restart()
        elif path == "/api/config/test":
            self._api_config_test()
        elif path == "/api/backups/restore":
            self._api_backups_restore()
        else:
            self._err(404, "接口不存在")

    def _api_nginx_start(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.start()
        if ok:
            self._ok({"ok": True, "message": msg})
        else:
            self._err(500, msg)

    def _api_nginx_stop(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.stop()
        if ok:
            self._ok({"ok": True, "message": msg})
        else:
            self._err(409, msg)

    def _api_nginx_reload(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.reload()
        if ok:
            self._ok({"ok": True, "message": msg})
        else:
            self._err(500, msg)

    def _api_nginx_restart(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.restart()
        if ok:
            self._ok({"ok": True, "message": msg})
        else:
            self._err(500, msg)

    def _api_config_test(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        code, result = ctl.test_config()
        if not result.get("ok") and "不存在" in result.get("output", ""):
            self._err(500, result["output"])
            return
        self._ok(result)

    def _api_backups_restore(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        body = self._read_json_body()
        backup_id = body.get("id")
        if not backup_id or not str(backup_id).replace("_", "").isdigit():
            self._err(400, "id 参数非法")
            return
        src_dir = os.path.join(self.data_dirs["backups"], str(backup_id))
        if not os.path.isdir(src_dir):
            self._err(404, "备份不存在")
            return

        # 收集备份内文件（相对路径，安全校验）
        staged = []
        for root, _dirs, fnames in os.walk(src_dir):
            for fn in sorted(fnames):
                rel = os.path.relpath(os.path.join(root, fn), src_dir).replace("\\", "/")
                if self._safe_rel(rel) is None:
                    self._err(400, f"备份内路径非法: {rel}")
                    return
                staged.append(rel)
        if not staged:
            self._err(400, "备份为空")
            return

        # 读取备份内容
        files_map = {}
        for rel in staged:
            src = os.path.join(src_dir, rel.replace("/", os.sep))
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                files_map[rel] = f.read()

        # 记录当前内容（用于校验失败时恢复原状）
        current_map = {}
        for rel in staged:
            abs_path = self._conf_abs(rel)
            if self._in_conf_dir(abs_path) and os.path.isfile(abs_path):
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    current_map[rel] = f.read()

        # 先备份当前状态（可回退），再写入备份内容
        pre_backup_ids = []
        for rel in staged:
            abs_path = self._conf_abs(rel)
            if os.path.isfile(abs_path):
                pre_backup_ids.append(make_backup(self.data_dirs["backups"], ctl.conf_dir, rel))
        for rel in staged:
            abs_path = self._conf_abs(rel)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(files_map[rel])

        ok, result = ctl.test_config()
        if not ok:
            # 校验失败：恢复原状
            for rel, content in current_map.items():
                abs_path = self._conf_abs(rel)
                with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content)
            self._err(409, "回滚后 nginx -t 校验失败，已恢复原状", result.get("output", ""))
            return
        self._ok({"ok": True, "restored": staged, "test": result, "preBackupIds": pre_backup_ids})

    # ---- API: PUT ----

    def _route_api_put(self, path: str) -> None:
        if path == "/api/config/file":
            self._api_config_file_put()
        elif path == "/api/settings":
            self._api_settings_put()
        else:
            self._err(404, "接口不存在")

    def _api_config_file_put(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        body = self._read_json_body()
        rel = self._safe_rel(str(body.get("path", "")))
        content = body.get("content")
        run_test = body.get("runTest", True)
        if rel is None:
            self._err(400, "path 参数非法")
            return
        if not isinstance(content, str):
            self._err(400, "content 必须为字符串")
            return
        abs_path = self._conf_abs(rel)
        if not self._in_conf_dir(abs_path):
            self._err(403, "路径越出配置目录")
            return
        if not os.path.isfile(abs_path):
            self._err(404, "目标文件不存在，不允许新建文件")
            return

        backup_id = make_backup(self.data_dirs["backups"], ctl.conf_dir, rel)
        try:
            with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
        except OSError as e:
            self._err(500, f"写入文件失败: {e}")
            return

        if run_test:
            _ok, result = ctl.test_config()
            if not result.get("ok"):
                self._send_json(409, {
                    "error": "nginx -t 校验失败，配置已保存但未应用，请修正后重试或回滚",
                    "detail": result.get("output", ""),
                    "saved": True,
                    "backupId": backup_id,
                    "test": result,
                })
                return
            self._ok({"ok": True, "backupId": backup_id, "test": result})
        else:
            self._ok({"ok": True, "backupId": backup_id})

    def _api_settings_put(self) -> None:
        body = self._read_json_body()
        nginx_path = body.get("nginxPath")
        conf_dir = body.get("confDir")
        if not nginx_path or not conf_dir:
            self._err(400, "nginxPath 与 confDir 均必填")
            return
        if not os.path.isfile(nginx_path):
            self._err(409, f"nginx 可执行文件不存在: {nginx_path}")
            return
        if not os.path.isdir(conf_dir) or not os.path.isfile(os.path.join(conf_dir, "nginx.conf")):
            self._err(409, f"配置目录无效（需包含 nginx.conf）: {conf_dir}")
            return
        self.settings.set("nginxPath", nginx_path)
        self.settings.set("confDir", conf_dir)
        Handler.controller = create_controller(nginx_path, conf_dir)
        self._ok({"ok": True, "nginxPath": nginx_path, "confDir": conf_dir})

    # ---- 日志 ----

    def log_message(self, fmt, *args) -> None:  # 静默默认访问日志
        pass


# ---------- 端口 ----------

def find_free_port(preferred: int | None) -> int:
    if preferred:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- 入口 ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="nginx 轻量网页管理端")
    parser.add_argument("--port", type=int, default=None, help="监听端口（缺省随机空闲端口）")
    parser.add_argument("--nginx-path", default=None, help="nginx 可执行文件路径（跳过首次选择对话框）")
    parser.add_argument("--conf-dir", default=None, help="nginx 配置目录（跳过首次选择对话框）")
    args = parser.parse_args()

    data_dirs = ensure_data_dirs()
    Handler.data_dirs = data_dirs
    Handler.settings = SettingsStore(data_dirs["root"])

    nginx_path = args.nginx_path or Handler.settings.get("nginxPath")
    conf_dir = args.conf_dir or Handler.settings.get("confDir")

    if not nginx_path or not conf_dir:
        print("[首次使用] 需要指定 nginx 路径与配置目录…")
        picked = pick_nginx_via_dialog()
        if not picked:
            print("未完成选择，退出。可用 --nginx-path / --conf-dir 参数指定，或重试。")
            return 1
        nginx_path = picked["nginxPath"]
        conf_dir = picked["confDir"]
        Handler.settings.set("nginxPath", nginx_path)
        Handler.settings.set("confDir", conf_dir)
        print(f"已保存配置: nginx={nginx_path}\n            confDir={conf_dir}")

    Handler.controller = create_controller(nginx_path, conf_dir)
    port = find_free_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"nginx 管理端已启动: {url}")
    print("仅监听 127.0.0.1；Ctrl+C 退出。")
    try:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

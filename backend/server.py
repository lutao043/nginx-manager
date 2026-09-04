# -*- coding: utf-8 -*-
"""server.py — nginx 轻量网页管理端后端服务（Python 标准库，零第三方依赖）

启动方式：
    python backend/server.py [--port 8080] [--nginx-path <exe>] [--conf-dir <dir>] [--preview]

特性：
  - 仅监听 127.0.0.1；固定默认端口 8310（--port 可覆盖，被占用时自动换随机端口）
  - 支持通过外部 nginx 反向代理以 /nginx-manager/ 前缀访问（见 VIBE_CODING_GUIDE.md）
  - 本地开发默认：若工作区根目录存在 nginx-1.30.4/（测试用 nginx），直接作为管理对象；
    否则首次使用（settings 未配置且未传参数）弹系统文件选择对话框让用户指定
    nginx 程序路径与配置目录（tkinter，打包 exe 内可用）；无图形环境时可
    用 --nginx-path / --conf-dir 参数预置，或用 API 配置。
  - 预览模式（--preview，或无图形环境且未配置 nginx）：不要求 nginx 已安装/配置，
    仅提供前端 UI 预览与接口调试；在「设置」中配置有效 nginx 后自动退出预览。详见 API.md。
  - 运行时数据存用户数据目录（Windows %APPDATA%，macOS ~/Library/Application
    Support，Linux ~/.config），避免 onefile 解压临时目录不可写。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from nginxctl import NginxController, create_controller
from proxymgr import ProxyManager, _pool_key

APP_NAME = "nginx-manager"
IS_FROZEN = getattr(sys, "frozen", False)
# server.py 位于 backend/ 下，项目根为其父目录
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
# 前端资源目录：开发时读项目根 frontend/；打包后读 sys._MEIPASS/frontend/
FRONTEND_DIR = os.path.join(getattr(sys, "_MEIPASS", PROJECT_ROOT), "frontend")


# ---------- 用户数据目录 ----------

def default_data_dir() -> str:
    """平台默认数据目录（配置文件 settings.json、备份、单实例锁的默认存放位置）。
    可用环境变量 NGINX_MANAGER_DEFAULT_DATA_DIR 整体覆盖（便携部署/测试用）。"""
    env = os.environ.get("NGINX_MANAGER_DEFAULT_DATA_DIR")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", APP_NAME)
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), APP_NAME)


POINTER_FILE = "data_dir.txt"  # 默认目录下的指针文件：内容为自定义数据目录路径
_data_dir_cli: str | None = None  # --data-dir 启动参数（优先级最高，界面不可改）


def data_dir_locked() -> bool:
    """数据目录是否被启动参数/环境变量锁定（锁定时界面不可修改）。"""
    return bool(_data_dir_cli or os.environ.get("NGINX_MANAGER_DATA_DIR"))


def resolve_data_dir() -> str:
    """manager 自身数据目录（即配置文件地址）解析顺序：
    1) --data-dir 启动参数；2) 环境变量 NGINX_MANAGER_DATA_DIR；
    3) 默认目录下 data_dir.txt 指针文件；4) 平台默认目录。"""
    if _data_dir_cli:
        return _data_dir_cli
    env = os.environ.get("NGINX_MANAGER_DATA_DIR")
    if env:
        return env
    pointer = os.path.join(default_data_dir(), POINTER_FILE)
    try:
        with open(pointer, "r", encoding="utf-8") as f:
            custom = f.read().strip()
        if custom:
            return custom
    except OSError:
        pass
    return default_data_dir()


def write_data_dir_pointer(target: str) -> None:
    """把自定义数据目录写入默认目录下的指针文件；target 为默认目录时删除指针。"""
    default = default_data_dir()
    os.makedirs(default, exist_ok=True)
    pointer = os.path.join(default, POINTER_FILE)
    if os.path.abspath(target) == os.path.abspath(default):
        try:
            os.remove(pointer)
        except OSError:
            pass
        return
    with open(pointer, "w", encoding="utf-8") as f:
        f.write(target)


def ensure_data_dirs() -> dict:
    """确保数据目录结构存在，返回 {root, settingsFile, backups} 路径。"""
    root = resolve_data_dir()
    backups = os.path.join(root, "backups")
    os.makedirs(root, exist_ok=True)
    os.makedirs(backups, exist_ok=True)
    return {"root": root, "settingsFile": os.path.join(root, "settings.json"), "backups": backups}


# ---------- 单实例 ----------

def _pid_alive(pid: int) -> bool:
    """判断 PID 进程是否存活（跨平台）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_existing_instance(lock_path: str) -> int:
    """读取单实例锁文件，若记录的 PID 存活则强制终止，返回被杀 PID；否则返回 0。"""
    if not os.path.isfile(lock_path):
        return 0
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pid = int(data.get("pid", 0))
    except (json.JSONDecodeError, OSError, ValueError, AttributeError):
        return 0
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, 9)  # Windows 下为强制终止（TerminateProcess）
            print(f"[单实例] 已终止旧实例 (PID {pid})，以当前启动为准")
            return pid
        except OSError as e:
            print(f"[单实例] 终止旧实例失败: {e}")
    return 0


def write_instance_lock(lock_path: str, pid: int, port: int) -> None:
    """写单实例锁文件（记录 PID 与端口）。"""
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "port": port, "startedAt": datetime.now().isoformat()}, f)
    except OSError:
        pass


def restart_command() -> list:
    """构造重启命令：exe 直接重启自身；开发模式去掉 --port 及其值
    （兼容 `--port 8471` 与 `--port=8471` 两种写法，新实例从 settings 读端口）。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包
        return [sys.executable]
    args = []
    skip_next = False
    for a in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a == "--port":
            skip_next = True  # 下一个参数是端口值，一并去掉
            continue
        if a.startswith("--port="):
            continue
        args.append(a)
    return [sys.executable] + args


def spawn_and_exit(cmd: list) -> None:
    """以分离进程启动新实例，然后立即退出当前进程。
    子进程输出写入 <数据目录>/restart_child.log，便于排查重启失败。"""
    try:
        flags = 0
        kwargs = {}
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            kwargs["start_new_session"] = True
        log_path = os.path.join(resolve_data_dir(), "restart_child.log")
        log_f = open(log_path, "a", encoding="utf-8")
        try:
            subprocess.Popen(
                cmd, cwd=os.getcwd(), creationflags=flags, stdin=subprocess.DEVNULL,
                stdout=log_f, stderr=subprocess.STDOUT, **kwargs,
            )
        finally:
            log_f.close()
    except Exception as e:
        print(f"[重启] 启动新实例失败: {e}")
        return
    os._exit(0)  # 当前进程立即退出，由新实例接管（含单实例清理）


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


# 自动保留备份份数（0 = 不自动清理）；main() 启动时从 settings 覆盖
BACKUP_RETENTION = 7


def prune_backups(backups_dir: str, retention: int) -> tuple:
    """删除超过 retention 份的最旧备份目录，返回 (removed, failed)。
    retention<=0 不清理。删除失败（如文件被占用）不阻塞备份，计入 failed。"""
    if retention <= 0 or not os.path.isdir(backups_dir):
        return 0, 0
    names = [n for n in sorted(os.listdir(backups_dir), reverse=True)
             if os.path.isdir(os.path.join(backups_dir, n)) and n.replace("_", "").isdigit()]
    removed, failed = 0, 0
    for old in names[retention:]:
        d = os.path.join(backups_dir, old)
        try:
            shutil.rmtree(d)
            if os.path.isdir(d):
                failed += 1
            else:
                removed += 1
        except OSError:
            failed += 1
    return removed, failed


def delete_backup(backups_dir: str, backup_id: str) -> str:
    """删除单个备份目录。返回 'ok' / 'not_found' / 'failed'。"""
    d = os.path.join(backups_dir, backup_id)
    if not os.path.isdir(d):
        return "not_found"
    try:
        shutil.rmtree(d)
    except OSError:
        return "failed"
    return "ok" if not os.path.isdir(d) else "failed"


def make_backup(backups_dir: str, conf_dir: str, rel_path: str) -> str:
    """备份单个文件到 backups/<时间戳>/<相对路径>，返回备份 id。
    备份后自动清理超出 BACKUP_RETENTION 份的最旧备份。"""
    backup_id = timestamp_id()
    src = os.path.abspath(os.path.join(conf_dir, rel_path))
    dst = os.path.join(backups_dir, backup_id, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    prune_backups(backups_dir, BACKUP_RETENTION)
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


# ---------- 系统文件/目录选择对话框 ----------

_dialog_lock = threading.Lock()  # tkinter 对话框串行化：并发多开 Tk 实例会崩溃


def pick_path_via_dialog(kind: str, title: str = "", initial: str = "",
                         filetypes: list = None) -> dict:
    """弹系统选择框选单个文件（kind="file"）或目录（kind="dir"）。
    initial 为输入框已有路径，作为对话框起始位置。返回 {path}，用户取消时 path=None。"""
    import tkinter as tk
    from tkinter import filedialog

    initial = initial or ""
    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            if kind == "dir":
                path = filedialog.askdirectory(
                    title=title or "请选择目录",
                    initialdir=initial if os.path.isdir(initial) else None,
                    mustexist=True,
                    parent=root,
                )
            else:
                path = filedialog.askopenfilename(
                    title=title or "请选择文件",
                    initialdir=os.path.dirname(initial) if os.path.isdir(os.path.dirname(initial)) else None,
                    filetypes=filetypes or [("所有文件", "*.*")],
                    parent=root,
                )
        finally:
            root.destroy()
    return {"path": path or None}


def pick_nginx_via_dialog() -> dict:
    """弹系统对话框依次选择 nginx 程序与配置目录。返回 {nginxPath, confDir}。"""
    nginx_path = pick_path_via_dialog(
        "file", "请选择 nginx 可执行文件 (nginx.exe / nginx)",
        filetypes=[("nginx 可执行文件", "*.exe"), ("所有文件", "*.*")],
    ).get("path")
    if not nginx_path:
        return {}
    conf_dir = pick_path_via_dialog(
        "dir", "请选择 nginx 配置目录（含 nginx.conf 的目录）",
    ).get("path")
    if not conf_dir:
        return {}
    return {"nginxPath": nginx_path, "confDir": conf_dir}


# ---------- HTTP 处理 ----------

# /api/status 检测结果缓存：detect_process 每次要跑 nginx -v + PowerShell/tasklist
# 子进程（Windows 上 PowerShell 冷启动可达数秒，最坏 20-45s），前端 10s 轮询
# 每次都真跑会堆积请求线程。TTL 15s：每两次轮询才真实检测一次，锁保证同一
# 时刻只有一个线程在跑子进程，其余请求拿到缓存立即返回。
_STATUS_TTL = 15.0
_status_cache = {"data": None, "ts": 0.0}
_status_lock = threading.Lock()


def _invalidate_status_cache() -> None:
    """nginx 启停/重载/重启后调用，让下次轮询强制真实检测。"""
    with _status_lock:
        _status_cache["ts"] = 0.0


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer 定制：
    1. 客户端提前断开（刷新页面/服务重启瞬间的轮询中止）是正常现象，Windows 上
       表现为 ConnectionAbortedError [WinError 10053]，默认会把完整 traceback
       打到控制台（exe 里尤其吓人），这里静默之；其他异常仍走默认 handle_error 打印。
    2. request_queue_size 默认仅 5（listen backlog）：/api/status 每次要跑
       PowerShell/tasklist 子进程（最坏 20-45s），轮询堆积时 backlog 满，
       新连接直接被拒——表现就是「服务假死、必须重启」。加大到 128。
    3. daemon_threads=True：进程退出时不等待残留请求线程。"""

    request_queue_size = 128
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "nginx-manager/0.5.2"
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

    def _csrf_allowed(self) -> bool:
        """写操作需携带 X-Requested-With: XMLHttpRequest（前端统一添加）。
        跨站 <form>/<img> 无法设置自定义请求头，可防本机 CSRF 误触发启停/重载/重启。"""
        return self.headers.get("X-Requested-With") == "XMLHttpRequest"

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
        if not self._csrf_allowed():
            self._err(403, "非法请求（缺少 X-Requested-With 头）")
            return
        parsed = urlparse(self.path)
        self._route_api_post(parsed.path)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._csrf_allowed():
            self._err(403, "非法请求（缺少 X-Requested-With 头）")
            return
        parsed = urlparse(self.path)
        self._route_api_put(parsed.path)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._csrf_allowed():
            self._err(403, "非法请求（缺少 X-Requested-With 头）")
            return
        parsed = urlparse(self.path)
        self._route_api_delete(parsed.path)

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
        elif path == "/api/proxies":
            self._api_proxies_get()
        elif path == "/api/proxy-pool":
            self._api_proxy_pool_get()
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
        with _status_lock:
            now = time.time()  # 锁内取时间：等锁期间前任检测可能刚刷新缓存
            cached = _status_cache["data"]
            if cached is not None and now - _status_cache["ts"] < _STATUS_TTL:
                data = cached
            else:
                info = self.controller.detect_process()
                conf_path = self.controller.main_conf_path()
                data = {
                    "running": info.get("running", False),
                    "version": info.get("version"),
                    "pid": info.get("pid"),
                    "nginxPath": self.controller.nginx_path,
                    "confDir": self.controller.conf_dir,
                    "confPath": conf_path,
                    "confFileExists": os.path.isfile(conf_path),
                }
                _status_cache["data"] = data
                _status_cache["ts"] = time.time()
        self._ok(data)

    def _api_config(self) -> None:
        if self.controller is None:
            # 预览模式（未配置 nginx）：返回空树，前端渲染空状态而非报错
            self._ok({"tree": [], "included": [], "preview": True})
            return
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
        self._ok({
            "backups": list_backups(self.data_dirs["backups"]),
            "retention": BACKUP_RETENTION,
        })

    def _api_backups_delete(self) -> None:
        body = self._read_json_body()
        backup_id = str(body.get("id", ""))
        if not backup_id or not backup_id.replace("_", "").isdigit():
            self._err(400, "id 参数非法")
            return
        result = delete_backup(self.data_dirs["backups"], backup_id)
        if result == "not_found":
            self._err(404, f"备份不存在: {backup_id}")
            return
        if result == "failed":
            self._err(500, f"删除备份失败（文件可能被占用）: {backup_id}")
            return
        self._ok({
            "ok": True,
            "deleted": backup_id,
            "backups": list_backups(self.data_dirs["backups"]),
            "retention": BACKUP_RETENTION,
        })

    def _api_logs_error(self, qs: dict) -> None:
        if self.controller is None:
            # 预览模式：无 nginx，无错误日志可读
            self._ok({"logPath": None, "content": "（预览模式：未配置 nginx，暂无错误日志）"})
            return
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
            "port": int(self.settings.get("port", 0) or 0) or DEFAULT_PORT,
            "backupRetention": self.settings.get("backupRetention", BACKUP_RETENTION),
            "configured": self.settings.configured(),
            "preview": Handler.controller is None,
            # manager 自身配置文件地址（数据目录）
            "dataDir": self.data_dirs["root"],
            "settingsFile": self.data_dirs["settingsFile"],
            "dataDirLocked": data_dir_locked(),
        })

    def _api_pick_path(self) -> None:
        """弹系统选择框选文件/目录：body {kind: "file"|"dir", initial?, title?}。
        用户取消时返回 {path: null}。请求会阻塞到对话框关闭（ThreadingHTTPServer 不影响其他请求）。"""
        body = self._read_json_body()
        kind = str(body.get("kind") or "dir")
        if kind not in ("file", "dir"):
            self._err(400, "kind 必须为 file 或 dir")
            return
        if not _tkinter_available():
            self._err(501, "当前环境无图形界面，无法弹出系统选择框，请手动输入路径")
            return
        try:
            res = pick_path_via_dialog(
                kind, str(body.get("title") or ""), str(body.get("initial") or ""),
            )
        except Exception as e:
            self._err(500, f"弹出系统选择框失败: {e}")
            return
        self._ok(res)

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
        elif path == "/api/proxies":
            self._api_proxies_add()
        elif path == "/api/proxy-pool":
            self._api_proxy_pool_add()
        elif path == "/api/pick-path":
            self._api_pick_path()
        elif path == "/api/restart":
            self._api_restart()
        else:
            self._err(404, "接口不存在")

    def _api_nginx_start(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.start()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(500, msg)

    def _api_nginx_stop(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.stop()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(409, msg)

    def _api_nginx_reload(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.reload()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(500, msg)

    def _api_nginx_restart(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.restart()
        if ok:
            _invalidate_status_cache()
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
        elif path == "/api/proxies/switch":
            self._api_proxies_switch()
        elif path == "/api/proxies/targets":
            self._api_proxies_targets()
        elif path == "/api/proxy-pool":
            self._api_proxy_pool_put()
        else:
            self._err(404, "接口不存在")

    # ---- API: DELETE ----

    def _route_api_delete(self, path: str) -> None:
        if path == "/api/proxies":
            self._api_proxies_remove()
        elif path == "/api/proxy-pool":
            self._api_proxy_pool_remove()
        elif path == "/api/backups":
            self._api_backups_delete()
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
        do_backup = bool(body.get("doBackup", False))  # 显式确认才备份，默认不备份
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

        backup_id = None
        if do_backup:
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
                    "backedUp": do_backup,
                    "test": result,
                })
                return
            self._ok({"ok": True, "backupId": backup_id, "backedUp": do_backup, "test": result})
        else:
            self._ok({"ok": True, "backupId": backup_id, "backedUp": do_backup})

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
        # 备份保留份数（可选，0=不自动清理；范围 0~100）
        retention = body.get("backupRetention")
        if retention is not None:
            try:
                r = int(retention)
                if not (0 <= r <= 100):
                    raise ValueError
                self.settings.set("backupRetention", r)
                global BACKUP_RETENTION
                BACKUP_RETENTION = r
            except (TypeError, ValueError):
                self._err(400, "backupRetention 必须为 0~100 的整数")
                return
        # 监听端口（可选，1~65535）
        new_port = body.get("port")
        if new_port is not None:
            try:
                np = int(new_port)
                if not (1 <= np <= 65535):
                    raise ValueError
                self.settings.set("port", np)
            except (TypeError, ValueError):
                self._err(400, "port 必须为 1~65535 的整数")
                return
        # manager 自身数据目录（可选）：改地址后需重启生效（新实例按指针文件读取）
        data_dir_raw = str(body.get("dataDir") or "").strip()
        data_dir_changed = False
        if data_dir_raw and os.path.abspath(data_dir_raw) != os.path.abspath(self.data_dirs["root"]):
            if data_dir_locked():
                self._err(409, "当前数据目录由 --data-dir 参数或环境变量指定，无法在界面修改")
                return
            target = os.path.abspath(data_dir_raw)
            # 校验目标目录可创建、可写
            try:
                os.makedirs(target, exist_ok=True)
                probe = os.path.join(target, ".write_probe")
                with open(probe, "w", encoding="utf-8") as f:
                    f.write("ok")
                os.remove(probe)
            except OSError as e:
                self._err(409, f"目标数据目录不可用: {e}")
                return
            # 迁移既有数据（不覆盖目标已有文件；设置/备份随身带走，旧目录保留作回退）
            try:
                old_settings = os.path.join(self.data_dirs["root"], "settings.json")
                new_settings = os.path.join(target, "settings.json")
                if os.path.isfile(old_settings) and not os.path.isfile(new_settings):
                    shutil.copyfile(old_settings, new_settings)
                old_backups = self.data_dirs["backups"]
                if os.path.isdir(old_backups):
                    new_backups = os.path.join(target, "backups")
                    os.makedirs(new_backups, exist_ok=True)
                    for name in os.listdir(old_backups):
                        src = os.path.join(old_backups, name)
                        dst = os.path.join(new_backups, name)
                        if os.path.isdir(src):
                            if not os.path.isdir(dst):
                                shutil.copytree(src, dst)
                        elif not os.path.isfile(dst):
                            shutil.copyfile(src, dst)
            except OSError as e:
                self._err(500, f"迁移配置文件到新目录失败: {e}")
                return
            # 写指针文件（目标即平台默认目录时删除指针），重启后新实例按指针读取
            try:
                write_data_dir_pointer(target)
            except OSError as e:
                self._err(500, f"写入数据目录指针文件失败: {e}")
                return
            data_dir_changed = True
        Handler.controller = create_controller(nginx_path, conf_dir)
        _invalidate_status_cache()  # nginx 路径变化，检测对象已不同
        if data_dir_changed:
            # 延迟 0.5s 让响应先返回；新实例按指针文件从新目录读取配置
            threading.Timer(0.5, spawn_and_exit, args=(restart_command(),)).start()
            self._ok({
                "ok": True, "restarting": True, "dataDir": data_dir_raw,
                "nginxPath": nginx_path, "confDir": conf_dir,
                "port": int(self.settings.get("port", 0) or 0) or DEFAULT_PORT,
                "backupRetention": self.settings.get("backupRetention", BACKUP_RETENTION),
            })
            return
        self._ok({
            "ok": True,
            "nginxPath": nginx_path,
            "confDir": conf_dir,
            "port": int(self.settings.get("port", 0) or 0) or DEFAULT_PORT,
            "backupRetention": self.settings.get("backupRetention", BACKUP_RETENTION),
        })

    # ---- 服务重启 ----

    def _api_restart(self) -> None:
        """保存端口后自动重启：启动新实例（分离进程），随后退出当前进程。
        新实例启动时通过单实例锁杀掉旧实例（本进程），以新启动为准。"""
        body = self._read_json_body()
        new_port = body.get("port")
        if new_port is not None:
            try:
                np = int(new_port)
                if not (1 <= np <= 65535):
                    raise ValueError
                self.settings.set("port", np)
            except (TypeError, ValueError):
                self._err(400, "port 必须为 1~65535 的整数")
                return
        cmd = restart_command()
        target_port = int(self.settings.get("port", 0) or 0) or DEFAULT_PORT
        self._ok({"ok": True, "restarting": True, "port": target_port})
        # 延迟 0.5s 让响应先返回，再启动新实例并退出当前进程
        threading.Timer(0.5, spawn_and_exit, args=(cmd,)).start()

    # ---- 代理管理 ----

    def _proxy_manager(self):
        """构造 ProxyManager（锁定 nginx.conf）；未配置时返回 None 并已回错误。"""
        ctl = self._require_controller()
        if ctl is None:
            return None
        conf_path = ctl.main_conf_path()
        if not os.path.isfile(conf_path):
            self._err(409, f"主配置文件不存在: {conf_path}")
            return None
        return ProxyManager(conf_path)

    def _proxy_apply(self, pm, mutate, path=None, extra=None):
        """统一执行代理写变更：mutate(pm) 返回 {ok, ...}。

        - mutate 失败：按错误语义发 4xx 响应，返回 True（调用方直接 return）。
        - 内容无变化（pm.content 未改变）：跳过备份与 nginx -t，直接发成功响应
          （backupId 为 None），避免无意义的冗余备份。
        - 内容有变化：先备份 → commit → nginx -t 校验；校验失败回滚原文并回 409；
          成功发成功响应。
        - mutate 返回值中除 ok/error 外的键合并进成功响应（如池操作的 targets）。
        返回 True 表示响应已发送，调用方应 return。"""
        original = pm.content
        res = mutate(pm)
        if not res.get("ok"):
            err = res.get("error", "操作失败")
            status = 400
            if "不存在" in err or "不在池中" in err:
                status = 404
            elif "备选" in err or "校验" in err or "激活" in err or "已在池中" in err or "没有代理" in err:
                status = 409
            self._err(status, err)
            return True
        res_extra = {k: v for k, v in res.items() if k not in ("ok", "error")}
        if pm.content == original:
            # 无实际变化（如切换到已激活目标、备选列表与当前一致），不备份不校验
            proxy = next((p for p in pm.list_proxies() if p["path"] == path), None) if path else None
            payload = {"ok": True, "proxy": proxy, "backupId": None,
                       "test": {"ok": True, "output": "配置无变化，未做改动"}}
            payload.update(res_extra)
            payload.update(extra or {})
            self._ok(payload)
            return True
        backup_id = make_backup(self.data_dirs["backups"], self.controller.conf_dir, "nginx.conf")
        pm.commit()
        _code, result = self.controller.test_config()
        if not result.get("ok"):
            pm.restore(original)
            self._send_json(409, {
                "error": "修改后 nginx -t 校验失败，已回滚（配置未改动）",
                "detail": result.get("output", ""),
                "test": result,
            })
            return True
        proxy = next((p for p in pm.list_proxies() if p["path"] == path), None) if path else None
        payload = {"ok": True, "proxy": proxy, "backupId": backup_id, "test": result}
        payload.update(res_extra)
        payload.update(extra or {})
        self._ok(payload)
        return True

    def _api_proxies_get(self) -> None:
        if self.controller is None:
            # 预览模式：无 nginx.conf，返回空代理列表
            self._ok({"proxies": [], "sourceFile": None, "preview": True})
            return
        pm = self._proxy_manager()
        if pm is None:
            return
        self._ok({"proxies": pm.list_proxies(), "sourceFile": os.path.basename(pm.conf_path)})

    def _api_proxies_add(self) -> None:
        pm = self._proxy_manager()
        if pm is None:
            return
        body = self._read_json_body()
        path = str(body.get("path", ""))
        target = str(body.get("target", ""))

        def mutate(pm):
            return pm.add(path, target)
        if self._proxy_apply(pm, mutate, path):
            return

    def _api_proxies_switch(self) -> None:
        pm = self._proxy_manager()
        if pm is None:
            return
        body = self._read_json_body()
        path = str(body.get("path", ""))
        target = str(body.get("target", ""))
        # 若该地址（含等价写法，如末尾斜杠/大小写/默认端口差异）已在备选中，
        # 直接复用已有行切换，避免写入重复地址；否则先自动追加为备选再切换
        proxy_cur = next((p for p in pm.list_proxies() if p["path"] == path), None)
        if proxy_cur is None:
            self._err(404, f"代理不存在: {path}")
            return
        targets = proxy_cur.get("targets", [])
        hit = next((t for t in targets if _pool_key(t) == _pool_key(target)), None)
        if hit is not None:
            target = hit
        elif target not in targets:
            new_targets = list(targets) + [target]
            res = pm.update_targets(path, new_targets)
            if not res.get("ok"):
                self._err(409, res.get("error", "自动加入备选失败"))
                return

        def mutate(pm):
            return pm.switch(path, target)
        if self._proxy_apply(pm, mutate, path):
            return

    # ---- 目标地址池（与 nginx.conf 合一：池 = 全部 proxy_pass 目标并集，增删改查直接写配置文件）----

    def _api_proxy_pool_get(self) -> None:
        if self.controller is None:
            # 预览模式：无 nginx.conf，返回空池
            self._ok({"targets": [], "preview": True})
            return
        pm = self._proxy_manager()
        if pm is None:
            return
        self._ok({"targets": pm.pool_targets()})

    def _api_proxy_pool_add(self) -> None:
        body = self._read_json_body()
        target = str(body.get("target", "")).strip()
        alias = " ".join(str(body.get("alias", "")).split())
        if not target:
            self._err(400, "target 必填")
            return
        pm = self._proxy_manager()
        if pm is None:
            return

        def mutate(p):
            res = p.pool_add(target, alias)
            if res.get("ok"):
                res["targets"] = p.pool_targets()
            return res
        if self._proxy_apply(pm, mutate):
            return

    def _api_proxy_pool_put(self) -> None:
        body = self._read_json_body()
        target = str(body.get("target", "")).strip()
        alias = " ".join(str(body.get("alias", "")).split())
        if not target:
            self._err(400, "target 必填")
            return
        pm = self._proxy_manager()
        if pm is None:
            return

        def mutate(p):
            res = p.pool_set_alias(target, alias)
            if res.get("ok"):
                res["targets"] = p.pool_targets()
            return res
        if self._proxy_apply(pm, mutate):
            return

    def _api_proxy_pool_remove(self) -> None:
        body = self._read_json_body()
        target = str(body.get("target", "")).strip()
        if not target:
            self._err(400, "target 必填")
            return
        pm = self._proxy_manager()
        if pm is None:
            return

        def mutate(p):
            res = p.pool_remove(target)
            if res.get("ok"):
                res["targets"] = p.pool_targets()
            return res
        if self._proxy_apply(pm, mutate):
            return

    def _api_proxies_targets(self) -> None:
        pm = self._proxy_manager()
        if pm is None:
            return
        body = self._read_json_body()
        path = str(body.get("path", ""))
        targets = body.get("targets")
        if not isinstance(targets, list):
            self._err(400, "targets 必须为数组")
            return

        def mutate(pm):
            return pm.update_targets(path, targets)
        if self._proxy_apply(pm, mutate, path):
            return

    def _api_proxies_remove(self) -> None:
        pm = self._proxy_manager()
        if pm is None:
            return
        body = self._read_json_body()
        path = str(body.get("path", ""))

        def mutate(pm):
            return pm.remove(path)
        if self._proxy_apply(pm, mutate, path, extra={"deleted": path}):
            return

    # ---- 日志 ----

    def log_message(self, fmt, *args) -> None:  # 静默默认访问日志
        pass


# ---------- 端口 ----------

DEFAULT_PORT = 8310  # 固定默认端口；--port 可覆盖


def find_free_port(preferred: int | None, retries: int = 10) -> int:
    """返回可绑定端口。preferred 被占用时短暂重试（旧实例被杀后端口释放有延迟），
    仍不可用则退回随机空闲端口。"""
    if preferred:
        for _ in range(retries):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", preferred))
                    return preferred
                except OSError:
                    time.sleep(0.2)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- 入口 ----------

def find_workspace_nginx() -> dict:
    """开发默认：若工作区根目录存在 nginx-1.30.4/（标准 Windows 版布局），
    直接作为管理对象，跳过首次对话框。返回 {nginxPath, confDir} 或空 dict。"""
    exe = os.path.join(PROJECT_ROOT, "nginx-1.30.4", "nginx.exe")
    conf_dir = os.path.join(PROJECT_ROOT, "nginx-1.30.4", "conf")
    if os.path.isfile(exe) and os.path.isfile(os.path.join(conf_dir, "nginx.conf")):
        return {"nginxPath": exe, "confDir": conf_dir}
    return {}


def _tkinter_available() -> bool:
    """检测当前环境是否可导入 tkinter（无图形环境/CI/服务器返回 False）。"""
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def migrate_legacy_pool(ctl: NginxController) -> None:
    """一次性迁移：旧版目标地址池文件 targets.json（独立 JSON 存储）合并进 nginx.conf——
    池中尚未存在于配置的地址，追加为所有代理块的注释备选；完成后将文件改名为
    targets.json.migrated。失败不阻塞启动，保留原文件待下次重试。"""
    from proxymgr import _normalize_target
    legacy_path = os.path.join(Handler.data_dirs["root"], "targets.json")
    if not os.path.isfile(legacy_path):
        return
    conf_path = ctl.main_conf_path()
    if not os.path.isfile(conf_path):
        print("[迁移] 跳过：主配置文件不存在，旧目标池 targets.json 保留")
        return
    try:
        with open(legacy_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, list):
            os.replace(legacy_path, legacy_path + ".migrated")
            return
        pm = ProxyManager(conf_path)
        existing = {t["target"] for t in pm.pool_targets()}
        added = skipped = failed = 0
        for item in d:
            if isinstance(item, str):
                target, alias = item.strip(), ""
            elif isinstance(item, dict):
                target = str(item.get("target") or "").strip()
                alias = " ".join(str(item.get("alias") or "").split())
            else:
                skipped += 1
                continue
            if not target or target in existing:
                skipped += 1
                continue
            if _normalize_target(target) is None:
                print(f"[迁移] 跳过非法地址: {target}")
                skipped += 1
                continue
            if pm.pool_add(target, alias).get("ok"):
                existing.add(target)
                added += 1
            else:
                failed += 1  # 如配置中没有任何代理块，暂无法写入
        if added:
            backup_id = make_backup(Handler.data_dirs["backups"], ctl.conf_dir, "nginx.conf")
            pm.commit()
            print(f"[迁移] 旧目标池 {added} 个地址已写入 nginx.conf（备份 {backup_id}）")
        if failed:
            print(f"[迁移] {failed} 个地址未能写入（如当前配置没有代理），targets.json 保留待下次重试")
            return
        os.replace(legacy_path, legacy_path + ".migrated")
        if added or skipped:
            print(f"[迁移] 旧目标池已合并进配置文件，原文件改名为 targets.json.migrated")
    except (json.JSONDecodeError, OSError) as e:
        print(f"[迁移] 旧目标池迁移失败（忽略，不影响启动）: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="nginx 轻量网页管理端")
    parser.add_argument("--port", type=int, default=None, help=f"监听端口（缺省 {DEFAULT_PORT}）")
    parser.add_argument("--nginx-path", default=None, help="nginx 可执行文件路径（跳过首次选择对话框）")
    parser.add_argument("--conf-dir", default=None, help="nginx 配置目录（跳过首次选择对话框）")
    parser.add_argument(
        "--preview", action="store_true",
        help="预览模式：不要求 nginx 已安装/配置，仅提供前端 UI 预览与接口调试；"
             "可通过「设置」填写 nginxPath/confDir 后重载以退出预览",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="manager 自身数据目录（settings.json/备份的存放位置，默认按平台约定，"
             "亦可用环境变量 NGINX_MANAGER_DATA_DIR 指定）；优先级高于界面修改",
    )
    args = parser.parse_args()

    global _data_dir_cli
    _data_dir_cli = args.data_dir

    data_dirs = ensure_data_dirs()
    Handler.data_dirs = data_dirs
    Handler.settings = SettingsStore(data_dirs["root"])
    # 备份保留份数：settings 覆盖模块默认（0=不自动清理）
    global BACKUP_RETENTION
    BACKUP_RETENTION = int(Handler.settings.get("backupRetention", 7) or 7)

    # 单实例：若已有旧实例在运行，强制终止，以当前启动为准
    lock_path = os.path.join(data_dirs["root"], "instance.lock")
    killed = kill_existing_instance(lock_path)

    # 预览模式：显式 --preview，或当前无图形环境（tkinter 不可用，如服务器/CI 本地调试）。
    # 但若 settings 已配置有效 nginx，则仍以正常模式运行——
    # 否则「预览中配置 nginx 后重启服务」会因 --preview 仍在 argv 而丢回 controller=None。
    preview_requested = bool(args.preview) or (not _tkinter_available())
    already_configured = bool(Handler.settings.get("nginxPath") and Handler.settings.get("confDir"))
    if preview_requested and not already_configured:
        Handler.controller = None
        print("[预览模式] 未配置 nginx（controller=None），仅提供前端 UI 预览与接口调试。")
        print("            如需管理真实配置，请在「设置」中填写 nginx 路径与配置目录。")
    else:
        nginx_path = args.nginx_path or Handler.settings.get("nginxPath")
        conf_dir = args.conf_dir or Handler.settings.get("confDir")

        if not nginx_path or not conf_dir:
            # 开发默认：工作区自带 nginx-1.30.4（测试用）
            ws = find_workspace_nginx()
            if ws:
                nginx_path = ws["nginxPath"]
                conf_dir = ws["confDir"]
                print(f"[默认] 使用工作区 nginx: {nginx_path}")
                print(f"      confDir: {conf_dir}")
            else:
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
        # 旧版 targets.json 目标池合并进 nginx.conf（一次性，文件不存在时为空操作）
        migrate_legacy_pool(Handler.controller)
    # 端口优先级：--port > settings.port > DEFAULT_PORT
    prefer_port = args.port or int(Handler.settings.get("port", 0) or 0) or DEFAULT_PORT
    port = find_free_port(prefer_port)
    if port != prefer_port:
        print(f"[提示] 端口 {prefer_port} 被占用，改用随机端口 {port}")
    server = Server(("127.0.0.1", port), Handler)
    write_instance_lock(lock_path, os.getpid(), port)
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

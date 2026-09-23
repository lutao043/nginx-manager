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
import difflib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from email.utils import formatdate, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from nginxctl import NginxController, create_controller
from proxymgr import ProxyManager, _pool_key, atomic_write_text

APP_NAME = "nginx-manager"
IS_FROZEN = getattr(sys, "frozen", False)
# server.py 位于 backend/ 下，项目根为其父目录
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
# 前端资源目录：开发时读项目根 frontend/；打包后读 sys._MEIPASS/frontend/
FRONTEND_DIR = os.path.join(getattr(sys, "_MEIPASS", PROJECT_ROOT), "frontend")
# 发布说明目录（「更新历史」的数据源）：开发时读项目根 release-notes/；打包后读解压目录，
# 启动时由 stage_release_notes 持久化到数据目录
NOTES_DIR = os.path.join(getattr(sys, "_MEIPASS", PROJECT_ROOT), "release-notes")


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


# ---------- 前端资源目录（exe 运行时持久化） ----------

# 静态资源缺失的一次性告警开关（运行期间只打印一次，避免轮询刷屏）
_static_missing_warned = False


def _warn_static_missing(fp: str) -> None:
    """静态资源读取失败时在控制台提示真实原因，便于现场排查。"""
    global _static_missing_warned
    if _static_missing_warned:
        return
    _static_missing_warned = True
    print(f"[警告] 静态资源缺失: {fp}")
    print(f"       前端目录: {FRONTEND_DIR}")
    if IS_FROZEN:
        print("       exe 正从解压临时目录读取前端资源，该目录可能已被系统/清理软件删除；"
              "API 不受影响，仅页面 404。重启服务可临时恢复。")


def manager_version() -> str:
    """manager 自身版本号：唯一来源 Handler.server_version（去掉 "nginx-manager/" 前缀）。"""
    return Handler.server_version.split("/", 1)[-1]


def _stage_versioned(src: str, base: str, ready, label: str) -> str:
    """把打包资源复制到数据目录按版本持久保存，返回实际使用的目录。

    源码运行：直接用项目里的原始目录。
    exe 运行：onefile 的解压目录（%TEMP%\\_MEI*）可能在运行期间被磁盘清理/存储感知/
    管家类软件删除（症状：API 正常但页面 404、更新历史为空），故启动时把资源复制到
    数据目录按版本持久保存；复制失败回退解压目录。ready(dst) 判断副本是否已可用。
    """
    if not IS_FROZEN:
        return src
    dst = os.path.join(base, f"v{manager_version()}")
    if ready(dst):
        return dst
    try:
        tmp = dst + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        shutil.rmtree(dst, ignore_errors=True)
        os.replace(tmp, dst)
        # 顺带清理旧版本目录与残留临时目录（只动 v* 与 *.tmp，不碰其他内容）
        for name in os.listdir(base):
            if name == os.path.basename(dst) or not (name.startswith("v") or name.endswith(".tmp")):
                continue
            stale = os.path.join(base, name)
            if os.path.isdir(stale):
                shutil.rmtree(stale, ignore_errors=True)
        return dst
    except OSError as e:
        print(f"[警告] {label}持久化失败，回退解压目录: {e}")
        return src


def stage_frontend(root: str) -> str:
    """前端资源目录（见 _stage_versioned）：以 index.html 判断副本是否可用。"""
    src = os.path.join(getattr(sys, "_MEIPASS", PROJECT_ROOT), "frontend")
    return _stage_versioned(src, os.path.join(root, "frontend"),
                            lambda d: os.path.isfile(os.path.join(d, "index.html")), "前端资源")


def _notes_ready(d: str) -> bool:
    return os.path.isdir(d) and any(name.endswith(".md") for name in os.listdir(d))


def stage_release_notes(root: str) -> str:
    """发布说明目录（见 _stage_versioned）：以目录内存在 .md 判断副本是否可用。"""
    return _stage_versioned(NOTES_DIR, os.path.join(root, "release-notes"), _notes_ready, "发布说明")


# ---------- 更新历史（release-notes/*.md → GET /api/changelog） ----------

_NOTES_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_NOTES_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_NOTES_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_NOTES_LINK_RE = re.compile(r"Full Changelog[^\n]*?(https?://\S+)", re.IGNORECASE)


def version_sort_key(version: str) -> tuple:
    """版本排序键：同号下正式版排在预发布版之前，预发布之间按后缀逐段比较。

    不能用 tuple(int(x) for x in version.split("."))：预发布号如 "1.0.0-rc.1"
    会切出非整数的段，直接转 int 抛 ValueError。
    """
    core = version.lstrip("v")
    pre = ""
    if "-" in core:
        core, pre = core.split("-", 1)
    nums = tuple(int(x) if x.isdigit() else 0 for x in core.split("."))
    if not pre:
        return (nums, 1, ())
    return (nums, 0, tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[._-]", pre)))


def parse_release_note(text: str, version: str) -> dict:
    """把一份 release-notes 正文解析成 {version, title, sections, link}。

    版本号取自文件名（v0.3.x 的旧说明正文里没有版本号）；title 取首个 "## " 标题并去掉
    开头的版本号前缀；items 收 "### " 小节下的 "-" 列表项与散文段落（围栏代码块、引用、
    表格、分隔线不进历史）；出现在任何小节之前的内容归入标题为空的小节，不静默丢弃。
    """
    title = ""
    sections = []

    def add_item(value: str) -> None:
        if not sections:
            sections.append({"title": "", "items": []})
        sections[-1]["items"].append(value)

    link = ""
    in_fence = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _NOTES_H3_RE.match(line)
        if m:
            sections.append({"title": m.group(1), "items": []})
            continue
        m = _NOTES_H2_RE.match(line)
        if m:
            if not title:
                title = m.group(1)
            continue
        m = _NOTES_BULLET_RE.match(line)
        if m:
            add_item(m.group(1))
            continue
        m = _NOTES_LINK_RE.search(line)
        if m:
            link = m.group(1)
            continue
        # 散文段落（说明开头常有一段「这一版做了什么」，v0.6.3 的「根因」整节都是散文）
        if line and not line.startswith((">", "|", "#", "---", "***")):
            add_item(line)
    for prefix in (f"{version}：", f"{version}:", f"v{version}：", f"v{version}:"):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
            break
    return {"version": version, "title": title, "sections": sections, "link": link}


def load_releases(notes_dir: str) -> list:
    """读取 notes_dir 下的 v*.md，按版本倒序返回；目录不可读或无说明文件时返回空列表。"""
    try:
        names = os.listdir(notes_dir)
    except OSError:
        return []
    releases = []
    for name in sorted(names):
        if not (name.startswith("v") and name.endswith(".md")):
            continue
        version = name[1:-3]
        if not version or not version[0].isdigit():
            continue
        try:
            with open(os.path.join(notes_dir, name), "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        releases.append(parse_release_note(text, version))
    releases.sort(key=lambda r: version_sort_key(r["version"]), reverse=True)
    return releases


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
        # 原子写：settings.json 写坏会让下次启动直接读不到 nginx 配置（表现为「未配置」）
        atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                          newline="\n")

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()


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
    备份后自动清理超出 BACKUP_RETENTION 份的最旧备份。

    id 取秒级时间戳，但同一秒内的多次备份（连续保存/连续代理操作）会撞同一个目录——
    旧快照会被静默覆盖，前端「回滚到 <id>」就会退回到别的时点。故 id 被占用时顺延到
    下一个空闲秒（格式保持 `%Y%m%d_%H%M%S`，与 list_backups 的解析、按名排序兼容）。"""
    base = datetime.now()
    backup_id = timestamp_id()
    for offset in range(1, 60):
        if not os.path.exists(os.path.join(backups_dir, backup_id)):
            break
        backup_id = (base + timedelta(seconds=offset)).strftime("%Y%m%d_%H%M%S")
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


class _DiffSourceNotFound(Exception):
    """备份对比数据源缺失（当前文件或指定备份内无此文件）。"""


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
    server_version = "nginx-manager/1.0.0-rc.2"

    # HTTP/1.1：默认带 keep-alive，轮询不再每次新建 TCP 连接 + 新线程（空闲时约 1.2 req/s，
    # 按 HTTP/1.0 算每天要新建约 10 万次线程）。开启前提是**每个响应都必须带准确的
    # Content-Length**（否则客户端会一直等下一条响应）——本服务只有 _send_json 与
    # _serve_static/304 两条响应写出路径，均显式设置。
    protocol_version = "HTTP/1.1"
    # 空闲 keep-alive 连接的超时：到期即释放线程，避免连接被挂在后台不放
    timeout = 30

    settings: SettingsStore = None  # type: ignore
    data_dirs: dict = {}
    controller: NginxController = None  # type: ignore

    # ---- 工具 ----

    _body_consumed = True  # 每个请求开始时由 handle_one_request 复位
    _head_only = False     # HEAD 请求：只发头部、不发正文

    def handle_one_request(self) -> None:
        """每个请求复位「请求体已读」标记。

        HTTP/1.1 下同一个 Handler 实例会在一条连接上服务多个请求，故这个标记必须
        按请求复位，不能只在 __init__ 里设一次。
        """
        self._body_consumed = False
        self._head_only = False
        super().handle_one_request()

    def _drain_body(self) -> None:
        """读掉尚未被读走的请求体。

        HTTP/1.1 复用连接后，这一点是必须的：提前返回（CSRF 校验失败、参数非法等）时
        若请求体还留在 socket 里，那些字节会被当成**下一个请求**的请求行，表现为
        莫名其妙的 501/400，且之后整条连接全乱。原 HTTP/1.0 每请求一条连接，残留字节
        随连接一起丢弃，所以没暴露这个问题。
        """
        if self._body_consumed:
            return
        self._body_consumed = True
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            try:
                self.rfile.read(length)
            except (OSError, ValueError):
                self.close_connection = True

    def _send_json(self, status: int, payload: dict) -> None:
        self._drain_body()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def _ok(self, payload: dict) -> None:
        self._send_json(200, payload)

    def _err(self, status: int, error: str, detail: str = None) -> None:
        payload = {"error": error}
        if detail:
            payload["detail"] = detail
        self._send_json(status, payload)

    def _read_json_body(self) -> dict:
        self._body_consumed = True
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

    @staticmethod
    def _static_validators(st) -> Tuple[str, str]:
        """静态资源的 (ETag, Last-Modified)。ETag 由内容身份派生：升级 exe 后资源一变，
        标签必然跟着变，浏览器不会拿旧 JS 去跑新后端。"""
        return '"%x-%x"' % (st.st_mtime_ns, st.st_size), formatdate(st.st_mtime, usegmt=True)

    def _if_none_match(self, etag: str, mtime: float) -> bool:
        """条件请求判定：If-None-Match 优先于 If-Modified-Since（RFC 9110 §13.1.3）。"""
        inm = self.headers.get("If-None-Match")
        if inm:
            tags = [t.strip() for t in inm.split(",")]
            return etag in tags or "*" in tags
        ims = self.headers.get("If-Modified-Since")
        if not ims:
            return False
        try:
            since = parsedate_to_datetime(ims)
        except (TypeError, ValueError, IndexError):
            return False
        if since is None:
            return False
        if since.tzinfo is None:  # 无时区的日期按 GMT 解释（HTTP 日期一律 GMT）
            since = since.replace(tzinfo=timezone.utc)
        return int(mtime) <= int(since.timestamp())

    def _serve_static(self, path: str) -> None:
        self._drain_body()  # GET 一般无体；异常客户端带体时必须读掉，否则连接串位
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        if ".." in rel.split("/"):
            self._err(404, "Not Found")
            return
        fp = os.path.join(FRONTEND_DIR, rel)
        if not os.path.isfile(fp):
            _warn_static_missing(fp)
            self._err(404, "Not Found")
            return
        try:
            st = os.stat(fp)
        except OSError:
            self._err(500, "读取静态资源失败")
            return
        etag, last_modified = self._static_validators(st)
        # 条件请求命中：回 304 且不带正文（前端资源合计约 200KB，刷新页面时省掉整份重传）。
        # 注：304 不带 Content-Length 是合规的（无正文），http.client 对 304 直接按 length=0 处理。
        if self._if_none_match(etag, st.st_mtime):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", last_modified)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
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
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", last_modified)
        self.send_header("Cache-Control", "no-cache")  # 每次重校验（revalidate），不直接吃旧缓存
        self.end_headers()
        if not self._head_only:
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

    def do_HEAD(self) -> None:  # noqa: N802
        """HEAD 与 GET 同路由，只是不写正文（`curl -I` 做探活/看状态码用得上）。

        Content-Length 仍按 GET 的实际长度给出，这是 HEAD 的合规形态；正文由
        _send_json / 静态写出点按 _head_only 跳过，故 keep-alive 下不会串位。
        """
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

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
        elif path == "/api/changelog":
            self._api_changelog()
        elif path == "/api/config":
            self._api_config()
        elif path == "/api/config/file":
            self._api_config_file_get(qs)
        elif path == "/api/backups":
            self._api_backups()
        elif path == "/api/backups/diff":
            self._api_backups_diff(qs)
        elif path == "/api/logs/error":
            self._api_logs_error(qs)
        elif path == "/api/logs/access":
            self._api_logs_access(qs)
        elif path == "/api/metrics":
            self._api_metrics_get()
        elif path == "/api/proxies":
            self._api_proxies_get()
        elif path == "/api/proxy-pool":
            self._api_proxy_pool_get()
        elif path == "/api/upstreams":
            self._api_upstreams_get()
        elif path == "/api/settings":
            self._api_settings_get()
        else:
            self._err(404, "接口不存在")

    def _api_status(self) -> None:
        frontend_ok = os.path.isfile(os.path.join(FRONTEND_DIR, "index.html"))
        if self.controller is None:
            self._ok({
                "running": False, "version": None, "pid": None,
                "nginxPath": self.settings.get("nginxPath"),
                "confDir": self.settings.get("confDir"),
                "confPath": None, "confFileExists": False,
                "managerVersion": manager_version(),
                "frontendOk": frontend_ok,
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
        data["frontendOk"] = frontend_ok  # 缓存外现算：资源健康度不受 15s TTL 影响
        data["managerVersion"] = manager_version()  # 缓存外现算：运行版本与 TTL 无关
        self._ok(data)

    def _api_changelog(self) -> None:
        """更新历史：release-notes/*.md 按版本倒序（最新在前）。

        纯本地读取，不做任何联网更新检查——本产品没有自动更新。
        """
        releases = load_releases(NOTES_DIR)
        self._ok({
            "version": manager_version(),
            "releases": releases,
            "notesAvailable": bool(releases),
        })

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

    def _api_backups_diff(self, qs: dict) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        a = (qs.get("a") or [""])[0].strip()
        b = (qs.get("b") or [""])[0].strip()
        rel = self._safe_rel((qs.get("path") or [""])[0])
        for sid in (a, b):
            if sid != "current" and not (sid and sid.replace("_", "").isdigit()):
                self._err(400, "a/b 须为 current 或备份 id")
                return
        if rel is None:
            self._err(400, "path 参数非法")
            return
        try:
            content_a = self._read_diff_source(ctl, a, rel)
            content_b = self._read_diff_source(ctl, b, rel)
        except _DiffSourceNotFound as e:
            self._err(404, str(e))
            return
        diff = difflib.unified_diff(
            content_a.split("\n"), content_b.split("\n"),
            fromfile=f"{a}:{rel}", tofile=f"{b}:{rel}", lineterm="",
        )
        self._ok({"diff": "\n".join(diff)})

    def _read_diff_source(self, ctl: NginxController, sid: str, rel: str) -> str:
        """读取 diff 数据源：current = 当前配置文件，否则备份目录内的同名文件。"""
        if sid == "current":
            abs_path = self._conf_abs(rel)
            if not os.path.isfile(abs_path):
                raise _DiffSourceNotFound(f"当前配置中不存在文件: {rel}")
        else:
            abs_path = os.path.join(self.data_dirs["backups"], sid, rel.replace("/", os.sep))
            if not os.path.isfile(abs_path):
                raise _DiffSourceNotFound(f"备份 {sid} 中不存在文件: {rel}")
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _api_logs_error(self, qs: dict) -> None:
        if self.controller is None:
            # 预览模式：无 nginx，无错误日志可读
            self._ok({"logPath": None, "content": "（预览模式：未配置 nginx，暂无错误日志）",
                      "offset": 0, "size": 0, "reset": True, "hasMore": False})
            return
        ctl = self._require_controller()
        if ctl is None:
            return
        lines = _int_arg(qs, "lines", 200) or 200
        log_path, content, offset, size, reset, has_more = ctl.read_error_log_since(
            _offset_arg(qs), lines)
        self._ok({"logPath": log_path, "content": content, "offset": offset,
                  "size": size, "reset": reset, "hasMore": has_more})

    def _api_logs_access(self, qs: dict) -> None:
        if self.controller is None:
            self._ok({"logPath": None, "paths": [],
                      "content": "（预览模式：未配置 nginx，暂无访问日志）",
                      "offset": 0, "size": 0, "reset": True, "hasMore": False})
            return
        ctl = self._require_controller()
        if ctl is None:
            return
        lines = _int_arg(qs, "lines", 500) or 500
        paths = ctl.find_access_log_paths()
        sel = (qs.get("path") or [""])[0].strip()
        if sel:
            abs_sel = os.path.abspath(sel)
            prefix = os.path.abspath(ctl.prefix)
            conf_root = os.path.abspath(ctl.conf_dir)
            # 除 prefix/confDir 之外，还允许「用户自己 nginx.conf 里声明的候选路径」：
            # 候选是服务端从配置解析出来的，缺省分支本来就会读它；若显式传回同一路径反而被拒，
            # 就会出现「下拉里选中同一个文件 → 403」的自相矛盾（发行版把日志写到 /var/log 这类
            # prefix 之外的位置时必现）。仍拒绝其他任意路径。
            declared = {os.path.abspath(p) for p in paths}
            if not (abs_sel.startswith(prefix + os.sep) or abs_sel.startswith(conf_root + os.sep)
                    or abs_sel in (prefix, conf_root) or abs_sel in declared):
                self._err(403, "日志路径越出 nginx 目录范围")
                return
            log_path = abs_sel
        else:
            log_path = next((p for p in paths if os.path.isfile(p)), paths[0] if paths else None)
        since = _offset_arg(qs)
        content, offset, size, reset, has_more = (
            ctl.read_log_since(log_path, since, lines) if log_path
            else ("", 0, 0, since is not None, False))
        self._ok({"logPath": log_path, "paths": paths, "content": content,
                  "offset": offset, "size": size, "reset": reset, "hasMore": has_more})

    def _api_metrics_get(self) -> None:
        if self.controller is None:
            self._ok({"available": False, "preview": True})
            return
        ctl = self._require_controller()
        if ctl is None:
            return
        stub = ctl.find_stub_status()
        # 端口必须与 stub_status 所在 server 配对：配置里第一个 listen 未必是它那台
        # （旧实现分开取，多 server 配置下会去错误的端口抓指标，永远拿不到数据）
        port = (stub or {}).get("port") or ctl.detect_listen_port() or 80
        if not stub:
            self._ok({"available": False, "reason": "not_configured", "port": port})
            return
        metrics, reason = ctl.fetch_stub_status(port, stub["path"])
        if metrics is None:
            self._ok({"available": False, "reason": reason, "port": port, "stubPath": stub["path"]})
            return
        self._ok({"available": True, "port": port, "stubPath": stub["path"], "metrics": metrics})

    def _api_metrics_enable(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        stub = ctl.find_stub_status()
        if stub:
            self._ok({"ok": True, "already": True, "stubPath": stub["path"]})
            return
        body = self._read_json_body()
        path = str(body.get("path") or "/nginx_status")
        pm = self._proxy_manager()
        if pm is None:
            return

        def mutate(p):
            return p.enable_stub_status(path)
        if self._proxy_apply(pm, mutate, extra={"stubPath": path}):
            return

    def _api_settings_get(self) -> None:
        # 路径以「当前生效」为准（控制器优先，settings 兜底）：用 --nginx-path/--conf-dir
        # 启动时 controller 已有值而 settings 可能是空的，只回 settings 会让界面以为尚未
        # 配置、把用户丢回首次向导——而这两个参数在 README 里的作用正是「跳过首次选择」。
        ctl = self.controller
        self._ok({
            "nginxPath": (ctl.nginx_path if ctl else None) or self.settings.get("nginxPath"),
            "confDir": (ctl.conf_dir if ctl else None) or self.settings.get("confDir"),
            "port": int(self.settings.get("port", 0) or 0) or DEFAULT_PORT,
            "backupRetention": self.settings.get("backupRetention", BACKUP_RETENTION),
            "configured": ctl is not None,
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
        elif path == "/api/upstreams":
            self._api_upstreams_save(create=True)
        elif path == "/api/metrics/enable":
            self._api_metrics_enable()
        elif path == "/api/pick-path":
            self._api_pick_path()
        elif path == "/api/restart":
            self._api_restart()
        else:
            self._err(404, "接口不存在")

    @staticmethod
    def _is_state_conflict(msg: str) -> bool:
        """运行状态类失败（未在运行 / 已在运行 / 有长连接未退出）按契约回 409，其余按 500。

        nginxctl 只返回 (ok, message)，这里按语义归类，避免把「状态不对」报成「服务器错误」——
        前端据状态码区分「可重试的状态问题」与「真故障」（API.md 已按此约定）。
        """
        return any(k in msg for k in ("未在运行", "已在运行", "仍在运行", "没有退出"))

    def _api_nginx_start(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.start()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(409 if self._is_state_conflict(msg) else 500, msg)

    def _api_nginx_stop(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.stop()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(409 if self._is_state_conflict(msg) else 500, msg)

    def _api_nginx_reload(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.reload()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(409 if self._is_state_conflict(msg) else 500, msg)

    def _api_nginx_restart(self) -> None:
        ctl = self._require_controller()
        if ctl is None:
            return
        ok, msg = ctl.restart()
        if ok:
            _invalidate_status_cache()
            self._ok({"ok": True, "message": msg})
        else:
            self._err(409 if self._is_state_conflict(msg) else 500, msg)

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
            atomic_write_text(abs_path, files_map[rel])
        ctl.invalidate_config_cache()

        ok, result = ctl.test_config()
        if not ok:
            # 校验失败：恢复原状
            for rel, content in current_map.items():
                abs_path = self._conf_abs(rel)
                atomic_write_text(abs_path, content)
            ctl.invalidate_config_cache()
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
        elif path == "/api/upstreams":
            self._api_upstreams_save(create=False)
        else:
            self._err(404, "接口不存在")

    # ---- API: DELETE ----

    def _route_api_delete(self, path: str) -> None:
        if path == "/api/proxies":
            self._api_proxies_remove()
        elif path == "/api/proxy-pool":
            self._api_proxy_pool_remove()
        elif path == "/api/upstreams":
            self._api_upstreams_remove()
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
            # 原子写 + 沿用文件既有行尾：非原子写在中途失败（磁盘满/进程被杀）
            # 会留下半截 nginx.conf；固定写 LF 会把 Windows 用户整份配置改成 LF。
            atomic_write_text(abs_path, content)
        except OSError as e:
            self._err(500, f"写入文件失败: {e}")
            return
        ctl.invalidate_config_cache()  # 配置已改：日志路径/stub_status 等派生结果必须重算

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
        """统一执行配置写变更（代理/地址池/upstream/stub_status）：mutate(pm) 返回 {ok, ...}。

        - mutate 失败：按错误语义发 4xx 响应，返回 True（调用方直接 return）。
        - 内容无变化（pm.content 未改变）：跳过备份与 nginx -t，直接发成功响应
          （backupId 为 None），避免无意义的冗余备份。
        - 内容有变化：先备份 → commit → nginx -t 校验；校验失败回滚原文并回 409；
          成功发成功响应。
        - mutate 返回值中除 ok/error 外的键合并进成功响应（如池操作的 targets）。
        - path 存在时（代理操作）响应附带变更后该代理的 ProxyInfo。
        返回 True 表示响应已发送，调用方应 return。"""
        original = pm.content
        res = mutate(pm)
        if not res.get("ok"):
            err = res.get("error", "操作失败")
            status = 400
            if "不存在" in err or "不在池中" in err:
                status = 404
            elif ("备选" in err or "校验" in err or "激活" in err or "已在池中" in err
                  or "没有代理" in err or "已存在" in err or "引用" in err
                  or "http 块" in err or "server 块" in err
                  or "未建模" in err or "单行写法" in err):
                status = 409
            self._err(status, err)
            return True
        res_extra = {k: v for k, v in res.items() if k not in ("ok", "error")}
        if pm.content == original:
            # 无实际变化（如切换到已激活目标、备选列表与当前一致），不备份不校验
            proxy = next((p for p in pm.list_proxies() if p["path"] == path), None) if path else None
            payload = {"ok": True, "backupId": None,
                       "test": {"ok": True, "output": "配置无变化，未做改动"}}
            if path:
                payload["proxy"] = proxy
            payload.update(res_extra)
            payload.update(extra or {})
            self._ok(payload)
            return True
        backup_id = make_backup(self.data_dirs["backups"], self.controller.conf_dir, "nginx.conf")
        pm.commit()
        self.controller.invalidate_config_cache()  # 配置已改：派生结果必须重算
        _code, result = self.controller.test_config()
        if not result.get("ok"):
            pm.restore(original)
            self.controller.invalidate_config_cache()  # 回滚同样改了文件
            self._send_json(409, {
                "error": "修改后 nginx -t 校验失败，已回滚（配置未改动）",
                "detail": result.get("output", ""),
                "test": result,
            })
            return True
        payload = {"ok": True, "backupId": backup_id, "test": result}
        if path:
            payload["proxy"] = next((p for p in pm.list_proxies() if p["path"] == path), None)
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
        template = str(body.get("template") or "standard")

        def mutate(pm):
            return pm.add(path, target, template)
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
        # 规范化口径只算一次：原实现把 _pool_key(target) 写在生成器里，每比较一个元素
        # 都会重跑一次正则
        want_key = _pool_key(target)
        hit = next((t for t in targets if _pool_key(t) == want_key), None)
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
        active = str(body.get("active") or "")
        if not isinstance(targets, list):
            self._err(400, "targets 必须为数组")
            return

        def mutate(pm):
            return pm.update_targets(path, targets, active)
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

    # ---- 负载均衡 upstream ----

    def _api_upstreams_get(self) -> None:
        if self.controller is None:
            self._ok({"upstreams": [], "preview": True})
            return
        pm = self._proxy_manager()
        if pm is None:
            return
        self._ok({"upstreams": pm.upstream_list()})

    def _api_upstreams_save(self, create: bool = True) -> None:
        pm = self._proxy_manager()
        if pm is None:
            return
        body = self._read_json_body()
        name = str(body.get("name", "")).strip()
        method = str(body.get("method") or "round_robin")
        servers = body.get("servers")

        def mutate(p):
            exists = any(u["name"] == name for u in p.upstream_list())
            if create and exists:
                return {"ok": False, "error": f"upstream 已存在: {name}"}
            if not create and not exists:
                return {"ok": False, "error": f"upstream 不存在: {name}"}
            res = p.upstream_save(name, method, servers)
            if res.get("ok"):
                res["upstreams"] = p.upstream_list()
            return res
        if self._proxy_apply(pm, mutate, extra={"name": name}):
            return

    def _api_upstreams_remove(self) -> None:
        pm = self._proxy_manager()
        if pm is None:
            return
        body = self._read_json_body()
        name = str(body.get("name", "")).strip()
        if not name:
            self._err(400, "name 必填")
            return

        def mutate(p):
            res = p.upstream_remove(name)
            if res.get("ok"):
                res["upstreams"] = p.upstream_list()
            return res
        if self._proxy_apply(pm, mutate, extra={"deleted": name}):
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


def _port_arg(value: str) -> int:
    """--port 参数校验：越界端口在 bind 阶段才会抛 OverflowError（且 <1024 需管理员权限），
    故在入口就拒绝，避免启动到一半才报栈。"""
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("端口必须是整数")
    if not (1 <= port <= 65535):
        raise argparse.ArgumentTypeError("端口必须在 1~65535 之间")
    return port


def _int_arg(qs: dict, name: str, default: int) -> int:
    """查询参数取整数，非法时回落到默认值（GET 参数手抖不该 500）。"""
    try:
        return int((qs.get(name) or [""])[0])
    except (TypeError, ValueError):
        return default


def _offset_arg(qs: dict) -> Optional[int]:
    """日志增量读取的起始字节偏移 `since`：非整数或负数一律视为缺省（按尾部读取）。"""
    try:
        value = int((qs.get("since") or [""])[0])
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


# ---------- 入口 ----------

def find_workspace_nginx() -> dict:
    """开发默认：若工作区根目录存在 nginx-1.30.4/（Windows 官方版布局 nginx.exe，
    或 Unix 源码构建布局 sbin/nginx），直接作为管理对象，跳过首次对话框。
    返回 {nginxPath, confDir} 或空 dict。"""
    base = os.path.join(PROJECT_ROOT, "nginx-1.30.4")
    conf_dir = os.path.join(base, "conf")
    for rel in ("nginx.exe", os.path.join("sbin", "nginx")):
        exe = os.path.join(base, rel)
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
    parser.add_argument("--port", type=_port_arg, default=None, help=f"监听端口（缺省 {DEFAULT_PORT}）")
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

    # exe 运行时把前端资源持久化到数据目录，避免 %TEMP% 解压目录被清理后页面 404
    global FRONTEND_DIR, NOTES_DIR
    FRONTEND_DIR = stage_frontend(data_dirs["root"])
    if os.path.isfile(os.path.join(FRONTEND_DIR, "index.html")):
        print(f"[前端] 资源目录: {FRONTEND_DIR}")
    else:
        print(f"[警告] 前端资源缺失（{FRONTEND_DIR} 下无 index.html），页面将无法打开")

    # 发布说明（界面「更新历史」）同样持久化：解压目录被清理时只剩历史为空，其余功能不受影响
    NOTES_DIR = stage_release_notes(data_dirs["root"])
    _note_count = len(load_releases(NOTES_DIR))
    if _note_count:
        print(f"[更新历史] 发布说明目录: {NOTES_DIR}（{_note_count} 个版本）")
    else:
        print(f"[警告] 未找到发布说明（{NOTES_DIR} 下无 v*.md），界面更新历史将为空")

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

# -*- coding: utf-8 -*-
"""nginxctl.py — nginx 跨平台控制模块（Windows / macOS / Linux）

职责：
  - nginx -t 语法校验
  - start / stop / reload / restart 进程控制
  - 运行状态检测（进程 + 版本 + pid）
  - 配置文件 include 解析（生成配置树）
  - 错误日志定位与读取

设计约定：
  - 所有命令通过 subprocess 执行，参数一律用列表传递，不经过 shell（防注入）。
  - prefix 推断：取配置目录（confDir，含 nginx.conf 的目录）的父目录。
    对标准布局成立：Windows 官方包 C:/nginx/conf、apt/brew 的 /etc/nginx 或 /usr/local/etc/nginx。
  - 本模块不抛业务异常，方法返回 (ok: bool, data: dict) 或 (ok, message)，
    由 server.py 统一转 HTTP 响应。
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import time
from typing import List, Optional, Tuple

WIN = sys.platform.startswith("win")
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

CONF_MAIN = "nginx.conf"


def _run(cmd: List[str], timeout: int = 15, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """执行命令，返回 (returncode, stdout, stderr)。编码按平台处理。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"无法执行命令: {' '.join(cmd)}（文件不存在）"
    except subprocess.TimeoutExpired:
        return 124, "", "命令执行超时"


class NginxController:
    """nginx 控制核心。构造后所有方法自动适配当前平台。"""

    def __init__(self, nginx_path: str, conf_dir: str):
        self.nginx_path = os.path.abspath(nginx_path)
        self.conf_dir = os.path.abspath(conf_dir)
        # prefix = confDir 的父目录；confDir 本身也可能是 prefix（如 /etc/nginx 下无子目录）
        parent = os.path.dirname(self.conf_dir)
        self.prefix = parent if parent and parent != self.conf_dir else self.conf_dir

    # ---------- 基础命令构造 ----------

    def _base_cmd(self) -> List[str]:
        return [self.nginx_path, "-p", self.prefix]

    def _test_cmd(self) -> List[str]:
        return self._base_cmd() + ["-t", "-c", self.main_conf_path()]

    def main_conf_path(self) -> str:
        return os.path.join(self.conf_dir, CONF_MAIN)

    # ---------- 配置校验 ----------

    def test_config(self) -> Tuple[bool, dict]:
        """nginx -t 校验。返回 (ok, {ok, output})。"""
        if not os.path.isfile(self.nginx_path):
            return False, {"ok": False, "output": f"nginx 可执行文件不存在: {self.nginx_path}"}
        code, _out, err = _run(self._test_cmd())
        output = (err or _out).strip()
        ok = code == 0 and "successful" in output
        return code == 0, {"ok": ok, "output": output}

    # ---------- 版本 / 进程 ----------

    def get_version(self) -> Optional[str]:
        """nginx -v 结果缓存：版本随 exe 固定，无需每次起子进程；exe 被替换（mtime 变化）时自动失效。"""
        try:
            mtime = os.path.getmtime(self.nginx_path)
        except OSError:
            mtime = None
        cached = getattr(self, "_version_cache", None)
        if cached and cached[0] == self.nginx_path and cached[1] == mtime:
            return cached[2]
        code, _out, err = _run(self._base_cmd() + ["-v"], timeout=10)
        output = (err or _out).strip()
        m = re.search(r"nginx/([\d.]+)", output)
        version = m.group(1) if m else None
        self._version_cache = (self.nginx_path, mtime, version)
        return version

    def _windows_nginx_procs(self) -> List[dict]:
        """Windows：通过 CIM 获取 nginx.exe 进程（含可执行路径，用于匹配多版本场景）。

        PowerShell 在部分环境（杀毒扫描/策略限制）下会卡到数十秒，两道防线：
        1. 超时收紧到 8s，快速降级 tasklist；
        2. 连续失败 2 次后熔断——本次运行内不再尝试 PowerShell，直接走 tasklist。
        """
        # 熔断命中：直接走降级路径
        if getattr(self, "_ps_broken", False):
            return self._tasklist_nginx_procs()
        ps_script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Get-CimInstance Win32_Process -Filter \"Name='nginx.exe'\" "
            "| Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
        )
        try:
            code, out, err = _run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                timeout=8,
            )
        except Exception:
            code, out, err = 1, "", ""
        if code != 0 or not out.strip():
            # 连续失败计数，达到 2 次熔断
            fails = getattr(self, "_ps_fail_count", 0) + 1
            self._ps_fail_count = fails
            if fails >= 2:
                self._ps_broken = True
            return self._tasklist_nginx_procs()
        self._ps_fail_count = 0
        try:
            import json

            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            procs = []
            for p in data:
                pid = p.get("ProcessId")
                exe = p.get("ExecutablePath")
                procs.append({"pid": int(pid) if str(pid).isdigit() else None, "exePath": exe})
            return procs
        except Exception:
            return []

    @staticmethod
    def _tasklist_nginx_procs() -> List[dict]:
        """降级：tasklist 仅按文件名，拿不到 exePath（多版本精确匹配失效，退化为单实例判定）。"""
        code, out, _ = _run(["tasklist", "/FI", "IMAGENAME eq nginx.exe", "/FO", "CSV", "/NH"], timeout=15)
        procs = []
        for line in out.splitlines():
            parts = [p.strip().strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == "nginx.exe":
                pid = parts[1] if parts[1].isdigit() else None
                procs.append({"pid": int(pid) if pid else None, "exePath": None})
        return procs

    def _posix_nginx_pid(self) -> Optional[int]:
        """类 Unix：优先读 pid 文件，其次 pgrep。"""
        pid_file = os.path.join(self.prefix, "logs", "nginx.pid")
        if os.path.isfile(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip())
                if pid > 0 and self._pid_alive(pid):
                    return pid
            except (ValueError, OSError):
                pass
        code, out, _ = _run(["pgrep", "-f", "nginx: master process"], timeout=10)
        if code == 0 and out.strip():
            pids = [int(x) for x in out.split() if x.strip().isdigit()]
            if pids:
                return pids[0]
        return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if WIN:
            code, out, _ = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
            return code == 0 and f'"{pid}"' in out
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def detect_process(self) -> Optional[dict]:
        """检测 nginx 是否在运行。返回 {running, pid, version, matched} 或 None（未配置时）。"""
        if not os.path.isfile(self.nginx_path):
            return {"running": False, "pid": None, "version": None, "matched": True}
        version = self.get_version()
        if WIN:
            procs = self._windows_nginx_procs()
            running_proc = None
            for p in procs:
                # 匹配可执行路径；多版本共存时只认配置指向的那个
                if p.get("exePath"):
                    if os.path.normcase(os.path.abspath(p["exePath"])) == os.path.normcase(self.nginx_path):
                        running_proc = p
                        break
            if running_proc is None and procs:
                # 找不到精确路径匹配时，若只存在一份 nginx.exe 则视为正在运行
                if len(procs) == 1:
                    running_proc = procs[0]
            return {
                "running": running_proc is not None,
                "pid": running_proc["pid"] if running_proc else None,
                "version": version,
                "matched": True,
            }
        else:
            pid = self._posix_nginx_pid()
            return {"running": pid is not None, "pid": pid, "version": version, "matched": True}

    # ---------- 进程控制 ----------

    @staticmethod
    def _clean_stale_pid_file(prefix: str) -> None:
        """清理空或损坏的 nginx.pid 文件。

        nginx 异常退出（崩溃/被强杀/断电）时 pid 文件可能残留为空或含非数字内容，
        导致下次 nginx 启动/重载时报「invalid PID number ""」。
        此方法在 start/reload/restart 前调用，安全删除无效文件（nginx 启动时会自动重建）。
        """
        pid_file = os.path.join(prefix, "logs", "nginx.pid")
        if not os.path.isfile(pid_file):
            return
        try:
            raw = open(pid_file, "r", encoding="utf-8").read().strip()
            if not raw:
                os.remove(pid_file)
                return
            int(raw)  # 验证是否为有效整数
        except (ValueError, OSError):
            try:
                os.remove(pid_file)
            except OSError:
                pass

    def start(self) -> Tuple[bool, str]:
        """启动 nginx。关键：master 常驻不退出，必须用 Popen 分离启动，
        不能用 subprocess.run（会因不退出而超时，超时后连带杀掉 master 进程树）。"""
        if not os.path.isfile(self.nginx_path):
            return False, f"nginx 可执行文件不存在: {self.nginx_path}"
        self._clean_stale_pid_file(self.prefix)  # 清理空/损坏 pid 文件
        # 启动前先校验配置，拿错误信息（比启动失败后再猜原因直观）
        code, result = self.test_config()
        if not result.get("ok"):
            return False, f"nginx -t 校验失败，未启动: {result.get('output', '')}"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0
        try:
            subprocess.Popen(
                self._base_cmd() + ["-c", self.main_conf_path()],
                cwd=self.prefix,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except OSError as e:
            return False, f"nginx 启动失败: {e}"
        time.sleep(1.2)  # 给 master 一点启动时间
        info = self.detect_process()
        if info and info["running"]:
            return True, "nginx 已启动"
        return False, "nginx 启动失败：请检查端口占用或错误日志"

    def stop(self) -> Tuple[bool, str]:
        info = self.detect_process()
        if not (info and info["running"]):
            return False, "nginx 未在运行"
        code, _out, err = _run(self._base_cmd() + ["-s", "quit"], timeout=15)
        if code != 0:
            # 优雅退出失败，强制 stop
            _run(self._base_cmd() + ["-s", "stop"], timeout=15)
        time.sleep(1.0)
        return True, "nginx 已停止"

    def reload(self) -> Tuple[bool, str]:
        info = self.detect_process()
        if not (info and info["running"]):
            return False, "nginx 未在运行"
        self._clean_stale_pid_file(self.prefix)  # 重载前清理，避免 nginx 读空 pid 报错
        code, _out, err = _run(self._base_cmd() + ["-s", "reload"], timeout=15)
        if code != 0:
            detail = (err or _out).strip()
            return False, f"nginx 重载失败" + (f": {detail}" if detail else "")
        return True, "nginx 配置已重载"

    def restart(self) -> Tuple[bool, str]:
        info = self.detect_process()
        if info and info["running"]:
            self.stop()
            time.sleep(1.0)
        self._clean_stale_pid_file(self.prefix)  # 重启前清理
        return self.start()

    # ---------- 配置树 / include 解析 ----------

    def _resolve_include_pattern(self, pattern: str) -> List[str]:
        """把 include 指令的 pattern 展开为实际文件列表（支持绝对/相对路径与通配符）。"""
        pattern = pattern.strip().strip("'\"")
        if not pattern:
            return []
        candidates = []
        if os.path.isabs(pattern):
            candidates.append(pattern)
        else:
            # nginx 的 include 相对 prefix 解析；这里兼容 prefix 与 confDir 两种基准
            candidates.append(os.path.join(self.prefix, pattern))
            if self.conf_dir != self.prefix:
                candidates.append(os.path.join(self.conf_dir, pattern))
        files: List[str] = []
        seen: set = set()
        for base in candidates:
            if any(ch in pattern for ch in "*?["):
                for hit in glob.glob(base):
                    fp = os.path.abspath(hit)
                    if os.path.isfile(fp) and fp not in seen:
                        seen.add(fp)
                        files.append(fp)
            else:
                fp = os.path.abspath(base)
                if os.path.isfile(fp) and fp not in seen:
                    seen.add(fp)
                    files.append(fp)
        return files

    _INCLUDE_RE = re.compile(r"include\s+([^;]+);")

    def collect_included_files(self) -> List[str]:
        """递归解析 nginx.conf 的 include，返回所有被引用的配置文件绝对路径（含主配置）。"""
        result: List[str] = []
        visited: set = set()
        queue: List[str] = [self.main_conf_path()]

        def rel_ok(fp: str) -> bool:
            return fp.startswith(self.conf_dir) or fp.startswith(self.prefix)

        while queue:
            cur = queue.pop(0)
            cur = os.path.abspath(cur)
            if cur in visited or not os.path.isfile(cur):
                continue
            visited.add(cur)
            if rel_ok(cur) and cur not in result:
                result.append(cur)
            try:
                with open(cur, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            for m in self._INCLUDE_RE.finditer(content):
                pattern = m.group(1)
                for hit in self._resolve_include_pattern(pattern):
                    hit = os.path.abspath(hit)
                    if rel_ok(hit) and hit not in visited:
                        queue.append(hit)
        result.sort(key=lambda p: (p.count(os.sep), p.lower()))
        return result

    def build_config_tree(self) -> Tuple[List[dict], List[str]]:
        """返回 (tree, included)。tree 为配置目录下按目录分组的文件树。"""
        included_files = self.collect_included_files()
        if not included_files:
            return [], []

        # 相对路径集合
        rels = []
        for fp in included_files:
            if fp.startswith(self.conf_dir):
                rels.append(os.path.relpath(fp, self.conf_dir))
            else:
                # 在 confDir 之外（例如 prefix/conf.d），用相对 prefix 路径展示
                rels.append(os.path.relpath(fp, self.prefix))

        def make_node(rel: str, is_dir: bool) -> dict:
            return {"path": rel.replace("\\", "/"), "name": os.path.basename(rel) or rel, "isDir": is_dir, "children": []}

        # 构建目录树
        root: List[dict] = []

        def find_child(nodes: List[dict], name: str) -> Optional[dict]:
            for n in nodes:
                if n["name"] == name and n["isDir"]:
                    return n
            return None

        for rel in rels:
            parts = rel.split(os.sep)
            cur = root
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                if is_last:
                    node = make_node("/".join(parts[: i + 1]), False)
                    node.pop("children")
                    cur.append(node)
                else:
                    child = find_child(cur, part)
                    if child is None:
                        child = make_node("/".join(parts[: i + 1]), True)
                        cur.append(child)
                    cur = child["children"]
        return root, [r.replace("\\", "/") for r in rels]

    # ---------- 错误日志 ----------

    def locate_error_log(self) -> Optional[str]:
        """定位 error.log：优先 prefix/logs/error.log，其次 confDir/logs。"""
        candidates = [
            os.path.join(self.prefix, "logs", "error.log"),
            os.path.join(self.conf_dir, "logs", "error.log"),
            os.path.join(self.conf_dir, "error.log"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    def read_error_log(self, lines: int = 200) -> Tuple[Optional[str], str]:
        log_path = self.locate_error_log()
        if not log_path:
            return None, ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return log_path, "".join(all_lines[-max(1, min(lines, 5000)):])
        except OSError:
            return log_path, ""


# ---------- 便捷工厂 ----------

def create_controller(nginx_path: Optional[str], conf_dir: Optional[str]) -> Optional[NginxController]:
    """settings 齐备时创建控制器；缺配置返回 None（由 server 层引导配置）。"""
    if not nginx_path or not conf_dir:
        return None
    return NginxController(nginx_path, conf_dir)

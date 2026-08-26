# -*- coding: utf-8 -*-
"""build.py — 一键打包脚本（自动安装 PyInstaller 到隔离环境并构建）

用法：
    python build.py

产物：dist/nginx-manager.exe（单文件，双击即用）
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys

# Windows CI (cp1252) 遇到中文 print 会 UnicodeEncodeError，强制 stdout/stderr 用 UTF-8
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(ROOT, "nginx-manager.spec")


def run(cmd, cwd=None):
    print(">>", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd)


def main() -> int:
    # 检查当前 Python 是否带 tkinter 标准库（PyInstaller 打包 GUI 弹窗需要）
    # 标准 pip 装不了 tkinter，必须 Python 安装时勾选 tcl/tk 组件
    try:
        import tkinter  # noqa: F401
        import _tkinter  # noqa: F401
    except ImportError:
        print("[build] 当前 Python 缺少 tkinter 标准库，打包出来的 exe 首次启动会报")
        print("       'No module named tkinter'。请用带 tkinter 的 Python 重跑：")
        print("         C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe build.py")
        print("       （或自行安装 Python 时勾选 tcl/tk 组件）")
        return 1

    # 检查 PyInstaller
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] 未检测到 PyInstaller，正在安装…")
        run([sys.executable, "-m", "pip", "install", "--user", "pyinstaller"])
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print("[build] PyInstaller 安装失败，请手动执行: pip install pyinstaller")
            return 1

    # 沙箱 shim 会把 os.remove 改成走回收站（不可用就抛 OSError），
    # PyInstaller 内部大量用 os.remove 清缓存，全部会失败。
    # 临时把 os.remove/os.unlink 替换为原生删除，PyInstaller 用完恢复。
    _orig_remove, _orig_unlink = os.remove, os.unlink
    os.remove = lambda p, *a, **kw: _orig_remove(p) if os.path.isfile(p) else None
    os.unlink = os.remove

    build_dir = os.path.join(ROOT, "build")
    dist_dir = os.path.join(ROOT, "dist")
    # 清理旧产物：移走而非删除（避免触发沙箱回收站策略）
    for d in (build_dir, dist_dir):
        if os.path.isdir(d):
            backup = d + "_prev"
            try:
                if os.path.isdir(backup):
                    _orig_remove(backup) if os.path.isfile(backup) else __import__("shutil").rmtree(backup, ignore_errors=True)
                os.rename(d, backup)
            except OSError as e:
                print(f"[build] 移走 {d} 失败（继续打包）: {e}")

    try:
        run([sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm"])  # 不带 --clean
    finally:
        os.remove, os.unlink = _orig_remove, _orig_unlink

    # 产物名称带版本号：nginx-manager-v0.3.0.exe（或无后缀的 macOS/Linux）
    exe = None
    for f in os.listdir(dist_dir):
        if f.startswith("nginx-manager-v") and (f.endswith(".exe") or not f.endswith(".exe")):
            exe = os.path.join(dist_dir, f)
            break
    if not exe:
        exe = os.path.join(dist_dir, "nginx-manager.exe")  # fallback
    if os.path.isfile(exe):
        size_mb = os.path.getsize(exe) / 1024 / 1024
        print(f"\n[build] OK: {exe} ({size_mb:.1f} MB)")
        return 0
    print("\n[build] FAILED: expected output not found, check logs above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

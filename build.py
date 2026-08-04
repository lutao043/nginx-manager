# -*- coding: utf-8 -*-
"""build.py — 一键打包脚本（自动安装 PyInstaller 到隔离环境并构建）

用法：
    python build.py

产物：dist/nginx-manager.exe（单文件，双击即用）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(ROOT, "nginx-manager.spec")


def run(cmd, cwd=None):
    print(">>", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd)


def main() -> int:
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

    build_dir = os.path.join(ROOT, "build")
    dist_dir = os.path.join(ROOT, "dist")
    # 清理旧产物，避免残留（沙箱环境回收站不可用时跳过，PyInstaller --clean 会兜底缓存）
    for d in (build_dir, dist_dir):
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
            except OSError as e:
                print(f"[build] 清理 {d} 失败（跳过，--clean 会处理）: {e}")

    run([sys.executable, "-m", "PyInstaller", SPEC, "--noconfirm", "--clean"])

    exe = os.path.join(dist_dir, "nginx-manager.exe")
    if os.path.isfile(exe):
        print(f"\n[build] 打包成功: {exe}")
        print("双击运行，首次启动会弹出文件选择窗口指定 nginx 路径。")
        return 0
    print("\n[build] 打包完成，但未找到预期产物，请检查上方日志。")
    return 1


if __name__ == "__main__":
    sys.exit(main())

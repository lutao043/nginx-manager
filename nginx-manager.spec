# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 exe（onefile）

用法：
    pyinstaller nginx-manager.spec --noconfirm

产物：dist/nginx-manager.exe
运行时数据（settings/backups）写入用户数据目录（见 backend/server.py），
不依赖程序所在目录，保证 onefile 解压临时目录可写性问题不出现。
"""
import os
import re as _re
import sys as _sys

APP_NAME = "nginx-manager"
# PyInstaller 执行 spec 时提供 SPECPATH（脚本所在目录），__file__ 不可用
ROOT = os.path.abspath(SPECPATH)

# 版本号唯一来源：backend/server.py 的 server_version
_server_py = os.path.join(ROOT, "backend", "server.py")
APP_VERSION = "0.0.0"  # fallback，正常不会用到
with open(_server_py, encoding="utf-8") as _f:
    _m = _re.search(r'server_version\s*=\s*["\']nginx-manager/([\d.]+)["\']', _f.read())
    if _m:
        APP_VERSION = _m.group(1)

a = Analysis(
    [os.path.join(ROOT, "backend", "server.py")],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[],
    datas=[(os.path.join(ROOT, "frontend"), "frontend")],
    # tkinter 是标准库，但 PyInstaller 静态分析不会自动收齐 _tkinter.pyd
    # 与 tcl/tk DLL；用 collect_submodules 显式抓全所有子模块
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "_tkinter", "tcl", "tk"],
    noarchive=False,
)
pyz = PYZ(a.pure)

import sys as _sys
_ext = ".exe" if _sys.platform == "win32" else ""

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"{APP_NAME}-v{APP_VERSION}" + _ext,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # 服务器程序，保留控制台输出
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=os.path.join(ROOT, "version_info.py") if _sys.platform == "win32" else None,
)

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 exe（onefile）

用法：
    pyinstaller nginx-manager.spec --noconfirm

产物：dist/nginx-manager.exe
运行时数据（settings/backups）写入用户数据目录（见 backend/server.py），
不依赖程序所在目录，保证 onefile 解压临时目录可写性问题不出现。
"""
import os

APP_NAME = "nginx-manager"
# PyInstaller 执行 spec 时提供 SPECPATH（脚本所在目录），__file__ 不可用
ROOT = os.path.abspath(SPECPATH)

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    [os.path.join(ROOT, "backend", "server.py")],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[],
    datas=[(os.path.join(ROOT, "frontend"), "frontend")],
    # tkinter 是标准库，但 PyInstaller 静态分析不会自动收齐 _tkinter.pyd
    # 与 tcl/tk DLL；用 collect_submodules 显式抓全所有子模块
    hiddenimports=collect_submodules("tkinter"),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 程序，不弹出黑窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

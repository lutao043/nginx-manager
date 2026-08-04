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
ROOT = os.path.abspath(os.path.dirname(__file__))

a = Analysis(
    [os.path.join(ROOT, "backend", "server.py")],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[],
    datas=[(os.path.join(ROOT, "frontend"), "frontend")],
    hiddenimports=["tkinter"],
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

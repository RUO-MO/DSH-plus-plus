# -*- mode: python ; coding: utf-8 -*-
"""DSH++ 桌面应用构建配置（PyInstaller onefile）

构建：python -m PyInstaller --noconfirm --clean build_desktop.spec
产物：dist/DSH++.exe（双击即用：独立窗口 + 托盘 + 单实例，后端数据仍在 ~/.dsh-skins）
"""
import os

HERE = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['desktop_app.py'],
    pathex=[HERE],
    binaries=[],
    datas=[
        ('panel.html', '.'),
        ('dsh-skin.py', '.'),
        ('market', 'market'),
        ('assets', 'assets'),
        ('enhance-modules', 'enhance-modules'),
        ('tools', 'tools'),
    ],
    hiddenimports=[
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
        'tkinter',
        'tkinter.filedialog',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DSH++',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(HERE, 'assets', 'DSH++.ico'),
)

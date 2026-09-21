# -*- mode: python ; coding: utf-8 -*-
"""DSH++ 桌面应用 onedir 构建（在受限环境下比 onefile 更稳：无需 %TEMP% 自解压）
产物：dist/DSH++/DSH++.exe（+ 同目录依赖）
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
        # 函数体内 import 的模块，显式声明避免静态分析漏收
        'we_scanner',
        'wallpaper_engine',
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DSH++',
)
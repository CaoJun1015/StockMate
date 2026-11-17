
# -*- mode: python ; coding: utf-8 -*-
"""
调货助手 PyInstaller 打包配置
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.version import APP_VERSION

# Keep unrelated tools (Poppler, libheif, etc.) out of DLL resolution.
# Qt uses Windows ICU exports; Poppler's same-named icuuc.dll has a
# different ABI and causes QtWidgets import to fail before app startup.
from PyQt6.QtCore import QLibraryInfo

windows_root = Path(os.environ.get('SystemRoot', r'C:\Windows')).resolve()
os.environ['PATH'] = os.pathsep.join([
    QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath),
    str(Path(sys.base_prefix)),
    str(windows_root / 'System32'),
    str(windows_root),
])

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'openpyxl',
        'docx',
        'PIL',
        'PIL._tkinter_finder',
        'src.ui',
        'src.ui.style',
        'src.ui.dialogs',
        'src.ui.quote_panel',
        'src.ui.product_tab',
        'src.ui.record_tab',
        'src.ui.customer_tab',
        'src.ui.supplier_tab',
        'src.ui.finance_tab',
        'src.ui.finance_dialogs',
        'src.ui.utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Fail the build if dependency collection ever escapes this installation.
allowed_roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), windows_root]
unexpected = [(name, source) for name, source, _ in a.binaries
              if not any(Path(source).resolve().is_relative_to(root) for root in allowed_roots)]
if unexpected:
    raise RuntimeError(f'Unexpected external DLL dependencies: {unexpected}')

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f'调货助手 v{APP_VERSION} 简洁版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None  # 这里可以添加 .ico 图标文件路径
)


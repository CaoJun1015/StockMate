
# -*- mode: python ; coding: utf-8 -*-
"""
调货助手 PyInstaller 打包配置
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.version import APP_VERSION

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
        'src.models',
        'src.models.connection',
        'src.models.migrations',
        'src.models.schema',
        'src.models.queries',
        'src.models.repositories',
        'src.services',
        'src.services.exceptions',
        'src.services.inventory_service',
        'src.services.order_service',
        'src.services.payment_service',
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f'调货助手 v{APP_VERSION}',
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


# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件
RPA 自动化工具 - Windows 可执行文件打包
"""

import sys
import os

# 项目根目录 - spec文件所在目录
project_root = os.path.dirname(os.path.abspath(SPEC))

block_cipher = None

# 分析依赖
a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # 包含 rpa 包
        ('rpa', 'rpa'),
    ],
    hiddenimports=[
        # tkinter 相关
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        # PIL
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        # OpenCV
        'cv2',
        'cv2.cv2',
        # NumPy
        'numpy',
        'numpy.core._dtype_ctypes',
        # 其他依赖
        'pyautogui',
        'pyperclip',
        'win32gui',
        'win32con',
        'requests',
        'pandas',
        'openpyxl',
        # 确保所有 rpa 模块都被包含
        'rpa.config',
        'rpa.utils',
        'rpa.icon_data',
        'rpa.win_api',
        'rpa.win_driver',
        'rpa.vision',
        'rpa.color_utils',
        'rpa.ui_pickers',
        'rpa.action_helper',
        'rpa.execution',
        'rpa.label_manager',
        'rpa.test_lab',
        'rpa.gui',
        # tkcalendar 日历控件
        'tkcalendar',
        'tkcalendar.DateEntry',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不必要的模块以减小体积
        'matplotlib',
        'scipy',
        'pytest',
        'unittest',
        'pdb',
        'pydoc',
        'html',
        'lib2to3',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 去除重复项
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 创建可执行文件
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RPA自动化工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 由 CI 环境提供，本地打包可手动改为 True
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 无控制台窗口（GUI 应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, 'icon.ico'),
)

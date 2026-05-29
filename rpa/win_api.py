"""
RPA自动化工具 - WinAPI 声明层

封装 Windows 底层 API 调用，包括结构体定义、常量和函数签名设置。
所有 ctypes 结构体作为模块级类定义，WinAPI 类只保留 API 调用封装。
"""

import ctypes
from ctypes import wintypes
from typing import Dict, Any

from rpa.config import (
    WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
    WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK, WM_MOUSEWHEEL, WM_CHAR,
    WM_KEYDOWN, WM_KEYUP, WM_SHOWWINDOW, WM_SYSCOMMAND,
    MK_LBUTTON, MK_RBUTTON, SRCCOPY, DIB_RGB_COLORS,
    GA_ROOT, PW_CLIENTONLY, PW_RENDERFULLCONTENT,
    VK_LBUTTON, VK_RBUTTON, VK_CONTROL, VK_MENU, VK_SHIFT,
    VK_LWIN, VK_RETURN, VK_Q,
    SW_HIDE, SW_SHOW, SW_MINIMIZE, SW_RESTORE, SW_MAXIMIZE,
    KEYEVENTF_KEYUP,
    HWND_TOPMOST, HWND_NOTOPMOST, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE,
    GW_CHILD, GW_HWNDNEXT,
)


# ============================================================
# DPI 感知设置（必须在其他 API 调用之前设置）
# ============================================================

def _set_dpi_aware():
    """设置进程为 DPI 感知模式，解决高 DPI 显示器坐标偏移问题"""
    try:
        # 尝试使用 SetProcessDpiAwareness (Windows 8.1+)
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness(2)  # 2 = PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            # 回退到 SetProcessDpiAware (Windows Vista+)
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

_set_dpi_aware()


# ============================================================
# ctypes 结构体定义（模块级）
# ============================================================

class POINT(ctypes.Structure):
    """Windows POINT 结构体，表示一个点的坐标。"""
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class RECT(ctypes.Structure):
    """Windows RECT 结构体，表示一个矩形的左上角和右下角坐标。"""
    _fields_ = [
        ('left', ctypes.c_long),
        ('top', ctypes.c_long),
        ('right', ctypes.c_long),
        ('bottom', ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    """Windows BITMAPINFOHEADER 结构体，描述 DIB 位图的信息头。"""
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', ctypes.c_long),
        ('biHeight', ctypes.c_long),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', ctypes.c_long),
        ('biYPelsPerMeter', ctypes.c_long),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    """Windows BITMAPINFO 结构体，包含位图信息头和颜色表。"""
    pass


# BITMAPINFO 需要在 BITMAPINFOHEADER 定义之后设置 _fields_
BITMAPINFO._fields_ = [
    ('bmiHeader', BITMAPINFOHEADER),
    ('bmiColors', wintypes.DWORD * 3),
]


class GUITHREADINFO(ctypes.Structure):
    """Windows GUITHREADINFO 结构体，用于 GetGUIThreadInfo 获取线程 GUI 信息。"""
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('hwndActive', wintypes.HWND),
        ('hwndFocus', wintypes.HWND),
        ('hwndCapture', wintypes.HWND),
        ('hwndMenuOwner', wintypes.HWND),
        ('hwndMoveSize', wintypes.HWND),
        ('hwndCaret', wintypes.HWND),
        ('rcCaret', RECT),
    ]


# ============================================================
# WinAPI 类
# ============================================================

class WinAPI:
    """
    Windows API 封装类。

    提供 user32 / gdi32 / kernel32 的常用函数调用封装，
    并在模块加载时通过 setup_api() 设置所有函数的参数和返回类型。
    """

    # DLL 引用
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32

    # 消息常量（从 config 导入，保留类属性访问方式以兼容旧代码）
    WM_MOUSEMOVE = WM_MOUSEMOVE
    WM_LBUTTONDOWN = WM_LBUTTONDOWN
    WM_LBUTTONUP = WM_LBUTTONUP
    WM_LBUTTONDBLCLK = WM_LBUTTONDBLCLK
    WM_RBUTTONDOWN = WM_RBUTTONDOWN
    WM_RBUTTONUP = WM_RBUTTONUP
    WM_RBUTTONDBLCLK = WM_RBUTTONDBLCLK
    WM_MOUSEWHEEL = WM_MOUSEWHEEL
    WM_CHAR = WM_CHAR
    WM_KEYDOWN = WM_KEYDOWN
    WM_KEYUP = WM_KEYUP
    WM_SHOWWINDOW = WM_SHOWWINDOW
    WM_SYSCOMMAND = WM_SYSCOMMAND

    MK_LBUTTON = MK_LBUTTON
    MK_RBUTTON = MK_RBUTTON
    SRCCOPY = SRCCOPY
    DIB_RGB_COLORS = DIB_RGB_COLORS

    GA_ROOT = GA_ROOT
    PW_CLIENTONLY = PW_CLIENTONLY
    PW_RENDERFULLCONTENT = PW_RENDERFULLCONTENT

    VK_LBUTTON = VK_LBUTTON
    VK_RBUTTON = VK_RBUTTON
    VK_CONTROL = VK_CONTROL
    VK_MENU = VK_MENU
    VK_SHIFT = VK_SHIFT
    VK_LWIN = VK_LWIN
    VK_RETURN = VK_RETURN
    VK_Q = VK_Q

    SW_HIDE = SW_HIDE
    SW_SHOW = SW_SHOW
    SW_MINIMIZE = SW_MINIMIZE
    SW_RESTORE = SW_RESTORE
    SW_MAXIMIZE = SW_MAXIMIZE

    KEYEVENTF_KEYUP = KEYEVENTF_KEYUP

    HWND_TOPMOST = HWND_TOPMOST
    HWND_NOTOPMOST = HWND_NOTOPMOST
    SWP_NOSIZE = SWP_NOSIZE
    SWP_NOMOVE = SWP_NOMOVE
    SWP_NOACTIVATE = SWP_NOACTIVATE

    # 结构体引用（保持向后兼容）
    POINT = POINT
    RECT = RECT
    BITMAPINFOHEADER = BITMAPINFOHEADER
    BITMAPINFO = BITMAPINFO
    GUITHREADINFO = GUITHREADINFO

    # 结构体对象缓存，避免重复创建
    _cached_structs: Dict[str, Any] = {}

    @classmethod
    def get_point(cls, x: int, y: int) -> POINT:
        """
        获取缓存的 POINT 结构体。

        使用缓存的 POINT 结构体避免重复创建对象，提高性能。

        Args:
            x: X 坐标
            y: Y 坐标

        Returns:
            设置好坐标的 POINT 结构体
        """
        key = 'point'
        if key not in cls._cached_structs:
            cls._cached_structs[key] = cls.POINT()
        cls._cached_structs[key].x = x
        cls._cached_structs[key].y = y
        return cls._cached_structs[key]

    @classmethod
    def get_rect(cls) -> RECT:
        """
        获取缓存的 RECT 结构体。

        使用缓存的 RECT 结构体避免重复创建对象，提高性能。

        Returns:
            RECT 结构体
        """
        key = 'rect'
        if key not in cls._cached_structs:
            cls._cached_structs[key] = cls.RECT()
        return cls._cached_structs[key]

    @staticmethod
    def MAKELPARAM(l: int, h: int) -> int:
        """
        构建 LPARAM 值。

        Args:
            l: 低位字（通常为 x 坐标或低 16 位值）
            h: 高位字（通常为 y 坐标或高 16 位值）

        Returns:
            组合后的 LPARAM 整数值
        """
        return ((h & 0xFFFF) << 16) | (l & 0xFFFF)

    @staticmethod
    def setup_api() -> None:
        """
        设置所有 Windows API 函数的参数类型 (argtypes) 和返回类型 (restype)。

        必须在调用任何 API 函数之前调用此方法。
        """
        u = WinAPI.user32
        g = WinAPI.gdi32

        # --- user32 ---
        u.WindowFromPoint.argtypes = [POINT]
        u.WindowFromPoint.restype = wintypes.HWND

        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetAncestor.restype = wintypes.HWND

        u.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int

        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int

        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short

        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.GetClientRect.restype = ctypes.c_int  # BOOL

        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.GetWindowRect.restype = ctypes.c_int  # BOOL

        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.ClientToScreen.restype = ctypes.c_int  # BOOL

        u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.ScreenToClient.restype = ctypes.c_int  # BOOL

        u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
        u.PrintWindow.restype = ctypes.c_int  # BOOL

        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindow.restype = ctypes.c_int  # BOOL

        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = ctypes.c_int  # BOOL

        u.VkKeyScanW.argtypes = [wintypes.WCHAR]
        u.VkKeyScanW.restype = ctypes.c_short

        u.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
        u.MapVirtualKeyW.restype = ctypes.c_uint

        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC

        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        u.ReleaseDC.restype = ctypes.c_int

        u.PostMessageW.argtypes = [
            wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM,
        ]
        u.PostMessageW.restype = ctypes.c_int  # BOOL

        u.SendMessageW.argtypes = [
            wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM,
        ]
        u.SendMessageW.restype = wintypes.LPARAM

        u.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD

        u.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        u.GetGUIThreadInfo.restype = ctypes.c_int  # BOOL

        u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindow.restype = wintypes.HWND

        u.AttachThreadInput.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.BOOL,
        ]
        u.AttachThreadInput.restype = ctypes.c_int  # BOOL

        u.GetCursorPos.argtypes = [ctypes.c_void_p]  # 使用 c_void_p 避免与 pyautogui 的 POINT 类型冲突
        u.GetCursorPos.restype = ctypes.c_int  # BOOL

        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.ShowWindow.restype = ctypes.c_int  # BOOL

        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.restype = ctypes.c_int  # BOOL

        u.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        u.SetWindowPos.restype = ctypes.c_int  # BOOL

        u.SetFocus.argtypes = [wintypes.HWND]
        u.SetFocus.restype = wintypes.HWND

        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsIconic.restype = ctypes.c_int  # BOOL

        u.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int]  # DWORD, DWORD, BOOL
        u.AttachThreadInput.restype = ctypes.c_int  # BOOL

        u.BringWindowToTop.argtypes = [wintypes.HWND]
        u.BringWindowToTop.restype = ctypes.c_int  # BOOL

        u.SetActiveWindow.argtypes = [wintypes.HWND]
        u.SetActiveWindow.restype = wintypes.HWND

        u.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int

        u.FindWindowExW.argtypes = [
            wintypes.HWND, wintypes.HWND,
            wintypes.LPCWSTR, wintypes.LPCWSTR,
        ]
        u.FindWindowExW.restype = wintypes.HWND

        u.EnumChildWindows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        ]
        u.EnumChildWindows.restype = ctypes.c_int  # BOOL

        u.EnumWindows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        ]
        u.EnumWindows.restype = ctypes.c_int  # BOOL

        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = wintypes.HWND

        u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_void_p]
        u.keybd_event.restype = None

        # --- gdi32 ---
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC

        g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = wintypes.HBITMAP

        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.SelectObject.restype = wintypes.HGDIOBJ

        g.BitBlt.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_ulong,
        ]
        g.BitBlt.restype = ctypes.c_int  # BOOL

        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteObject.restype = ctypes.c_int  # BOOL

        g.DeleteDC.argtypes = [wintypes.HDC]
        g.DeleteDC.restype = ctypes.c_int  # BOOL

        g.GetDIBits.argtypes = [
            wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
        ]
        g.GetDIBits.restype = ctypes.c_int


# 模块加载时自动设置 API
WinAPI.setup_api()

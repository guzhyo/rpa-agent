"""
RPA自动化工具 - 通用操作辅助模块

提供统一的操作接口，自动根据 hwnd 是否为 None
选择前台（pyautogui）或后台（WinDriver）模式执行操作。

包含以下功能：
- 图像识别（带偏移量）
- 鼠标点击（左键/右键/双击）
- 按键发送
- 文本输入
- 滚轮滚动
"""

import ctypes
import time
from typing import Optional, Tuple

import pyautogui
import pyperclip

from rpa.config import (
    MOUSE_EVENT_DELAY, DOUBLE_CLICK_DELAY, KEY_PRESS_DELAY,
    POST_CLICK_DELAY, CLIPBOARD_DELAY, DEFAULT_CONFIDENCE,
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
)

from rpa.win_driver import WinDriver
from rpa.vision import VisionEngine




def _win32_click(x: int, y: int, button: str = 'left', double: bool = False):
    """
    使用 Win32 API 进行前台点击（直接使用物理像素坐标）

    Args:
        x: 屏幕 X 坐标（物理像素）
        y: 屏幕 Y 坐标（物理像素）
        button: 'left' 或 'right'
        double: 是否双击
    """
    user32 = ctypes.windll.user32

    def single_click(btn):
        """执行单次点击"""
        if btn == 'left':
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(MOUSE_EVENT_DELAY)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        else:
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(MOUSE_EVENT_DELAY)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

    def double_click(btn):
        """执行双击"""
        # 直接使用 Win32 API 执行双击，更可靠
        if btn == 'left':
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(DOUBLE_CLICK_DELAY)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(DOUBLE_CLICK_DELAY)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(DOUBLE_CLICK_DELAY)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        else:
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(DOUBLE_CLICK_DELAY)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            time.sleep(DOUBLE_CLICK_DELAY)
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(DOUBLE_CLICK_DELAY)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

    # 使用 SetCursorPos 直接移动到目标位置（物理像素）
    user32.SetCursorPos(x, y)
    time.sleep(KEY_PRESS_DELAY)

    if double:
        double_click(button)
    else:
        single_click(button)


class ActionHelper:
    """
    通用操作辅助类。

    所有方法均为静态方法，提供前后台统一操作接口。
    当 hwnd 不为 None 时使用后台模式（WinDriver），
    当 hwnd 为 None 时使用前台模式（pyautogui）。
    """

    @staticmethod
    def find_image_with_params(
        vision: VisionEngine,
        template_name: str,
        hwnd: Optional[int] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
        confidence: float = DEFAULT_CONFIDENCE,
        find_nth: int = 1,
        color_sensitive: bool = False,
        offset_x: int = 0,
        offset_y: int = 0,
    ) -> Optional[Tuple[int, int]]:
        """
        通用图像识别函数。

        Args:
            vision: VisionEngine 实例
            template_name: 模板图片名称或路径
            hwnd: 后台窗口句柄，None 为前台
            region: 搜索区域，None 为全屏/全窗口
            confidence: 相似度阈值
            find_nth: 第几个匹配
            color_sensitive: 是否颜色敏感
            offset_x: X 坐标偏移
            offset_y: Y 坐标偏移

        Returns:
            识别位置 (x, y) 或 None
        """
        vision.confidence = confidence
        pos = vision.find_image(
            template_name,
            hwnd=hwnd,
            region=region,
            find_nth=find_nth,
            color_sensitive=color_sensitive,
        )

        # 应用偏移
        if pos:
            pos = (pos[0] + offset_x, pos[1] + offset_y)

        return pos

    @staticmethod
    def click_action(
        hwnd: Optional[int],
        x: int,
        y: int,
        button: str = 'left',
        double: bool = False,
    ) -> None:
        """
        通用点击函数。

        Args:
            hwnd: 后台窗口句柄，None 为前台
            x: X 坐标（后台为客户区坐标，前台为屏幕坐标）
            y: Y 坐标（后台为客户区坐标，前台为屏幕坐标）
            button: 'left' 或 'right'
            double: 是否双击
        """
        if hwnd:
            # 后台点击
            WinDriver.click(hwnd, x, y, button=button, double=double)
        else:
            # 前台点击 - 使用 Win32 API（避免 DPI 缩放问题）
            _win32_click(int(x), int(y), button=button, double=double)

    @staticmethod
    def send_keys_action(hwnd: Optional[int], key: str) -> None:
        """
        通用按键函数。

        Args:
            hwnd: 后台窗口句柄，None 为前台
            key: 按键字符串，如 'a', 'ctrl+c', 'ctrl+shift+a'
        """
        if hwnd:
            # 后台模式：纯 PostMessage 方式，不激活前台窗口
            WinDriver.send_keys(hwnd, key)
        else:
            parts = [k.strip().lower() for k in key.split('+')]
            if len(parts) == 1:
                pyautogui.press(parts[0])
            else:
                # 显式控制按键时序，确保修饰键先生效
                for mod in parts[:-1]:
                    pyautogui.keyDown(mod)
                time.sleep(KEY_PRESS_DELAY)
                pyautogui.press(parts[-1])
                time.sleep(KEY_PRESS_DELAY)
                for mod in reversed(parts[:-1]):
                    pyautogui.keyUp(mod)

    @staticmethod
    def send_text_action(
        hwnd: Optional[int],
        text: str,
        click_pos: Optional[Tuple[int, int]] = None,
    ) -> None:
        """
        通用文本输入函数。

        点击位置来源（统一区域变量机制）：
        1. pos_var 传递 → 区域中心点或直接坐标
        2. click_pos=None 时 → 窗口客户区中心（保底焦点定位）

        这样兼容了图片识别、OCR识别、手动设置三类区域
        以及直接输入（无区域）场景下的焦点获取。

        Args:
            hwnd: 后台窗口句柄，None 为前台
            text: 要输入的文本
            click_pos: 点击位置 (x, y)，用于获取焦点
                       后台为客户区坐标，前台为屏幕坐标
        """
        pyperclip.copy(text)
        time.sleep(CLIPBOARD_DELAY)

        if hwnd:
            # 后台模式：纯 PostMessage 方式，不激活前台窗口
            # 先通过后台点击获取焦点，再用后台按键发送 Ctrl+V 粘贴
            if click_pos:
                WinDriver.click(hwnd, click_pos[0], click_pos[1])
                time.sleep(POST_CLICK_DELAY)
            else:
                # 无指定位置：点击客户区中心以获取焦点（保底逻辑）
                try:
                    cx, cy = WinDriver.get_client_size(hwnd)
                    if cx > 0 and cy > 0:
                        WinDriver.click(hwnd, cx // 2, cy // 2)
                        time.sleep(POST_CLICK_DELAY)
                except Exception:
                    pass  # 获取大小失败，跳过点击
            WinDriver.send_keys(hwnd, 'ctrl+v')
        else:
            # 前台模式
            if click_pos:
                pyautogui.click(click_pos[0], click_pos[1])
                time.sleep(POST_CLICK_DELAY)
            pyautogui.hotkey('ctrl', 'v')

    @staticmethod
    def scroll_action(
        hwnd: Optional[int],
        x: int,
        y: int,
        clicks: int = 1,
        direction: str = 'up',
    ) -> None:
        """
        通用滚轮函数。

        Args:
            hwnd: 后台窗口句柄，None 为前台
            x: 滚轮位置 X 坐标（后台为客户区坐标，前台为屏幕坐标）
            y: 滚轮位置 Y 坐标（后台为客户区坐标，前台为屏幕坐标）
            clicks: 滚动次数
            direction: 'up' 或 'down'
        """
        if hwnd:
            # 后台滚轮
            delta = clicks if direction == 'up' else -clicks
            WinDriver.scroll(hwnd, x, y, delta)
        else:
            # 前台滚轮
            if direction == 'up':
                pyautogui.scroll(clicks, x, y)
            else:
                pyautogui.scroll(-clicks, x, y)

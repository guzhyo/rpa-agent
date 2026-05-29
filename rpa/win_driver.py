"""
RPA自动化工具 - 底层驱动层

提供窗口操作的后台驱动功能，包括：
- 窗口客户区坐标获取
- 窗口截图（PrintWindow / BitBlt）
- 后台鼠标点击、文本输入、按键发送、滚轮滚动
- 坐标转换（屏幕 <-> 客户区）
- 窗口等待（出现/消失）
"""

import ctypes
import time
from typing import Optional, Tuple

import win32gui

from rpa.win_api import WinAPI, POINT, RECT, BITMAPINFO, BITMAPINFOHEADER
from rpa.utils import retry_on_failure
from rpa.config import (
    CLICK_DELAY, DOUBLE_CLICK_DELAY, TEXT_INPUT_DELAY,
    KEY_PRESS_DELAY, SCROLL_DELAY, POST_CLICK_DELAY, SCROLL_DELTA_UNIT,
    WINDOW_POLL_INTERVAL, DEFAULT_TIMEOUT,
    PW_CLIENTONLY, PW_RENDERFULLCONTENT, SRCCOPY, DIB_RGB_COLORS,
    MK_LBUTTON, MK_RBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN,
    WM_RBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONDBLCLK,
    WM_MOUSEWHEEL, WM_CHAR,
    WM_KEYDOWN, WM_KEYUP, VK_RETURN, VK_CONTROL, VK_SHIFT,
    WM_MOUSEACTIVATE, MA_NOACTIVATE,
    MODIFIER_KEYS, SPECIAL_KEYS, GW_CHILD,
    KEYEVENTF_KEYUP,
)


class WinDriver:
    """
    Windows 底层驱动类。

    所有方法均为静态方法，通过 Windows API 实现后台窗口操作。
    """

    @staticmethod
    def get_client_origin(hwnd: int) -> Tuple[int, int]:
        """
        获取窗口客户区左上角的屏幕坐标。

        Args:
            hwnd: 窗口句柄

        Returns:
            (x, y) 屏幕坐标元组，无效句柄时返回 (0, 0)
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return (0, 0)
        pt = POINT(0, 0)
        WinAPI.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    @staticmethod
    def get_client_size(hwnd: int) -> Tuple[int, int]:
        """
        获取窗口客户区大小。

        Args:
            hwnd: 窗口句柄

        Returns:
            (width, height) 元组，无效句柄时返回 (0, 0)
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return (0, 0)
        rect = RECT()
        WinAPI.user32.GetClientRect(hwnd, ctypes.byref(rect))
        return rect.right - rect.left, rect.bottom - rect.top

    @staticmethod
    def activate_window(hwnd: int) -> bool:
        """
        强制激活窗口（绕过 Windows 前台锁定限制）。

        通过 AttachThreadInput 挂接到前台线程后调用 SetForegroundWindow，
        这是绕过 Windows 禁止后台进程抢焦点的标准方案。
        同时执行 ShowWindow/SW_RESTORE、SetFocus、BringWindowToTop、
        SetActiveWindow 的组合，确保窗口在前台并拥有键盘焦点。

        适用场景：后台模式对现代 UWP/XAML 窗口（如 Win11 记事本）失效时，
        可用此方法将窗口拉到前台后再操作。

        Args:
            hwnd: 目标窗口句柄

        Returns:
            bool: 是否成功
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            print(f"[DEBUG activate_window] hwnd无效: {hwnd}", flush=True)
            return False

        try:
            # 1. 如果窗口最小化，先恢复
            if WinAPI.user32.IsIconic(hwnd):
                WinAPI.user32.ShowWindow(hwnd, 9)  # SW_RESTORE

            # 2. 获取当前线程ID（worker线程，即调用 SetForegroundWindow 的线程）
            #    AttachThreadInput 要求：调用 SetForegroundWindow 的线程必须是
            #    idAttach 线程。这里运行在 worker 线程中，必须挂接 worker 线程
            #    而非 GUI 线程，否则 foreground lock timeout 期间会静默失败。
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            lpdw = ctypes.c_ulong()
            target_tid = WinAPI.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lpdw))

            # 3. AttachThreadInput 绕过焦点限制（核心步骤）
            #    AttachThreadInput(idAttach, idAttachTo, ...)
            #    把当前线程（worker）挂到目标窗口线程上
            attached = False
            if current_tid and target_tid and current_tid != target_tid:
                attached = bool(WinAPI.user32.AttachThreadInput(current_tid, target_tid, True))

            try:
                # 4. 设为前台窗口（现在 worker 线程已挂接，可以通过检查）
                sf_ret = WinAPI.user32.SetForegroundWindow(hwnd)
                print(f"[DEBUG activate_window] SetForegroundWindow ret={sf_ret}", flush=True)
                # 5. 获取键盘焦点
                WinAPI.user32.SetFocus(hwnd)
            finally:
                # 6. 解除线程挂接
                if attached:
                    WinAPI.user32.AttachThreadInput(current_tid, target_tid, False)

            # 7. 强化置顶和激活
            WinAPI.user32.BringWindowToTop(hwnd)
            WinAPI.user32.SetActiveWindow(hwnd)

            print(f"[DEBUG activate_window] hwnd={hwnd} 激活成功", flush=True)
            return True
        except Exception as e:
            print(f"[DEBUG activate_window] 异常: {e}", flush=True)
            return False

    @staticmethod
    @retry_on_failure(max_retries=2, delay=0.1, exceptions=(OSError,))
    def capture_window(
        hwnd: int,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[object]:
        """
        捕获窗口客户区的截图。

        依次尝试三种截图方式：PrintWindow(PW_RENDERFULLCONTENT)、
        PrintWindow(PW_CLIENTONLY)、BitBlt。

        使用 gdi_context 上下文管理器确保 GDI 资源正确释放。

        Args:
            hwnd: 窗口句柄
            region: 可选的裁剪区域 (x1, y1, x2, y2)，相对于客户区坐标

        Returns:
            OpenCV BGR 格式的 numpy 数组，失败时返回 None
        """
        import cv2
        import numpy as np

        from rpa.utils import gdi_context

        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return None

        try:
            with gdi_context(hwnd) as (hdc, mfc_dc, bitmap, old_obj, w, h):
                if w <= 0 or h <= 0:
                    return None

                # 依次尝试三种截图方式
                success = False
                if WinAPI.user32.PrintWindow(
                    hwnd, mfc_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT
                ):
                    success = True
                elif WinAPI.user32.PrintWindow(hwnd, mfc_dc, PW_CLIENTONLY):
                    success = True
                elif WinAPI.gdi32.BitBlt(
                    mfc_dc, 0, 0, w, h, hdc, 0, 0, SRCCOPY
                ):
                    success = True

                if not success:
                    return None

                # 获取图像数据
                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = w
                bmi.bmiHeader.biHeight = -h  # 自顶向下
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                buff = ctypes.create_string_buffer(w * h * 4)
                WinAPI.gdi32.GetDIBits(
                    mfc_dc, bitmap, 0, h, buff, ctypes.byref(bmi), DIB_RGB_COLORS
                )

                # 转换为 OpenCV 格式
                img = np.frombuffer(buff, dtype=np.uint8).reshape((h, w, 4))
                bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                # 应用区域裁剪
                if region:
                    x1, y1, x2, y2 = region
                    bgr = bgr[max(0, y1):min(h, y2), max(0, x1):min(w, x2)].copy()

                return bgr

        except Exception as e:
            return None

    @staticmethod
    def screen_to_client(
        hwnd: int, screen_x: int, screen_y: int
    ) -> Tuple[int, int]:
        """
        将屏幕坐标转换为窗口客户区坐标。

        Args:
            hwnd: 窗口句柄
            screen_x: 屏幕 X 坐标
            screen_y: 屏幕 Y 坐标

        Returns:
            (x, y) 客户区坐标元组
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return (0, 0)
        pt = POINT(screen_x, screen_y)
        WinAPI.user32.ScreenToClient(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    @staticmethod
    def client_to_screen(
        hwnd: int, client_x: int, client_y: int
    ) -> Tuple[int, int]:
        """
        将窗口客户区坐标转换为屏幕坐标。

        Args:
            hwnd: 窗口句柄
            client_x: 客户区 X 坐标
            client_y: 客户区 Y 坐标

        Returns:
            (x, y) 屏幕坐标元组
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return (0, 0)
        pt = POINT(client_x, client_y)
        WinAPI.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    @staticmethod
    def click(
        hwnd: int,
        x: int,
        y: int,
        button: str = 'left',
        double: bool = False,
    ) -> bool:
        """
        向窗口发送后台点击消息（不激活窗口）。

        先尝试 PostMessageW（异步），若失败则回退到 SendMessageW（同步）。
        现代 UWP/XAML 窗口（如 Win11 记事本）可能需要 SendMessage 才能响应。

        Args:
            hwnd: 窗口句柄
            x: 客户区 X 坐标
            y: 客户区 Y 坐标
            button: 鼠标按键，'left' 或 'right'
            double: 是否双击

        Returns:
            bool: 是否成功
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return False

        lparam = WinAPI.MAKELPARAM(x, y)

        if button == 'left':
            down_msg, up_msg = WM_LBUTTONDOWN, WM_LBUTTONUP
            wparam, dbl_msg = MK_LBUTTON, WM_LBUTTONDBLCLK
        elif button == 'right':
            down_msg, up_msg = WM_RBUTTONDOWN, WM_RBUTTONUP
            wparam, dbl_msg = MK_RBUTTON, WM_RBUTTONDBLCLK
        else:
            return False

        def _send_click_pair(send_fn):
            """发送一对 down+up 消息"""
            send_fn(hwnd, down_msg, wparam, lparam)
            time.sleep(CLICK_DELAY)
            send_fn(hwnd, up_msg, 0, lparam)

        def _send_dbl_msg(send_fn):
            """发送双击序列"""
            send_fn(hwnd, dbl_msg, wparam, lparam)
            time.sleep(CLICK_DELAY)
            send_fn(hwnd, down_msg, wparam, lparam)
            time.sleep(CLICK_DELAY)
            send_fn(hwnd, up_msg, 0, lparam)

        # 主策略：PostMessageW（异步，不阻塞）
        post_click = WinAPI.user32.PostMessageW
        _send_click_pair(post_click)

        if double:
            time.sleep(POST_CLICK_DELAY)
            _send_dbl_msg(post_click)
        return True

    @staticmethod
    def send_text(
        hwnd: int,
        text: str,
        x: int = 0,
        y: int = 0,
    ) -> None:
        """
        向窗口发送文本输入。

        智能路由：
        - 如果目标窗口是当前前台窗口 → 使用 pyautogui.write()（系统级输入），
          确保现代 UWP/XAML 窗口（如 Win11 记事本）能正确接收文本。
        - 如果目标窗口在后台 → 使用 PostMessageW 投递 WM_CHAR。

        Args:
            hwnd: 主窗口句柄
            text: 要发送的文本
            x: 光标位置 X 坐标（嵌入 LPARAM）
            y: 光标位置 Y 坐标（嵌入 LPARAM）
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return

        # 判断目标窗口是否在前台
        fg = WinAPI.user32.GetForegroundWindow()

        if fg and fg == hwnd:
            # 前台模式：使用 pyautogui.write() 系统级注入
            print("[DEBUG send_text] 前台模式 → 使用 pyautogui.write()", flush=True)
            import pyautogui
            pyautogui.write(text, interval=TEXT_INPUT_DELAY)
            return

        # 后台模式：PostMessageW 投递 WM_CHAR
        print("[DEBUG send_text] 后台模式 → 使用 WM_CHAR", flush=True)

        # 找到焦点子窗口，WM_CHAR 必须发给编辑控件而非主窗口
        target = WinDriver._get_focus_window(hwnd)
        if not target or not WinAPI.user32.IsWindow(target):
            target = hwnd

        # 确保目标窗口有键盘焦点（挂接 worker 线程到目标线程）
        try:
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            lpdw = ctypes.c_ulong()
            target_tid = WinAPI.user32.GetWindowThreadProcessId(
                hwnd, ctypes.byref(lpdw))
            if current_tid and target_tid and current_tid != target_tid:
                WinAPI.user32.AttachThreadInput(current_tid, target_tid, True)
            WinAPI.user32.SetFocus(target)
            if current_tid and target_tid and current_tid != target_tid:
                WinAPI.user32.AttachThreadInput(current_tid, target_tid, False)
            print(f"[DEBUG send_text] SetFocus to target={target}", flush=True)
        except Exception as e:
            print(f"[DEBUG send_text] SetFocus 失败: {e}", flush=True)

        for char in text:
            if char == '\n':
                WinAPI.user32.PostMessageW(target, WM_KEYDOWN, VK_RETURN,
                                           WinDriver._build_key_lparam(VK_RETURN, False))
                time.sleep(KEY_PRESS_DELAY)
                WinAPI.user32.PostMessageW(target, WM_KEYUP, VK_RETURN,
                                           WinDriver._build_key_lparam(VK_RETURN, True))
            else:
                lparam = WinAPI.MAKELPARAM(x, y)
                WinAPI.user32.PostMessageW(target, WM_CHAR, ord(char), lparam)
            time.sleep(TEXT_INPUT_DELAY)

    @staticmethod
    def _find_input_child(hwnd: int) -> Optional[int]:
        """
        枚举所有子窗口，找到最适合接收键盘/文本输入的控件。

        优先匹配的窗口类名：
        - Edit, RichEdit* (传统编辑框)
        - TextInputHostWindowClass (现代 UWP/Notepad 文本输入宿主)
        - Scintilla (代码编辑器)
        - Internet Explorer_Server (嵌入式浏览器)

        Args:
            hwnd: 主窗口句柄

        Returns:
            最佳子窗口句柄，未找到时返回 None
        """
        candidates: list = []
        candidate_class = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long))

        def enum_proc(child_hwnd, lparam):
            try:
                buf = ctypes.create_unicode_buffer(256)
                WinAPI.user32.GetClassNameW(child_hwnd, buf, 256)
                cn = buf.value
                if cn:
                    candidate_class.append((child_hwnd, cn))
                    cn_lower = cn.lower()
                    # 优先匹配已知的文本输入控件类名
                    if 'edit' in cn_lower or 'richedit' in cn_lower:
                        candidates.append((1, child_hwnd, cn))  # 最高优先级
                    elif ('textinput' in cn_lower or
                          'scintilla' in cn_lower):
                        candidates.append((2, child_hwnd, cn))
                    elif ('richedit' not in cn_lower and
                          'edit' not in cn_lower and
                          cn_lower != 'button' and
                          cn_lower != 'static' and
                          cn_lower != 'scrollbar'):
                        # 其他非 UI 装饰类控件，作为低优先级备选
                        candidates.append((3, child_hwnd, cn))
            except Exception:
                pass
            return True

        try:
            child_proc = WNDENUMPROC(enum_proc)
            WinAPI.user32.EnumChildWindows(hwnd, child_proc, 0)
            print(
                f"[DEBUG _find_input_child] hwnd={hwnd} "
                f"found {len(candidate_class)} child windows: "
                f"{candidate_class}", flush=True)
            if candidates:
                candidates.sort(key=lambda x: x[0])
                best = candidates[0]
                print(
                    f"[DEBUG _find_input_child] best={best[1]} "
                    f"class={best[2]} priority={best[0]}", flush=True)
                return best[1]
        except Exception as e:
            print(f"[DEBUG _find_input_child] 异常: {e}", flush=True)
        return None

    @staticmethod
    def _get_focus_window(hwnd: int) -> int:
        """
        获取后台窗口内当前拥有键盘焦点的子窗口句柄。

        优先级：
        1. GetGUIThreadInfo 获取焦点子窗口
        2. GetWindow(GW_CHILD) 获取第一个直接子窗口（窗口后台时焦点可能丢失）
        3. EnumChildWindows 智能搜索最佳文本输入子窗口
        4. 回退到主窗口自身

        Args:
            hwnd: 主窗口句柄

        Returns:
            焦点子窗口句柄，获取失败时返回主窗口句柄
        """
        try:
            lpdw = ctypes.c_ulong()
            thread_id = WinAPI.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lpdw))
            if not thread_id:
                print(f"[DEBUG _get_focus_window] thread_id为0, 回退到hwnd={hwnd}", flush=True)
                return hwnd
            info = WinAPI.GUITHREADINFO()
            info.cbSize = ctypes.sizeof(info)
            if WinAPI.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
                if info.hwndFocus and info.hwndFocus != hwnd and WinAPI.user32.IsWindow(info.hwndFocus):
                    print(f"[DEBUG _get_focus_window] GetGUIThreadInfo hwndFocus={info.hwndFocus}", flush=True)
                    return info.hwndFocus
                print(f"[DEBUG _get_focus_window] hwndFocus={info.hwndFocus}, 尝试子窗口", flush=True)
            # 窗口在后台时焦点子窗口可能丢失，回退到第一个子窗口
            child = WinAPI.user32.GetWindow(hwnd, GW_CHILD)
            print(f"[DEBUG _get_focus_window] GetWindow(hwnd={hwnd}, GW_CHILD=5) returned {child}", flush=True)
            if child and child != hwnd and WinAPI.user32.IsWindow(child):
                return child
            # 枚举所有子窗口，智能查找最佳输入控件
            best_child = WinDriver._find_input_child(hwnd)
            if best_child and WinAPI.user32.IsWindow(best_child):
                return best_child
            print(f"[DEBUG _get_focus_window] 无子窗口, 回退到hwnd={hwnd}", flush=True)
        except Exception as e:
            print(f"[DEBUG _get_focus_window] 异常: {e}", flush=True)
        return hwnd

    @staticmethod
    def _build_key_lparam(vk_code: int, is_keyup: bool = False) -> int:
        """
        构建 WM_KEYDOWN / WM_KEYUP 的正确 lParam。

        lParam 各字段含义（从低位到高位）：
        - bits 0-15:  重复计数
        - bits 16-23: 扫描码（OEM 扫描码）
        - bit 24:     扩展键标记（右 Ctrl/Alt、方向键等）
        - bits 25-28: 保留
        - bit 29:     上下文码（Alt 键按下时为 1）
        - bit 30:     前一个键状态（WM_KEYUP 时为 1）
        - bit 31:     转换状态（按键释放时为 1）

        Args:
            vk_code: 虚拟键码
            is_keyup: True 为 WM_KEYUP，False 为 WM_KEYDOWN

        Returns:
            组合后的 lParam 整数值
        """
        scan_code = WinAPI.user32.MapVirtualKeyW(vk_code, 0)
        repeat_count = 0
        extended_flag = 0
        extended_keys = {0x11, 0x12, 0x25, 0x26, 0x27, 0x28, 0x21, 0x22,
                         0x23, 0x24, 0x2D, 0x2E, 0xA2, 0xA3, 0xA4, 0xA5}
        if vk_code in extended_keys:
            extended_flag = 1
        context_code = 0
        prev_state = 1 if is_keyup else 0
        transition = 1 if is_keyup else 0

        lparam = (repeat_count |
                  (scan_code << 16) |
                  (extended_flag << 24) |
                  (context_code << 29) |
                  (prev_state << 30) |
                  (transition << 31))
        return lparam

    @staticmethod
    def send_keys(hwnd: int, keys_string: str) -> None:
        """
        向后台窗口发送按键或组合键消息。

        智能路由：
        - 如果目标窗口是当前前台窗口 → 使用 keybd_event（系统级注入），
          确保现代 UWP/XAML 窗口（如 Win11 记事本）能正确响应。
        - 如果目标窗口在后台 → 使用 PostMessageW 投递 WM_KEYDOWN/UP。

        Args:
            hwnd: 主窗口句柄
            keys_string: 按键字符串，如 'a', 'ctrl+c', 'ctrl+shift+a'
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            print(f"[DEBUG send_keys] hwnd无效: {hwnd}", flush=True)
            return

        # 获取实际接收按键的焦点子窗口
        target = WinDriver._get_focus_window(hwnd)
        print(f"[DEBUG send_keys] hwnd={hwnd} target={target}", flush=True)
        if not target or not WinAPI.user32.IsWindow(target):
            print(f"[DEBUG send_keys] target无效: {target}", flush=True)
            return

        parts = [k.strip().lower() for k in keys_string.split('+')]
        to_press: list = []
        main_key = None

        for p in parts:
            if p in MODIFIER_KEYS:
                to_press.append(MODIFIER_KEYS[p])
            elif p in SPECIAL_KEYS:
                main_key = SPECIAL_KEYS[p]
            else:
                if p and len(p) > 0:
                    res = WinAPI.user32.VkKeyScanW(p[0])
                    if res != -1:
                        main_key = res & 0xFF

        if main_key is None and not to_press:
            print(f"[DEBUG send_keys] 无有效按键: keys_string={keys_string}", flush=True)
            return

        print(f"[DEBUG send_keys] modifiers={to_press} main_key={main_key}", flush=True)

        # 判断目标窗口是否在前台
        fg = WinAPI.user32.GetForegroundWindow()

        if fg and (fg == hwnd or fg == target):
            # 前台模式：使用 keybd_event 系统级注入（现代 UWP 窗口必须走这路）
            print("[DEBUG send_keys] 前台模式 → 使用 keybd_event", flush=True)
            # 按下修饰键
            for m in to_press:
                WinAPI.user32.keybd_event(m, 0, 0, 0)
                time.sleep(KEY_PRESS_DELAY)
            # 按下并释放主键
            if main_key is not None:
                WinAPI.user32.keybd_event(main_key, 0, 0, 0)
                time.sleep(KEY_PRESS_DELAY)
                WinAPI.user32.keybd_event(main_key, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(KEY_PRESS_DELAY)
            # 释放修饰键（逆序）
            for m in reversed(to_press):
                WinAPI.user32.keybd_event(m, 0, KEYEVENTF_KEYUP, 0)
        else:
            # 后台模式：PostMessageW 投递 WM_KEYDOWN/UP
            print("[DEBUG send_keys] 后台模式 → 使用 PostMessageW", flush=True)
            # 确保目标窗口有键盘焦点（挂接 worker 线程到目标线程）
            try:
                current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
                lpdw = ctypes.c_ulong()
                target_tid = WinAPI.user32.GetWindowThreadProcessId(target, ctypes.byref(lpdw))
                if current_tid and target_tid and current_tid != target_tid:
                    WinAPI.user32.AttachThreadInput(current_tid, target_tid, True)
                WinAPI.user32.SetFocus(target)
                if current_tid and target_tid and current_tid != target_tid:
                    WinAPI.user32.AttachThreadInput(current_tid, target_tid, False)
            except Exception as e:
                print(f"[DEBUG send_keys] SetFocus 异常: {e}", flush=True)

            # 按下修饰键
            for m in to_press:
                lparam = WinDriver._build_key_lparam(m, is_keyup=False)
                ret = WinAPI.user32.PostMessageW(target, WM_KEYDOWN, m, lparam)
                print(f"[DEBUG send_keys] PostMessageW KEYDOWN vk={hex(m)} lparam={hex(lparam)} ret={ret}", flush=True)
                time.sleep(KEY_PRESS_DELAY)
            # 按下并释放主键
            if main_key is not None:
                lparam_down = WinDriver._build_key_lparam(main_key, is_keyup=False)
                lparam_up = WinDriver._build_key_lparam(main_key, is_keyup=True)
                ret1 = WinAPI.user32.PostMessageW(target, WM_KEYDOWN, main_key, lparam_down)
                print(f"[DEBUG send_keys] PostMessageW KEYDOWN vk={hex(main_key)} lparam={hex(lparam_down)} ret={ret1}", flush=True)
                time.sleep(KEY_PRESS_DELAY)
                ret2 = WinAPI.user32.PostMessageW(target, WM_KEYUP, main_key, lparam_up)
                print(f"[DEBUG send_keys] PostMessageW KEYUP   vk={hex(main_key)} lparam={hex(lparam_up)} ret={ret2}", flush=True)
            time.sleep(KEY_PRESS_DELAY)
            # 释放修饰键（逆序）
            for m in reversed(to_press):
                lparam = WinDriver._build_key_lparam(m, is_keyup=True)
                ret = WinAPI.user32.PostMessageW(target, WM_KEYUP, m, lparam)
                print(f"[DEBUG send_keys] PostMessageW KEYUP   vk={hex(m)} lparam={hex(lparam)} ret={ret}", flush=True)

    @staticmethod
    def scroll(hwnd: int, x: int, y: int, delta: int) -> None:
        """
        向窗口发送后台鼠标滚轮消息。

        Args:
            hwnd: 窗口句柄
            x: 客户区 X 坐标
            y: 客户区 Y 坐标
            delta: 滚动量（正数向上，负数向下，单位为"格"）
        """
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            return

        # WM_MOUSEWHEEL 需要屏幕坐标
        screen_x, screen_y = WinDriver.client_to_screen(hwnd, x, y)

        # 构建消息参数
        delta_value = delta * SCROLL_DELTA_UNIT
        lparam = WinAPI.MAKELPARAM(screen_x, screen_y)
        wparam = (delta_value << 16) | 0

        WinAPI.user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
        time.sleep(SCROLL_DELAY)

    @staticmethod
    def wait_for_window(
        window_title: str,
        timeout: float = DEFAULT_TIMEOUT,
        wait_type: str = 'appear',
    ) -> bool:
        """
        等待窗口出现或消失。

        Args:
            window_title: 窗口标题（支持部分匹配，不区分大小写）
            timeout: 超时时间（秒）
            wait_type: 'appear' 等待窗口出现, 'disappear' 等待窗口消失

        Returns:
            True 表示等待成功，False 表示超时
        """
        if wait_type == 'appear':
            return WinDriver._wait_for_appear(window_title, timeout)
        else:
            return WinDriver._wait_for_disappear(window_title, timeout)

    @staticmethod
    def _wait_for_appear(window_title: str, timeout: float) -> bool:
        """等待窗口出现。"""
        start_time = time.time()
        title_lower = window_title.lower()

        found = False

        def _check_appear(hwnd, _extra):
            nonlocal found
            title = win32gui.GetWindowText(hwnd)
            if title_lower in title.lower() and win32gui.IsWindowVisible(hwnd):
                found = True
                return False  # 找到，停止枚举
            return True  # 继续枚举

        while time.time() - start_time < timeout:
            found = False
            win32gui.EnumWindows(_check_appear, None)
            if found:
                return True
            time.sleep(WINDOW_POLL_INTERVAL)

        return False

    @staticmethod
    def _wait_for_disappear(window_title: str, timeout: float) -> bool:
        """等待窗口消失。"""
        start_time = time.time()
        title_lower = window_title.lower()

        def _check_exist(hwnd, _extra):
            title = win32gui.GetWindowText(hwnd)
            if title_lower in title.lower() and win32gui.IsWindowVisible(hwnd):
                return False  # 窗口还在，停止枚举（表示"存在"）
            return True  # 继续枚举

        while time.time() - start_time < timeout:
            # 如果存在任何匹配窗口，EnumWindows 会因为回调返回 False 而停止
            # 返回 False 表示窗口还存在
            exist = not win32gui.EnumWindows(_check_exist, None)
            if not exist:
                return True  # 窗口消失了
            time.sleep(WINDOW_POLL_INTERVAL)

        return False

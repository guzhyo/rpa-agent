"""
RPA自动化工具 - 执行引擎模块

提供流程的后台执行功能：
- 在独立线程中运行流程步骤
- 通过 ui_queue 与主线程通信
- 支持超时重试/跳过对话框（通过 ui_queue 机制）
- 支持暂停对话框（通过 ui_queue 机制）
"""

import base64
import ctypes
import io
import os
import re
import threading
import time
import traceback

import cv2
from ctypes import wintypes
from queue import Queue
from typing import Any, Dict, List, Optional, Tuple

import pyautogui
from PIL import Image

from rpa.win_api import WinAPI
from rpa.win_driver import WinDriver
from rpa.vision import VisionEngine
from rpa.action_helper import ActionHelper
from rpa.label_manager import LabelManager
from rpa.config import (
    DEFAULT_TIMEOUT, DEFAULT_RETRY_INTERVAL, DEFAULT_CONFIDENCE,
    BUTTON_MAP, WINDOW_ACTIONS, NODE_TYPES,
    OCR_DEFAULT_URL, INPUT_TYPES, LOOP_MODES,
    SW_RESTORE, SW_MAXIMIZE, SW_MINIMIZE,
    HWND_TOPMOST, HWND_NOTOPMOST,
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE,
    OVERLAY_HIDE_DELAY, POST_CLICK_DELAY,
    WAIT_RETRY_INTERVAL, WINDOW_POLL_INTERVAL, DIALOG_POLL_INTERVAL,
    OCR_ERROR_RETRY_DELAY,
    BG_WINDOW_RETRY_INTERVAL, BG_WINDOW_RETRY_COUNT,
)

from rpa.utils import StopException, logger


def find_window_by_title(title: str) -> Optional[int]:
    """
    根据窗口标题查找窗口句柄（模块级工具函数）。

    先尝试精确匹配，再尝试模糊匹配（枚举所有可见窗口）。
    使用 win32gui.EnumWindows 确保兼容 WPS Office 等非标准窗口。

    Args:
        title: 窗口标题（支持部分匹配）

    Returns:
        窗口句柄，未找到时返回 None
    """
    if not title:
        return None

    try:
        import win32gui

        # 先尝试精确匹配
        hwnd = WinAPI.user32.FindWindowW(None, title)
        if hwnd and WinAPI.user32.IsWindow(hwnd):
            return hwnd

        # 模糊匹配 — 使用 win32gui 而非原始 ctypes 回调
        # ctypes EnumWindows 回调在某些 WPS/国产软件窗口上可能
        # 无法正确获取标题文本，win32gui 封装更可靠
        found_hwnd: Optional[int] = None

        def enum_proc(hwnd: int, _: Any) -> bool:
            nonlocal found_hwnd
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                text = win32gui.GetWindowText(hwnd)
                if text and title.lower() in text.lower():
                    found_hwnd = hwnd
                    return False  # 停止枚举
            except Exception:
                pass
            return True

        win32gui.EnumWindows(enum_proc, None)
        return found_hwnd
    except Exception:
        return None


class ExecutionEngine(threading.Thread):
    """
    执行引擎类。

    在独立线程中运行流程步骤，通过 ui_queue 与主线程通信。
    """

    def __init__(
        self,
        flow_data: List[Dict[str, Any]],
        vision: VisionEngine,
        ui_queue: Queue,
        stop_event: threading.Event,
    ) -> None:
        """
        初始化执行引擎。

        Args:
            flow_data: 流程数据（步骤列表）
            vision: VisionEngine 实例
            ui_queue: 与主线程通信的队列
            stop_event: 停止事件
        """
        super().__init__(daemon=True)
        self.flow = flow_data
        self.vision = vision
        self.ui_queue = ui_queue
        self.stop_event = stop_event
        self.runtime_vars: Dict[str, Any] = {}
        self.runtime_bg_hwnd: Optional[int] = None
        self.runtime_bg_title: str = ''  # 记录后台窗口标题，用于重试找窗

    def log(self, msg: str) -> None:
        """
        发送日志消息到主线程。

        Args:
            msg: 日志消息
        """
        self.ui_queue.put({"type": "log", "msg": msg})

    @staticmethod
    def _get_visible_window_list() -> str:
        """枚举所有可见窗口标题，返回格式化字符串。同时打印到控制台。"""
        try:
            import win32gui
        except Exception:
            return "(无法枚举窗口)"
        titles: list = []
        visited: set = set()

        def proc(h: int, _: Any) -> bool:
            try:
                if not win32gui.IsWindowVisible(h):
                    return True
                title_str = win32gui.GetWindowText(h).strip()
                if title_str and title_str not in visited:
                    visited.add(title_str)
                    titles.append(f"    \"{title_str}\"")
            except Exception:
                pass
            return True

        win32gui.EnumWindows(proc, None)
        if titles:
            result = "当前可见窗口（共{}个）:\n{}".format(len(titles), "\n".join(titles[:25]))
            if len(titles) > 25:
                result += f"\n    ... 还有 {len(titles) - 25} 个"
            print("[DIAG] " + result.replace("\n", "\n[DIAG] "), flush=True)
        else:
            result = "未找到任何可见窗口"
            print("[DIAG] " + result, flush=True)
        return result

    def check_stop(self) -> None:
        """检查是否收到停止信号，收到则抛出 StopException 异常。"""
        if self.stop_event.is_set():
            raise StopException("流程已停止")

    def show_timeout_dialog(self, message: str) -> Optional[str]:
        """
        通过 ui_queue 请求主线程显示超时对话框，并等待用户选择。

        不再创建新的 tk.Tk() 实例，而是发送消息到主线程处理。

        Args:
            message: 超时提示消息

        Returns:
            "retry" 或 "skip"
        """
        # 发送超时对话框请求到主线程
        result_event = threading.Event()
        user_choice: Dict[str, Optional[str]] = {'action': None}

        # 在 ui_queue 上注册等待回调
        self.ui_queue.put({
            "type": "timeout",
            "msg": message,
            "result_event": result_event,
            "user_choice": user_choice,
        })

        # 等待用户选择
        while not result_event.is_set():
            self.check_stop()
            time.sleep(DIALOG_POLL_INTERVAL)

        return user_choice['action']

    def show_pause_dialog(self, pause_msg: str) -> Optional[str]:
        """
        通过 ui_queue 请求主线程显示暂停对话框，并等待用户选择。

        不再创建新的 tk.Tk() 实例，而是发送消息到主线程处理。

        Args:
            pause_msg: 暂停提示消息

        Returns:
            'Yes' 或 'No'
        """
        result_event = threading.Event()
        user_choice: Dict[str, Optional[str]] = {'continue': None}

        self.ui_queue.put({
            "type": "pause",
            "msg": pause_msg,
            "result_event": result_event,
            "user_choice": user_choice,
        })

        while not result_event.is_set():
            self.check_stop()
            time.sleep(DIALOG_POLL_INTERVAL)

        return user_choice['continue']

    def _execute_window_action(self, title: str, action: str) -> None:
        """
        执行窗口操作。

        Args:
            title: 窗口标题
            action: 操作类型
        """
        try:
            if not title:
                self.log("\u274c 窗口操作失败: 未设置窗口标题")
                return

            # 记录后台窗口标题，供后续步骤重试找窗使用
            if action == '激活(设为后台目标)':
                self.runtime_bg_title = title

            # 带重试的窗口查找（带超时）
            # 在超时时间内静默轮询查找，超时后才弹窗
            while True:
                self.check_stop()
                hwnd = find_window_by_title(title)
                if hwnd:
                    self.log(
                        f"\U0001f527 通过Windows API找到窗口: {title} "
                        f"(HWND: {hwnd})")
                    break

                # 未找到 → 在超时时间内静默轮询，超时后才弹窗
                start_time = time.time()
                while time.time() - start_time < DEFAULT_TIMEOUT:
                    self.check_stop()
                    time.sleep(WINDOW_POLL_INTERVAL)
                    hwnd = find_window_by_title(title)
                    if hwnd:
                        self.log(
                            f"\U0001f527 通过Windows API找到窗口: {title} "
                            f"(HWND: {hwnd})")
                        break
                else:
                    # 超时 → 弹窗让用户选择重试/跳过
                    self.log(f"\u23f0 窗口查找超时（{DEFAULT_TIMEOUT}秒），窗口 '{title}' 未找到")
                    window_list = self._get_visible_window_list()
                    choice = self.show_timeout_dialog(
                        f"未找到窗口: {title}\n\n{window_list}\n"
                    )
                    if choice != "retry":
                        self.log(f"\u23ed 用户选择跳过，窗口 '{title}' 未设为后台目标")
                        return
                    self.log(f"\U0001f504 用户选择重试找窗: {title}")
                    continue  # 重试：回到外层循环重新开始
                break  # 找到窗口，退出外层循环

            if action == '激活(设为后台目标)':
                self.runtime_bg_hwnd = hwnd
                self.log(
                    f"\u2705 窗口 '{title}' 已设为后台目标 "
                    f"HWND: {hwnd} (不改变窗口状态)")
                self.log(f"\U0001f9ea 调试: runtime_bg_hwnd 已设置为 {hwnd}")

            elif action == '激活(前台)':
                try:
                    from rpa.win_driver import WinDriver as _WD
                    if _WD.activate_window(hwnd):
                        self.log(f"\u2705 窗口 '{title}' 已激活(前台)")
                    else:
                        self.log(f"\u274c 窗口 '{title}' 激活失败")
                except Exception as e:
                    self.log(f"\u274c 窗口激活失败: {e}")

            elif action == '最大化':
                try:
                    WinAPI.user32.ShowWindow(hwnd, SW_MAXIMIZE)
                    self.log(f"\u2705 窗口 '{title}' 已最大化")
                except Exception as e:
                    self.log(f"\u274c 窗口最大化失败: {e}")

            elif action == '最小化':
                try:
                    WinAPI.user32.ShowWindow(hwnd, SW_MINIMIZE)
                    self.log(f"\u2705 窗口 '{title}' 已最小化")
                except Exception as e:
                    self.log(f"\u274c 窗口最小化失败: {e}")

            elif action == '置顶':
                try:
                    WinAPI.user32.SetWindowPos(
                        hwnd, HWND_TOPMOST,
                        0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
                    )
                    self.log(f"\u2705 窗口 '{title}' 已置顶")
                except Exception as e:
                    self.log(f"\u274c 窗口置顶失败: {e}")

            elif action == '取消置顶':
                try:
                    WinAPI.user32.SetWindowPos(
                        hwnd, HWND_NOTOPMOST,
                        0, 0, 0, 0,
                        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
                    )
                    self.log(f"\u2705 窗口 '{title}' 已取消置顶")
                except Exception as e:
                    self.log(f"\u274c 窗口取消置顶失败: {e}")

        except Exception as e:
            self.log(f"\u274c 窗口操作异常: {e}")

    def run(self) -> None:
        """执行引擎主入口。"""
        # 执行前最小化主窗口
        self.ui_queue.put({"type": "minimize_gui"})
        time.sleep(OVERLAY_HIDE_DELAY)  # 等待窗口最小化
        try:
            self._dispatch_steps(self.flow)
            self.log("\U0001f4ca 执行完成")
        except StopException:
            # 捕获停止异常，正常退出
            self.log("\U0001f6d1 流程已停止")
        except Exception as e:
            if str(e) == "STOP":
                self.log("\U0001f6d1 已停止")
            else:
                self.log(f"\u274c 异常: {e}")
                logger.error(f"执行异常: {e}", exc_info=True)
        finally:
            # 执行完后恢复主窗口
            self.ui_queue.put({"type": "show_gui"})
            self.ui_queue.put({"type": "done"})

    def _dispatch_steps(self, steps: List[Dict[str, Any]]) -> None:
        """
        按顺序分发执行步骤列表。

        Args:
            steps: 步骤列表
        """
        idx = 0
        while idx < len(steps):
            self.check_stop()
            step = steps[idx]

            # 处理跳转节点
            if step['type'] == '跳转':
                target_label = step['params'].get('target_label', '').strip()
                if not target_label:
                    self.log("\u274c 跳转节点错误：目标标签为空")
                    idx += 1
                    continue

                label_pos = LabelManager.get_label_position(
                    self.flow, target_label)
                if not label_pos:
                    self.log(
                        f"\u274c 跳转失败：未找到标签「{target_label}」")
                    idx += 1
                    continue

                self.log(
                    f"\U0001f680 跳转到标签「{target_label}」"
                    f"（路径：{label_pos['path']}）")

                current_list = steps
                target_list = label_pos['list']

                if current_list is target_list:
                    idx = label_pos['index'] + 1
                else:
                    self.log("\u26a0\ufe0f 标签在子流程中，将进入子流程执行")
                    self._execute_node(step)
                    idx += 1
            else:
                self._execute_node(step)
                idx += 1

    def _execute_node(self, step: Dict[str, Any]) -> None:
        """
        执行单个步骤节点。

        Args:
            step: 步骤数据字典
        """
        t = step['type']
        p = step.get('params', {})
        use_bg = p.get('use_bg', False)
        hwnd = self.runtime_bg_hwnd if use_bg else None

        # 后台模式下 HWND 为空时：尝试恢复后台窗口
        # 最多重试 BG_WINDOW_RETRY_COUNT 次，每次间隔 BG_WINDOW_RETRY_INTERVAL
        # 如果重试后仍找不到窗口，自动降级为前台模式继续执行（不弹窗，不跳过）
        # 例外：窗口操作步骤本身就是用来设置后台 HWND 的，必须放行
        if t != '窗口' and use_bg and hwnd is None:
            if self.runtime_bg_title:
                retry_hwnd = None
                for attempt in range(BG_WINDOW_RETRY_COUNT):
                    self.check_stop()
                    retry_hwnd = find_window_by_title(self.runtime_bg_title)
                    if retry_hwnd:
                        break
                    time.sleep(BG_WINDOW_RETRY_INTERVAL)

                if retry_hwnd:
                    self.runtime_bg_hwnd = retry_hwnd
                    hwnd = retry_hwnd
                    self.log(
                        f"\U0001f527 恢复后台窗口: {self.runtime_bg_title} "
                        f"(HWND: {retry_hwnd}，尝试{attempt + 1}次)")
                else:
                    self.log(
                        f"\u26a0\ufe0f 后台窗口 '{self.runtime_bg_title}' "
                        f"未找到（尝试{BG_WINDOW_RETRY_COUNT}次），"
                        f"降级为前台模式执行步骤【{t}】")
                    # hwnd 保持 None，后续走前台代码路径
            else:
                self.log(f"\u26a0\ufe0f 无后台窗口标题，降级为前台模式执行步骤【{t}】")
                # hwnd 保持 None，后续走前台代码路径
        
        # 调试日志
        if t in ['点击', '等待', 'OCR']:
            self.log(f"\U0001f9ea 调试: use_bg={use_bg}, runtime_bg_hwnd={self.runtime_bg_hwnd}, hwnd={hwnd}")

        # 解析区域
        region: Optional[Tuple[int, ...]] = None
        reg_str = p.get('input_region_var', '').strip()
        if reg_str:
            if reg_str in self.runtime_vars:
                region = self.runtime_vars[reg_str]
            elif ',' in reg_str:
                region = tuple(map(int, reg_str.split(',')))

        if t == '延时':
            self._exec_delay(p)

        elif t == '点击':
            self._exec_click(p, hwnd, region)

        elif t == '等待':
            self._exec_wait(p, hwnd, region, step)

        elif t == '输入':
            self._exec_input(p, hwnd, region)

        elif t == '按键':
            self._exec_key(p, hwnd, region)

        elif t == '条件分支':
            self._exec_branch(p, hwnd, region, step)

        elif t == '普通循环':
            self._exec_loop(p, hwnd, region, step)

        elif t == '暂停':
            self._exec_pause(p)

        elif t == '退出':
            self._exec_exit()

        elif t == '滚轮':
            self._exec_scroll(p, hwnd)

        elif t == '窗口':
            self._exec_window(p)

        elif t == '文件':
            self._exec_file(p)

        elif t == 'OCR':
            self._exec_ocr(p, step)

        elif t == '数据循环':
            self._exec_data_loop(p, step)

        elif t == '变量计算':
            self._exec_var_calc(p)

        elif t == '变量管理':
            self._exec_var_manager(p)

    def _exec_delay(self, p: Dict[str, Any]) -> None:
        """执行延时节点。"""
        time.sleep(float(p.get('seconds', 1)))

    def _exec_click(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
    ) -> None:
        """执行点击节点。"""
        find_nth = int(p.get('find_nth', 1))
        confidence = float(p.get('confidence', 0.75))
        timeout = float(p.get('timeout', 5))
        color_sensitive = bool(p.get('color_sensitive', False))
        off_x = int(p.get('offset_x', 0))
        off_y = int(p.get('offset_y', 0))
        template = p.get('template', '')

        # 如果没有图片模板，直接使用区域中心点（快速路径）
        if not template:
            if region:
                x1, y1, x2, y2 = region
                center_x = (x1 + x2) // 2 + off_x
                center_y = (y1 + y2) // 2 + off_y
                btn = p.get('button', '左键单击')
                # 快速路径下"仅识别"无意义（区域中心坐标已知），自动降级为左键单击
                if btn == '仅识别':
                    btn = '左键单击'

                if btn == '仅识别':
                    self.log(f"\u2705 仅识别（区域中心）: ({center_x}, {center_y})")
                else:
                    btn_map = BUTTON_MAP.get(btn, 'left')
                    button = btn_map.replace('double', 'left') \
                        if btn_map == 'double' else btn_map
                    double = (btn_map == 'double')
                    # 后台模式：屏幕坐标 → 客户区坐标
                    if hwnd:
                        from rpa.win_driver import WinDriver as _WD
                        cx, cy = _WD.screen_to_client(hwnd, center_x, center_y)
                    else:
                        cx, cy = center_x, center_y
                    ActionHelper.click_action(hwnd, cx, cy, button=button, double=double)
                    self.log(f"\u2705 {btn}（区域中心）: ({center_x}, {center_y}){f' → 客户区({cx},{cy})' if hwnd else ''}")
            else:
                self.log("\u274c 未指定图片模板且无识别区域")
            return

        # 有图片模板时的超时重试机制（外层循环避免递归栈溢出）
        while True:
            start_time = time.time()
            pos: Optional[Tuple[int, int]] = None
            while time.time() - start_time < timeout:
                self.check_stop()
                self.vision.confidence = confidence
                pos = self.vision.find_image(
                    template, hwnd=hwnd, region=region,
                    find_nth=find_nth, color_sensitive=color_sensitive)
                if pos:
                    break
                time.sleep(DEFAULT_RETRY_INTERVAL)

            if pos:
                target = (pos[0] + off_x, pos[1] + off_y)
                btn = p.get('button', '左键单击')

                if btn == '仅识别':
                    self.log(
                        f"\u2705 仅识别: ({int(target[0])}, {int(target[1])})")
                else:
                    btn_map = BUTTON_MAP.get(btn, 'left')
                    button = btn_map.replace('double', 'left') \
                        if btn_map == 'double' else btn_map
                    double = (btn_map == 'double')
                    ActionHelper.click_action(
                        hwnd, target[0], target[1],
                        button=button, double=double)
                    self.log(
                        f"\u2705 {btn}: ({int(target[0])}, {int(target[1])})")
                return
            elif region:
                x1, y1, x2, y2 = region
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                target = (center_x + off_x, center_y + off_y)
                btn = p.get('button', '左键单击')
                # 兜底路径下"仅识别"无意义（区域中心坐标已知），自动降级为左键单击
                if btn == '仅识别':
                    btn = '左键单击'

                if btn == '仅识别':
                    self.log(
                        f"\u2705 仅识别（中心）: "
                        f"({int(target[0])}, {int(target[1])})")
                else:
                    btn_map = BUTTON_MAP.get(btn, 'left')
                    button = btn_map.replace('double', 'left') \
                        if btn_map == 'double' else btn_map
                    double = (btn_map == 'double')
                    # 后台模式：屏幕坐标 → 客户区坐标
                    if hwnd:
                        from rpa.win_driver import WinDriver as _WD
                        cx, cy = _WD.screen_to_client(hwnd, target[0], target[1])
                    else:
                        cx, cy = target[0], target[1]
                    ActionHelper.click_action(
                        hwnd, cx, cy,
                        button=button, double=double)
                    self.log(
                        f"\u2705 {btn}（中心）: "
                        f"({int(target[0])}, {int(target[1])}){f' → 客户区({cx},{cy})' if hwnd else ''}")
                return
            else:
                self.log("\u274c 未找到点击目标且无搜索范围")
                template_name = p.get('template', '未指定')
                region_info = (
                    f"({region[0]}, {region[1]}, {region[2]}, {region[3]})"
                    if region else "全屏")
                timeout_msg = (
                    f"点击超时！未找到【{template_name}】\n"
                    f"区域：{region_info} | 超时：{timeout}秒")
                user_choice = self.show_timeout_dialog(timeout_msg)
                if user_choice == "retry":
                    self.log("\U0001f504 用户选择重试...")
                    continue
                else:
                    self.log("\u23ed 用户选择跳过该节点")
                    return

    def _exec_wait(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
        step: Dict[str, Any],
    ) -> None:
        """执行等待节点（循环包装以避免递归栈溢出）。"""
        while True:
            self._exec_wait_retry = False
            self._exec_wait_inner(p, hwnd, region, step)
            if not self._exec_wait_retry:
                return

    def _exec_wait_inner(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
        step: Dict[str, Any],
    ) -> None:
        """执行等待节点（内部实现）。"""
        wait_type = p.get('wait_type', '等待图片')

        if wait_type == '等待窗口':
            timeout = float(p.get('timeout_window', DEFAULT_TIMEOUT))
            window_title = p.get('window_title', '')
            window_condition = p.get('window_condition', '窗口出现')

            if not window_title:
                self.log("\u274c 等待窗口节点错误：窗口标题为空")
                user_choice = self.show_timeout_dialog(
                    "等待窗口节点执行失败：窗口标题为空！\n"
                    "请输入窗口标题后再执行。\n"
                    "点击'重试'继续等待，点击'跳过'进入下一节点")
                if user_choice == "retry":
                    self.log("\U0001f504 用户选择重试...")
                    self._exec_wait_retry = True
                    return
                else:
                    self.log("\u23ed 用户选择跳过该节点")
                    return

            wait_type_code = (
                'appear' if window_condition == '窗口出现' else 'disappear')
            condition_text = (
                "出现" if window_condition == '窗口出现' else "消失")
            self.log(
                f"\U0001f9fa 等待窗口开始: 标题='{window_title}', "
                f"条件='{condition_text}', 超时={timeout}秒")

            success = WinDriver.wait_for_window(
                window_title, int(timeout), wait_type_code)

            if success:
                self.log(
                    f"\u2705 等待窗口成功: 窗口'{window_title}'"
                    f"已{condition_text}")
            else:
                self.log(
                    f"\u23f0 等待窗口超时: 窗口'{window_title}'"
                    f"未{condition_text}")
                user_choice = self.show_timeout_dialog(
                    f"等待窗口节点执行超时！\n"
                    f"窗口标题：{window_title}\n"
                    f"等待条件：窗口{condition_text}\n"
                    f"超时时间：{timeout}秒\n\n"
                    f"点击'重试'继续等待，点击'跳过'进入下一节点")
                if user_choice == "retry":
                    self.log("\U0001f504 用户选择重试...")
                    self._exec_wait_retry = True
                    return
                else:
                    self.log("\u23ed 用户选择跳过该节点")
                    return
            return

        # 图片等待模式
        timeout = float(p.get('timeout', 10))
        template_path = p.get('template', '')
        region_info = region if region else "全屏"
        hwnd_info = f"窗口句柄: {hwnd}" if hwnd else "前台窗口"

        if not template_path:
            self.log("\u274c 等待节点错误：模板图片路径为空")
            user_choice = self.show_timeout_dialog(
                "等待节点执行失败：模板图片路径为空！\n"
                "点击'重试'继续等待，点击'跳过'进入下一节点")
            if user_choice == "retry":
                self.log("\U0001f504 用户选择重试...")
                self._exec_wait_retry = True
                return
            else:
                self.log("\u23ed 用户选择跳过该节点")
                return

        # 检查模板文件是否存在
        base_template_name = (
            template_path[:-4] if template_path.endswith('.png')
            else template_path)
        template_full_path = os.path.join(
            self.vision.templates_dir, f"{base_template_name}.png")
        if not os.path.exists(template_full_path):
            self.log(
                f"\u274c 等待节点错误：模板图片不存在 - "
                f"{base_template_name}.png")
            user_choice = self.show_timeout_dialog(
                f"等待节点执行失败：模板图片不存在！\n"
                f"模板路径：{template_full_path}\n"
                f"点击'重试'继续等待，点击'跳过'进入下一节点")
            if user_choice == "retry":
                self.log("\U0001f504 用户选择重试...")
                self._exec_wait_retry = True
                return
            else:
                self.log("\u23ed 用户选择跳过该节点")
                return

        self.log(
            f"\U0001f50d 等待节点开始执行 - 超时时间: {timeout}秒, "
            f"目标图片: {template_path}, 区域: {region_info}, {hwnd_info}")

        start = time.time()
        retry = time.time()
        attempt_count = 0
        while True:
            self.check_stop()
            attempt_count += 1
            try:
                current_time = time.time()
                elapsed_time = current_time - start
                retry_time = current_time - retry
                remaining_time = timeout - retry_time
                self.log(
                    f"\U0001f4f8 找图尝试 #{attempt_count} - "
                    f"已耗时: {elapsed_time:.2f}秒, "
                    f"剩余时间: {remaining_time:.2f}秒")
                if self.vision.find_image(
                    template_path, hwnd=hwnd, region=region):
                    self.log(
                        f"\U0001f389 找图成功! "
                        f"耗时: {current_time - start:.2f}秒")
                    self.log("\u2705 等待成功")
                    break
                if remaining_time <= 0:
                    self.log("\u23f0 超时时间已到，等待用户选择重试或跳过...")
                    timeout_msg = (
                        f"等待超时！未找到【{template_path}】\n"
                        f"区域：{region_info} | 窗口：{hwnd_info} | "
                        f"超时：{timeout}秒 | 尝试：{attempt_count}次")
                    user_choice = self.show_timeout_dialog(timeout_msg)
                    if user_choice == "skip":
                        self.log("\u23ed 用户选择跳过该节点")
                        break
                    elif user_choice == "retry":
                        self.log("\U0001f504 用户选择重试...")
                        retry = time.time()
                time.sleep(DEFAULT_RETRY_INTERVAL)
            except Exception as e:
                self.log(f"\u26a0 找图尝试出错: {e}")
                time.sleep(DEFAULT_RETRY_INTERVAL)

    def _exec_input(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
    ) -> None:
        """执行输入节点。"""
        # 获取输入类型
        input_type = p.get('input_type', '直接输入')

        # 根据输入类型获取文本
        if input_type == '直接输入':
            txt = self._resolve_text_vars(p.get('text', ''))
        else:
            # 数据变量输入
            data_name = p.get('data_name', '数据')
            field_name = p.get('field_name', '')
            data_var = f"{data_name}.{field_name}"
            if data_var and data_var in self.runtime_vars:
                txt = self.runtime_vars[data_var]
            else:
                txt = data_var  # 如果变量不存在，使用变量名作为默认值

        pos_var = p.get('pos_var', '')
        click_pos: Optional[Tuple[int, int]] = None
        if pos_var:
            region_val: Any = None
            # 1. 先查运行时变量（OCR/vision/区域画框产出屏幕坐标）
            if pos_var in self.runtime_vars:
                region_val = self.runtime_vars[pos_var]
            # 2. 再尝试逗号分隔坐标字符串（区域画框直接产出屏幕坐标）
            elif ',' in pos_var:
                region_val = tuple(map(int, pos_var.split(',')))

            if region_val:
                if len(region_val) == 4:
                    click_pos = (
                        (region_val[0] + region_val[2]) // 2,
                        (region_val[1] + region_val[3]) // 2,
                    )
                elif len(region_val) == 2:
                    click_pos = (region_val[0], region_val[1])
            # 后台模式：区域画框/OCR/vision 统一产出屏幕坐标 → 转客户区
            if click_pos and hwnd:
                from rpa.win_driver import WinDriver as _WD
                click_pos = _WD.screen_to_client(hwnd, click_pos[0], click_pos[1])
        # 后台模式：自动激活窗口到前台（文本输入必须走系统级注入）
        if hwnd:
            from rpa.win_driver import WinDriver as _WD
            _WD.activate_window(hwnd)
        ActionHelper.send_text_action(hwnd, txt, click_pos)
        self.log(f"\u2328 输入: {txt}")

    def _exec_key(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
    ) -> None:
        """执行按键节点。"""
        key = p.get('key', '')
        if not key:
            return
        
        # 如果指定了模板图片，先识别位置并点击获取焦点
        template = p.get('template', '')
        has_focus_click = False
        if template and hwnd:
            from rpa.win_driver import WinDriver
            confidence = float(p.get('confidence', 0.75))
            timeout = float(p.get('timeout', 5))
            color_sensitive = bool(p.get('color_sensitive', False))
            find_nth = int(p.get('find_nth', 1))
            off_x = int(p.get('offset_x', 0))
            off_y = int(p.get('offset_y', 0))
            
            self.vision.confidence = confidence
            pos: Optional[Tuple[int, int]] = None
            start_time = time.time()
            while time.time() - start_time < timeout:
                self.check_stop()
                pos = self.vision.find_image(
                    template, hwnd=hwnd, region=region,
                    find_nth=find_nth, color_sensitive=color_sensitive)
                if pos:
                    pos = (pos[0] + off_x, pos[1] + off_y)
                    break
                time.sleep(WAIT_RETRY_INTERVAL)
            
            if pos:
                ActionHelper.click_action(hwnd, pos[0], pos[1])
                time.sleep(POST_CLICK_DELAY)
                has_focus_click = True
        
        # 后台模式下，如果没有模板图片，需要先点击窗口获取焦点
        # （很多程序需要鼠标点击才会激活键盘焦点，仅 SetFocus 不够）
        if hwnd and not has_focus_click:
            from rpa.win_driver import WinDriver
            # 自动激活窗口到前台（键盘输入必须走系统级注入）
            if not WinDriver.activate_window(hwnd):
                self.log("⚠ 窗口激活失败，按键可能无效")
            if region and len(region) == 4:
                # 有查找区域：点击区域中心
                x1, y1, x2, y2 = region
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            else:
                # 无查找区域：点击客户区中心（兜底）
                try:
                    cx, cy = WinDriver.get_client_size(hwnd)
                    cx //= 2
                    cy //= 2
                except Exception:
                    cx, cy = 0, 0
            if cx > 0 or cy > 0:
                ActionHelper.click_action(hwnd, cx, cy)
                time.sleep(POST_CLICK_DELAY)
        
        ActionHelper.send_keys_action(hwnd, key)
        self.log(f"⌨ 按键: {key}")

    def _exec_branch(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
        step: Dict[str, Any],
    ) -> None:
        """执行分支节点。"""
        condition_type = p.get('condition_type', '图片条件')

        if condition_type == '变量条件':
            # 变量条件分支
            self._exec_var_branch(p, step)
        else:
            # 图片条件分支
            self._exec_image_branch(p, hwnd, region, step)

    def _exec_var_branch(
        self,
        p: Dict[str, Any],
        step: Dict[str, Any],
    ) -> None:
        """执行变量条件分支。"""
        var_name = p.get('var_condition_name', '')
        var_op = p.get('var_condition_op', '等于')
        var_value = p.get('var_condition_value', '')
        var_result = p.get('var_condition_result', '执行真分支')

        self.log(
            f"\U0001f50d 变量分支判断开始: 变量='{var_name}', "
            f"操作='{var_op}', 比较值='{var_value}'")

        if not var_name:
            self.log("\u274c 变量条件错误：未选择变量")
            return

        # 获取变量值
        var_val = self.runtime_vars.get(var_name, '')

        # 执行比较
        result = self._compare_values(var_val, var_op, var_value)

        # 根据用户设置的result参数决定执行哪个分支
        # var_result: "执行真分支" 表示条件为真时执行true分支，"执行假分支" 表示条件为真时执行false分支
        if var_result == "执行真分支":
            is_true = result
        else:
            is_true = not result

        self.log(
            f"   变量分支判断完成: 变量{var_name}={repr(var_val)}, "
            f"比较结果={result}, 执行{'true' if is_true else 'false'}分支")
        self._dispatch_steps(
            step['true'] if is_true else step['false'])

    def _compare_values(
        self,
        actual: Any,
        op: str,
        expected: str,
    ) -> bool:
        """比较两个值。"""
        # 处理空值比较
        if op == '为空':
            return actual is None or str(actual).strip() == ''
        if op == '不为空':
            return actual is not None and str(actual).strip() != ''

        # 转换为字符串进行比较
        actual_str = str(actual) if actual is not None else ''
        expected_str = str(expected) if expected is not None else ''

        # 包含操作
        if op == '包含':
            return expected_str in actual_str

        # 尝试数值比较
        try:
            actual_num = float(actual_str)
            expected_num = float(expected_str)
            if op == '等于':
                return actual_num == expected_num
            elif op == '不等于':
                return actual_num != expected_num
            elif op == '大于':
                return actual_num > expected_num
            elif op == '小于':
                return actual_num < expected_num
            elif op == '大于等于':
                return actual_num >= expected_num
            elif op == '小于等于':
                return actual_num <= expected_num
        except (ValueError, TypeError):
            # 数值比较失败，使用字符串比较
            pass

        # 字符串比较
        if op == '等于':
            return actual_str == expected_str
        elif op == '不等于':
            return actual_str != expected_str
        elif op == '大于':
            return actual_str > expected_str
        elif op == '小于':
            return actual_str < expected_str
        elif op == '大于等于':
            return actual_str >= expected_str
        elif op == '小于等于':
            return actual_str <= expected_str

        return False

    def _exec_image_branch(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
        step: Dict[str, Any],
    ) -> None:
        """执行图片条件分支。"""
        cond = p.get('condition', '找到图片时')
        timeout = float(p.get('timeout', 0.5))
        self.vision.confidence = float(p.get('confidence', DEFAULT_CONFIDENCE))
        start = time.time()
        found = False
        color_sensitive = bool(p.get('color_sensitive', True))
        template = p.get('template')
        self.log(
            f"\U0001f50d 图片分支判断开始: 条件='{cond}', "
            f"模板='{template}', 超时={timeout}秒, "
            f"置信度={self.vision.confidence}, 颜色敏感={color_sensitive}")

        search_count = 0
        while time.time() - start < timeout:
            self.check_stop()
            res = self.vision.find_image(
                template, hwnd=hwnd, region=region,
                color_sensitive=color_sensitive)
            search_count += 1
            if (res is not None) if cond == '找到图片时' \
                    else (res is None):
                found = True
                break
            time.sleep(DEFAULT_RETRY_INTERVAL)

        self.log(
            f"   分支判断完成: found={found}, "
            f"搜索次数={search_count}, "
            f"耗时={time.time() - start:.2f}秒")
        is_true = found if cond == '找到图片时' else not found
        self.log(f"   执行分支: {'true' if is_true else 'false'}")
        self._dispatch_steps(
            step['true'] if is_true else step['false'])

    def _exec_loop(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
        step: Dict[str, Any],
    ) -> None:
        """执行循环节点。"""
        lt = p.get('loop_type')
        exit_condition_type = p.get('exit_condition_type', '无')

        # 获取退出条件区域（优先使用退出条件专用区域，否则使用循环区域）
        exit_region = region
        if p.get('exit_region_var'):
            reg_str = p.get('exit_region_var', '')
            if reg_str in self.runtime_vars:
                exit_region = self.runtime_vars[reg_str]
            elif ',' in reg_str:
                exit_region = tuple(map(int, reg_str.split(',')))

        if lt == '按次数':
            loop_count = int(p.get('value', 1))
            executed = 0
            self.log(f"\U0001f504 按次数循环开始: 次数={loop_count}")
            for i in range(loop_count):
                self.check_stop()
                # 检查退出条件
                if self._check_exit_condition(p, hwnd, exit_region):
                    self.log(f"\U0001f6d1 退出条件满足，退出循环（第{i + 1}次）")
                    break
                executed += 1
                self.log(f"   循环执行 #{i + 1}/{loop_count}")
                self._dispatch_steps(step['body'])
            self.log(f"\u2705 按次数循环完成: 共执行{executed}次")
        else:
            # 条件循环
            timeout = float(p.get('timeout', 30))
            self.vision.confidence = float(p.get('confidence', DEFAULT_CONFIDENCE))
            color_sensitive = p.get('color_sensitive', True)
            loop_condition = p.get('value', '')

            if not loop_condition:
                self.log(
                    "\u274c 循环节点错误：循环条件图片路径为空，终止循环")
                return

            base_template_name = (
                loop_condition[:-4]
                if loop_condition.endswith('.png') else loop_condition)
            template_full_path = os.path.join(
                self.vision.templates_dir, f"{base_template_name}.png")
            if not os.path.exists(template_full_path):
                self.log(
                    f"\u274c 循环节点错误：循环条件图片不存在 - "
                    f"{base_template_name}.png，终止循环")
                return

            self.log(
                f"\U0001f504 条件循环开始: 类型={lt}, "
                f"条件={loop_condition}, 超时={timeout}秒")

            start_time = time.time()
            loop_count = 0
            while True:
                self.check_stop()
                res = self.vision.find_image(
                    loop_condition, hwnd=hwnd, region=region,
                    color_sensitive=color_sensitive)
                condition_met = (res is not None) \
                    if lt == '找到图片时' else (res is None)
                if condition_met:
                    self.log(
                        f"   搜图{loop_condition}成功，执行循环体")
                    self._dispatch_steps(step['body'])
                    self.log("   循环体执行完成")
                    # 每次循环体执行完后检查退出条件
                    if self._check_exit_condition(p, hwnd, exit_region):
                        self.log("\U0001f6d1 退出条件满足，退出循环")
                        break
                else:
                    loop_count += 1
                    self.log(
                        f"   搜图{loop_condition}不成功，"
                        f"开始第{loop_count}次循环搜图")
                    if timeout > 0 \
                            and time.time() - start_time > timeout:
                        self.log(
                            f"   搜图{loop_condition}不成功，"
                            f"已超时{timeout}秒，退出循环")
                        region_info = (
                            f"({region[0]}, {region[1]}, "
                            f"{region[2]}, {region[3]})"
                            if region else "全屏")
                        timeout_msg = (
                            f"循环超时！未满足【{lt}】\n"
                            f"图片：【{loop_condition}】\n"
                            f"区域：{region_info} | "
                            f"超时：{timeout}秒 | "
                            f"检查：{loop_count}次")
                        user_choice = self.show_timeout_dialog(timeout_msg)
                        if user_choice == "retry":
                            self.log("\U0001f504 用户选择重试...")
                            start_time = time.time()
                            loop_count = 0
                            continue
                        else:
                            self.log("\u23ed 用户选择跳过该节点")
                            break
            self.log(
                f"\u2705 条件循环完成: 共执行{loop_count}次检查")

    def _check_exit_condition(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
    ) -> bool:
        """检查退出条件是否满足。返回True表示满足退出条件。"""
        exit_condition_type = p.get('exit_condition_type', '无')

        if exit_condition_type == '无':
            return False

        if exit_condition_type == '图片条件':
            return self._check_image_exit_condition(p, hwnd, region)
        elif exit_condition_type == '变量条件':
            return self._check_var_exit_condition(p)

        return False

    def _check_image_exit_condition(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
        region: Optional[Tuple[int, ...]],
    ) -> bool:
        """检查图片退出条件。"""
        exit_template = p.get('exit_template', '')
        if not exit_template:
            return False

        exit_confidence = float(p.get('exit_confidence', DEFAULT_CONFIDENCE))
        exit_color_sensitive = bool(p.get('exit_color_sensitive', True))
        exit_cond = p.get('exit_condition', '找到图片时退出')

        # 设置查找置信度
        original_confidence = self.vision.confidence
        self.vision.confidence = exit_confidence

        res = self.vision.find_image(
            exit_template, hwnd=hwnd, region=region,
            color_sensitive=exit_color_sensitive)

        # 恢复原始置信度
        self.vision.confidence = original_confidence

        found = res is not None
        should_exit = (found and exit_cond == '找到图片时退出') or \
                       (not found and exit_cond == '未找到图片时退出')

        if should_exit:
            self.log(f"\U0001f50d 退出条件检测: 找到{exit_template}，满足退出条件")
        return should_exit

    def _check_var_exit_condition(self, p: Dict[str, Any]) -> bool:
        """检查变量退出条件。"""
        var_name = p.get('exit_var_name', '')
        var_op = p.get('exit_var_op', '等于')
        var_value = p.get('exit_var_value', '')

        if not var_name:
            return False

        var_val = self.vars.get(var_name, '')
        result = self._compare_values(var_val, var_op, var_value)

        if result:
            self.log(f"\U0001f50d 退出条件检测: 变量{var_name}={repr(var_val)}满足条件，退出循环")
        return result

    def _resolve_text_vars(self, text: str) -> str:
        """解析文本中的 {变量名} 占位符，替换为运行时变量的值。

        支持简单的变量名（如 {账单号}）和点号分隔的嵌套变量名
        （如 {数据.账单号}），均从 self.runtime_vars 字典中查找。
        如果变量不存在，保留原始占位符不变。

        Args:
            text: 包含 {变量名} 占位符的原始文本

        Returns:
            替换后的文本字符串
        """
        def replacer(match):
            var_name = match.group(1).strip()
            if not var_name:
                return match.group(0)
            if var_name in self.runtime_vars:
                return str(self.runtime_vars[var_name])
            return match.group(0)  # 变量不存在时保持原样
        return re.sub(r'\{([^}]+)\}', replacer, text)

    def _exec_pause(self, p: Dict[str, Any]) -> None:
        """执行暂停节点。"""
        pause_msg = p.get('pause_msg', '流程已暂停，请手动确认...')
        pause_msg = self._resolve_text_vars(pause_msg)
        self.log(f"\u23f8 暂停: {pause_msg}")
        user_choice = self.show_pause_dialog(pause_msg)

        if user_choice == 'Yes':
            self.log("\u2705 用户选择继续执行")
        else:
            self.log("\U0001f6d1 用户选择停止运行")
            self.stop_event.set()
            raise Exception("STOP")

    def _exec_exit(self) -> None:
        """执行退出节点，立即终止整个流程。"""
        self.log("\U0001f6d1 退出流程")
        self.stop_event.set()
        raise Exception("STOP")

    def _exec_scroll(
        self,
        p: Dict[str, Any],
        hwnd: Optional[int],
    ) -> None:
        """执行滚轮节点。"""
        direction = p.get('direction', '向下')
        clicks = int(p.get('clicks', 1))
        delta = clicks if direction == '向上' else -clicks
        use_bg = p.get('use_bg', False)
        self.log(
            f"\U0001f504 开始执行滚轮操作: 方向={direction}, "
            f"次数={clicks}, delta={delta}, "
            f"后台窗口={hwnd}, use_bg={use_bg}, "
            f"runtime_bg_hwnd={self.runtime_bg_hwnd}")

        if hwnd:
            try:
                client_width, client_height = WinDriver.get_client_size(hwnd)
                self.log(
                    f"   窗口客户区大小: {client_width}x{client_height}")
                if client_width > 0 and client_height > 0:
                    center_x, center_y = (
                        client_width // 2, client_height // 2)
                    self.log(
                        f"   使用中心坐标: ({center_x}, {center_y}) "
                        f"执行后台滚轮")
                    WinDriver.scroll(hwnd, center_x, center_y, delta)
                    self.log(
                        f"\u2705 滚轮: {direction} {clicks}次 (后台执行)")
                else:
                    self.log(
                        "   无法获取有效客户区大小，切换到前台模式")
                    pyautogui.scroll(delta)
                    self.log(
                        f"\u2705 滚轮: {direction} {clicks}次 "
                        f"(前台执行-后台客户区获取失败)")
            except Exception as e:
                self.log(f"\u274c 后台滚轮执行失败: {e}")
                self.log(f"   错误详情: {traceback.format_exc()}")
                self.log("   切换为前台模式...")
                pyautogui.scroll(delta)
                self.log(
                    f"\u2705 滚轮: {direction} {clicks}次 (回退前台执行)")
        else:
            self.log("   没有指定后台窗口，使用前台模式")
            pyautogui.scroll(delta)
            self.log(
                f"\u2705 滚轮: {direction} {clicks}次 (前台执行)")

    def _exec_window(self, p: Dict[str, Any]) -> None:
        """执行窗口节点。"""
        title = p.get('window_title', '')
        action = p.get('window_action', '激活(前台)')
        if action in WINDOW_ACTIONS:
            self._execute_window_action(title, action)

    def _exec_file(self, p: Dict[str, Any]) -> None:
        """执行文件节点。"""
        file_path = p.get('file_path', '')
        if file_path:
            if os.path.exists(file_path):
                try:
                    os.startfile(file_path)
                    self.log(f"\u2705 打开文件: {file_path}")
                except Exception as e:
                    self.log(f"\u274c 打开文件失败: {e}")
            else:
                self.log(f"\u274c 文件不存在: {file_path}")
        else:
            self.log("\u274c 文件节点错误：文件路径为空")

    def _exec_ocr(
        self,
        p: Dict[str, Any],
        step: Dict[str, Any],
    ) -> None:
        """执行 OCR 节点（循环包装以避免递归栈溢出）。"""
        while True:
            self._exec_ocr_retry = False
            self._exec_ocr_inner(p, step)
            if not self._exec_ocr_retry:
                return

    def _exec_ocr_inner(
        self,
        p: Dict[str, Any],
        step: Dict[str, Any],
    ) -> None:
        """执行 OCR 节点（内部实现）。"""
        import pyautogui
        try:
            import requests
        except ImportError:
            self.log("\u274c OCR节点错误：缺少必要的库 (requests)")
            return

        # 获取关键字（支持直接输入和数据变量）
        keyword_input_type = p.get('keyword_input_type', '直接输入')
        if keyword_input_type == '直接输入':
            keyword = p.get('keyword', '').strip()
        else:
            # 数据变量输入
            keyword_data_name = p.get('keyword_data_name', '数据')
            keyword_field_name = p.get('keyword_field_name', '')
            # 从运行时变量获取数据变量值
            data_var_key = f"{keyword_data_name}.{keyword_field_name}"
            keyword = str(self.runtime_vars.get(data_var_key, '')).strip()

        ocr_url = p.get('ocr_url', OCR_DEFAULT_URL)
        offset_x = int(p.get('offset_x', 0))
        offset_y = int(p.get('offset_y', 0))
        click_action = p.get('click_action', '左键单击')
        timeout = float(p.get('timeout', 10))
        use_bg = p.get('use_bg', False)

        if not keyword:
            self.log("\u274c OCR节点错误：关键字为空")
            return

        # 处理区域（与图像识别节点逻辑一致）
        region: Optional[Tuple[int, ...]] = None
        region_var = p.get('input_region_var', '').strip()
        if region_var and region_var != '全屏':
            if region_var in self.runtime_vars:
                region = self.runtime_vars[region_var]
            elif ',' in region_var:
                # 直接是坐标字符串
                region = tuple(map(int, region_var.split(',')))

        # 获取窗口句柄
        hwnd: Optional[int] = None
        if use_bg:
            hwnd = self.runtime_bg_hwnd if hasattr(
                self, 'runtime_bg_hwnd') else None
            if not hwnd:
                self.log(
                    "\u26a0\ufe0f 警告：后台模式但未设置后台窗口，"
                    "将使用前台模式")

        self.log(
            f"\U0001f50d OCR识别开始: 关键字='{keyword}', "
            f"区域={region if region else '全屏'}, "
            f"超时={timeout}秒, 后台={hwnd is not None}")

        try:
            start_time = time.time()
            found = False

            while time.time() - start_time < timeout:
                self.check_stop()

                # 截取指定区域或全屏
                if region and len(region) == 4:
                    x1, y1, x2, y2 = region
                    screenshot_region = (x1, y1, x2 - x1, y2 - y1)
                else:
                    screenshot_region = None
                
                self.log(f"[DEBUG] screenshot_region = {screenshot_region}")

                # 截图
                if hwnd:
                    try:
                        import numpy as np
                        self.log(f"[DEBUG] 尝试后台截图: hwnd={hwnd}, region={region}")
                        screenshot_np = WinDriver.capture_window(hwnd, region)
                        self.log(f"[DEBUG] capture_window 返回: {screenshot_np is not None}, 形状={screenshot_np.shape if screenshot_np is not None else None}")
                        if screenshot_np is not None:
                            # 转换为 PIL Image（BGR -> RGB）
                            screenshot_np_rgb = cv2.cvtColor(
                                screenshot_np, cv2.COLOR_BGR2RGB)
                            screenshot = Image.fromarray(screenshot_np_rgb)
                            # 保存调试截图
                            debug_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                                    'debug_screenshot.png')
                            screenshot.save(debug_path)
                            self.log(f"[DEBUG] 后台截图已保存: {debug_path}, 尺寸={screenshot.width}x{screenshot.height}")
                        else:
                            self.log("\u26a0\ufe0f capture_window 返回 None，降级到前台截图")
                            screenshot = pyautogui.screenshot(
                                region=screenshot_region)
                    except Exception as e:
                        self.log(f"\u26a0\ufe0f 后台截图异常: {e}")
                        self.log(f"[DEBUG] 异常详情: {traceback.format_exc()}")
                        screenshot = pyautogui.screenshot(
                            region=screenshot_region)
                else:
                    # 前台模式截图
                    screenshot = pyautogui.screenshot(region=screenshot_region)
                    self.log(
                        f"[DEBUG] pyautogui 报告屏幕尺寸: "
                        f"{pyautogui.size()}")
                    # 保存截图用于调试
                    debug_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                            'debug_screenshot.png')
                    screenshot.save(debug_path)
                    self.log(f"[DEBUG] 截图已保存到: {debug_path}")

                # 转换为 base64
                buf = io.BytesIO()
                screenshot.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()

                # 调用 OCR API
                try:
                    response = requests.post(
                        ocr_url,
                        json={
                            "base64": b64,
                            "options": {
                                "ocr.language": "models/config_chinese.txt"
                            },
                        },
                        timeout=5,
                    )
                    ocr_result = response.json()

                    # 查找包含关键字的文本
                    for item in ocr_result.get("data", []):
                        text = item.get("text", "")
                        if keyword in text:
                            box = item.get("box", [])
                            if len(box) == 4:
                                # 计算 OCR 文字区域的中心点
                                ocr_center_x = (box[0][0] + box[2][0]) / 2
                                ocr_center_y = (box[0][1] + box[3][1]) / 2

                                # 坐标转换：OCR 坐标是相对于截取的图片的
                                if hwnd:
                                    # 后台模式：坐标是窗口客户区坐标
                                    # 加上 region 偏移（如果有），然后转换为屏幕坐标
                                    if region and len(region) == 4:
                                        # 有 region 时：OCR 坐标相对于截取的 region
                                        # 先加 region 偏移得到客户区坐标，再转屏幕坐标
                                        client_x = region[0] + ocr_center_x
                                        client_y = region[1] + ocr_center_y
                                        screen_x, screen_y = WinDriver.client_to_screen(
                                            hwnd, int(client_x), int(client_y))
                                        center_x = screen_x + offset_x
                                        center_y = screen_y + offset_y
                                    else:
                                        # 没有 region 时：OCR 坐标相对于客户区
                                        # 直接转换为屏幕坐标
                                        screen_x, screen_y = WinDriver.client_to_screen(
                                            hwnd, int(ocr_center_x), int(ocr_center_y))
                                        center_x = screen_x + offset_x
                                        center_y = screen_y + offset_y
                                else:
                                    # 前台模式：坐标是屏幕坐标
                                    # 加上 region 偏移（如果有）
                                    if region and len(region) == 4:
                                        center_x = (
                                            region[0] + ocr_center_x + offset_x)
                                        center_y = (
                                            region[1] + ocr_center_y + offset_y)
                                    else:
                                        center_x = ocr_center_x + offset_x
                                        center_y = ocr_center_y + offset_y
                                
                                # 调试日志：输出坐标转换详情
                                self.log(
                                    f"[DEBUG] 截图尺寸: {screenshot.width}x{screenshot.height}, "
                                    f"OCR坐标: ({ocr_center_x:.1f}, {ocr_center_y:.1f}), "
                                    f"region: {region}, "
                                    f"最终坐标: ({center_x:.1f}, {center_y:.1f})")

                                self.log(
                                    f"\u2705 OCR识别成功: 找到关键字 "
                                    f"'{keyword}' 在位置 "
                                    f"({int(center_x)}, {int(center_y)})")

                                if click_action != '仅识别':
                                    btn_map = BUTTON_MAP.get(
                                        click_action, 'left')
                                    button = btn_map.replace('double', 'left') \
                                        if btn_map == 'double' else btn_map
                                    double = (btn_map == 'double')
                                    # 后台模式点击需要客户区坐标
                                    if hwnd:
                                        click_x, click_y = WinDriver.screen_to_client(
                                            hwnd, int(center_x), int(center_y))
                                        # 使用 PostMessage 纯后台点击
                                        WinDriver.click(hwnd, click_x, click_y,
                                                        button='left', double=double)
                                    else:
                                        click_x, click_y = int(center_x), int(center_y)
                                        ActionHelper.click_action(
                                            hwnd, click_x, click_y,
                                            button=button, double=double)
                                    self.log(
                                        f"\u2705 {click_action}: "
                                        f"({int(center_x)}, "
                                        f"{int(center_y)})")
                                else:
                                    self.log(
                                        f"\u2705 仅识别: "
                                        f"({int(center_x)}, "
                                        f"{int(center_y)})")

                                # 保存坐标到输出变量
                                output_var = p.get('output_var', '')
                                if output_var:
                                    self.runtime_vars[output_var] = (
                                        center_x, center_y)
                                    self.log(
                                        f"\U0001f4e6 坐标已保存到变量: "
                                        f"{output_var}")

                                found = True
                                break

                    if found:
                        break

                except requests.exceptions.RequestException as e:
                    self.log(f"\u26a0\ufe0f OCR API请求失败: {e}")
                    time.sleep(OCR_ERROR_RETRY_DELAY)
                    continue
                except Exception as e:
                    self.log(f"\u26a0\ufe0f OCR处理异常: {e}")
                    time.sleep(OCR_ERROR_RETRY_DELAY)
                    continue

                time.sleep(DEFAULT_RETRY_INTERVAL)

            if not found:
                self.log(
                    f"\u274c OCR识别超时: 未找到关键字 '{keyword}'")
                timeout_msg = (
                    f"OCR识别超时！\n"
                    f"未找到关键字：【{keyword}】\n"
                    f"区域：{region if region else '全屏'} | "
                    f"超时：{timeout}秒\n\n"
                    f"请确保Umi-OCR服务已启动")
                user_choice = self.show_timeout_dialog(timeout_msg)
                if user_choice == "retry":
                    self.log("\U0001f504 用户选择重试...")
                    self._exec_ocr_retry = True
                else:
                    self.log("\u23ed 用户选择跳过该节点")

        except ImportError:
            self.log("\u274c OCR节点错误：缺少必要的库 (requests)")
        except Exception as e:
            self.log(f"\u274c OCR节点异常: {e}")
            self.log(f"   错误详情: {traceback.format_exc()}")

    def _exec_data_loop(
        self,
        p: Dict[str, Any],
        step: Dict[str, Any],
    ) -> None:
        """
        执行数据循环节点。

        读取 CSV/Excel 文件，逐行循环执行循环体内的步骤。
        支持向下取数和向上取数两种模式。
        支持图片条件和变量条件退出。

        Args:
            p: 节点参数字典
            step: 步骤数据字典
        """
        data_file = p.get('data_file', '')
        if not data_file:
            self.log("\u274c 数据循环节点错误：数据文件路径为空")
            return

        try:
            import pandas as pd

            # 读取数据文件
            if data_file.endswith('.csv'):
                df = pd.read_csv(data_file, encoding='utf-8')
            elif data_file.endswith('.xls') or data_file.endswith('.xlsx'):
                df = pd.read_excel(data_file)
            else:
                self.log(f"\u274c 数据循环节点错误：不支持的文件格式: {data_file}")
                return

            # 获取循环设置
            loop_mode = p.get('loop_mode', '向下取数')
            start_index = int(p.get('start_index', '1')) - 1  # 转换为0-based索引
            end_index_str = p.get('end_index', '')
            end_index = int(end_index_str) if end_index_str else len(df)

            # 验证索引范围
            if start_index < 0:
                start_index = 0
            if end_index > len(df):
                end_index = len(df)
            if start_index >= end_index:
                self.log("\u274c 数据循环节点错误：开始索引大于或等于结束索引")
                return

            # 生成索引序列
            if loop_mode == '向下取数':
                indices = range(start_index, end_index)
            else:  # 向上取数
                indices = range(end_index - 1, start_index - 1, -1)

            # 获取退出条件区域
            exit_region = None
            if p.get('exit_region_var'):
                reg_str = p.get('exit_region_var', '')
                if reg_str in self.runtime_vars:
                    exit_region = self.runtime_vars[reg_str]
                elif ',' in reg_str:
                    exit_region = tuple(map(int, reg_str.split(',')))

            self.log(
                f"\U0001f504 数据循环开始: 文件={data_file}, "
                f"模式={loop_mode}, 范围={start_index + 1}-{end_index}, "
                f"共{len(indices)}条记录"
            )

            # 执行循环
            executed = 0
            for i, idx in enumerate(indices):
                self.check_stop()

                # 检查退出条件
                if self._check_exit_condition(p, None, exit_region):
                    self.log(f"\U0001f6d1 退出条件满足，退出数据循环（第{i + 1}条记录）")
                    break

                executed += 1

                # 获取当前记录
                row = df.iloc[idx]

                # 获取数据名称
                data_name = p.get('data_name', '数据')

                # 保存当前记录为变量：数据名称.字段名
                data_vars = {}
                for col in df.columns:
                    var_name = f"{data_name}.{col}"
                    data_vars[var_name] = str(row[col])
                    # 保存到运行时变量
                    self.runtime_vars[var_name] = str(row[col])

                self.log(f"   执行记录 #{i + 1}/{len(indices)}: {data_vars}")

                # 执行循环体
                self._dispatch_steps(step['body'])

            self.log(f"\u2705 数据循环完成: 共执行{executed}条记录")

        except StopException:
            # 用户主动停止，正常退出
            self.log(f"\U0001f6d1 数据循环已停止")
            raise
        except Exception as e:
            self.log(f"\u274c 数据循环节点错误: {e}")
            self.log(f"   错误详情: {traceback.format_exc()}")

    def _exec_var_manager(self, p: Dict[str, Any]) -> None:
        """
        执行变量管理节点。

        将定义的变量添加到运行时变量中。

        Args:
            p: 节点参数字典
        """
        variables = p.get('variables', [])
        if not variables:
            self.log("\U0001f4cb 变量管理节点：无变量定义")
            return

        for var in variables:
            name = var.get('name', '').strip()
            var_type = var.get('type', '字符串')
            value = var.get('value', '')

            if not name:
                continue

            # 类型转换
            if var_type == '数字':
                try:
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                except (ValueError, TypeError):
                    value = 0
            elif var_type == '布尔':
                value = str(value).lower() in ('true', '1', '是')

            self.runtime_vars[name] = value

        self.log(
            f"\U0001f4cb 变量管理节点：已注册 {len(variables)} 个变量 "
            f"({', '.join(v.get('name', '') for v in variables if v.get('name'))})"
        )

    def _exec_var_calc(self, p: Dict[str, Any]) -> None:
        """
        执行变量计算节点。

        使用表达式对变量进行计算，支持四则运算和常用函数。
        计算结果保存到指定的变量名中。

        Args:
            p: 节点参数字典
        """
        import re

        expression = p.get('expression', '').strip()
        result_var_name = p.get('calc_result_var', '').strip()

        if not expression:
            self.log("\u274c 变量计算节点错误：表达式为空")
            return

        if not result_var_name:
            self.log("\u274c 变量计算节点错误：结果变量名为空")
            return

        try:
            # 获取运行时变量中的所有数值类型变量
            vars_context = {}
            for var_name, var_value in self.runtime_vars.items():
                vars_context[var_name] = var_value

            # 安全替换变量名
            # 匹配变量名的正则（中文、字母、数字、下划线）
            def replace_var(match):
                var_name = match.group(0)
                if var_name in vars_context:
                    value = vars_context[var_name]
                    # 尝试转换为数字
                    try:
                        if isinstance(value, str):
                            if '.' in value:
                                return str(float(value))
                            else:
                                return str(int(value))
                        return str(value)
                    except (ValueError, TypeError):
                        return repr(value)
                return var_name

            # 替换表达式中的变量名为实际值
            safe_expr = re.sub(r'[\u4e00-\u9fa5a-zA-Z_][\u4e00-\u9fa5a-zA-Z0-9_]*', replace_var, expression)

            # 使用 eval 计算表达式（仅允许数字运算和安全函数）
            allowed_names = {
                'abs': abs, 'round': round, 'min': min, 'max': max,
                'len': len, 'str': str, 'int': int, 'float': float,
                'bool': bool, 'list': list, 'tuple': tuple,
                'sum': sum, 'pow': pow, 'divmod': divmod,
                'True': True, 'False': False, 'None': None,
            }

            result = eval(safe_expr, {"__builtins__": {}}, allowed_names)

            # 保存结果到运行时变量
            self.runtime_vars[result_var_name] = result

            self.log(
                f"\U0001f522 变量计算完成: {expression} = {result} "
                f"(保存到 {result_var_name})"
            )

        except StopException:
            raise
        except ZeroDivisionError:
            self.log("\u274c 变量计算节点错误：除数不能为零")
        except NameError as e:
            self.log(f"\u274c 变量计算节点错误：表达式中使用了未定义的变量 - {e}")
        except SyntaxError as e:
            self.log(f"\u274c 变量计算节点错误：表达式语法错误 - {e}")
        except Exception as e:
            self.log(f"\u274c 变量计算节点错误：{e}")
            self.log(f"   表达式: {expression}")
            self.log(f"   错误详情: {traceback.format_exc()}")

"""
RPA自动化工具 - 测试工具模块

提供独立的测试窗口，用于测试图像识别、颜色验证和后台模式操作。
"""

import ctypes
import os
import time
import traceback
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk

import pyautogui

from rpa.win_api import WinAPI
from rpa.win_driver import WinDriver
from rpa.vision import VisionEngine
from rpa.action_helper import ActionHelper
from rpa.ui_pickers import UIPickers
from rpa.utils import ocr_find_keyword, ocr_get_texts
from rpa.config import (
    OVERLAY_HIDE_DELAY,
    WINDOW_ACTIONS,
    OCR_DEFAULT_URL,
)


def _find_window_by_title(title: str) -> Optional[int]:
    """
    根据窗口标题查找窗口句柄（模块级工具函数）。

    先尝试精确匹配，再尝试模糊匹配。

    Args:
        title: 窗口标题

    Returns:
        窗口句柄，未找到时返回 None
    """
    if not title:
        return None

    try:
        # 先尝试精确匹配
        hwnd = WinAPI.user32.FindWindowW(None, title)
        if hwnd and WinAPI.user32.IsWindow(hwnd):
            return hwnd

        # 如果精确匹配失败，尝试模糊匹配
        found_hwnd: Optional[int] = None

        def enum_windows_proc(hwnd: int, _: Any) -> bool:
            nonlocal found_hwnd
            try:
                if WinAPI.user32.IsWindowVisible(hwnd):
                    length = WinAPI.user32.GetWindowTextLengthW(hwnd) + 1
                    buff = ctypes.create_unicode_buffer(length)
                    WinAPI.user32.GetWindowTextW(hwnd, buff, length)
                    if length > 1 and title.lower() in buff.value.lower():
                        found_hwnd = hwnd
                        return False  # 停止枚举
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM,
        )
        WinAPI.user32.EnumWindows(WNDENUMPROC(enum_windows_proc), 0)
        return found_hwnd
    except Exception:
        return None


class TestLabWindow:
    """
    独立测试窗口类。

    用于测试图像识别、颜色验证和后台模式操作。
    """

    def __init__(
        self,
        root: tk.Tk,
        vision_engine: VisionEngine,
        templates_dir: str,
        step_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        初始化测试窗口。

        Args:
            root: 主窗口 Tk 实例
            vision_engine: VisionEngine 实例
            templates_dir: 模板图片目录路径
            step_params: 从选中步骤传入的参数（可选）
        """
        self.root = root
        self.vision = vision_engine
        self.templates_dir = templates_dir
        self.step_params = step_params  # 保存步骤参数
        self.runtime_bg_hwnd: Optional[int] = None  # 后台目标窗口句柄

    def open(self) -> None:
        """打开测试窗口。"""
        self.win = tk.Toplevel(self.root)
        self.win.title("测试识别 & 颜色验证")
        self.win.geometry("500x850")
        self.win.transient(self.root)
        self.win.resizable(False, False)

        # 变量绑定
        self.v_img = tk.StringVar()
        self.v_conf = tk.StringVar(value="0.95")
        self.v_nth = tk.StringVar(value="1")
        self.v_color_on = tk.BooleanVar(value=True)  # 颜色敏感
        self.v_verify_color = tk.BooleanVar(value=False)  # 颜色验证
        self.v_target_color = tk.StringVar()
        self.v_tolerance = tk.StringVar(value="10")
        self.v_region = tk.StringVar()
        self.v_use_bg = tk.BooleanVar(value=False)
        self.v_bg_title = tk.StringVar()
        self.v_action = tk.StringVar(value="仅识别")
        self.v_offset_x = tk.StringVar(value="0")
        self.v_offset_y = tk.StringVar(value="0")

        # 动作参数变量
        self.v_wheel_clicks = tk.StringVar(value="1")
        self.v_key = tk.StringVar(value="")
        self.v_text = tk.StringVar(value="")

        # OCR/文字模式变量
        self.v_ocr_mode = tk.BooleanVar(value=False)  # False=图片模式, True=文字模式
        self.v_ocr_keyword = tk.StringVar(value="")   # OCR识别关键字

        self._build_ui()

    def _build_ui(self) -> None:
        """构建测试窗口的 UI 界面。"""
        main_frame = tk.Frame(self.win, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Image / OCR ---
        recognition_group = tk.LabelFrame(main_frame, text="图片/文字识别", padx=5, pady=5)
        recognition_group.pack(fill=tk.X, pady=(0, 10))

        # 模式切换按钮
        mode_frame = tk.Frame(recognition_group)
        mode_frame.pack(fill=tk.X, pady=(0, 5))
        tk.Label(mode_frame, text="识别模式:", anchor=tk.W).pack(side=tk.LEFT)
        
        mode_toggle_frame = tk.Frame(mode_frame)
        mode_toggle_frame.pack(side=tk.LEFT, padx=10)
        
        img_mode_btn = tk.Radiobutton(
            mode_toggle_frame, text="图片", variable=self.v_ocr_mode,
            value=False, command=self._on_recognition_mode_change)
        img_mode_btn.pack(side=tk.LEFT, padx=(0, 10))
        text_mode_btn = tk.Radiobutton(
            mode_toggle_frame, text="文字(OCR)", variable=self.v_ocr_mode,
            value=True, command=self._on_recognition_mode_change)
        text_mode_btn.pack(side=tk.LEFT)

        # 图片输入区域
        self.img_input_frame = tk.Frame(recognition_group)
        self.img_input_frame.pack(fill=tk.X, pady=(0, 5))
        
        img_preview_container = tk.Frame(self.img_input_frame)
        img_preview_container.pack(fill=tk.X, padx=(0, 5))
        
        img_select_frame = tk.Frame(img_preview_container)
        img_select_frame.pack(side=tk.LEFT, fill=tk.Y, expand=False)
        self._add_test_row(
            img_select_frame, "图片:", self.v_img,
            lambda: self._sel_img(), show_preview=True,
        )

        # OCR文字输入区域
        self.ocr_input_frame = tk.Frame(recognition_group)
        # 默认隐藏，切换模式时显示
        
        ocr_keyword_row = tk.Frame(self.ocr_input_frame)
        ocr_keyword_row.pack(fill=tk.X, pady=2)
        tk.Label(ocr_keyword_row, text="识别的文字:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        tk.Entry(ocr_keyword_row, textvariable=self.v_ocr_keyword).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # OCR URL配置
        ocr_url_row = tk.Frame(self.ocr_input_frame)
        ocr_url_row.pack(fill=tk.X, pady=2)
        tk.Label(ocr_url_row, text="OCR服务:", width=10, anchor=tk.W).pack(side=tk.LEFT)
        self.v_ocr_url = tk.StringVar(value="http://127.0.0.1:1224/api/ocr")
        tk.Entry(ocr_url_row, textvariable=self.v_ocr_url, width=40).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # OCR识别结果文本框
        ocr_result_row = tk.Frame(self.ocr_input_frame)
        ocr_result_row.pack(fill=tk.BOTH, expand=True, pady=(5, 2))
        tk.Label(ocr_result_row, text="识别结果:", width=10, anchor=tk.W).pack(side=tk.LEFT, anchor=tk.N)
        # 创建识别结果文本框（支持多行和右键菜单）
        self.ocr_result_text = tk.Text(ocr_result_row, height=4, width=35, wrap=tk.WORD)
        self.ocr_result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        # 添加滚动条
        ocr_scroll = ttk.Scrollbar(ocr_result_row, command=self.ocr_result_text.yview)
        ocr_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.ocr_result_text.config(yscrollcommand=ocr_scroll.set)
        # 创建右键菜单（记事本常用功能）
        self.ocr_result_menu = tk.Menu(self.ocr_result_text, tearoff=0)
        self.ocr_result_menu.add_command(label="剪切", command=lambda: self.ocr_result_text.event_generate("<<Cut>>"))
        self.ocr_result_menu.add_command(label="复制", command=lambda: self.ocr_result_text.event_generate("<<Copy>>"))
        self.ocr_result_menu.add_command(label="粘贴", command=lambda: self.ocr_result_text.event_generate("<<Paste>>"))
        self.ocr_result_menu.add_command(label="删除", command=lambda: self._clear_ocr_result())
        self.ocr_result_menu.add_separator()
        self.ocr_result_menu.add_command(label="全选", command=lambda: self.ocr_result_text.tag_add("sel", "1.0", "end"))
        self.ocr_result_text.bind("<Button-3>", lambda e: self.ocr_result_menu.post(e.x_root, e.y_root))

        # 共享参数：第N个和XY偏差（图片和OCR模式共用）
        self.shared_params_frame = tk.Frame(recognition_group)
        self.shared_params_frame.pack(fill=tk.X, pady=(5, 0))

        nth_row = tk.Frame(self.shared_params_frame)
        nth_row.pack(fill=tk.X, pady=2)
        nth_row.grid_columnconfigure(1, weight=1)

        tk.Label(nth_row, text="第N个:", width=8, anchor=tk.W).grid(
            row=0, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(nth_row, textvariable=self.v_nth).grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 10))

        # 相似度（仅图片模式）
        self.conf_row = tk.Frame(self.shared_params_frame)
        self.conf_row.pack(fill=tk.X, pady=2)

        tk.Label(self.conf_row, text="相似度:", width=8, anchor=tk.W).grid(
            row=0, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(self.conf_row, textvariable=self.v_conf).grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 10))

        offset_row = tk.Frame(self.shared_params_frame)
        self.offset_row = offset_row
        offset_row.pack(fill=tk.X, pady=2)
        offset_row.grid_columnconfigure(0, weight=0)
        offset_row.grid_columnconfigure(1, weight=1)
        offset_row.grid_columnconfigure(2, weight=0)
        offset_row.grid_columnconfigure(3, weight=1)

        tk.Label(offset_row, text="X偏差:", width=8, anchor=tk.W).grid(
            row=0, column=0, sticky=tk.W, padx=(0, 5))
        tk.Entry(offset_row, textvariable=self.v_offset_x).grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 10))

        tk.Label(offset_row, text="Y偏差:", width=8, anchor=tk.W).grid(
            row=0, column=2, sticky=tk.W, padx=(0, 5))
        tk.Entry(offset_row, textvariable=self.v_offset_y).grid(
            row=0, column=3, sticky=tk.EW)

        # 颜色敏感匹配选项（仅图片模式）
        color_sensitive_frame = tk.Frame(recognition_group)
        color_sensitive_frame.pack(fill=tk.X, pady=(5, 0))
        tk.Checkbutton(
            color_sensitive_frame,
            text="启用颜色敏感匹配 (区分不同颜色的相同形状)",
            variable=self.v_color_on,
        ).pack(anchor=tk.W)

        # --- Color ---
        self.color_group = tk.LabelFrame(main_frame, text="颜色验证", padx=5, pady=5)
        self.color_group.pack(fill=tk.X, pady=(0, 10))
        tk.Checkbutton(
            self.color_group, text="启用颜色测试", variable=self.v_verify_color,
        ).pack(anchor=tk.W)
        self._add_test_row(
            self.color_group, "目标颜色:", self.v_target_color,
            lambda: self._pick_col(), btn_text='\U0001f3a8',
        )
        self._add_test_row(self.color_group, "颜色偏差:", self.v_tolerance)

        # --- Region ---
        region_group = tk.LabelFrame(
            main_frame, text="搜索范围 (留空为全屏/全窗口)", padx=5, pady=5,
        )
        region_group.pack(fill=tk.X, pady=(0, 10))
        self._add_test_row(
            region_group, "区域:", self.v_region,
            lambda: self._pick_reg(), btn_text='\u2702\ufe0f',
        )

        # --- Background Mode ---
        bg_group = tk.LabelFrame(main_frame, text="后台识别", padx=5, pady=5)
        bg_group.pack(fill=tk.X, pady=(0, 15))
        bg_top_frame = tk.Frame(bg_group)
        bg_top_frame.pack(fill=tk.X)

        bg_checkbox = tk.Checkbutton(
            bg_top_frame, text="启用后台", variable=self.v_use_bg,
        )
        bg_checkbox.pack(side=tk.LEFT, padx=(0, 10))

        bg_entry_frame = tk.Frame(bg_top_frame)
        bg_entry_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(bg_entry_frame, text="窗口标题:", width=8, anchor=tk.W).pack(
            side=tk.LEFT)
        bg_entry = tk.Entry(bg_entry_frame, textvariable=self.v_bg_title)
        bg_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(
            bg_entry_frame, text='\U0001f3af', width=3,
            command=lambda: self._spy_win(), relief=tk.GROOVE,
        ).pack(side=tk.LEFT)

        def toggle_bg_entry() -> None:
            if self.v_use_bg.get():
                bg_entry.config(state="normal")
            else:
                bg_entry.config(state="disabled")

        bg_checkbox.config(command=toggle_bg_entry)
        toggle_bg_entry()

        # 其他测试参数框架
        action_param_frame = tk.LabelFrame(
            main_frame, text="其他测试参数", padx=10, pady=5,
        )
        action_param_frame.pack(fill=tk.X, pady=(0, 15))

        action_param_frame.grid_columnconfigure(0, minsize=60, weight=0)
        action_param_frame.grid_columnconfigure(1, minsize=40, weight=0)
        action_param_frame.grid_columnconfigure(2, minsize=30, weight=0)
        action_param_frame.grid_columnconfigure(3, minsize=120, weight=1)
        action_param_frame.grid_columnconfigure(4, minsize=60, weight=0)

        # 第一行：滚轮次数和按键
        tk.Label(action_param_frame, text="滚轮次数:", width=8, anchor=tk.W).grid(
            row=0, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        wheel_entry = tk.Entry(
            action_param_frame, textvariable=self.v_wheel_clicks, width=5,
        )
        wheel_entry.grid(row=0, column=1, padx=(0, 10), pady=5, sticky=tk.W)

        tk.Label(action_param_frame, text="按键:", width=4, anchor=tk.W).grid(
            row=0, column=2, padx=(0, 5), pady=5, sticky=tk.W)
        key_entry = tk.Entry(
            action_param_frame, textvariable=self.v_key, width=15,
        )
        key_entry.grid(row=0, column=3, padx=(0, 5), pady=5, sticky=tk.W)

        tk.Button(
            action_param_frame, text="\u2328\ufe0f",
            command=lambda: self.select_key(self.v_key), relief=tk.GROOVE,
        ).grid(row=0, column=4, padx=(0, 5), pady=5, sticky=tk.W)

        # 第二行：发送文本
        tk.Label(action_param_frame, text="发送文本:", width=8, anchor=tk.W).grid(
            row=1, column=0, padx=(0, 5), pady=5, sticky=tk.W)
        text_entry = tk.Entry(
            action_param_frame, textvariable=self.v_text, width=30,
        )
        text_entry.grid(
            row=1, column=1, padx=(0, 5), pady=5,
            columnspan=4, sticky=tk.W + tk.E,
        )

        # --- Test Action ---
        action_frame = tk.Frame(main_frame)
        action_frame.pack(fill=tk.X, pady=(0, 15))
        tk.Label(action_frame, text="测试动作:", width=8, anchor=tk.W).pack(
            side=tk.LEFT)

        all_actions = [
            "仅识别", "左键单击", "右键单击", "左键双击",
            "滚轮向上", "滚轮向下", "发送文本", "按键",
        ] + WINDOW_ACTIONS
        action_combo = ttk.Combobox(
            action_frame, textvariable=self.v_action,
            values=all_actions, state="readonly", width=20,
        )
        action_combo.pack(side=tk.LEFT, padx=(5, 0))

        # 隐藏动作参数，根据选择的动作显示对应的控件
        def update_action_param_visibility() -> None:
            action = self.v_action.get()
            wheel_entry.config(
                state="normal" if action in ["滚轮向上", "滚轮向下"] else "disabled")
            text_entry.config(
                state="normal" if action == "发送文本" else "disabled")
            key_entry.config(
                state="normal" if action == "按键" else "disabled")
            # 窗口操作需要启用后台窗口标题输入框
            bg_entry.config(
                state="normal" if action in WINDOW_ACTIONS else "disabled")

        update_action_param_visibility()
        action_combo.bind(
            "<<ComboboxSelected>>", lambda e: update_action_param_visibility())

        # 按钮
        tk.Button(
            main_frame, text="\u25b6 开始测试", command=self._run_test,
            bg="#fff0f6", width=15,
        ).pack(fill=tk.X, pady=10, ipady=5)

        # 应用从选中步骤传入的参数
        if self.step_params:
            self._apply_step_params()

    def _apply_step_params(self) -> None:
        """应用从选中步骤传入的参数到测试窗口。"""
        p = self.step_params
        if not p:
            return

        try:
            # OCR模式检测（如果步骤有keyword参数则切换到OCR模式）
            if 'keyword' in p and p['keyword']:
                self.v_ocr_mode.set(True)
                self.v_ocr_keyword.set(p['keyword'])
                self._on_recognition_mode_change()
            # OCR URL
            if 'ocr_url' in p and p['ocr_url']:
                self.v_ocr_url.set(str(p['ocr_url']))
            # 图像/模板参数
            elif 'template' in p and p['template']:
                self.v_img.set(p['template'])

            # 相似度
            if 'confidence' in p:
                self.v_conf.set(str(p['confidence']))

            # 第N个
            if 'find_nth' in p:
                self.v_nth.set(str(p['find_nth']))

            # 坐标偏移
            if 'offset_x' in p:
                self.v_offset_x.set(str(p['offset_x']))
            if 'offset_y' in p:
                self.v_offset_y.set(str(p['offset_y']))

            # 颜色敏感
            if 'color_enable' in p:
                self.v_color_on.set(bool(p['color_enable']))

            # 颜色验证
            if 'target_color' in p and p['target_color']:
                self.v_verify_color.set(True)
                self.v_target_color.set(str(p['target_color']))
            if 'color_tolerance' in p:
                self.v_tolerance.set(str(p['color_tolerance']))

            # 搜索区域
            if 'region' in p and p['region']:
                self.v_region.set(str(p['region']))

            # 后台模式
            self.v_use_bg.set(bool(p.get('use_bg', False)))
            if p.get('use_bg', False):
                if 'bg_window_title' in p and p['bg_window_title']:
                    self.v_bg_title.set(str(p['bg_window_title']))

            # 窗口标题（窗口步骤）
            if 'bg_window_title' in p and p['bg_window_title'] and not p.get('use_bg'):
                self.v_bg_title.set(str(p['bg_window_title']))

            # 输入文本
            if 'text' in p and p['text']:
                self.v_action.set("发送文本")
                self.v_text.set(str(p['text']))
                self.win.after(100, self._update_action_param_visibility)

            # 按键参数
            if 'key' in p and p['key']:
                self.v_action.set("按键")
                self.v_key.set(str(p['key']))
                self.win.after(100, self._update_action_param_visibility)

            # 滚轮参数
            if 'direction' in p and p['direction']:
                action_dir = '向上' if p['direction'] == '向上' else '向下'
                self.v_action.set(f'滚轮{action_dir}')
                if 'clicks' in p:
                    self.v_wheel_clicks.set(str(p['clicks']))
                self.win.after(100, self._update_action_param_visibility)

            # 测试动作
            if 'action' in p and p['action']:
                self.v_action.set(p['action'])
                # 触发动作参数可见性更新
                self.win.after(100, self._update_action_param_visibility)

        except Exception:
            pass  # 忽略参数应用错误

    def _on_recognition_mode_change(self) -> None:
        """切换识别模式（图片/文字）时更新界面显示。"""
        if self.v_ocr_mode.get():
            # 文字模式
            self.img_input_frame.pack_forget()
            self.ocr_input_frame.pack(fill=tk.X, pady=(0, 5))
            self.color_group.pack_forget()
            self.conf_row.pack_forget()
        else:
            # 图片模式
            self.ocr_input_frame.pack_forget()
            self.img_input_frame.pack(fill=tk.X, pady=(0, 5))
            self.color_group.pack(fill=tk.X, pady=(0, 10))
            self.conf_row.pack(fill=tk.X, pady=2, before=self.offset_row)

    def _update_action_param_visibility(self) -> None:
        """更新动作参数的可见性（供外部调用）。"""
        action = self.v_action.get()
        # 查找相关控件并更新状态
        for widget in self.win.winfo_children():
            self._update_widget_states(widget, action)

    def _update_widget_states(self, widget: tk.Widget, action: str) -> None:
        """递归更新控件状态。"""
        try:
            if hasattr(widget, 'winfo_children'):
                for child in widget.winfo_children():
                    self._update_widget_states(child, action)
        except Exception:
            pass

    def _add_test_row(
        self,
        parent: tk.Widget,
        label: str,
        var: tk.StringVar,
        cmd: Optional[Callable[[], None]] = None,
        btn_text: str = '\U0001f4c2',
        show_preview: bool = False,
    ) -> None:
        """
        添加一行测试参数控件。

        Args:
            parent: 父容器
            label: 标签文本
            var: 关联的 StringVar
            cmd: 按钮点击回调
            btn_text: 按钮文本
            show_preview: 是否显示图片预览
        """
        row = tk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=label, width=8, anchor=tk.W).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        if cmd:
            tk.Button(
                row, text=btn_text, width=3, command=cmd, relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=(0, 5))

        # 添加预览框
        if show_preview:
            canvas_preview = tk.Canvas(
                row, bg="#f0f0f0", width=98, height=72,
                bd=0, cursor="hand2", highlightthickness=0,
            )
            canvas_preview.pack(side=tk.LEFT, padx=(0, 5))

            def get_image_path() -> Optional[str]:
                img_name = var.get().strip()
                if not img_name:
                    return None
                return os.path.join(self.templates_dir, img_name)

            def refresh_preview(*args: object) -> None:
                try:
                    canvas_preview.delete("all")
                    full_path = get_image_path()
                    if full_path and os.path.exists(full_path):
                        try:
                            pil_image = Image.open(full_path)
                            pil_image.thumbnail((94, 70), Image.Resampling.LANCZOS)
                            tk_image = ImageTk.PhotoImage(pil_image)
                            canvas_preview.create_image(
                                0, 0, anchor=tk.NW, image=tk_image)
                            canvas_preview.image = tk_image

                            img_x = (96 - tk_image.width()) // 2
                            img_y = (72 - tk_image.height()) // 2
                            canvas_preview.coords(
                                canvas_preview.find_all()[-1], img_x, img_y)

                            center_x = img_x + tk_image.width() // 2
                            center_y = img_y + tk_image.height() // 2

                            try:
                                offset_x = int(self.v_offset_x.get())
                                offset_y = int(self.v_offset_y.get())
                            except ValueError:
                                offset_x, offset_y = 0, 0

                            offset_point_x = center_x + offset_x
                            offset_point_y = center_y + offset_y

                            marker_size = 4
                            canvas_preview.create_oval(
                                center_x - marker_size, center_y - marker_size,
                                center_x + marker_size, center_y + marker_size,
                                fill="#0000ff", outline="white", width=1,
                            )
                            canvas_preview.create_oval(
                                offset_point_x - marker_size,
                                offset_point_y - marker_size,
                                offset_point_x + marker_size,
                                offset_point_y + marker_size,
                                fill="#ff0000", outline="white", width=1,
                            )
                        except Exception:
                            canvas_preview.create_text(
                                48, 36, text="损坏",
                                fill="black", font=("Arial", 10))
                    else:
                        canvas_preview.create_text(
                            48, 36, text="预览",
                            fill="black", font=("Arial", 10))
                    # 最后绘制虚线边框，确保显示在最上层
                    canvas_preview.create_rectangle(
                        0, 0, 97, 71,
                        outline="#999999", dash=(3, 2), width=2
                    )
                except Exception:
                    pass

            var.trace_add("write", refresh_preview)
            self.root.after(10, refresh_preview)

    def _add_row(
        self,
        parent: tk.Widget,
        label: str,
        var: tk.StringVar,
        btn_cmd: Optional[Callable[[], None]] = None,
        btn_txt: str = "...",
    ) -> None:
        """
        添加一行参数控件。

        Args:
            parent: 父容器
            label: 标签文本
            var: 关联的 StringVar
            btn_cmd: 按钮点击回调
            btn_txt: 按钮文本
        """
        r = tk.Frame(parent)
        r.pack(fill=tk.X, pady=2)
        tk.Label(r, text=label, width=10, anchor="e").pack(side=tk.LEFT)
        tk.Entry(r, textvariable=var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        if btn_cmd:
            tk.Button(r, text=btn_txt, command=btn_cmd, width=3).pack(side=tk.LEFT)

    # --- 辅助绑定 ---

    def _sel_img(self) -> None:
        """选择图片文件。"""
        f = filedialog.askopenfilename(
            initialdir=self.templates_dir, filetypes=[("PNG", "*.png")])
        if f:
            self.v_img.set(os.path.basename(f))

    def _pick_col(self) -> None:
        """颜色拾取。"""
        UIPickers.pick_color(
            self.root,
            lambda color: self.v_target_color.set(color) if color else None,
        )

    def _pick_reg(self) -> None:
        """区域拾取。"""
        UIPickers.capture_area_tool(
            self.root, 'region',
            lambda region: self.v_region.set(region) if region else None,
        )

    def _spy_win(self) -> None:
        """窗口探测。"""
        UIPickers.start_window_spy(
            self.root,
            lambda title: self.v_bg_title.set(title) if title else None,
        )

    def select_key(self, var: tk.StringVar) -> None:
        """
        按键选择。

        Args:
            var: 要设置的目标 StringVar
        """
        UIPickers.select_key(
            self.root,
            lambda key: var.set(key) if key else None,
        )

    def _update_ocr_result(self, texts: List[str]) -> None:
        """
        更新OCR识别结果文本框。

        Args:
            texts: OCR识别到的文字列表
        """
        self.ocr_result_text.config(state="normal")
        self.ocr_result_text.delete("1.0", tk.END)
        if texts:
            # 每行显示一个识别到的文字
            self.ocr_result_text.insert("1.0", "\n".join(texts))
        else:
            self.ocr_result_text.insert("1.0", "(未识别到文字)")
        self.ocr_result_text.config(state="normal")

    def _clear_ocr_result(self) -> None:
        """清空OCR识别结果文本框。"""
        self.ocr_result_text.config(state="normal")
        self.ocr_result_text.delete("1.0", tk.END)

    # --- 测试逻辑 ---

    def _run_test(self) -> None:
        """执行测试。"""
        # 隐藏主窗口和测试窗口
        self.root.withdraw()
        self.win.withdraw()
        time.sleep(OVERLAY_HIDE_DELAY)

        logs: List[str] = []
        try:
            logs.append("\U0001f680 开始测试执行...")

            # 1. 准备环境
            logs.append("\U0001f527 准备测试环境...")
            hwnd = self._prepare_environment(logs)
            logs.append(
                f"\U0001f527 环境准备完成，后台模式: {'是' if hwnd else '否'}")

            # 2. 解析区域
            logs.append("\U0001f4d0 解析搜索区域...")
            reg = self._parse_region(logs)
            logs.append(f"\U0001f4d0 搜索区域: {reg if reg else '全屏/全窗口'}")

            # 3. 根据模式执行识别（图片或OCR）
            if self.v_ocr_mode.get():
                logs.append("\U0001f50d 执行OCR文字识别...")
                pos = self._execute_ocr_recognition(hwnd, reg, logs)
                # OCR模式跳过颜色验证
            else:
                logs.append("\U0001f50d 执行图像识别...")
                pos = self._execute_image_recognition(hwnd, reg, logs)
                # 4. 颜色验证（仅图片模式）
                logs.append("\U0001f3a8 执行颜色验证...")
                self._execute_color_verification(logs)

            # 5. 动作执行
            logs.append(f"\u26a1 执行测试动作: {self.v_action.get()}")
            self._execute_action(hwnd, pos, logs)

            logs.append("\u2705 测试执行完成")

        except Exception as e:
            error_detail = traceback.format_exc()
            logs.append(f"\u274c 测试执行错误: {e}")
            logs.append(f"\U0001f4cb 详细错误信息:\n{error_detail}")

        self.win.deiconify()
        self.root.deiconify()
        messagebox.showinfo("测试报告", "\n".join(logs))

    def _prepare_environment(self, logs: List[str]) -> Optional[int]:
        """
        准备测试环境，包括查找后台窗口。

        Args:
            logs: 日志列表

        Returns:
            后台窗口句柄，未启用后台模式时返回 None
        """
        hwnd: Optional[int] = None
        if self.v_use_bg.get():
            title = self.v_bg_title.get()
            hwnd = _find_window_by_title(title)
            if not hwnd:
                logs.append(f"⚠️ 未找到后台窗口: {title}")
            else:
                logs.append(f"\U0001f527 后台句柄: {hwnd}")
        return hwnd

    def _parse_region(self, logs: List[str]) -> Optional[Tuple[int, ...]]:
        """
        解析搜索区域参数。

        Args:
            logs: 日志列表

        Returns:
            区域元组，未设置时返回 None
        """
        reg: Optional[Tuple[int, ...]] = None
        if self.v_region.get():
            try:
                reg = tuple(map(int, self.v_region.get().split(',')))
                logs.append(f"\U0001f4cf 搜索区域: {reg}")
            except ValueError:
                logs.append(f"⚠️ 区域参数无效: {self.v_region.get()}")
        return reg

    def _execute_image_recognition(
        self,
        hwnd: Optional[int],
        reg: Optional[Tuple[int, ...]],
        logs: List[str],
    ) -> Optional[Tuple[int, int]]:
        """
        执行图片识别。

        Args:
            hwnd: 后台窗口句柄
            reg: 搜索区域
            logs: 日志列表

        Returns:
            识别位置 (x, y) 或 None
        """
        img_name = self.v_img.get()
        pos: Optional[Tuple[int, int]] = None
        if img_name:
            try:
                logs.append(f"\U0001f4f7 图片名称: {img_name}")

                confidence = float(self.v_conf.get())
                find_nth = int(self.v_nth.get())
                color_sensitive = self.v_color_on.get()

                logs.append(
                    f"\U0001f3af 识别参数: 相似度={confidence}, "
                    f"第{find_nth}个, 颜色敏感={color_sensitive}")

                # 检查图片名称是否包含路径
                if os.path.sep in img_name or os.path.isabs(img_name):
                    template_name = img_name
                    img_path = img_name if os.path.isabs(img_name) \
                        else os.path.join(self.templates_dir, img_name)
                else:
                    template_name = img_name
                    img_path = os.path.join(
                        self.templates_dir,
                        img_name if img_name.endswith('.png')
                        else img_name + '.png',
                    )

                logs.append(f"\U0001f4c2 模板名称: {template_name}")
                logs.append(f"\U0001f4c2 完整路径: {img_path}")

                if not os.path.exists(img_path):
                    logs.append(f"\u274c 图片文件不存在: {img_path}")
                    return None

                logs.append(f"\U0001f5bc\ufe0f 图片文件存在，开始识别...")

                # 添加图片信息诊断
                try:
                    with Image.open(img_path) as img:
                        img_size = img.size
                        img_mode = img.mode
                    logs.append(
                        f"\U0001f4d0 图片信息: 尺寸={img_size}, "
                        f"颜色模式={img_mode}")
                except Exception as e:
                    logs.append(f"⚠️ 无法读取图片信息: {e}")

                # 添加搜索区域信息
                if reg:
                    logs.append(
                        f"\U0001f4cf 搜索区域: ({reg[0]}, {reg[1]}, "
                        f"{reg[2]}, {reg[3]}) "
                        f"(宽={reg[2]-reg[0]}, 高={reg[3]-reg[1]})")
                else:
                    logs.append("\U0001f4cf 搜索区域: 全屏")

                logs.append(f"\U0001f504 正在搜索 '{template_name}'...")

                try:
                    offset_x = int(self.v_offset_x.get())
                    offset_y = int(self.v_offset_y.get())
                except ValueError:
                    offset_x, offset_y = 0, 0

                original_pos = ActionHelper.find_image_with_params(
                    self.vision, template_name, hwnd=hwnd, region=reg,
                    confidence=confidence, find_nth=find_nth,
                    color_sensitive=color_sensitive,
                )

                if original_pos:
                    pos = (
                        original_pos[0] + offset_x,
                        original_pos[1] + offset_y,
                    )
                    logs.append(
                        f"\u2705 图片识别成功: "
                        f"原位置({int(original_pos[0])}, {int(original_pos[1])}) "
                        f"-> 偏移后({int(pos[0])}, {int(pos[1])}) "
                        f"(偏移: {offset_x}, {offset_y})")
                else:
                    logs.append(
                        f"\u274c 图片识别失败: 未找到 '{img_name}'")
                    logs.append("\U0001f50d 失败分析:")
                    logs.append(f"   \u2022 相似度阈值: {confidence} (是否过高？)")
                    logs.append(
                        f"   \u2022 颜色敏感: {color_sensitive} "
                        f"(图片颜色是否有变化？)")
                    logs.append(
                        f"   \u2022 搜索顺序: 第{find_nth}个 "
                        f"(是否应该第1个？)")
                    logs.append(f"   \u2022 后台模式: {'是' if hwnd else '否'}")
                    logs.append("\U0001f4a1 解决建议:")
                    logs.append(
                        f"   1. 降低相似度阈值 "
                        f"(当前{confidence} \u2192 尝试0.8或0.7)")
                    logs.append("   2. 关闭颜色敏感模式")
                    logs.append("   3. 确保目标图片在屏幕上完全可见")
                    logs.append("   4. 检查图片是否被其他窗口遮挡")
                    logs.append("   5. 重新截取更清晰的模板图片")

                    # 尝试更低相似度的快速测试
                    if confidence > 0.8:
                        logs.append(
                            "\U0001f9ea 正在尝试相似度0.8重新识别...")
                        try:
                            original_confidence = self.vision.confidence
                            self.vision.confidence = 0.8
                            pos_test = self.vision.find_image(
                                template_name, hwnd=hwnd, region=reg,
                                find_nth=find_nth,
                            )
                            self.vision.confidence = original_confidence

                            if pos_test:
                                logs.append(
                                    f"\U0001f3af 相似度0.8下找到: "
                                    f"({int(pos_test[0])}, {int(pos_test[1])})")
                                logs.append(
                                    "\U0001f4a1 建议: 将相似度设置为0.8或更低")
                            else:
                                logs.append("\u274c 相似度0.8下仍未找到")
                        except Exception as e:
                            logs.append(f"⚠️ 测试失败: {e}")

            except ValueError as e:
                logs.append(f"\u274c 参数值错误: {str(e)}")
            except Exception as e:
                error_detail = traceback.format_exc()
                logs.append(f"\u274c 找图过程异常: {str(e)}")
                logs.append(f"\U0001f4cb 详细错误:\n{error_detail}")
        else:
            logs.append("⚠️ 未设置图片模板")

        return pos

    def _execute_ocr_recognition(
        self,
        hwnd: Optional[int],
        reg: Optional[Tuple[int, ...]],
        logs: List[str],
    ) -> Optional[Tuple[int, int]]:
        """
        执行OCR文字识别（使用 utils.py 中的 ocr_find_keyword 函数）。

        Args:
            hwnd: 后台窗口句柄
            reg: 搜索区域
            logs: 日志列表

        Returns:
            识别位置 (x, y) 或 None
        """
        keyword = self.v_ocr_keyword.get().strip()
        ocr_url = self.v_ocr_url.get().strip() or OCR_DEFAULT_URL
        logs.append(f"\U0001f4d1 OCR服务地址: {ocr_url}")

        # 关键字为空但有搜索区域：返回区域中心
        if not keyword and reg and len(reg) == 4:
            center_x = (reg[0] + reg[2]) // 2
            center_y = (reg[1] + reg[3]) // 2
            logs.append(
                f"\U0001f4cd 关键字为空，使用搜索区域中心: "
                f"({center_x}, {center_y})")
            return (center_x, center_y)

        if not keyword:
            logs.append("\u26a0\ufe0f 未设置OCR识别关键字且无搜索区域")
            return None

        logs.append(f"\U0001f50d OCR关键字: '{keyword}'")
        find_nth = int(self.v_nth.get())

        try:
            # 截图
            if hwnd:
                screenshot_np = WinDriver.capture_window(hwnd, reg)
                if screenshot_np is not None:
                    screenshot = Image.fromarray(screenshot_np[:, :, ::-1])
                else:
                    logs.append("\u26a0\ufe0f 后台截图失败，降级到前台截图")
                    screenshot = pyautogui.screenshot(region=reg if reg else None)
            else:
                screenshot = pyautogui.screenshot(region=reg if reg else None)

            logs.append(f"\U0001f5bc\ufe0f 截图尺寸: {screenshot.width}x{screenshot.height}")

            # 先获取所有识别到的文字，更新结果文本框
            all_ocr_texts = ocr_get_texts(screenshot, ocr_url)
            all_texts = [item.get("text", "") for item in all_ocr_texts]
            
            # 更新识别结果文本框
            self._update_ocr_result(all_texts)

            # 使用 utils.py 中的 OCR 函数查找第N个匹配的关键字
            box = ocr_find_keyword(screenshot, keyword, ocr_url, nth=find_nth)

            if box:
                # 读取坐标偏差
                try:
                    off_x = int(self.v_offset_x.get())
                    off_y = int(self.v_offset_y.get())
                except ValueError:
                    off_x, off_y = 0, 0

                # 计算文字区域中心点
                x1, y1, x2, y2 = box
                ocr_center_x = (x1 + x2) / 2
                ocr_center_y = (y1 + y2) / 2

                # OCR坐标相对于截图区域，加上区域偏移得到屏幕坐标
                if reg and len(reg) == 4:
                    center_x = reg[0] + ocr_center_x
                    center_y = reg[1] + ocr_center_y
                else:
                    center_x = ocr_center_x
                    center_y = ocr_center_y

                final_x = center_x + off_x
                final_y = center_y + off_y

                detail = f" -> 偏差({off_x},{off_y})" if off_x or off_y else ""
                logs.append(
                    f"\u2705 OCR识别成功: 第{find_nth}个关键字 "
                    f"'{keyword}' 在位置 ({int(center_x)}, {int(center_y)})"
                    f"{detail}")

                return (int(final_x), int(final_y))

            # 未找到关键字，输出调试信息
            logs.append(f"\u274c OCR识别失败: 未找到第{find_nth}个关键字 '{keyword}'")
            if all_texts:
                logs.append(f"\U0001f4cb OCR识别到的文字: {all_texts[:10]}{'...' if len(all_texts) > 10 else ''}")

        except ImportError:
            logs.append("\u274c OCR测试失败：缺少 requests 库")
        except Exception as e:
            logs.append(f"\u274c OCR识别异常: {str(e)}")

        return None

    def _execute_color_verification(self, logs: List[str]) -> None:
        """
        执行颜色验证。

        Args:
            logs: 日志列表
        """
        if self.v_verify_color.get() and self.v_target_color.get():
            try:
                color_rgb = tuple(
                    map(int, self.v_target_color.get().split(',')))
                tolerance = int(self.v_tolerance.get())
                logs.append(
                    f"\U0001f3a8 颜色验证: RGB{color_rgb}, 偏差: {tolerance}")
            except ValueError:
                logs.append("⚠️ 颜色参数无效")

    def _execute_action(
        self,
        hwnd: Optional[int],
        pos: Optional[Tuple[int, int]],
        logs: List[str],
    ) -> None:
        """
        执行测试动作。

        Args:
            hwnd: 后台窗口句柄
            pos: 识别位置
            logs: 日志列表
        """
        act = self.v_action.get()

        # 处理窗口操作（不依赖位置）
        if act in WINDOW_ACTIONS:
            self._execute_window_action(act, logs)
            return

        # 如果是"仅识别"，只记录找到的位置
        if act == "仅识别":
            if pos:
                logs.append(
                    f"\u2705 仅识别模式找到位置: "
                    f"({int(pos[0])}, {int(pos[1])})")
            else:
                logs.append("\u274c 仅识别模式未找到目标")
            return

        # 位置相关动作（需要先找到图像位置）
        position_required_actions = [
            "左键单击", "右键单击", "左键双击",
            "滚轮向上", "滚轮向下", "发送文本", "按键",
        ]
        is_position_action = any(
            action in act for action in position_required_actions)

        if is_position_action:
            if not pos:
                # 如果没有找到目标但有搜索区域，使用区域中心
                reg = self._parse_region(logs)
                if reg and len(reg) == 4:
                    center_x = (reg[0] + reg[2]) // 2
                    center_y = (reg[1] + reg[3]) // 2
                    logs.append(
                        f"\U0001f4cd 未找到目标，使用搜索区域中心: "
                        f"({center_x}, {center_y})")
                    pos = (center_x, center_y)
                else:
                    logs.append(
                        f"\u274c 执行失败: 未找到目标位置，无法执行 {act}")
                    return

            try:
                if "单击" in act or "双击" in act:
                    button = 'left' if "左键" in act else 'right'
                    double = "双击" in act
                    logs.append(f"\U0001f9ea 调试: hwnd={hwnd}, button={button}, double={double}, pos=({pos[0]}, {pos[1]})")
                    ActionHelper.click_action(
                        hwnd, int(pos[0]), int(pos[1]),
                        button=button, double=double,
                    )
                    mode = "后台" if hwnd else "前台"
                    logs.append(
                        f"\U0001f5b1 {mode}{act}: "
                        f"({int(pos[0])}, {int(pos[1])})")

                elif "滚轮" in act:
                    clicks = int(self.v_wheel_clicks.get()) \
                        if self.v_wheel_clicks.get() else 1
                    direction = 'up' if "向上" in act else 'down'
                    ActionHelper.scroll_action(
                        hwnd, pos[0], pos[1],
                        clicks=clicks, direction=direction,
                    )
                    mode = "后台" if hwnd else "前台"
                    logs.append(
                        f"\U0001f3b2 {mode}{act}: {clicks}次, "
                        f"坐标: ({int(pos[0])}, {int(pos[1])})")

                elif "发送文本" in act:
                    text = self.v_text.get()
                    if text:
                        ActionHelper.send_text_action(
                            hwnd, text, click_pos=pos)
                        mode = "后台" if hwnd else "前台"
                        logs.append(
                            f"\U0001f4dd {mode}{act}: '{text}' "
                            f"到坐标: ({int(pos[0])}, {int(pos[1])})")

                elif "按键" in act:
                    key = self.v_key.get()
                    if key:
                        if hwnd:
                            ActionHelper.click_action(
                                hwnd, pos[0], pos[1])
                            time.sleep(0.1)
                        ActionHelper.send_keys_action(hwnd, key)
                        mode = "后台" if hwnd else "前台"
                        logs.append(
                            f"\u2328\ufe0f {mode}{act}: '{key}' "
                            f"到坐标: ({int(pos[0])}, {int(pos[1])})")

                elif "移动" in act:
                    if hwnd:
                        try:
                            client_pos = WinDriver.screen_to_client(
                                hwnd, pos[0], pos[1])
                        except AttributeError:
                            client_origin = WinDriver.get_client_origin(hwnd)
                            client_pos = (
                                pos[0] - client_origin[0],
                                pos[1] - client_origin[1],
                            )
                        lparam = WinAPI.MAKELPARAM(
                            client_pos[0], client_pos[1])
                        WinAPI.user32.PostMessageW(
                            hwnd, WinAPI.WM_MOUSEMOVE, 0, lparam)
                        logs.append(
                            f"\U0001f5b1 后台移动鼠标: "
                            f"({int(pos[0])}, {int(pos[1])}) -> "
                            f"客户区坐标: "
                            f"({int(client_pos[0])}, {int(client_pos[1])})")
                    else:
                        pyautogui.moveTo(pos)
                        logs.append(
                            f"\U0001f5b1 前台移动鼠标: {pos}")

            except Exception as e:
                logs.append(f"⚠️ 动作执行失败: {str(e)}")

    def _execute_window_action(self, action: str, logs: List[str]) -> None:
        """
        执行窗口操作。

        Args:
            action: 窗口操作类型
            logs: 日志列表
        """
        try:
            title = self.v_bg_title.get()
            if not title:
                logs.append("❌ 窗口操作失败: 未设置窗口标题")
                return

            hwnd = _find_window_by_title(title)
            if not hwnd:
                logs.append(f"\u274c 未找到窗口: {title}")
                return
            else:
                logs.append(
                    f"\U0001f527 找到窗口: {title} (HWND: {hwnd})")

            if action == '激活(设为后台目标)':
                self.runtime_bg_hwnd = hwnd
                logs.append(
                    f"\u2705 窗口 '{title}' 已设为后台目标 HWND: {hwnd}")

            elif action == '激活(前台)':
                try:
                    WinAPI.user32.ShowWindow(hwnd, WinAPI.SW_RESTORE)
                    WinAPI.user32.SetForegroundWindow(hwnd)
                    logs.append(f"\u2705 窗口 '{title}' 已激活(前台)")
                except Exception as e:
                    logs.append(f"\u274c 窗口激活失败: {e}")

            elif action == '最大化':
                try:
                    WinAPI.user32.ShowWindow(hwnd, WinAPI.SW_MAXIMIZE)
                    logs.append(f"\u2705 窗口 '{title}' 已最大化")
                except Exception as e:
                    logs.append(f"\u274c 窗口最大化失败: {e}")

            elif action == '最小化':
                try:
                    WinAPI.user32.ShowWindow(hwnd, WinAPI.SW_MINIMIZE)
                    logs.append(f"\u2705 窗口 '{title}' 已最小化")
                except Exception as e:
                    logs.append(f"\u274c 窗口最小化失败: {e}")

            elif action == '置顶':
                try:
                    WinAPI.user32.SetWindowPos(
                        hwnd, WinAPI.HWND_TOPMOST,
                        0, 0, 0, 0,
                        WinAPI.SWP_NOSIZE | WinAPI.SWP_NOMOVE
                        | WinAPI.SWP_NOACTIVATE,
                    )
                    logs.append(f"\u2705 窗口 '{title}' 已置顶")
                except Exception as e:
                    logs.append(f"\u274c 窗口置顶失败: {e}")

            elif action == '取消置顶':
                try:
                    WinAPI.user32.SetWindowPos(
                        hwnd, WinAPI.HWND_NOTOPMOST,
                        0, 0, 0, 0,
                        WinAPI.SWP_NOSIZE | WinAPI.SWP_NOMOVE
                        | WinAPI.SWP_NOACTIVATE,
                    )
                    logs.append(f"\u2705 窗口 '{title}' 已取消置顶")
                except Exception as e:
                    logs.append(f"\u274c 窗口取消置顶失败: {e}")

        except Exception as e:
            logs.append(f"\u274c 窗口操作异常: {e}")

    def _get_virtual_key_code(self, key: str) -> Optional[int]:
        """
        获取虚拟键码。

        Args:
            key: 按键名称

        Returns:
            虚拟键码，未找到时返回 None
        """
        key_map: Dict[str, int] = {
            'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45,
            'f': 0x46, 'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A,
            'k': 0x4B, 'l': 0x4C, 'm': 0x4D, 'n': 0x4E, 'o': 0x4F,
            'p': 0x50, 'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54,
            'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58, 'y': 0x59,
            'z': 0x5A,
            '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
            '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
            'enter': 0x0D, 'esc': 0x1B, 'space': 0x20, 'tab': 0x09,
            'backspace': 0x08,
            'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
            'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
            'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
        }
        return key_map.get(key.lower())

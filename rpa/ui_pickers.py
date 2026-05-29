"""
RPA自动化工具 - UI拾取组件模块

提供所有的屏幕拾取操作：
- 窗口探测器 (Window Spy)
- 区域截图工具 (Capture Area)
- 坐标拾取 (Pick Coordinate)
- 颜色拾取 (Pick Color)
- 按键选择器 (Select Key)
"""

import ctypes
import os
import time
from typing import Callable, Optional

import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageTk

import pyautogui

from rpa.win_api import WinAPI, POINT
from rpa.config import (
    OVERLAY_ALPHA, OVERLAY_HIDE_DELAY, MIN_SELECT_SIZE,
    SPY_REFRESH_INTERVAL, SPY_OFFSET_X, SPY_OFFSET_Y,
)


class UIPickers:
    """
    UI拾取工具类。

    所有方法均为静态方法，提供各种屏幕拾取功能。
    """

    @staticmethod
    def _prepare_overlay(root: tk.Tk) -> tk.Toplevel:
        """
        通用：隐藏主窗口并创建全屏透明覆盖层。

        Args:
            root: 主窗口 Tk 实例

        Returns:
            创建的全屏覆盖层 Toplevel 窗口
        """
        root.iconify()
        time.sleep(OVERLAY_HIDE_DELAY)
        top = tk.Toplevel(root)
        top.attributes('-fullscreen', True, '-topmost', True)
        top.attributes('-alpha', OVERLAY_ALPHA)
        top.configure(bg='black', cursor="cross")
        top.overrideredirect(True)
        return top

    @staticmethod
    def _restore_root(root: tk.Tk, top: Optional[tk.Toplevel]) -> None:
        """
        通用：销毁覆盖层并恢复主窗口。

        Args:
            root: 主窗口 Tk 实例
            top: 要销毁的覆盖层 Toplevel 窗口
        """
        if top:
            top.destroy()
        root.deiconify()
        root.focus_force()
        root.attributes('-topmost', True)
        root.lift()
        root.after(100, lambda: root.attributes('-topmost', False))

    @staticmethod
    def start_window_spy(
        root: tk.Tk,
        callback_func: Callable[[str], None],
    ) -> None:
        """
        窗口探测器 (Spy)。

        创建悬浮窗跟随鼠标，实时显示鼠标下方窗口的标题和句柄。
        左键锁定窗口标题并回调，右键退出。

        Args:
            root: 主窗口 Tk 实例
            callback_func: 回调函数，接收 (window_title, hwnd) 参数
        """
        root.iconify()
        time.sleep(OVERLAY_HIDE_DELAY)

        # 创建悬浮信息窗
        info_win = tk.Toplevel(root)
        info_win.attributes('-topmost', True, '-alpha', 0.9)
        info_win.overrideredirect(True)
        info_win.configure(bg='#333')

        lbl = tk.Label(
            info_win, text="...", fg="white", bg="#333",
            font=("Arial", 9), justify=tk.LEFT,
        )
        lbl.pack(padx=5, pady=5)

        running = True

        def spy_loop() -> None:
            if not running:
                return
            pt = POINT()
            WinAPI.user32.GetCursorPos(ctypes.byref(pt))

            # 让悬浮窗跟随鼠标
            info_win.geometry(f"+{pt.x + SPY_OFFSET_X}+{pt.y + SPY_OFFSET_Y}")

            hwnd = WinAPI.user32.WindowFromPoint(pt)

            # 避免探测到悬浮窗自己
            if hwnd == info_win.winfo_id():
                info_win.withdraw()
                hwnd = WinAPI.user32.WindowFromPoint(pt)
                info_win.deiconify()

            title_str = "未捕获"
            if hwnd:
                root_hwnd = WinAPI.user32.GetAncestor(hwnd, WinAPI.GA_ROOT) or hwnd
                length = WinAPI.user32.GetWindowTextLengthW(root_hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                WinAPI.user32.GetWindowTextW(root_hwnd, buff, length + 1)
                title_str = buff.value or "<无标题>"
                lbl.config(
                    text=f'标题: {title_str}\n句柄: {root_hwnd}\n'
                         f'[左键]锁定 [右键]退出'
                )

                # 检测点击
                if (WinAPI.user32.GetAsyncKeyState(WinAPI.VK_LBUTTON) & 0x8000):
                    callback_func(title_str)
                    close_spy()
                elif (WinAPI.user32.GetAsyncKeyState(WinAPI.VK_RBUTTON) & 0x8000):
                    close_spy()

            info_win.after(SPY_REFRESH_INTERVAL, spy_loop)

        def close_spy() -> None:
            nonlocal running
            running = False
            info_win.destroy()
            root.deiconify()

        spy_loop()

    @staticmethod
    def capture_area_tool(
        root: tk.Tk,
        mode: str,
        callback_func: Callable[[str], None],
        save_dir: Optional[str] = None,
    ) -> None:
        """
        通用区域选择工具。

        在全屏截图上框选区域，根据模式返回坐标字符串或保存模板图片。

        Args:
            root: 主窗口 Tk 实例
            mode: 'region' 返回坐标字符串，'template' 保存图片并返回文件名
            callback_func: 回调函数，接收结果字符串
            save_dir: 模板保存目录（仅 mode='template' 时使用）
        """
        root.iconify()
        time.sleep(OVERLAY_HIDE_DELAY)

        # 获取全屏截图用于"冻结"屏幕
        screenshot = pyautogui.screenshot()
        img_tk = ImageTk.PhotoImage(screenshot)

        top = tk.Toplevel(root)
        top.attributes('-fullscreen', True, '-topmost', True)

        canvas = tk.Canvas(top, cursor="cross", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_image(0, 0, anchor="nw", image=img_tk)
        canvas.image = img_tk  # 保持引用

        start_x: int = 0
        start_y: int = 0
        rect_id = None

        def on_down(e: tk.Event) -> None:
            nonlocal start_x, start_y, rect_id
            start_x, start_y = e.x, e.y
            rect_id = canvas.create_rectangle(
                start_x, start_y, start_x, start_y,
                outline="red", width=2,
            )

        def on_move(e: tk.Event) -> None:
            if rect_id:
                canvas.coords(rect_id, start_x, start_y, e.x, e.y)

        def on_up(e: tk.Event) -> None:
            x1, x2 = sorted([start_x, e.x])
            y1, y2 = sorted([start_y, e.y])
            width, height = x2 - x1, y2 - y1

            top.destroy()
            root.deiconify()

            if width > MIN_SELECT_SIZE and height > MIN_SELECT_SIZE:
                if mode == 'region':
                    callback_func(f"{x1},{y1},{x2},{y2}")
                elif mode == 'template' and save_dir:
                    cropped = screenshot.crop((x1, y1, x2, y2))
                    filename = filedialog.asksaveasfilename(
                        initialdir=save_dir,
                        title="保存模板图片",
                        filetypes=[("PNG", "*.png")],
                        defaultextension=".png",
                    )
                    if filename:
                        cropped.save(filename)
                        callback_func(os.path.basename(filename))

        canvas.bind("<ButtonPress-1>", on_down)
        canvas.bind("<B1-Motion>", on_move)
        canvas.bind("<ButtonRelease-1>", on_up)

        # 右键或ESC取消
        top.bind("<Button-3>", lambda e: [top.destroy(), root.deiconify()])
        top.bind("<Escape>", lambda e: [top.destroy(), root.deiconify()])

    @staticmethod
    def pick_coordinate(
        root: tk.Tk,
        callback_func: Callable[[str], None],
    ) -> None:
        """
        拾取单一坐标 (x, y)。

        Args:
            root: 主窗口 Tk 实例
            callback_func: 回调函数，接收 "x,y" 格式的坐标字符串
        """
        top = UIPickers._prepare_overlay(root)

        def on_click(e: tk.Event) -> None:
            UIPickers._restore_root(root, top)
            callback_func(f"{e.x_root},{e.y_root}")

        top.bind("<Button-1>", on_click)
        top.bind("<Button-3>", lambda e: UIPickers._restore_root(root, top))

    @staticmethod
    def pick_color(
        root: tk.Tk,
        callback_func: Callable[[str], None],
    ) -> None:
        """
        拾取屏幕颜色 RGB。

        Args:
            root: 主窗口 Tk 实例
            callback_func: 回调函数，接收 "r,g,b" 格式的颜色字符串
        """
        top = UIPickers._prepare_overlay(root)

        def on_click(e: tk.Event) -> None:
            UIPickers._restore_root(root, top)
            try:
                r, g, b = pyautogui.pixel(e.x_root, e.y_root)
                callback_func(f"{r},{g},{b}")
            except Exception:
                pass

        top.bind("<Button-1>", on_click)
        top.bind("<Button-3>", lambda e: UIPickers._restore_root(root, top))

    @staticmethod
    def select_key(
        root: tk.Tk,
        callback_func: Callable[[str], None],
    ) -> None:
        """
        按键选择器。

        弹出对话框，允许用户选择修饰键和主键的组合。

        Args:
            root: 主窗口 Tk 实例
            callback_func: 回调函数，接收按键组合字符串（如 "CTRL+A"）
        """
        win = tk.Toplevel(root)
        win.title("按键选择")
        win.geometry("350x480")
        win.transient(root)
        win.grab_set()
        win.resizable(False, False)

        # 设置窗口样式
        win.configure(bg='#f0f0f0')

        # 样式定义
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Title.TLabel', font=('Arial', 10, 'bold'))
        style.configure('Section.TLabelframe', font=('Arial', 9))
        style.configure('Mod.TCheckbutton', font=('Arial', 9))

        # 预览区域
        preview_frame = tk.LabelFrame(
            win, text="按键预览", font=("Arial", 9),
            bg='#f0f0f0', fg='#333333', padx=12, pady=8,
        )
        preview_frame.pack(fill=tk.X, padx=15, pady=(8, 10))

        var_preview = tk.StringVar(value="请选择按键...")
        lbl_preview = tk.Label(
            preview_frame, textvariable=var_preview,
            font=("Arial", 14, "bold"), fg="#333333",
            bg="#ffffff", relief=tk.GROOVE, bd=1, padx=15, pady=6,
        )
        lbl_preview.pack(fill=tk.X)

        # 状态存储
        modifiers = {k: tk.BooleanVar() for k in ['ctrl', 'alt', 'shift', 'win']}
        var_main = tk.StringVar(value='')

        def update(*args: object) -> None:
            mods = [k.upper() for k, v in modifiers.items() if v.get()]
            key = var_main.get()
            if key:
                mods.append(key.upper())
            preview_text = '+'.join(mods) if mods else "请选择按键..."
            var_preview.set(preview_text)

            # 更新预览标签颜色
            if mods:
                lbl_preview.config(bg="#e6ffe6", fg="#333333")
            else:
                lbl_preview.config(bg="#ffffff", fg="#333333")

        # 修饰键区域
        modifiers_frame = tk.LabelFrame(
            win, text="修饰键", font=("Arial", 9),
            bg='#f0f0f0', fg='#333333', padx=8, pady=6,
        )
        modifiers_frame.pack(fill=tk.X, padx=15, pady=(0, 8))

        mod_frame = tk.Frame(modifiers_frame, bg='#f0f0f0')
        mod_frame.pack()

        mod_style = [('ctrl', '#2196F3'), ('alt', '#FF9800'),
                     ('shift', '#4CAF50'), ('win', '#9C27B0')]
        for i, (k, v) in enumerate(modifiers.items()):
            color = mod_style[i][1]
            cb = tk.Checkbutton(
                mod_frame, text=k.upper(), variable=v,
                command=update, font=("Arial", 8, "bold"),
                bg='#f0f0f0', activebackground='#f0f0f0',
                selectcolor=color, fg=color,
            )
            cb.grid(row=0, column=i, padx=6, pady=3, ipadx=3, ipady=2)

        # 主键区域
        main_key_frame = tk.LabelFrame(
            win, text="主键", font=("Arial", 9),
            bg='#f0f0f0', fg='#333333', padx=8, pady=6,
        )
        main_key_frame.pack(fill=tk.X, padx=15, pady=(0, 8))

        basic_frame = tk.Frame(main_key_frame, bg='#f0f0f0')
        basic_frame.pack(fill=tk.X, pady=3)

        # 定义主键选项
        basic_keys = list("abcdefghijklmnopqrstuvwxyz")
        number_keys = list("0123456789")
        function_keys = [f"f{i}" for i in range(1, 13)]
        special_keys = [
            "enter", "space", "tab", "esc", "up", "down", "left", "right",
            "home", "end", "pageup", "pagedown", "insert", "delete", "backspace",
        ]
        main_key_options = basic_keys + number_keys + function_keys + special_keys

        combobox = ttk.Combobox(
            basic_frame, textvariable=var_main,
            values=main_key_options, width=28,
            font=("Arial", 9), height=12,
        )
        combobox.pack(padx=8, pady=3, fill=tk.X)
        combobox.set("")

        style.configure(
            'TCombobox',
            fieldbackground='#ffffff',
            background='#f5f5f5',
            foreground='#333333',
            arrowsize=14,
        )

        combobox.set("选择主键...")

        def on_focus_in(event: tk.Event) -> None:
            if combobox.get() == "选择主键...":
                combobox.set("")

        def on_focus_out(event: tk.Event) -> None:
            if not combobox.get():
                combobox.set("选择主键...")

        combobox.bind('<FocusIn>', on_focus_in)
        combobox.bind('<FocusOut>', on_focus_out)

        # 常用组合键快速选择
        quick_frame = tk.LabelFrame(
            main_key_frame, text="常用组合",
            font=("Arial", 8),
            bg='#f0f0f0', fg='#333333', padx=6, pady=4,
        )
        quick_frame.pack(fill=tk.X, pady=(6, 3))

        quick_combos = [
            ("Ctrl+C", "ctrl+c"), ("Ctrl+V", "ctrl+v"), ("Ctrl+A", "ctrl+a"),
            ("Ctrl+Z", "ctrl+z"), ("Alt+F4", "alt+f4"), ("Win+R", "win+r"),
            ("Enter", "enter"), ("Esc", "esc"), ("Tab", "tab"),
        ]

        q_box = tk.Frame(quick_frame, bg='#f0f0f0')
        q_box.pack()

        def sel_quick(combo: str) -> None:
            for v in modifiers.values():
                v.set(False)
            parts = combo.split('+')
            var_main.set(parts[-1])
            for mod in parts[:-1]:
                if mod.lower() in modifiers:
                    modifiers[mod.lower()].set(True)

        for i, (disp, combo) in enumerate(quick_combos):
            btn = tk.Button(
                q_box, text=disp, width=9,
                command=lambda c=combo: sel_quick(c),
                font=("Arial", 8), bg='#e6f7ff',
                activebackground='#cce8ff', relief=tk.GROOVE,
            )
            btn.grid(row=i // 3, column=i % 3, padx=1, pady=1)

        # 绑定更新事件
        for v in modifiers.values():
            v.trace_add("write", update)
        var_main.trace_add("write", update)

        # 按钮区域
        btn_frame = tk.Frame(win, bg='#f0f0f0')
        btn_frame.pack(fill=tk.X, padx=15, pady=(8, 12))

        def confirm() -> None:
            preview_text = var_preview.get()
            if preview_text and preview_text != "请选择按键...":
                callback_func(preview_text)
                win.destroy()
            else:
                lbl_preview.config(bg="#fff1f0", fg="#cc0000", text="请先选择按键！")
                win.after(
                    800,
                    lambda: lbl_preview.config(
                        bg="#ffffff", fg="#333333",
                        text=var_preview.get()
                        if var_preview.get() != "请先选择按键！"
                        else "请选择按键...",
                    ),
                )

        def cancel() -> None:
            win.destroy()

        confirm_btn = tk.Button(
            btn_frame, text="确定", command=confirm,
            bg="#f6ffed", fg="black",
            font=("Arial", 9), relief=tk.GROOVE,
        )
        confirm_btn.pack(side=tk.LEFT, padx=2, pady=2, fill=tk.X, expand=True)

        cancel_btn = tk.Button(
            btn_frame, text="取消", command=cancel,
            bg="#fff1f0", fg="black",
            font=("Arial", 9), relief=tk.GROOVE,
        )
        cancel_btn.pack(side=tk.RIGHT, padx=2, pady=2, fill=tk.X, expand=True)

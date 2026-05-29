"""
RPA自动化工具 - 主界面模块

提供 RPA 流程编辑器的主 GUI 界面，包括：
- 流程步骤的增删改查
- 拖拽排序
- 参数配置面板
- 运行控制（启动/停止）
- UI 日志轮询
- 全局热键监听
- 鼠标坐标实时显示
"""

import copy
import json
import os
import sys
import threading
import time
import uuid
from queue import Queue, Empty
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from PIL import Image, ImageTk

import pyautogui

from rpa.win_api import WinAPI
from rpa.vision import VisionEngine
from rpa.action_helper import ActionHelper
from rpa.label_manager import LabelManager
from rpa.ui_pickers import UIPickers
from rpa.test_lab import TestLabWindow, _find_window_by_title
from rpa.execution import ExecutionEngine
from rpa.config import (
    APP_TITLE, VERSION,
    TEMPLATES_DIR,
    HOTKEY_STOP,
    COORD_REFRESH_INTERVAL,
    HOTKEY_CHECK_INTERVAL,
    HOTKEY_COOLDOWN,
    MIN_SELECT_SIZE,
    OVERLAY_HIDE_DELAY,
    BUTTON_MAP,
    WINDOW_ACTIONS,
    NODE_TYPES,
    DEFAULT_CONFIDENCE,
    INPUT_TYPES, LOOP_MODES,
    DEFAULT_DATA_NAME, DEFAULT_START_INDEX, DEFAULT_LOOP_MODE,
    DATA_FILE_CACHE_TTL,
    OCR_DEFAULT_URL,
    THREAD_ERROR_RECOVERY_DELAY, THREAD_RECOVERY_DELAY,
    AI_DEFAULT_API_URL, AI_DEFAULT_MODEL, AI_DEFAULT_TIMEOUT,
)

from rpa.utils import image_pool
from rpa.icon_data import ICON_DATA


# UI 样式常量
BUTTON_STYLE = {"relief": tk.GROOVE, "font": ("Arial", 9)}
LABEL_STYLE = {"font": ("Arial", 9)}
ENTRY_STYLE = {"font": ("Arial", 9)}


class RPAGUI:
    """
    RPA 流程编辑器主界面类。

    管理整个应用的 UI 和交互逻辑。
    """

    def __init__(self, root: tk.Tk) -> None:
        """
        初始化主界面。

        Args:
            root: 主窗口 Tk 实例
        """
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("800x720")

        # 初始化图标相关属性
        self._icon_path = None
        self._icon_bitmap = None
        self._setup_window_icon()

    def _setup_window_icon(self) -> None:
        """设置窗口图标。"""
        try:
            from io import BytesIO
            icon_img = Image.open(BytesIO(ICON_DATA))
            icon_img = icon_img.resize((32, 32), Image.LANCZOS)
            self._icon_bitmap = ImageTk.PhotoImage(icon_img)
        except Exception:
            pass

        self.core = VisionEngine()
        self.stop_flag = False
        self._executing = False
        self.data: List[Dict[str, Any]] = []
        self.tree_map: Dict[str, Dict[str, Any]] = {}
        self.drag_data: Optional[Dict[str, Any]] = None
        self.clipboard: List[Dict[str, Any]] = []  # 复制粘贴剪贴板
        self.runtime_vars: Dict[str, Any] = {}
        self.runtime_bg_hwnd: Optional[int] = None
        self.runtime_bg_title: str = ''
        self._undo_stack: List[List[Dict[str, Any]]] = []  # 撤销历史栈
        self._redo_stack: List[List[Dict[str, Any]]] = []  # 重做历史栈
        self._max_undo = 20  # 最大撤销次数
        self._editing_step_data: Optional[Dict[str, Any]] = None  # 当前编辑的步骤数据
        self._modified: bool = False  # 是否有未保存的修改

        self.global_step_counter = 1
        self.mouse_position: Tuple[int, int] = (0, 0)
        self.drag_feedback_item: Optional[str] = None
        self.drag_highlight_items: List[str] = []

        self.picking_coord_var: Optional[tk.StringVar] = None
        self.picking_region_var: Optional[tk.StringVar] = None

        self.current_flow_name = "未命名"
        self.current_flow_dir = "未命名"

        # 等待节点相关的 Frame 引用
        self._wait_image_frame: Optional[tk.Frame] = None
        self._wait_window_frame: Optional[tk.Frame] = None
        self._confidence_frame: Optional[tk.Frame] = None
        self._color_sensitive_frame: Optional[tk.Frame] = None
        self._timeout_frame: Optional[tk.Frame] = None

        # 数据变量缓存，用于优化输入节点的响应速度
        self.data_vars_cache: Optional[Tuple[Set[str], Dict[str, List[str]]]] = None
        self.cache_timestamp: float = 0
        self.file_fields_cache: Dict[str, List[str]] = {}

        self.setup_ui()
        self.refresh_tree()
        self.ui_queue: Queue = Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[ExecutionEngine] = None

        # 日志文件（运行日志保存）
        self.run_log_file = None
        log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.run_log_path = os.path.join(log_dir, 'rpa_run.log')
        try:
            self.run_log_file = open(self.run_log_path, 'w', encoding='utf-8')
        except Exception:
            pass

        # 启动 UI 日志轮询
        try:
            self.start_ui_poll()
        except Exception:
            pass

        self.hotkey_thread = threading.Thread(
            target=self.monitor_global_hotkey, daemon=True)
        self.hotkey_thread.start()

        self.mouse_monitor_thread = threading.Thread(
            target=self.monitor_mouse_position, daemon=True)
        self.mouse_monitor_thread.start()

        # 窗口关闭时保存日志
        def on_app_close() -> None:
            if self.run_log_file:
                try:
                    self.run_log_file.close()
                except Exception:
                    pass
            if self._modified:
                result = messagebox.askyesnocancel(
                    "提示", "流程尚未保存，是否保存？")
                if result is None:  # 取消
                    return
                if result:  # 是
                    self.save_json()
                    # 如果用户取消了保存对话框，不关闭窗口
                    if self._modified:
                        return
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_app_close)

    # 辅助方法
    @staticmethod
    def _safe_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """安全调用函数，捕获所有异常。"""
        try:
            func(*args, **kwargs)
        except Exception:
            pass

    def _write_log(self, message: str) -> None:
        """写入日志文件。"""
        if self.run_log_file:
            self._safe_call(self._write_log_impl, message)

    def _write_log_impl(self, message: str) -> None:
        """写入日志文件实现。"""
        timestamp = time.strftime("%H:%M:%S")
        self.run_log_file.write(f"[{timestamp}] {message}\n")
        self.run_log_file.flush()

    def _handle_log_event(self, ev: Dict[str, Any]) -> None:
        """处理日志事件。"""
        msg = ev.get("msg", "")
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {msg}"
        self.txt_log.insert(tk.END, log_line + "\n")
        self.txt_log.see(tk.END)
        self._write_log(msg)

    # UI 构建
    def setup_ui(self) -> None:
        """构建主界面布局。"""
        # 菜单
        menu = tk.Menu(self.root)
        self.root.config(menu=menu)
        file_menu = tk.Menu(menu, tearoff=0)
        menu.add_cascade(label="流程管理", menu=file_menu)
        file_menu.add_command(
            label="\U0001f4c4 新建流程", command=self.new_file)
        file_menu.add_separator()
        file_menu.add_command(
            label="\U0001f4be 保存流程", command=self.save_json)
        file_menu.add_separator()
        file_menu.add_command(
            label="\U0001f4c2 加载流程", command=self.load_json)

        # 坐标显示
        self.coord_frame = tk.Frame(
            self.root, bg="#f0f0f0", bd=1, relief=tk.SUNKEN)
        self.coord_frame.place(relx=0.99, rely=0.01, anchor="ne")
        self.coord_label = tk.Label(
            self.coord_frame, text="坐标: (0, 0)",
            bg="#f0f0f0", font=("Arial", 10))
        self.coord_label.pack(padx=8, pady=4)

        # 主分割面板
        self.paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL, sashwidth=6, bg="#d9d9d9")
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_frame = tk.Frame(self.paned)
        self.paned.add(left_frame, minsize=300, stretch="always")

        # 左侧垂直分割面板
        left_paned = ttk.Panedwindow(left_frame, orient=tk.VERTICAL)
        left_paned.pack(fill=tk.BOTH, expand=True)

        # 流程列表区域
        tree_frame = tk.Frame(left_paned)
        left_paned.add(tree_frame, weight=3)

        self.tree = ttk.Treeview(
            tree_frame, columns=("desc", "var"), selectmode="extended")
        self.tree.heading("#0", text="流程步骤")
        self.tree.heading("desc", text="参数 / 标记")
        self.tree.heading("var", text="输出")
        self.tree.column("#0", width=100)
        self.tree.column("desc", width=300)
        self.tree.column("var", width=80)

        ysb = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=ysb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.tag_configure(
            'drag_source', background='#fff7e6', foreground='#ff7a45')
        self.tree.tag_configure(
            'drag_target_container',
            background='#f6ffed', foreground='#52c41a')
        self.tree.tag_configure(
            'drag_target_step',
            background='#e6f7ff', foreground='#1890ff')
        self.tree.tag_configure(
            'drag_indicator',
            background='#f0f0f0', foreground='#666',
            font=('Arial', 9, 'italic'))

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<ButtonPress-1>", self.on_drag_start)
        self.tree.bind("<B1-Motion>", self.on_drag_motion)
        self.tree.bind("<ButtonRelease-1>", self.on_drag_release)
        self.tree.bind("<Delete>", lambda e: self.delete_step())
        # 复制粘贴快捷键
        self.tree.bind("<Control-x>", lambda e: self.cut_steps())
        self.tree.bind("<Control-c>", lambda e: self.copy_steps())
        self.tree.bind("<Control-v>", lambda e: self.paste_steps())
        # 撤销快捷键
        self.root.bind("<Control-z>", lambda e: self.undo())
        # 右键菜单
        self.tree.bind("<Button-3>", self.on_tree_right_click)

        # 运行日志
        lb_run = tk.LabelFrame(left_paned, text="运行日志", padx=5, pady=5)
        left_paned.add(lb_run, weight=1)

        run_btn_frame = tk.Frame(lb_run)
        run_btn_frame.pack(fill=tk.X)
        self.btn_start = tk.Button(
            run_btn_frame, text="\u25b6 开始运行",
            command=self.start_run, bg="#f6ffed", relief=tk.GROOVE)
        self.btn_start.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=2)
        self.btn_stop = tk.Button(
            run_btn_frame,
            text=f"\u23f9 停止运行 ({HOTKEY_STOP.upper()})",
            command=self.stop_run, relief=tk.GROOVE, state=tk.DISABLED)
        self.btn_stop.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=2)

        self.txt_log = scrolledtext.ScrolledText(
            lb_run, height=10, font=('Arial', 9))
        self.txt_log.pack(fill=tk.BOTH, expand=True, pady=5)

        # 右侧面板
        right_frame = tk.Frame(self.paned)
        self.paned.add(right_frame, minsize=500, stretch="never")

        lb_tools = tk.LabelFrame(right_frame, text="工具箱", padx=5, pady=5)
        lb_tools.pack(fill=tk.X, pady=5)
        btn_tool_frame = tk.Frame(lb_tools)
        btn_tool_frame.pack(fill=tk.X)
        tk.Button(
            btn_tool_frame, text="\U0001f4f7 截取模板",
            command=self.capture_template, bg="#d9f7be",
            relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=2)
        tk.Button(
            btn_tool_frame, text="\U0001f50d 测试识别",
            command=self.test_recognition, bg="#fff0f6",
            relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2, pady=2)

        paned_params_log = ttk.Panedwindow(
            right_frame, orient=tk.VERTICAL)
        paned_params_log.pack(fill=tk.BOTH, expand=True, pady=5)

        lb_cfg = tk.LabelFrame(
            paned_params_log, text="步骤参数", padx=5, pady=5)
        paned_params_log.add(lb_cfg, weight=3)

        # 操作按钮 - 第一排（剪切、复制、粘贴、撤消）
        btn_crud_frame = tk.Frame(lb_cfg)
        btn_crud_frame.pack(fill=tk.X, pady=(0, 5))
        tk.Button(
            btn_crud_frame, text="\U00002702\ufe0f 剪切",
            command=self.cut_steps, bg="#f0f5ff", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(
            btn_crud_frame, text="\U0001f5b1 复制",
            command=self.copy_steps, bg="#f0f5ff", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(
            btn_crud_frame, text="\U0001f4e5 粘贴插入",
            command=self.paste_steps, bg="#f6ffed", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(
            btn_crud_frame, text="\u21a9\ufe0f 撤消",
            command=self.undo, bg="#fff7e6", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # 操作按钮 - 第二排（插入、删除、导入）
        btn_crud_frame2 = tk.Frame(lb_cfg)
        btn_crud_frame2.pack(fill=tk.X, pady=(0, 5))
        tk.Button(
            btn_crud_frame2, text="\u2795 插入步骤",
            command=self.add_step, bg="#e6f7ff", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(
            btn_crud_frame2, text="\u274c 删除步骤",
            command=self.delete_step, bg="#fff1f0", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        tk.Button(
            btn_crud_frame2, text="\U0001f4e5 导入流程",
            command=self.import_json, bg="#f0f5ff", relief=tk.GROOVE
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # 状态栏（添加到主流程 + OCR 状态）
        status_frame = tk.Frame(lb_cfg)
        status_frame.pack(anchor=tk.W, fill=tk.X, pady=(0, 5))
        self.lbl_status = tk.Label(status_frame, text="添加到主流程 ", fg="blue")
        self.lbl_status.pack(side=tk.LEFT)
        # OCR 状态：圆点放在"【OCR状态："后面
        ocr_frame = tk.Frame(status_frame)
        ocr_frame.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(ocr_frame, text="【OCR状态：", fg="blue").pack(side=tk.LEFT)
        self.ocr_dot_var = tk.StringVar(value="\u25cf")
        self.ocr_dot_label = tk.Label(
            ocr_frame, textvariable=self.ocr_dot_var,
            fg="#52c41a", font=("Arial", 14))
        self.ocr_dot_label.pack(side=tk.LEFT)
        self.ocr_status_var = tk.StringVar(value="就绪】")
        self.ocr_status_label = tk.Label(
            ocr_frame, textvariable=self.ocr_status_var, fg="blue")
        self.ocr_status_label.pack(side=tk.LEFT)
        self._check_ocr_service()

        tk.Label(lb_cfg, text="节点类型:").pack(anchor=tk.W)
        self.cb_type = ttk.Combobox(
            lb_cfg, values=NODE_TYPES, state="readonly")
        self.cb_type.pack(fill=tk.X, pady=(0, 5))
        self.cb_type.current(0)
        self.cb_type.bind("<<ComboboxSelected>>", self.update_params_ui)

        self.frm_params = tk.Frame(lb_cfg)
        self.frm_params.pack(fill=tk.BOTH, expand=True, pady=10)
        self.param_vars: Dict[str, Any] = {}

        # 用于收集所有局部预览的刷新回调
        self._preview_refresh_callbacks: List[Callable[..., None]] = []

        self.update_params_ui()

    def _check_ocr_service(self) -> None:
        """检查 OCR 服务状态并更新界面显示。"""
        import base64
        import io
        import requests
        try:
            # 创建一个最小的空白图片进行测试
            from PIL import Image
            test_img = Image.new('RGB', (10, 10), color='white')
            buf = io.BytesIO()
            test_img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()

            response = requests.post(
                'http://127.0.0.1:1224/api/ocr',
                json={
                    "base64": b64,
                    "options": {
                        "ocr.language": "models/config_chinese.txt"
                    },
                },
                timeout=3
            )
            if response.status_code == 200:
                self.ocr_status_var.set("就绪】")
                self.ocr_dot_label.config(fg="#52c41a")  # 绿色圆点
            else:
                self.ocr_status_var.set("异常】")
                self.ocr_dot_label.config(fg="#ff4d4f")  # 红色圆点
        except Exception:
            self.ocr_status_var.set("未连接】")
            self.ocr_dot_label.config(fg="#ff4d4f")  # 红色圆点

    def _refresh_ocr_status(self) -> None:
        """刷新 OCR 服务状态（供外部调用）。"""
        self._check_ocr_service()

    # 后台线程
    def monitor_global_hotkey(self) -> None:
        """全局热键监听线程。"""
        while True:
            try:
                ctrl_down = (
                    WinAPI.user32.GetAsyncKeyState(WinAPI.VK_CONTROL)
                    & 0x8000)
                q_down = (
                    WinAPI.user32.GetAsyncKeyState(WinAPI.VK_Q) & 0x8000)
                if ctrl_down and q_down:
                    if not self.stop_flag:
                        self.root.after(
                            0, lambda: self.log(
                                "\U0001f6d1 【全局热键】检测到 Ctrl+Q，"
                                "正在停止..."))
                        self.root.after(0, self.stop_run)
                        time.sleep(HOTKEY_COOLDOWN)
                time.sleep(HOTKEY_CHECK_INTERVAL)
            except Exception:
                time.sleep(THREAD_ERROR_RECOVERY_DELAY)

    def monitor_mouse_position(self) -> None:
        """鼠标坐标监听线程。"""
        while True:
            try:
                x, y = pyautogui.position()
                if (x, y) != self.mouse_position:
                    self.mouse_position = (x, y)
                    self.root.after(
                        0,
                        lambda x=x, y=y: self.coord_label.config(
                            text=f"坐标: ({x}, {y})"))
                time.sleep(COORD_REFRESH_INTERVAL)
            except Exception:
                time.sleep(THREAD_RECOVERY_DELAY)

    # 输出变量收集
    def get_all_output_vars(self) -> List[str]:
        """
        收集流程中所有输出变量名（包括变量节点定义的变量）。

        Returns:
            已排序的变量名列表
        """
        vars_set: Set[str] = set()

        def traverse(step_list: List[Dict[str, Any]]) -> None:
            for step in step_list:
                if 'params' in step:
                    # 从变量节点收集定义的变量
                    if step.get('type') == '变量管理' and 'variables' in step['params']:
                        for var in step['params']['variables']:
                            if var.get('name'):
                                vars_set.add(var['name'])
                    # 从其他节点收集使用的变量
                    if 'output_var' in step['params']:
                        v = step['params']['output_var'].strip()
                        if v:
                            vars_set.add(v)
                    if 'input_region_var' in step['params']:
                        v = step['params']['input_region_var'].strip()
                        if v and not v.startswith("("):
                            vars_set.add(v)
                if 'true' in step:
                    traverse(step['true'])
                if 'false' in step:
                    traverse(step['false'])
                if 'body' in step:
                    traverse(step['body'])

        traverse(self.data)
        return sorted(list(vars_set))

    def get_fields_from_file(self, data_file: str) -> List[str]:
        """
        从数据文件中读取字段名（带缓存）。

        Args:
            data_file: 数据文件路径

        Returns:
            字段名列表
        """
        try:
            import pandas as pd

            # 检查缓存
            cache_key = data_file
            if cache_key in self.file_fields_cache:
                return self.file_fields_cache[cache_key]

            # 读取文件
            if data_file.endswith('.csv'):
                df = pd.read_csv(data_file, encoding='utf-8')
            elif data_file.endswith('.xls') or data_file.endswith('.xlsx'):
                df = pd.read_excel(data_file)
            else:
                return []

            fields = list(df.columns)
            # 缓存字段名
            self.file_fields_cache[cache_key] = fields
            return fields
        except Exception:
            return []

    def get_data_vars_info(self) -> Tuple[Set[str], Dict[str, List[str]]]:
        """
        获取所有数据变量信息（使用类级别的缓存）。

        Returns:
            (数据名称集合, 字段名映射字典)
        """
        import time

        # 简单的缓存失效机制：每次调用时更新时间戳
        # 如果数据没有变化，缓存会保持有效
        current_time = time.time()

        # 检查缓存是否有效（60秒内）
        if (
            self.data_vars_cache is not None
            and (current_time - self.cache_timestamp) < DATA_FILE_CACHE_TTL
        ):
            return self.data_vars_cache

        # 缓存失效，重新计算
        data_names: Set[str] = set()
        fields_map: Dict[str, List[str]] = {}

        # 遍历所有步骤，查找数据循环节点
        def traverse_steps(steps: List[Dict[str, Any]]) -> None:
            for step in steps:
                if step['type'] == '数据循环':
                    data_name = step['params'].get('data_name', DEFAULT_DATA_NAME)
                    data_names.add(data_name)
                    # 尝试从数据文件中获取字段名
                    data_file = step['params'].get('data_file', '')
                    if data_file:
                        fields = self.get_fields_from_file(data_file)
                        if fields:
                            fields_map[data_name] = fields
                if 'true' in step:
                    traverse_steps(step['true'])
                if 'false' in step:
                    traverse_steps(step['false'])
                if 'body' in step:
                    traverse_steps(step['body'])

        traverse_steps(self.data)

        # 如果没有找到数据循环节点，使用默认值
        if not data_names:
            data_names = {DEFAULT_DATA_NAME}
            fields_map[DEFAULT_DATA_NAME] = ['字段1', '字段2', '字段3']

        # 更新缓存
        self.data_vars_cache = (data_names, fields_map)
        self.cache_timestamp = current_time

        return self.data_vars_cache

    def get_all_defined_vars(self) -> List[Tuple[str, str]]:
        """
        获取所有已定义的变量名和初始值。

        从所有节点中收集变量定义，包括：
        - 变量管理节点的变量
        - 其他节点的输出变量（如OCR、点击等）

        Returns:
            已定义变量列表，每项为 (变量名, 初始值描述) 元组
        """
        defined_vars: Dict[str, str] = {}

        def traverse_steps(steps: List[Dict[str, Any]]) -> None:
            for step in steps:
                step_type = step.get('type', '')
                params = step.get('params', {})

                # 变量管理节点
                if step_type == '变量管理':
                    variables = params.get('variables', [])
                    for var in variables:
                        name = var.get('name', '').strip()
                        if name:
                            var_type = var.get('type', '字符串')
                            value = var.get('value', '')
                            if var_type == '字符串':
                                initial = f'"{value}"' if value else '""'
                            elif var_type == '布尔':
                                initial = value if value else 'False'
                            elif var_type == '日期':
                                initial = f'"{value}"' if value else '""'
                            else:
                                initial = value if value else '0'
                            defined_vars[name] = initial

                # OCR节点 - 输出变量
                if step_type == 'OCR':
                    output_var = params.get('output_var', '').strip()
                    if output_var:
                        defined_vars[output_var] = 'OCR文本'

                # 点击/等待/输入节点 - 输出变量
                if step_type in ('点击', '等待', '输入'):
                    output_var = params.get('output_var', '').strip()
                    if output_var:
                        defined_vars[output_var] = '识别文本'

                # 遍历子节点
                if 'true' in step:
                    traverse_steps(step['true'])
                if 'false' in step:
                    traverse_steps(step['false'])
                if 'body' in step:
                    traverse_steps(step['body'])

        traverse_steps(self.data)

        return [(name, defined_vars[name]) for name in sorted(defined_vars.keys())]

    # 参数 UI 构建
    def _create_refresh_preview_callback(
        self,
        var: tk.StringVar,
        canvas_preview: tk.Canvas,
        preview_width: int = 80,
        preview_height: int = 40,
    ) -> Callable[..., None]:
        """
        创建统一的图片预览刷新回调。

        使用 ImagePreviewPool 缓存缩略图对象，避免内存泄漏。

        Args:
            var: 关联的模板名称变量
            canvas_preview: 预览画布
            preview_width: 预览宽度
            preview_height: 预览高度

        Returns:
            刷新回调函数
        """
        def get_image_path() -> Optional[str]:
            img_name = var.get().strip()
            if not img_name:
                return None
            if not img_name.endswith('.png'):
                test_path = os.path.join(
                    self.core.templates_dir, img_name + '.png')
                if os.path.exists(test_path):
                    img_name += '.png'
            return os.path.join(self.core.templates_dir, img_name)

        def show_large_view(event: tk.Event) -> None:
            full_path = get_image_path()
            if not full_path or not os.path.exists(full_path):
                return
            top = tk.Toplevel(self.root)
            top.title(f"预览: {os.path.basename(full_path)}")
            try:
                img = Image.open(full_path)
                tk_img = ImageTk.PhotoImage(img)
                label = tk.Label(top, image=tk_img)
                label.pack()
                label.image = tk_img
            except Exception:
                top.destroy()

        canvas_preview.bind("<Double-Button-1>", show_large_view)

        def refresh_preview(*args: object) -> None:
            try:
                canvas_preview.delete("all")
                # 绘制虚线边框
                canvas_preview.create_rectangle(
                    1, 1, preview_width - 1, preview_height - 1,
                    outline="#999999", dash=(3, 2)
                )
                full_path = get_image_path()
                if full_path and os.path.exists(full_path):
                    try:
                        # 使用 ImagePreviewPool 获取缓存的缩略图
                        tk_image = image_pool.get_thumbnail(
                            full_path, (preview_width, preview_height)
                        )

                        if tk_image:
                            canvas_preview.create_image(
                                0, 0, anchor=tk.NW, image=tk_image)
                            # 保持引用防止垃圾回收
                            canvas_preview.image = tk_image

                            img_x = (preview_width - tk_image.width()) // 2
                            img_y = (preview_height - tk_image.height()) // 2
                            canvas_preview.coords(
                                canvas_preview.find_all()[-1], img_x, img_y)

                            center_x = img_x + tk_image.width() // 2
                            center_y = img_y + tk_image.height() // 2
                            marker_size = 4

                            try:
                                offset_x = int(
                                    self.param_vars.get(
                                        'offset_x',
                                        tk.StringVar(value='0')).get())
                            except (ValueError, KeyError):
                                offset_x = 0
                            try:
                                offset_y = int(
                                    self.param_vars.get(
                                        'offset_y',
                                        tk.StringVar(value='0')).get())
                            except (ValueError, KeyError):
                                offset_y = 0

                            offset_point_x = center_x + offset_x
                            offset_point_y = center_y + offset_y

                            canvas_preview.create_oval(
                                center_x - marker_size, center_y - marker_size,
                                center_x + marker_size, center_y + marker_size,
                                fill="#0000ff", outline="white", width=2,
                            )
                            canvas_preview.create_oval(
                                offset_point_x - marker_size,
                                offset_point_y - marker_size,
                                offset_point_x + marker_size,
                                offset_point_y + marker_size,
                                fill="#ff0000", outline="white", width=2,
                            )
                        else:
                            # 缓存失败，显示损坏
                            if canvas_preview.winfo_exists():
                                canvas_preview.create_text(
                                    preview_width // 2, preview_height // 2,
                                    text="损坏", anchor=tk.CENTER)
                    except Exception:
                        if canvas_preview.winfo_exists():
                            canvas_preview.create_text(
                                preview_width // 2, preview_height // 2,
                                text="损坏", anchor=tk.CENTER)
                else:
                    if canvas_preview.winfo_exists():
                        canvas_preview.create_text(
                            preview_width // 2, preview_height // 2,
                            text="预览", anchor=tk.CENTER)
            except Exception:
                pass

        return refresh_preview

    def safe_refresh_preview(
        self, refresh_func: Callable[..., None],
    ) -> None:
        """
        安全调用刷新预览函数，防止组件已销毁。

        Args:
            refresh_func: 刷新回调函数
        """
        try:
            refresh_func()
        except Exception:
            pass

    def update_params_ui(self, event: Optional[tk.Event] = None,
                          existing_params: Optional[Dict[str, Any]] = None) -> None:
        """
        根据当前选择的节点类型更新参数 UI。

        Args:
            event: 事件对象（可选）
            existing_params: 已有的参数字典（可选）
        """
        for w in self.frm_params.winfo_children():
            w.destroy()
        self.param_vars = {}
        t = self.cb_type.get()
        p = existing_params if existing_params else {}

        existing_vars = self.get_all_output_vars()
        coord_vars = [""] + existing_vars

        def add_row(
            label: str,
            key: str,
            default: str = "",
            placeholder: Optional[str] = None,
            show_edit_button: bool = False,
            is_var_combo: bool = False,
            parent: Optional[tk.Widget] = None,
        ) -> None:
            container = parent if parent else self.frm_params
            row = tk.Frame(container)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, width=12, anchor=tk.E).pack(side=tk.LEFT)

            var = tk.StringVar(value=default)
            self.param_vars[key] = var

            if is_var_combo:
                ttk.Combobox(
                    row, textvariable=var, values=coord_vars
                ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            else:
                entry = tk.Entry(row, textvariable=var)
                entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            if show_edit_button:
                if key == "key":
                    tk.Button(
                        row, text="\u2328\ufe0f", width=3,
                        command=lambda v=var: self.select_key(v),
                    ).pack(side=tk.LEFT, padx=2)
                elif key == "window_title":
                    tk.Button(
                        row, text="\U0001f3af", width=3,
                        command=lambda v=var: self.start_window_spy(v),
                        bg="#ffccc7",
                    ).pack(side=tk.LEFT, padx=2)
                elif key in ["template", "exit_template", "value"] and not is_var_combo:
                    canvas_preview = tk.Canvas(
                        row, bg="#f0f0f0", width=80, height=40,
                        bd=0, cursor="hand2",
                        highlightthickness=0,
                    )
                    canvas_preview.pack(side=tk.RIGHT, padx=(4, 2))

                    refresh_cb = self._create_refresh_preview_callback(
                        var, canvas_preview, 80, 40)
                    var.trace_add("write", refresh_cb)
                    self.root.after(
                        10,
                        lambda: self.safe_refresh_preview(refresh_cb))
                    try:
                        self._preview_refresh_callbacks.append(refresh_cb)
                    except Exception:
                        pass

                    tk.Button(
                        row, text="\U0001f4c2", width=3,
                        command=lambda v=var: self.select_image_file(v),
                        relief=tk.GROOVE,
                    ).pack(side=tk.RIGHT, padx=2)

            if placeholder:
                tk.Label(
                    row, text=placeholder, fg="gray", font=("Arial", 8),
                ).pack(side=tk.RIGHT)

        def add_combo(
            label: str,
            key: str,
            values: List[str],
            default_val: Optional[str] = None,
            parent: Optional[tk.Widget] = None,
        ) -> None:
            container = parent if parent else self.frm_params
            row = tk.Frame(container)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, width=12, anchor=tk.E).pack(side=tk.LEFT)
            val = default_val if default_val else (values[0] if values else "")
            var = tk.StringVar(value=val)
            ttk.Combobox(
                row, textvariable=var, values=values, state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.param_vars[key] = var

        def add_bg_check(
            for_image_find: bool = False,
            parent: Optional[tk.Widget] = None,
        ) -> None:
            container = parent if parent else self.frm_params
            row = tk.Frame(container)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text="模式:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.BooleanVar(value=False)
            self.param_vars['use_bg'] = var
            text_info = "后台操作 (需先运行[窗口]节点激活目标)"
            if for_image_find:
                text_info = "后台找图 (需先运行[窗口]节点激活目标)"
            tk.Checkbutton(
                row, text=text_info, variable=var,
            ).pack(side=tk.LEFT, anchor=tk.W)

        def add_output_var(
            parent: Optional[tk.Widget] = None,
        ) -> tk.LabelFrame:
            container = parent if parent else self.frm_params
            output_frame = tk.LabelFrame(
                container, text="输出参数", padx=5, pady=5)
            output_frame.pack(fill=tk.X, pady=5)
            add_row(
                "坐标变量:", "output_var",
                placeholder="例如: found_pos",
                parent=output_frame)
            return output_frame

        def add_input_region_var(
            parent: Optional[tk.Widget] = None,
        ) -> None:
            container = parent if parent else self.frm_params
            row = tk.Frame(container)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text="查找区域:", width=12, anchor=tk.E).pack(
                side=tk.LEFT)
            var = tk.StringVar(value="")
            self.param_vars['input_region_var'] = var
            cb = ttk.Combobox(
                row, textvariable=var, values=[""] + existing_vars,
            )
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            cb.bind('<FocusIn>', lambda e, v=var: self._show_region_overlay_on_screen(v))
            tk.Button(
                row, text="\u2702\ufe0f", width=3,
                command=lambda v=var: self.start_region_picker(v),
                bg="#e0eaff", relief=tk.GROOVE,
            ).pack(side=tk.RIGHT, padx=2)

        def _call_all_previews(*_: object) -> None:
            for cb in list(getattr(self, '_preview_refresh_callbacks', [])):
                try:
                    self.safe_refresh_preview(cb)
                except Exception:
                    pass

        if t == "点击":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            add_row("图片:", "template", show_edit_button=True, parent=node_frame)
            add_input_region_var(parent=node_frame)

            # 查找第N个、相似度
            find_row = tk.Frame(node_frame)
            find_row.pack(fill=tk.X, pady=2)
            for col in range(2):
                find_row.grid_columnconfigure(col, weight=1)

            find_nth_frame = tk.Frame(find_row)
            find_nth_frame.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))
            tk.Label(find_nth_frame, text="查找第N个:", width=10, anchor=tk.E).pack(side=tk.LEFT)
            find_nth_var = tk.StringVar(value="1")
            self.param_vars["find_nth"] = find_nth_var
            tk.Entry(find_nth_frame, textvariable=find_nth_var, width=6).pack(side=tk.LEFT)

            confidence_frame = tk.Frame(find_row)
            confidence_frame.grid(row=0, column=1, sticky=tk.EW, padx=(5, 0))
            tk.Label(confidence_frame, text="相似度阈值:", width=10, anchor=tk.E).pack(side=tk.LEFT)
            confidence_var = tk.StringVar(value="0.95")
            self.param_vars["confidence"] = confidence_var
            tk.Entry(confidence_frame, textvariable=confidence_var, width=6).pack(side=tk.LEFT)

            # X/Y 坐标偏差
            offset_row = tk.Frame(node_frame)
            offset_row.pack(fill=tk.X, pady=2)
            offset_row.grid_columnconfigure(0, weight=1)
            offset_row.grid_columnconfigure(1, weight=1)

            offset_x_frame = tk.Frame(offset_row)
            offset_x_frame.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))
            tk.Label(offset_x_frame, text="X坐标偏差:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            offset_x_var = tk.StringVar(value="0")
            self.param_vars["offset_x"] = offset_x_var
            tk.Entry(offset_x_frame, textvariable=offset_x_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            offset_y_frame = tk.Frame(offset_row)
            offset_y_frame.grid(row=0, column=1, sticky=tk.EW)
            tk.Label(offset_y_frame, text="Y坐标偏差:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            offset_y_var = tk.StringVar(value="0")
            self.param_vars["offset_y"] = offset_y_var
            tk.Entry(offset_y_frame, textvariable=offset_y_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            # 颜色验证
            color_verify_row = tk.Frame(node_frame)
            color_verify_row.pack(fill=tk.X, pady=(5, 2))
            tk.Label(color_verify_row, text="颜色验证:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            color_enable_var = tk.BooleanVar(value=False)
            self.param_vars['color_enable'] = color_enable_var
            tk.Checkbutton(
                color_verify_row, text="启用",
                variable=color_enable_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            color_input_frame = tk.Frame(node_frame)
            color_input_frame.pack(fill=tk.X, pady=2)
            tk.Label(color_input_frame, text="目标颜色:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            color_var = tk.StringVar(value="")
            self.param_vars['target_color'] = color_var
            tk.Entry(color_input_frame, textvariable=color_var, width=12).pack(side=tk.LEFT, padx=(2, 5))
            tk.Button(
                color_input_frame, text="\U0001f3a8", width=3,
                command=lambda: self.start_color_picker(color_var),
                bg="#ffe0b2", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)
            tk.Label(color_input_frame, text="偏差:").pack(side=tk.LEFT, padx=(10, 2))
            tolerance_var = tk.StringVar(value="10")
            self.param_vars['color_tolerance'] = tolerance_var
            tk.Entry(color_input_frame, textvariable=tolerance_var, width=5).pack(side=tk.LEFT)

            # 颜色匹配
            color_row = tk.Frame(node_frame)
            color_row.pack(fill=tk.X, pady=2)
            tk.Label(color_row, text="颜色匹配:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            color_sensitive_var = tk.BooleanVar(value=True)
            self.param_vars['color_sensitive'] = color_sensitive_var
            tk.Checkbutton(
                color_row, text="启用验证",
                variable=color_sensitive_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(for_image_find=True, parent=node_frame)

            action_frame = tk.LabelFrame(self.frm_params, text="动作设置", padx=5, pady=5)
            action_frame.pack(fill=tk.X, pady=5)

            button_row = tk.Frame(action_frame)
            button_row.pack(fill=tk.X, pady=2)
            tk.Label(button_row, text="按键:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            button_var = tk.StringVar(value="仅识别")
            self.param_vars["button"] = button_var
            ttk.Combobox(
                button_row, textvariable=button_var,
                values=["仅识别", "左键单击", "右键单击", "左键双击"],
                state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            add_output_var()

            # 监听 offset 变化
            try:
                if 'offset_x' in self.param_vars:
                    self.param_vars['offset_x'].trace_add('write', _call_all_previews)
                if 'offset_y' in self.param_vars:
                    self.param_vars['offset_y'].trace_add('write', _call_all_previews)
            except Exception:
                pass

        elif t == "等待":
            add_combo("等待类型:", "wait_type", ["等待图片", "等待窗口"], default_val="等待图片")
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            self._wait_node_frame = node_frame

            # 等待图片参数
            wait_image_frame = tk.Frame(node_frame)
            self._wait_image_frame = wait_image_frame

            add_row("图片:", "template", show_edit_button=True, parent=wait_image_frame)
            add_input_region_var(parent=wait_image_frame)

            conf_row = tk.Frame(wait_image_frame)
            conf_row.pack(fill=tk.X, pady=2)
            tk.Label(conf_row, text="相似度阈值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="0.95")
            self.param_vars['confidence'] = var
            tk.Entry(conf_row, textvariable=var, width=8).pack(side=tk.LEFT, padx=(2, 10))
            tk.Label(conf_row, text="超时(秒):", width=10, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="10")
            self.param_vars['timeout'] = var
            tk.Entry(conf_row, textvariable=var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            # X/Y 偏移
            offset_row = tk.Frame(wait_image_frame)
            offset_row.pack(fill=tk.X, pady=2)
            offset_row.grid_columnconfigure(0, weight=1)
            offset_row.grid_columnconfigure(1, weight=1)

            offset_x_frame = tk.Frame(offset_row)
            offset_x_frame.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))
            tk.Label(offset_x_frame, text="X坐标偏差:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            offset_x_var = tk.StringVar(value="0")
            self.param_vars["offset_x"] = offset_x_var
            tk.Entry(offset_x_frame, textvariable=offset_x_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            offset_y_frame = tk.Frame(offset_row)
            offset_y_frame.grid(row=0, column=1, sticky=tk.EW)
            tk.Label(offset_y_frame, text="Y坐标偏差:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            offset_y_var = tk.StringVar(value="0")
            self.param_vars["offset_y"] = offset_y_var
            tk.Entry(offset_y_frame, textvariable=offset_y_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            color_row = tk.Frame(wait_image_frame)
            color_row.pack(fill=tk.X, pady=2)
            tk.Label(color_row, text="颜色匹配:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            color_var = tk.BooleanVar(value=True)
            self.param_vars['color_sensitive'] = color_var
            tk.Checkbutton(
                color_row, text="启用验证",
                variable=color_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(for_image_find=True, parent=wait_image_frame)

            # 等待窗口参数
            wait_window_frame = tk.Frame(node_frame)
            self._wait_window_frame = wait_window_frame

            win_row = tk.Frame(wait_window_frame)
            win_row.pack(fill=tk.X, pady=2)
            tk.Label(win_row, text="窗口标题:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="")
            self.param_vars['window_title'] = var
            tk.Entry(win_row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 5))
            tk.Button(
                win_row, text="\U0001f3af", width=3,
                command=lambda v=var: self.start_window_spy(v),
                bg="#ffccc7", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            cond_row = tk.Frame(wait_window_frame)
            cond_row.pack(fill=tk.X, pady=2)
            tk.Label(cond_row, text="等待条件:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="窗口出现")
            self.param_vars['window_condition'] = var
            ttk.Combobox(
                cond_row, textvariable=var,
                values=["窗口出现", "窗口消失"], state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            time_row = tk.Frame(wait_window_frame)
            time_row.pack(fill=tk.X, pady=2)
            tk.Label(time_row, text="超时(秒):", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="30")
            self.param_vars['timeout_window'] = var
            tk.Entry(time_row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            out_row = tk.Frame(self.frm_params)
            out_row.pack(fill=tk.X, pady=5)
            tk.Label(out_row, text="输出变量:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            out_var = tk.StringVar(value="")
            self.param_vars['output_var'] = out_var
            ttk.Combobox(
                out_row, textvariable=out_var, values=coord_vars,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 动态显示/隐藏
            def update_wait_visibility(*args: object) -> None:
                wait_type_value = self.param_vars['wait_type'].get()
                if wait_type_value == '等待图片':
                    wait_image_frame.pack(fill=tk.X, pady=2)
                    wait_window_frame.pack_forget()
                else:
                    wait_image_frame.pack_forget()
                    wait_window_frame.pack(fill=tk.X, pady=2)

            if 'wait_type' in self.param_vars:
                self.param_vars['wait_type'].trace_add("write", update_wait_visibility)
            update_wait_visibility()

        elif t == "输入":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            # 输入类型选择
            input_type_row = tk.Frame(node_frame)
            input_type_row.pack(fill=tk.X, pady=2)
            tk.Label(input_type_row, text="输入类型:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            input_type_var = tk.StringVar(value="直接输入")
            self.param_vars['input_type'] = input_type_var
            ttk.Combobox(
                input_type_row, textvariable=input_type_var,
                values=INPUT_TYPES, state="readonly"
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 直接输入框架
            direct_input_frame = tk.Frame(node_frame)
            tk.Label(direct_input_frame, text="文本:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            text_var = tk.StringVar(value="")
            self.param_vars['text'] = text_var
            tk.Entry(direct_input_frame, textvariable=text_var).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 数据变量输入框架
            data_var_frame = tk.Frame(node_frame)
            data_names, fields_map = self.get_data_vars_info()

            data_name_row = tk.Frame(data_var_frame)
            data_name_row.pack(fill=tk.X, pady=2)
            tk.Label(data_name_row, text="数据名称:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_name_var = tk.StringVar(value=DEFAULT_DATA_NAME)
            self.param_vars['data_name'] = data_name_var
            data_name_combo = ttk.Combobox(
                data_name_row, textvariable=data_name_var,
                values=list(data_names), state="readonly"
            )
            data_name_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            field_name_row = tk.Frame(data_var_frame)
            field_name_row.pack(fill=tk.X, pady=2)
            tk.Label(field_name_row, text="字段名:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            field_name_var = tk.StringVar(value="")
            self.param_vars['field_name'] = field_name_var

            def update_field_names(*args):
                selected_data = data_name_var.get()
                if selected_data in fields_map:
                    field_name_combo['values'] = fields_map[selected_data]
                else:
                    field_name_combo['values'] = []

            field_name_combo = ttk.Combobox(
                field_name_row, textvariable=field_name_var,
                values=[], state="readonly"
            )
            field_name_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            data_name_var.trace_add("write", update_field_names)
            update_field_names()

            # 根据输入类型显示/隐藏
            def update_input_visibility(*args):
                if input_type_var.get() == "直接输入":
                    direct_input_frame.pack(fill=tk.X, pady=2)
                    data_var_frame.pack_forget()
                else:
                    direct_input_frame.pack_forget()
                    data_var_frame.pack(fill=tk.X, pady=2)
                    nonlocal data_names, fields_map
                    data_names, fields_map = self.get_data_vars_info()
                    data_name_combo['values'] = list(data_names)
                    update_field_names()

            input_type_var.trace_add("write", update_input_visibility)
            update_input_visibility()

            # 点击位置
            coord_row = tk.Frame(node_frame)
            coord_row.pack(fill=tk.X, pady=2)
            tk.Label(coord_row, text="点击位置:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            pos_var = tk.StringVar(value="")
            self.param_vars['pos_var'] = pos_var
            ttk.Combobox(coord_row, textvariable=pos_var, values=coord_vars).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            tk.Button(
                coord_row, text="\U0001f4cd", width=3,
                command=lambda v=pos_var: self.start_coord_picker(v),
                bg="#e0eaff", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)
            tk.Label(coord_row, text="(变量/x,y)", fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

            add_bg_check(parent=node_frame)

        elif t == "按键":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            add_row("键名:", "key", show_edit_button=True, parent=node_frame)
            add_bg_check(parent=node_frame)

        elif t == "滚轮":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            add_combo("方向:", "direction", ["向上", "向下"], default_val="向下", parent=node_frame)
            add_row("次数:", "clicks", "1", placeholder="滚动次数", parent=node_frame)
            add_bg_check(parent=node_frame)

        elif t == "延时":
            add_row("秒数:", "seconds", "1")

        elif t == "条件分支":
            add_combo("条件类型:", "condition_type", ["图片条件", "变量条件"], default_val="图片条件")
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            # 图片条件框架
            image_condition_frame = tk.Frame(node_frame)
            self._image_condition_frame = image_condition_frame

            cond_row = tk.Frame(image_condition_frame)
            cond_row.pack(fill=tk.X, pady=2)
            tk.Label(cond_row, text="条件:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            cond_var = tk.StringVar(value="找到图片时")
            self.param_vars['condition'] = cond_var
            ttk.Combobox(cond_row, textvariable=cond_var, values=["找到图片时", "未找到图片时"], state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            add_row("图片:", "template", show_edit_button=True, parent=image_condition_frame)

            region_row = tk.Frame(image_condition_frame)
            region_row.pack(fill=tk.X, pady=2)
            tk.Label(region_row, text="识别区域:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            region_var = tk.StringVar(value="")
            self.param_vars['input_region_var'] = region_var
            cb = ttk.Combobox(region_row, textvariable=region_var, values=existing_vars)
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            cb.bind('<FocusIn>', lambda e, v=region_var: self._show_region_overlay_on_screen(v))
            tk.Button(
                region_row, text="\u2702\ufe0f", width=3,
                command=lambda: self.start_region_picker(region_var),
                bg="#e0eaff", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            conf_row = tk.Frame(image_condition_frame)
            conf_row.pack(fill=tk.X, pady=2)
            tk.Label(conf_row, text="相似度阈值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            conf_var = tk.StringVar(value="0.95")
            self.param_vars['confidence'] = conf_var
            tk.Entry(conf_row, textvariable=conf_var, width=8).pack(side=tk.LEFT, padx=(2, 10))
            tk.Label(conf_row, text="超时(秒):", width=10, anchor=tk.E).pack(side=tk.LEFT)
            timeout_var = tk.StringVar(value="0.5")
            self.param_vars['timeout'] = timeout_var
            tk.Entry(conf_row, textvariable=timeout_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            color_row = tk.Frame(image_condition_frame)
            color_row.pack(fill=tk.X, pady=2)
            tk.Label(color_row, text="颜色匹配:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            color_sensitive_var = tk.BooleanVar(value=True)
            self.param_vars['color_sensitive'] = color_sensitive_var
            tk.Checkbutton(
                color_row, text="启用验证",
                variable=color_sensitive_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(for_image_find=True, parent=image_condition_frame)

            # 变量条件框架
            var_condition_frame = tk.Frame(node_frame)
            self._var_condition_frame = var_condition_frame

            var_row = tk.Frame(var_condition_frame)
            var_row.pack(fill=tk.X, pady=2)
            tk.Label(var_row, text="变量:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var_select_var = tk.StringVar(value="")
            self.param_vars['var_condition_name'] = var_select_var
            ttk.Combobox(var_row, textvariable=var_select_var, values=coord_vars).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            op_row = tk.Frame(var_condition_frame)
            op_row.pack(fill=tk.X, pady=2)
            tk.Label(op_row, text="操作符:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            op_var = tk.StringVar(value="等于")
            self.param_vars['var_condition_op'] = op_var
            ttk.Combobox(
                op_row, textvariable=op_var,
                values=["等于", "不等于", "大于", "小于", "大于等于", "小于等于", "为空", "不为空", "包含"],
                state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            val_row = tk.Frame(var_condition_frame)
            val_row.pack(fill=tk.X, pady=2)
            tk.Label(val_row, text="比较值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            val_var = tk.StringVar(value="")
            self.param_vars['var_condition_value'] = val_var
            tk.Entry(val_row, textvariable=val_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            result_row = tk.Frame(var_condition_frame)
            result_row.pack(fill=tk.X, pady=2)
            tk.Label(result_row, text="条件为真时:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            result_var = tk.StringVar(value="执行真分支")
            self.param_vars['var_condition_result'] = result_var
            ttk.Combobox(
                result_row, textvariable=result_var,
                values=["执行真分支", "执行假分支"],
                state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 条件类型切换
            condition_type_var = self.param_vars.get('condition_type')
            if condition_type_var is None:
                condition_type_var = tk.StringVar(value="图片条件")
                self.param_vars['condition_type'] = condition_type_var

            def update_condition_visibility(*args):
                if condition_type_var.get() == "图片条件":
                    image_condition_frame.pack(fill=tk.X, pady=2)
                    var_condition_frame.pack_forget()
                else:
                    image_condition_frame.pack_forget()
                    var_condition_frame.pack(fill=tk.X, pady=2)

            condition_type_var.trace_add("write", update_condition_visibility)
            update_condition_visibility()

            add_output_var()

        elif t == "普通循环":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            add_combo("循环方式:", "loop_type", ["按次数", "找到图片时", "未找到图片时"], parent=node_frame)

            loop_type_var = self.param_vars.get('loop_type')
            if loop_type_var is None:
                loop_type_var = tk.StringVar(value="按次数")
                self.param_vars['loop_type'] = loop_type_var

            # 次数/图片行（统一）
            value_row = tk.Frame(node_frame)
            value_row.pack(fill=tk.X, pady=2)
            tk.Label(value_row, text="次数/图片:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            value_var = tk.StringVar(value="")
            self.param_vars['value'] = value_var
            tk.Entry(value_row, textvariable=value_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 5))
            tk.Button(
                value_row, text="\U0001f4c2", width=3,
                command=lambda v=value_var: self.select_image_file(v),
                bg="#e0ffe0", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            add_input_region_var(parent=node_frame)

            timeout_row = tk.Frame(node_frame)
            timeout_row.pack(fill=tk.X, pady=2)
            self._timeout_frame = timeout_row
            tk.Label(timeout_row, text="超时(秒):", width=12, anchor=tk.E).pack(side=tk.LEFT)
            timeout_var = tk.StringVar(value="30")
            self.param_vars['timeout'] = timeout_var
            tk.Entry(timeout_row, textvariable=timeout_var, width=10).pack(side=tk.LEFT, padx=(2, 5))
            tk.Label(timeout_row, text="0表示无限制", fg="gray", font=("Arial", 8)).pack(side=tk.LEFT)

            # 相似度（仅图片循环显示）
            conf_row = tk.Frame(node_frame)
            self._confidence_row = conf_row
            tk.Label(conf_row, text="相似度阈值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            conf_var = tk.StringVar(value="0.95")
            self.param_vars['confidence'] = conf_var
            tk.Entry(conf_row, textvariable=conf_var, width=10).pack(side=tk.LEFT, padx=(2, 0))
            tk.Label(conf_row, text="0.0-1.0", fg="gray", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

            # 颜色匹配
            color_row = tk.Frame(node_frame)
            color_row.pack(fill=tk.X, pady=2)
            tk.Label(color_row, text="颜色匹配:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            color_sensitive_var = tk.BooleanVar(value=True)
            self.param_vars['color_sensitive'] = color_sensitive_var
            tk.Checkbutton(
                color_row, text="启用验证",
                variable=color_sensitive_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(for_image_find=True, parent=node_frame)

            add_combo("退出条件:", "exit_condition_type", ["无", "图片条件", "变量条件"], default_val="无", parent=node_frame)

            exit_condition_frame = tk.Frame(node_frame)
            self._exit_condition_frame = exit_condition_frame

            # 图片条件退出框架
            exit_image_frame = tk.Frame(exit_condition_frame)
            self._exit_image_frame = exit_image_frame

            exit_cond_row = tk.Frame(exit_image_frame)
            exit_cond_row.pack(fill=tk.X, pady=2)
            tk.Label(exit_cond_row, text="退出条件:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            exit_cond_var = tk.StringVar(value="找到图片时退出")
            self.param_vars['exit_condition'] = exit_cond_var
            ttk.Combobox(exit_cond_row, textvariable=exit_cond_var, values=["找到图片时退出", "未找到图片时退出"], state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            add_row("退出图片:", "exit_template", show_edit_button=True, parent=exit_image_frame)

            exit_region_row = tk.Frame(exit_image_frame)
            exit_region_row.pack(fill=tk.X, pady=2)
            tk.Label(exit_region_row, text="识别区域:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            exit_region_var = tk.StringVar(value="")
            self.param_vars['exit_region_var'] = exit_region_var
            cb = ttk.Combobox(exit_region_row, textvariable=exit_region_var, values=existing_vars)
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            cb.bind('<FocusIn>', lambda e, v=exit_region_var: self._show_region_overlay_on_screen(v))
            tk.Button(
                exit_region_row, text="\u2702\ufe0f", width=3,
                command=lambda: self.start_region_picker(exit_region_var),
                bg="#e0eaff", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            exit_conf_row = tk.Frame(exit_image_frame)
            exit_conf_row.pack(fill=tk.X, pady=2)
            tk.Label(exit_conf_row, text="相似度阈值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            exit_conf_var = tk.StringVar(value="0.95")
            self.param_vars['exit_confidence'] = exit_conf_var
            tk.Entry(exit_conf_row, textvariable=exit_conf_var, width=8).pack(side=tk.LEFT, padx=(2, 10))
            tk.Label(exit_conf_row, text="颜色匹配:", width=10, anchor=tk.E).pack(side=tk.LEFT)
            exit_color_var = tk.BooleanVar(value=True)
            self.param_vars['exit_color_sensitive'] = exit_color_var
            tk.Checkbutton(
                exit_conf_row, text="启用",
                variable=exit_color_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(for_image_find=True, parent=exit_image_frame)

            # 变量条件退出框架
            exit_var_frame = tk.Frame(exit_condition_frame)
            self._exit_var_frame = exit_var_frame

            exit_var_row = tk.Frame(exit_var_frame)
            exit_var_row.pack(fill=tk.X, pady=2)
            tk.Label(exit_var_row, text="变量:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            exit_var_select_var = tk.StringVar(value="")
            self.param_vars['exit_var_name'] = exit_var_select_var
            ttk.Combobox(exit_var_row, textvariable=exit_var_select_var, values=coord_vars).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            exit_op_row = tk.Frame(exit_var_frame)
            exit_op_row.pack(fill=tk.X, pady=2)
            tk.Label(exit_op_row, text="操作符:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            exit_op_var = tk.StringVar(value="等于")
            self.param_vars['exit_var_op'] = exit_op_var
            ttk.Combobox(
                exit_op_row, textvariable=exit_op_var,
                values=["等于", "不等于", "大于", "小于", "大于等于", "小于等于", "为空", "不为空", "包含"],
                state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            exit_val_row = tk.Frame(exit_var_frame)
            exit_val_row.pack(fill=tk.X, pady=2)
            tk.Label(exit_val_row, text="比较值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            exit_val_var = tk.StringVar(value="")
            self.param_vars['exit_var_value'] = exit_val_var
            tk.Entry(exit_val_row, textvariable=exit_val_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 退出条件类型切换
            exit_type_var = self.param_vars.get('exit_condition_type')
            if exit_type_var is None:
                exit_type_var = tk.StringVar(value="无")
                self.param_vars['exit_condition_type'] = exit_type_var

            def update_exit_condition_visibility(*args):
                exit_type = exit_type_var.get()
                if exit_type == "无":
                    exit_condition_frame.pack_forget()
                else:
                    exit_condition_frame.pack(fill=tk.X, pady=2)
                    if exit_type == "图片条件":
                        exit_image_frame.pack(fill=tk.X, pady=2)
                        exit_var_frame.pack_forget()
                    else:
                        exit_image_frame.pack_forget()
                        exit_var_frame.pack(fill=tk.X, pady=2)

            exit_type_var.trace_add("write", update_exit_condition_visibility)
            update_exit_condition_visibility()

            add_output_var()

            def update_loop_visibility(*args: object) -> None:
                loop_type_value = loop_type_var.get()
                is_image_loop = loop_type_value in ["找到图片时", "未找到图片时"]
                if is_image_loop:
                    conf_row.pack(fill=tk.X, pady=2)
                    timeout_row.pack(fill=tk.X, pady=2)
                else:
                    conf_row.pack_forget()
                    timeout_row.pack_forget()

            loop_type_var.trace_add("write", update_loop_visibility)
            update_loop_visibility()

        elif t == "数据循环":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            # 数据文件
            file_row = tk.Frame(node_frame)
            file_row.pack(fill=tk.X, pady=2)
            tk.Label(file_row, text="文件路径:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_file_var = tk.StringVar(value="")
            self.param_vars['data_file'] = data_file_var
            tk.Entry(file_row, textvariable=data_file_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 5))
            tk.Button(
                file_row, text="\U0001f4c1", width=3,
                command=lambda v=data_file_var: self.select_data_file(v),
                bg="#e0ffe0", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            # 数据名称
            name_row = tk.Frame(node_frame)
            name_row.pack(fill=tk.X, pady=2)
            tk.Label(name_row, text="数据名称:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_name_var = tk.StringVar(value=DEFAULT_DATA_NAME)
            self.param_vars['data_name'] = data_name_var
            tk.Entry(name_row, textvariable=data_name_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 循环设置
            loop_frame = tk.LabelFrame(
                node_frame, text="循环设置", padx=5, pady=5)
            loop_frame.pack(fill=tk.X, pady=5)

            loop_mode_row = tk.Frame(loop_frame)
            loop_mode_row.pack(fill=tk.X, pady=2)
            tk.Label(loop_mode_row, text="循环模式:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            loop_mode_var = tk.StringVar(value=DEFAULT_LOOP_MODE)
            self.param_vars['loop_mode'] = loop_mode_var
            ttk.Combobox(
                loop_mode_row, textvariable=loop_mode_var,
                values=LOOP_MODES, state="readonly"
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            index_row = tk.Frame(loop_frame)
            index_row.pack(fill=tk.X, pady=2)
            tk.Label(index_row, text="起始索引:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            start_index_var = tk.StringVar(value=str(DEFAULT_START_INDEX))
            self.param_vars['start_index'] = start_index_var
            tk.Entry(index_row, textvariable=start_index_var, width=10).pack(side=tk.LEFT, padx=(2, 10))
            tk.Label(index_row, text="结束索引:", width=10, anchor=tk.E).pack(side=tk.LEFT)
            end_index_var = tk.StringVar(value="")
            self.param_vars['end_index'] = end_index_var
            tk.Entry(index_row, textvariable=end_index_var, width=10).pack(side=tk.LEFT, padx=(2, 0))

            # 数据预览（虚线边框）
            preview_frame = tk.Frame(
                self.frm_params, bd=1, relief=tk.GROOVE,
                highlightbackground="#999999", highlightthickness=0,
                padx=5, pady=5)
            preview_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            tk.Label(preview_frame, text="数据预览", font=("Arial", 9)).pack(anchor=tk.W)

            table_scroll_y = ttk.Scrollbar(preview_frame, orient="vertical")
            table_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
            table_scroll_x = ttk.Scrollbar(preview_frame, orient="horizontal")
            table_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
            data_table = ttk.Treeview(
                preview_frame,
                yscrollcommand=table_scroll_y.set,
                xscrollcommand=table_scroll_x.set,
                height=5,
                show="headings",
            )
            table_scroll_y.config(command=data_table.yview)
            table_scroll_x.config(command=data_table.xview)
            data_table.pack(fill=tk.BOTH, expand=True)

            def load_data_preview(*args):
                data_table.delete(*data_table.get_children())
                for col in data_table['columns']:
                    data_table.heading(col, text=col)
                file_path = data_file_var.get()
                if not file_path or not os.path.exists(file_path):
                    return
                try:
                    import pandas as pd
                    if file_path.endswith('.csv'):
                        df = pd.read_csv(file_path, encoding='utf-8')
                    elif file_path.endswith(('.xlsx', '.xls')):
                        df = pd.read_excel(file_path)
                    else:
                        return
                    data_table['columns'] = list(df.columns)
                    for col in df.columns:
                        data_table.heading(col, text=str(col))
                        data_table.column(col, width=100, anchor=tk.W)
                    for idx, row_data in df.head(8).iterrows():
                        values = [str(v) for v in row_data.values]
                        data_table.insert('', tk.END, values=values)
                    if len(df) > 8:
                        data_table.insert('', tk.END, values=['...', f'共 {len(df)} 行'])
                except Exception as e:
                    data_table.insert('', tk.END, values=[f'加载失败: {e}'])

            data_file_var.trace_add("write", load_data_preview)
            if data_file_var.get():
                load_data_preview()

            add_combo("退出条件:", "exit_condition_type", ["无", "图片条件", "变量条件"], default_val="无", parent=node_frame)

            data_exit_condition_frame = tk.Frame(node_frame)

            # 图片条件退出框架
            data_exit_image_frame = tk.Frame(data_exit_condition_frame)

            data_exit_cond_row = tk.Frame(data_exit_image_frame)
            data_exit_cond_row.pack(fill=tk.X, pady=2)
            tk.Label(data_exit_cond_row, text="退出条件:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_cond_var = tk.StringVar(value="找到图片时退出")
            self.param_vars['exit_condition'] = data_exit_cond_var
            ttk.Combobox(data_exit_cond_row, textvariable=data_exit_cond_var, values=["找到图片时退出", "未找到图片时退出"], state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            add_row("退出图片:", "exit_template", show_edit_button=True, parent=data_exit_image_frame)

            data_exit_region_row = tk.Frame(data_exit_image_frame)
            data_exit_region_row.pack(fill=tk.X, pady=2)
            tk.Label(data_exit_region_row, text="识别区域:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_region_var = tk.StringVar(value="")
            self.param_vars['exit_region_var'] = data_exit_region_var
            cb = ttk.Combobox(data_exit_region_row, textvariable=data_exit_region_var, values=existing_vars)
            cb.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            cb.bind('<FocusIn>', lambda e, v=data_exit_region_var: self._show_region_overlay_on_screen(v))
            tk.Button(
                data_exit_region_row, text="\u2702\ufe0f", width=3,
                command=lambda: self.start_region_picker(data_exit_region_var),
                bg="#e0eaff", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            data_exit_conf_row = tk.Frame(data_exit_image_frame)
            data_exit_conf_row.pack(fill=tk.X, pady=2)
            tk.Label(data_exit_conf_row, text="相似度阈值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_conf_var = tk.StringVar(value="0.95")
            self.param_vars['exit_confidence'] = data_exit_conf_var
            tk.Entry(data_exit_conf_row, textvariable=data_exit_conf_var, width=8).pack(side=tk.LEFT, padx=(2, 10))
            tk.Label(data_exit_conf_row, text="颜色匹配:", width=10, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_color_var = tk.BooleanVar(value=True)
            self.param_vars['exit_color_sensitive'] = data_exit_color_var
            tk.Checkbutton(
                data_exit_conf_row, text="启用",
                variable=data_exit_color_var,
            ).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(for_image_find=True, parent=data_exit_image_frame)

            # 变量条件退出框架
            data_exit_var_frame = tk.Frame(data_exit_condition_frame)

            data_exit_var_row = tk.Frame(data_exit_var_frame)
            data_exit_var_row.pack(fill=tk.X, pady=2)
            tk.Label(data_exit_var_row, text="变量:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_var_select_var = tk.StringVar(value="")
            self.param_vars['exit_var_name'] = data_exit_var_select_var
            ttk.Combobox(data_exit_var_row, textvariable=data_exit_var_select_var, values=coord_vars).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            data_exit_op_row = tk.Frame(data_exit_var_frame)
            data_exit_op_row.pack(fill=tk.X, pady=2)
            tk.Label(data_exit_op_row, text="操作符:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_op_var = tk.StringVar(value="等于")
            self.param_vars['exit_var_op'] = data_exit_op_var
            ttk.Combobox(
                data_exit_op_row, textvariable=data_exit_op_var,
                values=["等于", "不等于", "大于", "小于", "大于等于", "小于等于", "为空", "不为空", "包含"],
                state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            data_exit_val_row = tk.Frame(data_exit_var_frame)
            data_exit_val_row.pack(fill=tk.X, pady=2)
            tk.Label(data_exit_val_row, text="比较值:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            data_exit_val_var = tk.StringVar(value="")
            self.param_vars['exit_var_value'] = data_exit_val_var
            tk.Entry(data_exit_val_row, textvariable=data_exit_val_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 退出条件类型切换
            data_exit_type_var = self.param_vars.get('exit_condition_type')
            if data_exit_type_var is None:
                data_exit_type_var = tk.StringVar(value="无")
                self.param_vars['exit_condition_type'] = data_exit_type_var

            def update_data_exit_condition_visibility(*args):
                exit_type = data_exit_type_var.get()
                if exit_type == "无":
                    data_exit_condition_frame.pack_forget()
                else:
                    data_exit_condition_frame.pack(fill=tk.X, pady=2)
                    if exit_type == "图片条件":
                        data_exit_image_frame.pack(fill=tk.X, pady=2)
                        data_exit_var_frame.pack_forget()
                    else:
                        data_exit_image_frame.pack_forget()
                        data_exit_var_frame.pack(fill=tk.X, pady=2)

            data_exit_type_var.trace_add("write", update_data_exit_condition_visibility)
            update_data_exit_condition_visibility()

            add_output_var()

        elif t == "暂停":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            add_row("提示信息:", "pause_msg", "流程已暂停，请手动确认...", parent=node_frame)

        elif t == "变量管理":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            def add_var():
                create_var_row(vars_container)

            tk.Button(node_frame, text="+ 添加变量", command=add_var, bg="#e0ffe0", relief=tk.GROOVE).pack(anchor=tk.W, pady=(0, 5))

            vars_frame = tk.LabelFrame(node_frame, text="定义变量", padx=5, pady=5)
            vars_frame.pack(fill=tk.BOTH, expand=True)

            VAR_TYPES = ["字符串", "数字", "日期", "布尔"]
            VAR_DEFAULTS = {
                "字符串": "",
                "数字": "0",
                "日期": "",  # 日期使用日历控件
                "布尔": "False",
            }
            VAR_VALUE_OPTIONS = {
                "布尔": ["True", "False"],
            }
            var_name_error_labels: List[tk.Label] = []

            vars_container = tk.Frame(vars_frame)
            vars_container.pack(fill=tk.X, pady=5)

            # 表头
            header_row = tk.Frame(vars_container)
            header_row.pack(fill=tk.X, pady=(0, 5))
            tk.Label(header_row, text="变量名", width=15, anchor=tk.W).pack(side=tk.LEFT, padx=2)
            tk.Label(header_row, text="类型", width=10, anchor=tk.W).pack(side=tk.LEFT, padx=2)
            tk.Label(header_row, text="初始值", width=20, anchor=tk.W).pack(side=tk.LEFT, padx=2)
            tk.Label(header_row, text="操作", width=8, anchor=tk.CENTER).pack(side=tk.LEFT, padx=2)

            # 存储变量行的列表
            var_rows: List[Dict[str, Any]] = []

            # 从已有参数加载变量
            existing_vars = p.get('variables', [])

            # 用于生成默认变量名的计数器
            default_var_counter = [1]

            def create_value_widget(parent, row_data, var_type, default_value):
                """根据变量类型创建初始值控件（Entry、Combobox或DateEntry，可变宽度占据剩余空间）"""
                if var_type == "日期":
                    # 日期类型使用日历控件
                    try:
                        from tkcalendar import DateEntry
                        date_var = tk.StringVar(value=default_value)

                        # 日期控件（固定宽度，日期不需要扩展）
                        date_entry = DateEntry(
                            parent, textvariable=date_var,
                            width=12, background='darkblue',
                            foreground='white', borderwidth=2,
                            date_pattern='yyyy-mm-dd',
                            state='readonly'
                        )
                        date_entry.pack(side=tk.LEFT, padx=(2, 0))
                        row_data['value_var'] = date_var
                        row_data['value_widget'] = date_entry

                        # 监听日期变化，同步到StringVar
                        def on_date_selected(event):
                            try:
                                selected_date = date_entry.get_date()
                                date_var.set(selected_date.strftime('%Y-%m-%d'))
                            except Exception:
                                pass
                        date_entry.bind('<<DateEntrySelected>>', on_date_selected)

                        # 设置默认值
                        if not default_value:
                            from datetime import datetime
                            default_value = datetime.now().strftime("%Y-%m-%d")
                            date_var.set(default_value)
                        return date_var
                    except ImportError:
                        # 如果没有tkcalendar，回退到文本输入
                        pass

                value_var = tk.StringVar(value=default_value)
                row_data['value_var'] = value_var

                if var_type in VAR_VALUE_OPTIONS:
                    # 布尔类型使用下拉选择（固定宽度）
                    options = VAR_VALUE_OPTIONS[var_type]
                    if default_value not in options:
                        default_value = options[0]
                        value_var.set(default_value)
                    value_combo = ttk.Combobox(
                        parent, textvariable=value_var, values=options,
                        state="readonly", width=8)
                    value_combo.pack(side=tk.LEFT, padx=(2, 0))
                    row_data['value_widget'] = value_combo
                else:
                    # 字符串和数字类型使用文本输入（可变宽度，占据剩余空间）
                    value_entry = tk.Entry(parent, textvariable=value_var)
                    value_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
                    row_data['value_widget'] = value_entry

                return value_var

            def on_type_changed(row_data):
                """当变量类型改变时，自动更新初始值控件"""
                var_type = row_data['type_var'].get()

                # 销毁旧的控件
                if 'value_widget' in row_data and row_data['value_widget']:
                    row_data['value_widget'].destroy()

                # 创建新的控件和默认值（使用content_container）
                create_value_widget(row_data['content_container'], row_data, var_type, VAR_DEFAULTS.get(var_type, ""))

            def create_var_row(parent, var_data=None):
                """创建一行变量配置"""
                row = tk.Frame(parent, height=30)
                row.pack(fill=tk.X, pady=2)
                row.pack_propagate(False)  # 保持固定高度

                # 生成默认变量名：如果 var_data 为空或没有 name，则自动生成
                if var_data and var_data.get('name'):
                    default_name = var_data.get('name', '')
                else:
                    default_name = f"变量{default_var_counter[0]}"
                    default_var_counter[0] += 1

                # 内容容器（变量名、类型、初始值占据剩余空间）
                content_container = tk.Frame(row)
                content_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

                # 变量名（固定宽度）
                name_var = tk.StringVar(value=default_name)
                name_frame = tk.Frame(content_container)
                name_frame.pack(side=tk.LEFT, padx=(0, 2))
                tk.Entry(name_frame, textvariable=name_var, width=12).pack()

                # 类型（固定宽度）
                var_type = var_data.get('type', '字符串') if var_data else '字符串'
                default_value = var_data.get('value', '') if var_data else ''

                type_var = tk.StringVar(value=var_type)
                type_frame = tk.Frame(content_container)
                type_frame.pack(side=tk.LEFT, padx=2)
                ttk.Combobox(
                    type_frame, textvariable=type_var, values=VAR_TYPES,
                    state="readonly", width=8
                ).pack()

                # 初始值控件（可变宽度，占据剩余空间）
                if not default_value:
                    default_value = VAR_DEFAULTS.get(var_type, "")
                row_data = {
                    'frame': row,
                    'content_container': content_container,
                    'name_var': name_var,
                    'type_var': type_var,
                    'value_var': None,
                    'value_widget': None,
                    'error_label': None,
                }
                create_value_widget(content_container, row_data, var_type, default_value)

                # 类型改变时更新初始值控件
                type_var.trace_add('write', lambda *args, rd=row_data: on_type_changed(rd))

                # 删除按钮（固定宽度，最右边）
                def delete_row(rd=row_data):
                    rd['frame'].destroy()
                    if rd.get('error_label'):
                        rd['error_label'].destroy()
                    if rd in var_rows:
                        var_rows.remove(rd)

                tk.Button(
                    row, text="×", width=3, command=delete_row,
                    bg="#ffcccc", relief=tk.GROOVE,
                ).pack(side=tk.RIGHT, padx=(2, 0))

                var_rows.append(row_data)
                return row_data

            # 加载已有变量
            for var_data in existing_vars:
                create_var_row(vars_container, var_data)

            # 如果没有已有变量，默认添加一行
            if not existing_vars:
                add_var()

            # 保存变量列表到参数
            self.param_vars['variables'] = var_rows

        elif t == "变量计算":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.BOTH, expand=True, pady=5)

            expr_frame = tk.LabelFrame(node_frame, text="表达式计算", padx=5, pady=5)
            expr_frame.pack(fill=tk.BOTH, expand=True)

            # 获取所有已定义的变量名（从所有节点中收集）
            all_var_names = self.get_all_defined_vars()

            # 结果变量名（可以新建变量，但不能与已有变量重复）
            result_row = tk.Frame(expr_frame)
            result_row.pack(fill=tk.X, pady=3)
            tk.Label(result_row, text="结果变量:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            result_var = tk.StringVar(value="")
            self.param_vars['calc_result_var'] = result_var
            tk.Entry(result_row, textvariable=result_var, width=20).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 表达式输入
            expr_row = tk.Frame(expr_frame)
            expr_row.pack(fill=tk.X, pady=3)
            tk.Label(expr_row, text="表达式:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            expr_var = tk.StringVar(value="")
            self.param_vars['expression'] = expr_var
            tk.Entry(expr_row, textvariable=expr_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 帮助说明
            help_frame = tk.Frame(expr_frame)
            help_frame.pack(fill=tk.X, pady=5)
            help_text = tk.Label(
                help_frame,
                text="表达式说明：使用变量名直接引用，如 变量1 + 变量2\n"
                     "支持运算符: + - * / // % ** ( )\n"
                     "支持函数: abs() round() min() max() len() str() int() float()\n"
                     "示例: 变量1 + 变量2 * 2    |    int(变量3) + 1",
                justify=tk.LEFT, fg="#666666", font=("Arial", 9)
            )
            help_text.pack(anchor=tk.W)

            # 变量预览
            if all_var_names:
                var_preview_frame = tk.Frame(
                    expr_frame, bd=1, relief=tk.GROOVE,
                    highlightbackground="#999999", highlightthickness=0,
                    padx=5, pady=5)
                var_preview_frame.pack(fill=tk.X, pady=5)
                # 标题
                tk.Label(var_preview_frame, text="可用变量", font=("Arial", 9)).pack(anchor=tk.W)
                vars_preview = tk.Text(
                    var_preview_frame, height=min(6, max(3, len(all_var_names))), font=("Consolas", 9),
                    state="disabled", bg="#f5f5f5", bd=1, relief=tk.GROOVE
                )
                vars_preview.pack(fill=tk.X, pady=(2, 0))
                vars_preview.configure(state="normal")
                vars_preview.insert("1.0", "\n".join(f"  {v[0]} [{v[1]}]" for v in all_var_names))
                vars_preview.configure(state="disabled")
            else:
                # 无变量时的提示
                var_preview_frame = tk.Frame(
                    expr_frame, bd=1, relief=tk.GROOVE,
                    highlightbackground="#999999", highlightthickness=0,
                    padx=5, pady=5)
                var_preview_frame.pack(fill=tk.X, pady=5)
                tk.Label(var_preview_frame, text="可用变量", font=("Arial", 9)).pack(anchor=tk.W)
                tk.Label(
                    var_preview_frame, text="当前流程中尚未定义任何变量",
                    fg="#999999", font=("Arial", 9)
                ).pack(anchor=tk.W)

        elif t == "退出":
            node_frame = tk.LabelFrame(
                self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            tk.Label(
                node_frame, text="立即终止整个流程的执行",
                fg="blue"
            ).pack(pady=10)
            tk.Label(
                node_frame, text="（可作为条件分支的退出路径）",
                fg="blue"
            ).pack()

        elif t == "窗口":
            node_frame = tk.LabelFrame(
                self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            add_row("窗口标题:", "window_title", show_edit_button=True, parent=node_frame)
            add_combo("操作:", "window_action", WINDOW_ACTIONS, parent=node_frame)

        elif t == "文件":
            node_frame = tk.LabelFrame(
                self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            file_row = tk.Frame(node_frame)
            file_row.pack(fill=tk.X, pady=2)
            tk.Label(file_row, text="文件路径:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="")
            self.param_vars['file_path'] = var
            tk.Entry(file_row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            tk.Button(
                file_row, text="\U0001f4c1", width=3,
                command=lambda v=var: self.select_file(v),
                bg="#e0ffe0", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

        elif t == "OCR":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            # 关键字类型
            keyword_type_row = tk.Frame(node_frame)
            keyword_type_row.pack(fill=tk.X, pady=2)
            tk.Label(keyword_type_row, text="关键字类型:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            keyword_input_type_var = tk.StringVar(value="直接输入")
            self.param_vars['keyword_input_type'] = keyword_input_type_var
            ttk.Combobox(
                keyword_type_row, textvariable=keyword_input_type_var,
                values=INPUT_TYPES, state="readonly"
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 关键字/数据变量框架
            keyword_direct_frame = tk.Frame(node_frame)
            keyword_data_var_frame = tk.Frame(node_frame)
            data_names_kw, fields_map_kw = self.get_data_vars_info()

            tk.Label(keyword_direct_frame, text="关键字:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            keyword_var = tk.StringVar(value="")
            self.param_vars['keyword'] = keyword_var
            tk.Entry(keyword_direct_frame, textvariable=keyword_var).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            keyword_data_name_row = tk.Frame(keyword_data_var_frame)
            keyword_data_name_row.pack(fill=tk.X, pady=2)
            tk.Label(keyword_data_name_row, text="数据名称:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            keyword_data_name_var = tk.StringVar(value=DEFAULT_DATA_NAME)
            self.param_vars['keyword_data_name'] = keyword_data_name_var
            keyword_data_name_combo = ttk.Combobox(
                keyword_data_name_row, textvariable=keyword_data_name_var,
                values=list(data_names_kw), state="readonly"
            )
            keyword_data_name_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            keyword_field_row = tk.Frame(keyword_data_var_frame)
            keyword_field_row.pack(fill=tk.X, pady=2)
            tk.Label(keyword_field_row, text="字段名:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            keyword_field_name_var = tk.StringVar(value="")
            self.param_vars['keyword_field_name'] = keyword_field_name_var

            def update_keyword_field_names(*args):
                selected_data = keyword_data_name_var.get()
                if selected_data in fields_map_kw:
                    keyword_field_name_combo['values'] = fields_map_kw[selected_data]
                else:
                    keyword_field_name_combo['values'] = []

            keyword_field_name_combo = ttk.Combobox(
                keyword_field_row, textvariable=keyword_field_name_var,
                values=[], state="readonly"
            )
            keyword_field_name_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            keyword_data_name_var.trace_add("write", update_keyword_field_names)
            update_keyword_field_names()

            # OCR API地址
            api_row = tk.Frame(node_frame)
            api_row.pack(fill=tk.X, pady=2)
            tk.Label(api_row, text="OCR API地址:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            ocr_url_var = tk.StringVar(value=OCR_DEFAULT_URL)
            self.param_vars['ocr_url'] = ocr_url_var
            tk.Entry(api_row, textvariable=ocr_url_var).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

            # 识别区域
            region_row = tk.Frame(node_frame)
            region_row.pack(fill=tk.X, pady=2)
            tk.Label(region_row, text="识别区域:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            region_var = tk.StringVar(value="")
            self.param_vars['input_region_var'] = region_var
            cb = ttk.Combobox(region_row, textvariable=region_var, values=existing_vars)
            cb.pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
            cb.bind('<FocusIn>', lambda e, v=region_var: self._show_region_overlay_on_screen(v))
            tk.Button(
                region_row, text="\u2702\ufe0f", width=3,
                command=lambda v=region_var: self.start_region_picker(v),
                bg="#e0eaff", relief=tk.GROOVE,
            ).pack(side=tk.LEFT, padx=2)

            # XY偏移
            offset_row = tk.Frame(node_frame)
            offset_row.pack(fill=tk.X, pady=2)
            offset_row.grid_columnconfigure(0, weight=1)
            offset_row.grid_columnconfigure(1, weight=1)

            offset_x_frame = tk.Frame(offset_row)
            offset_x_frame.grid(row=0, column=0, sticky=tk.EW, padx=(0, 5))
            tk.Label(offset_x_frame, text="X偏移:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            offset_x_var = tk.StringVar(value="0")
            self.param_vars["offset_x"] = offset_x_var
            tk.Entry(offset_x_frame, textvariable=offset_x_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            offset_y_frame = tk.Frame(offset_row)
            offset_y_frame.grid(row=0, column=1, sticky=tk.EW)
            tk.Label(offset_y_frame, text="Y偏移:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            offset_y_var = tk.StringVar(value="0")
            self.param_vars["offset_y"] = offset_y_var
            tk.Entry(offset_y_frame, textvariable=offset_y_var, width=8).pack(side=tk.LEFT, padx=(2, 0))

            add_bg_check(parent=node_frame)

            action_frame = tk.LabelFrame(self.frm_params, text="动作设置", padx=5, pady=5)
            action_frame.pack(fill=tk.X, pady=5)

            add_combo("点击方式:", "click_action",
                ["左键单击", "左键双击", "右键单击", "仅识别"], default_val="左键单击",
                parent=action_frame)

            time_row = tk.Frame(action_frame)
            time_row.pack(fill=tk.X, pady=2)
            tk.Label(time_row, text="超时(秒):", width=12, anchor=tk.E).pack(side=tk.LEFT)
            timeout_var = tk.StringVar(value="10")
            self.param_vars['timeout'] = timeout_var
            tk.Entry(time_row, textvariable=timeout_var, width=10).pack(side=tk.LEFT, padx=(2, 0))

            add_output_var()

            # 动态切换关键字输入类型
            def update_keyword_input_visibility(*args):
                if keyword_input_type_var.get() == "直接输入":
                    keyword_direct_frame.pack(fill=tk.X, pady=2)
                    keyword_data_var_frame.pack_forget()
                else:
                    keyword_direct_frame.pack_forget()
                    keyword_data_var_frame.pack(fill=tk.X, pady=2)
                    data_names_kw, fields_map_kw = self.get_data_vars_info()
                    keyword_data_name_combo['values'] = list(data_names_kw)
                    update_keyword_field_names()

            keyword_input_type_var.trace_add("write", update_keyword_input_visibility)
            update_keyword_input_visibility()

        elif t == "标签":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            add_row("标签名(需唯一):", "label_name", parent=node_frame)

        elif t == "跳转":
            node_frame = tk.LabelFrame(self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            labels = LabelManager.collect_all_labels(self.data)
            label_list = [""] + list(labels.keys())
            row = tk.Frame(node_frame)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text="跳转到[标签]:", width=12, anchor=tk.E).pack(side=tk.LEFT)
            var = tk.StringVar(value="")
            self.param_vars['target_label'] = var
            # 使用可编辑的 Combobox，既可选择已有标签也可手动输入新标签
            combo = ttk.Combobox(row, textvariable=var, values=label_list, state="normal")
            combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

            hint = tk.Label(node_frame, text="", font=("Arial", 8))
            hint.pack(anchor=tk.W, pady=2)

            def check_label_exists(*args: object) -> None:
                target = var.get().strip()
                if not target:
                    hint.config(text="", fg="gray")
                    return
                if LabelManager.validate_jump(self.data, target):
                    hint.config(text="\u2705 标签存在", fg="green")
                else:
                    hint.config(text="\u274c 标签不存在", fg="red")

            var.trace_add("write", check_label_exists)

        elif t == "AI 决策":
            node_frame = tk.LabelFrame(
                self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)

            # 输入模式
            add_combo("输入模式:", "ai_mode", ["图片识别", "文本语义"], parent=node_frame)

            # API 配置
            api_frame = tk.LabelFrame(node_frame, text="API 配置", padx=5, pady=5)
            api_frame.pack(fill=tk.X, pady=5)
            add_row("API 地址:", "ai_api_url", default=AI_DEFAULT_API_URL, parent=api_frame)
            add_row("模型名称:", "ai_model", default=AI_DEFAULT_MODEL, parent=api_frame)
            add_row("API Key:", "ai_api_key", default="", parent=api_frame)

            # 提示词
            prompt_frame = tk.LabelFrame(node_frame, text="提示词", padx=5, pady=5)
            prompt_frame.pack(fill=tk.X, pady=5)
            tk.Label(prompt_frame, text="告诉 AI 要做什么判断：", anchor=tk.W).pack(fill=tk.X)
            prompt_text = tk.Text(prompt_frame, height=6, width=40)
            prompt_text.pack(fill=tk.X, pady=5)
            self.param_vars['ai_system_prompt'] = prompt_text

            # 文本来源（文本语义模式专用）
            add_row("文本来源:", "ai_text_source", default="", parent=node_frame)

            # 截图来源（图片识别模式专用）
            add_row("截图区域:", "ai_screenshot_region", default="", parent=node_frame)

            # 输出变量
            add_row("结果保存到变量:", "ai_output_var", default="", parent=node_frame)

            # 超时
            add_row("超时(秒):", "ai_timeout", default=str(AI_DEFAULT_TIMEOUT), parent=node_frame)

        elif t == "子流程":
            node_frame = tk.LabelFrame(
                self.frm_params, text="节点配置", padx=5, pady=5)
            node_frame.pack(fill=tk.X, pady=5)
            add_row("子流程名称:", "sub_name", default="", parent=node_frame)
            tk.Label(
                node_frame, text="在子流程节点下方添加步骤", fg="blue"
            ).pack(pady=(10, 0))

    # 流程树管理
    def refresh_tree(self) -> None:
        """刷新流程树视图。"""
        # 保存当前选中的项目
        current_selection = self.tree.selection()
        selected_data: Optional[Dict[str, Any]] = None
        if current_selection:
            info = self.tree_map.get(current_selection[0])
            if info and info.get('type') == 'step':
                selected_data = info['data']

        # 保存节点展开状态
        expanded_containers: Set[str] = set()

        def record_expanded_nodes(
            item_id: str, path: Optional[List[str]] = None,
        ) -> None:
            if path is None:
                path = []
            node_path = path.copy()
            info = self.tree_map.get(item_id)
            if info:
                if info['type'] == 'step':
                    node_path.append(f"step_{info['index']}")
                elif info['type'] == 'container':
                    parent_info = self.tree_map.get(self.tree.parent(item_id))
                    if parent_info and parent_info['type'] == 'step':
                        if parent_info['data']['type'] == '条件分支':
                            children = self.tree.get_children(
                                self.tree.parent(item_id))
                            if item_id == children[0]:
                                node_path.append(
                                    f"branch_{parent_info['index']}_true")
                            else:
                                node_path.append(
                                    f"branch_{parent_info['index']}_false")
                        elif parent_info['data']['type'] in ('循环', '普通循环'):
                            node_path.append(
                                f"loop_{parent_info['index']}_body")
                        elif parent_info['data']['type'] == '数据循环':
                            node_path.append(
                                f"dataloop_{parent_info['index']}_body")
                        elif parent_info['data']['type'] == '子流程':
                            node_path.append(
                                f"sub_{parent_info['index']}_body")
            else:
                node_path.append("root")

            if self.tree.item(item_id, 'open'):
                expanded_containers.add(",".join(node_path))

            for child in self.tree.get_children(item_id):
                child_path = node_path.copy()
                record_expanded_nodes(child, child_path)

        for item_id in self.tree.get_children(''):
            record_expanded_nodes(item_id)

        # 清空树和映射
        self.tree.delete(*self.tree.get_children())
        self.tree_map = {}
        self.global_step_counter = 1

        def build(
            pid: str,
            lst: List[Dict[str, Any]],
            current_path: Optional[List[str]] = None,
        ) -> None:
            if current_path is None:
                current_path = []
            for i, step in enumerate(lst):
                t = step['type']
                p = step.get('params', {})
                idx_str = f"[{self.global_step_counter:02d}]"
                self.global_step_counter += 1
                bg_mark = "\U0001f977" if p.get('use_bg') else ""
                color_mark = "\U0001f3a8" if p.get('color_enable') else ""
                text = f"{idx_str} {t} {bg_mark}{color_mark}"
                desc = ""
                if t in ['点击', '等待', '条件分支', '循环']:
                    desc = p.get('template', p.get('value', ''))
                    if p.get('input_region_var'):
                        desc += f" @{p['input_region_var']}"
                if t == '普通循环':
                    desc = f"{p.get('loop_type')} {desc}"
                elif t == '标签':
                    desc = f"\U0001f3f7\ufe0f {p.get('label_name')}"
                elif t == '跳转':
                    desc = f"\U0001f680 -> {p.get('target_label')}"
                elif t == '退出':
                    desc = "\U0001f6d1 退出流程"
                elif t == '按键':
                    desc = f"\u2328\ufe0f {p.get('key')}"
                elif t == '滚轮':
                    desc = f"{p.get('direction')} {p.get('clicks')}次"
                elif t == '窗口':
                    desc = (
                        f"{p.get('window_action')} "
                        f"[{p.get('window_title')}]")
                elif t == '文件':
                    file_path = p.get('file_path', '')
                    desc = (
                        f"\U0001f4c4 "
                        f"{os.path.basename(file_path) if file_path else '未选择'}")
                elif t == 'OCR':
                    keyword = p.get('keyword', '')
                    click_action = p.get('click_action', '左键单击')
                    if click_action == '仅识别':
                        desc = f"\U0001f50d '{keyword}'"
                    else:
                        desc = f"\U0001f50d '{keyword}' {click_action}"
                elif t == '输入':
                    desc = f"'{p.get('text', '')}'"
                    if p.get('pos_var'):
                        desc += f" @{p['pos_var']}"
                elif t == '延时':
                    desc = f"{p.get('seconds', 1)}秒"
                elif t == '子流程':
                    sub_count = sum(1 for s in lst[:i] if s.get('type') == '子流程') + 1
                    desc = f"\U0001f4c1 {p.get('sub_name', '') or f'子流程{sub_count}'}"

                step_path = current_path.copy()
                step_path.append(f"step_{i}")
                is_step_open = ",".join(step_path) in expanded_containers

                var_name = p.get('output_var', '')
                iid = self.tree.insert(
                    pid, "end", text=text, values=(desc, var_name),
                    open=is_step_open)
                self.tree_map[iid] = {
                    "list": lst, "index": i, "type": "step", "data": step,
                }

                if selected_data and step.get('id') == selected_data.get('id'):
                    self.tree.selection_set(iid)
                    self.tree.see(iid)

                container_path = step_path.copy()

                if t == '条件分支':
                    true_path = container_path.copy()
                    true_path.append(f"branch_{i}_true")
                    is_true_open = ",".join(true_path) in expanded_containers
                    tid = self.tree.insert(
                        iid, "end", text="\u2705 True",
                        open=is_true_open, tags=('folder',))
                    self.tree_map[tid] = {
                        "list": step.setdefault('true', []),
                        "type": "container",
                    }
                    build(tid, step['true'], true_path)

                    false_path = container_path.copy()
                    false_path.append(f"branch_{i}_false")
                    is_false_open = (
                        ",".join(false_path) in expanded_containers)
                    fid = self.tree.insert(
                        iid, "end", text="\u274e False",
                        open=is_false_open, tags=('folder',))
                    self.tree_map[fid] = {
                        "list": step.setdefault('false', []),
                        "type": "container",
                    }
                    build(fid, step['false'], false_path)
                elif t == '普通循环':
                    body_path = container_path.copy()
                    body_path.append(f"loop_{i}_body")
                    is_body_open = ",".join(body_path) in expanded_containers
                    bid = self.tree.insert(
                        iid, "end", text="\U0001f504 Body",
                        open=is_body_open, tags=('folder',))
                    self.tree_map[bid] = {
                        "list": step.setdefault('body', []),
                        "type": "container",
                    }
                    build(bid, step['body'], body_path)
                elif t == '数据循环':
                    # 循环体
                    body_path = container_path.copy()
                    body_path.append(f"dataloop_{i}_body")
                    is_body_open = ",".join(body_path) in expanded_containers
                    bid = self.tree.insert(
                        iid, "end", text="\U0001f504 Body",
                        open=is_body_open, tags=('folder',))
                    self.tree_map[bid] = {
                        "list": step.setdefault('body', []),
                        "type": "container",
                    }
                    build(bid, step['body'], body_path)

                elif t == '子流程':
                    # 计算是第几个子流程节点
                    sub_count = sum(1 for s in lst[:i] if s.get('type') == '子流程') + 1
                    sub_name = p.get('sub_name', '') or f"子流程{sub_count}"
                    body_path = container_path.copy()
                    body_path.append(f"sub_{i}_body")
                    is_body_open = ",".join(body_path) in expanded_containers
                    bid = self.tree.insert(
                        iid, "end", text=f"\U0001f4c1 {sub_name}",
                        open=is_body_open, tags=('folder',))
                    self.tree_map[bid] = {
                        "list": step.setdefault('body', []),
                        "type": "container",
                    }
                    build(bid, step['body'], body_path)

        build("", self.data)

    def on_drag_start(self, e: tk.Event) -> None:
        """拖拽开始。"""
        item = self.tree.identify_row(e.y)
        # 检测 Ctrl (0x0004) 或 Shift (0x0001) 是否按下，若是则不覆盖多选且不触发拖拽
        multi_select = bool(e.state & 0x0004) or bool(e.state & 0x0001)
        if multi_select:
            self.drag_data = None
            return

        if item:
            info = self.tree_map.get(item)
            if info and info.get('type') == 'step':
                self.drag_data = {"item": item, "x": e.x_root, "y": e.y_root}
                self.tree.selection_set(item)
            else:
                self.drag_data = None
        else:
            self.drag_data = None

    def on_drag_motion(self, e: tk.Event) -> None:
        """拖拽移动。"""
        if not self.drag_data:
            return

        # 清除所有拖拽相关标签
        for item_id in self.tree.get_children(''):
            tags = list(self.tree.item(item_id, 'tags'))
            new_tags = [t for t in tags if not t.startswith('drag_')]
            self.tree.item(item_id, tags=tuple(new_tags))
            for child_id in self.tree.get_children(item_id):
                child_tags = list(self.tree.item(child_id, 'tags'))
                new_child_tags = [
                    t for t in child_tags if not t.startswith('drag_')]
                self.tree.item(child_id, tags=tuple(new_child_tags))

        # 清除指示器
        if self.drag_highlight_items:
            for item in self.drag_highlight_items:
                try:
                    self.tree.delete(item)
                except tk.TclError:
                    pass
            self.drag_highlight_items = []
        self.drag_feedback_item = None

        source_id = self.drag_data["item"]
        target_id = self.tree.identify_row(e.y)

        try:
            self.tree.item(source_id, tags=('drag_source',))
        except tk.TclError:
            pass

        if not target_id or target_id == source_id:
            return

        # 检查是否拖拽到自己的子项目中
        parent = target_id
        while parent:
            if parent == source_id:
                return
            parent = self.tree.parent(parent)

        t_info = self.tree_map.get(target_id)
        if not t_info:
            return

        try:
            if t_info['type'] == 'container':
                self.tree.item(target_id, tags=('drag_target_container',))
                indicator_id = f"indicator_{int(time.time() * 1000000) % 1000000}"
                indicator = self.tree.insert(
                    target_id, 'end', indicator_id,
                    text='\u27a1\ufe0f 拖入此处', tags=('drag_indicator',))
                self.drag_highlight_items.append(indicator)
            else:
                self.tree.item(target_id, tags=('drag_target_step',))
                bbox = self.tree.bbox(target_id)
                if not bbox:
                    return

                parent = self.tree.parent(target_id)
                idx = self.tree.index(target_id)
                insert_pos = (
                    'before'
                    if (e.y - bbox[1]) < (bbox[3] - bbox[1]) / 2
                    else 'after')

                indicator_id = f"indicator_{int(time.time() * 1000000) % 1000000}"
                if insert_pos == 'before':
                    indicator = self.tree.insert(
                        parent, idx, indicator_id,
                        text='\u2b06\ufe0f 插入到上方',
                        tags=('drag_indicator',))
                else:
                    indicator = self.tree.insert(
                        parent, idx + 1, indicator_id,
                        text='\u2b07\ufe0f 插入到下方',
                        tags=('drag_indicator',))
                self.drag_highlight_items.append(indicator)
        except tk.TclError:
            if self.drag_highlight_items:
                for item in self.drag_highlight_items:
                    try:
                        self.tree.delete(item)
                    except tk.TclError:
                        pass
                self.drag_highlight_items = []

    def on_drag_release(self, e: tk.Event) -> None:
        """拖拽释放。"""
        if not self.drag_data:
            return

        source_id = self.drag_data["item"]
        target_id = self.tree.identify_row(e.y)

        # 清除指示器
        if self.drag_highlight_items:
            for item in self.drag_highlight_items:
                try:
                    self.tree.delete(item)
                except tk.TclError:
                    pass
            self.drag_highlight_items = []
        self.drag_feedback_item = None

        # 清除拖拽源标签
        try:
            tags = list(self.tree.item(source_id, 'tags'))
            new_tags = [t for t in tags if not t.startswith('drag_')]
            self.tree.item(source_id, tags=tuple(new_tags))
        except tk.TclError:
            pass

        self.drag_data = None

        if not target_id or source_id == target_id:
            # 没有实际拖拽（点击展开/选中时也会触发），不刷新树
            return

        # 检查是否拖拽到自己的子项目中
        parent = target_id
        while parent:
            if parent == source_id:
                return
            parent = self.tree.parent(parent)

        s_info = self.tree_map.get(source_id)
        t_info = self.tree_map.get(target_id)
        if not s_info or not t_info or s_info['type'] != 'step':
            return

        try:
            self._save_undo_snapshot()
            s_list, s_index = s_info['list'], s_info['index']
            step_data = s_list.pop(s_index)

            if t_info['type'] == 'container':
                t_list = t_info['list']
                t_insert_index = len(t_info['list'])
            else:
                t_list = t_info['list']
                t_item_index = t_info['index']
                bbox = self.tree.bbox(target_id)
                if not bbox:
                    t_insert_index = t_item_index + 1
                else:
                    is_before = (e.y - bbox[1]) < (bbox[3] - bbox[1]) / 2
                    t_insert_index = (
                        t_item_index if is_before else t_item_index + 1)

            if s_list is t_list and s_index < t_insert_index:
                t_insert_index -= 1

            t_list.insert(t_insert_index, step_data)
            self.refresh_tree()
        except (IndexError, ValueError, KeyError):
            self.refresh_tree()

    # 树选择与步骤操作
    def _apply_params_to_step(self) -> None:
        """将当前面板的参数写回到正在编辑的步骤数据中。"""
        if self._editing_step_data is None:
            return
        t = self.cb_type.get()
        p = {k: v.get() if not isinstance(v, list) else v
             for k, v in self.param_vars.items()}
        # 变量节点：处理变量列表
        if t == '变量管理' and 'variables' in self.param_vars and isinstance(self.param_vars['variables'], list):
            variables_data = []
            for var_row in self.param_vars['variables']:
                if isinstance(var_row, dict) and 'name_var' in var_row:
                    name = var_row['name_var'].get().strip()
                    if name:
                        variables_data.append({
                            'name': name,
                            'type': var_row['type_var'].get(),
                            'value': var_row['value_var'].get(),
                        })
            p['variables'] = variables_data
        self._editing_step_data['type'] = t
        self._editing_step_data['params'] = p

    def on_tree_select(self, e: tk.Event) -> None:
        """树节点选择事件。"""
        # 先保存当前编辑的步骤
        self._apply_params_to_step()

        sel = self.tree.selection()
        if not sel:
            self._editing_step_data = None
            return
        info = self.tree_map.get(sel[0])
        if not info:
            self._editing_step_data = None
            return
        if info['type'] == 'container':
            self.lbl_status.config(text="选中容器", fg="blue")
            self._editing_step_data = None
            return
        self.lbl_status.config(text="选中步骤", fg="green")
        step = info['data']
        self._editing_step_data = step
        self.cb_type.set(step['type'])
        p = step.get('params', {})
        self.update_params_ui(existing_params=p)
        for k, v in self.param_vars.items():
            if k in p:
                if isinstance(v, tk.BooleanVar):
                    v.set(bool(p[k]))
                elif k == 'variables':
                    pass  # 变量在 UI 中已加载
                elif isinstance(v, tk.Text):
                    try:
                        v.delete("1.0", tk.END)
                        v.insert("1.0", str(p[k]))
                    except Exception:
                        pass
                else:
                    try:
                        v.set(p[k])
                    except Exception:
                        pass

    def add_step(self) -> None:
        """添加新步骤。"""
        t = self.cb_type.get()
        p = {k: v.get() if not isinstance(v, list) else v
             for k, v in self.param_vars.items()}

        # 变量管理节点：处理变量列表并校验
        if t == '变量管理' and 'variables' in self.param_vars and isinstance(self.param_vars['variables'], list):
            variables_data = []
            var_names_seen = set()
            for var_row in self.param_vars['variables']:
                if isinstance(var_row, dict) and 'name_var' in var_row:
                    name = var_row['name_var'].get().strip()
                    if not name:
                        messagebox.showwarning("提示", "变量名不能为空！")
                        return
                    if name in var_names_seen:
                        messagebox.showwarning("提示", f"变量名「{name}」在同一节点内重复，请修改！")
                        return
                    # 校验变量类型对应的值格式
                    var_type = var_row['type_var'].get()
                    var_value = var_row['value_var'].get()
                    if var_type == "数字":
                        try:
                            float(var_value)
                        except ValueError:
                            messagebox.showwarning("提示", f"变量「{name}」的值「{var_value}」不是有效的数字！")
                            return
                    var_names_seen.add(name)
                    variables_data.append({
                        'name': name,
                        'type': var_type,
                        'value': var_value,
                    })
            # 检查与整个流程中其他节点定义的变量是否重复
            all_vars = self.get_all_defined_vars()
            existing_names = [v[0] for v in all_vars]
            for var_data in variables_data:
                if var_data['name'] in existing_names:
                    messagebox.showwarning("提示", f"变量名「{var_data['name']}」已存在于其他节点中，请使用不同的名称！")
                    return
            p['variables'] = variables_data

        # 标签节点：自动去重
        if t == '标签':
            label_name = p.get('label_name', '').strip()
            if not label_name:
                messagebox.showwarning("提示", "标签名不能为空！")
                return
            labels = LabelManager.collect_all_labels(self.data)
            if label_name in labels:
                messagebox.showwarning("提示", "标签名已存在，不能重复！")
                return

        # 跳转节点：校验目标标签
        if t == '跳转':
            target_label = p.get('target_label', '').strip()
            if not target_label:
                messagebox.showwarning("提示", "跳转目标标签不能为空！")
                return
            if not LabelManager.validate_jump(self.data, target_label):
                if not messagebox.askyesno(
                    "提示", "目标标签不存在，是否仍要创建？",
                    icon="question",
                ):
                    return
            p['target_label'] = target_label

        # AI 决策节点：将 Text 控件内容转为字符串
        if t == 'AI 决策' and 'ai_system_prompt' in self.param_vars:
            prompt_widget = self.param_vars['ai_system_prompt']
            try:
                p['ai_system_prompt'] = prompt_widget.get("1.0", "end-1c")
            except Exception:
                p['ai_system_prompt'] = str(prompt_widget) if prompt_widget else ""

        s: Dict[str, Any] = {
            "id": str(uuid.uuid4()), "type": t, "params": p}
        if t == '条件分支':
            s['true'] = []
            s['false'] = []
        elif t == '普通循环':
            s['body'] = []
        elif t == '数据循环':
            s['body'] = []
        elif t == '子流程':
            s['body'] = []

        self._save_undo_snapshot()
        sel = self.tree.selection()
        if sel:
            selected_item = sel[-1]
            info = self.tree_map.get(selected_item)
            if info:
                if info['type'] == 'container':
                    lst = info['list']
                    idx = len(info['list'])
                else:
                    lst = info['list']
                    idx = info['index'] + 1
                lst.insert(idx, s)
            else:
                self.data.append(s)
        else:
            self.data.append(s)
        self.refresh_tree()

    def update_step(self) -> None:
        """更新选中步骤的参数。"""
        self._save_undo_snapshot()
        sel = self.tree.selection()
        if not sel:
            return
        info = self.tree_map.get(sel[0])
        if info['type'] == 'step':
            info['data']['type'] = self.cb_type.get()
            p = {k: v.get() if not isinstance(v, list) else v
                 for k, v in self.param_vars.items()}

            # 变量节点：处理变量列表
            t = self.cb_type.get()
            if t == '变量管理' and 'variables' in self.param_vars and isinstance(self.param_vars['variables'], list):
                variables_data = []
                for var_row in self.param_vars['variables']:
                    if isinstance(var_row, dict) and 'name_var' in var_row:
                        name = var_row['name_var'].get().strip()
                        if name:
                            variables_data.append({
                                'name': name,
                                'type': var_row['type_var'].get(),
                                'value': var_row['value_var'].get(),
                            })
                p['variables'] = variables_data

            # AI 决策节点：将 Text 控件内容转为字符串
            if t == 'AI 决策' and 'ai_system_prompt' in self.param_vars:
                prompt_widget = self.param_vars['ai_system_prompt']
                try:
                    p['ai_system_prompt'] = prompt_widget.get("1.0", "end-1c")
                except Exception:
                    p['ai_system_prompt'] = str(prompt_widget) if prompt_widget else ""

            info['data']['params'] = p
            self.refresh_tree()

    def delete_step(self) -> None:
        """删除选中的步骤。"""
        self._save_undo_snapshot()
        selection = self.tree.selection()
        if not selection:
            return

        step_items = []
        for item_id in selection:
            info = self.tree_map.get(item_id)
            if info and info['type'] == 'step':
                step_items.append(item_id)

        if not step_items:
            messagebox.showinfo("提示", "请选择要删除的步骤")
            return

        if not messagebox.askyesno(
            "确认", f"删除选中的 {len(step_items)} 个步骤？"
        ):
            return

        to_delete = []
        for item_id in step_items:
            info = self.tree_map[item_id]
            to_delete.append((info['list'], info['index']))

        to_delete.sort(key=lambda x: x[1], reverse=True)

        for lst, idx in to_delete:
            if idx < len(lst):
                lst.pop(idx)

        # 先清空参数面板
        for w in self.frm_params.winfo_children():
            w.destroy()
        self.param_vars = {}
        self._editing_step_data = None

        # 刷新树（保留展开状态）
        self.refresh_tree()

    def copy_steps(self) -> None:
        """复制选中的步骤到剪贴板（支持多选）。"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要复制的步骤")
            return

        # 收集选中的步骤项（仅 step 类型）
        selected_steps: List[str] = []
        for item_id in selection:
            info = self.tree_map.get(item_id)
            if info and info.get('type') == 'step':
                selected_steps.append(item_id)

        if not selected_steps:
            messagebox.showinfo("提示", "请选择步骤节点（不能选择容器节点）")
            return

        # 过滤掉父级步骤已在选中列表中的子步骤
        # 例如：选中了条件分支节点，它内部的子步骤不应单独加入 clipboard
        # 因为父级步骤 deepcopy 时已包含完整子结构
        parent_ids: Set[str] = set()
        for item_id in selected_steps:
            parent = self.tree.parent(item_id)
            while parent:
                if parent in selected_steps:
                    parent_ids.add(item_id)
                    break
                parent = self.tree.parent(parent)

        step_items = [iid for iid in selected_steps if iid not in parent_ids]

        # 按树显示顺序排序（保持粘贴顺序与选中顺序一致）
        step_items.sort(key=lambda iid: (
            id(self.tree_map[iid]['list']),
            self.tree_map[iid]['index'],
        ))

        # 深度复制选中的步骤
        self.clipboard = []
        for item_id in step_items:
            info = self.tree_map[item_id]
            self.clipboard.append(copy.deepcopy(info['data']))

        self.lbl_status.config(
            text=f"已复制 {len(self.clipboard)} 个步骤", fg="blue")

    def cut_steps(self) -> None:
        """剪切选中的步骤到剪贴板（复制后删除）。"""
        self._save_undo_snapshot()
        selection_before = self.tree.selection()
        self.copy_steps()
        if self.clipboard:
            # 直接删除选中的步骤，不弹确认对话框
            step_items = []
            for item_id in selection_before:
                info = self.tree_map.get(item_id)
                if info and info['type'] == 'step':
                    step_items.append(item_id)
            if not step_items:
                return
            to_delete = []
            for item_id in step_items:
                info = self.tree_map[item_id]
                lst = info['list']
                step = info['data']
                if step in lst:
                    to_delete.append((lst, step))
            for lst, step in to_delete:
                lst.remove(step)
            self.refresh_tree()
            self.lbl_status.config(
                text=f"已剪切 {len(self.clipboard)} 个步骤", fg="blue")

    @staticmethod
    def _regenerate_ids(step_list: List[Dict[str, Any]]) -> None:
        """递归重新生成步骤列表中所有步骤的 ID（包括嵌套子步骤）。

        Args:
            step_list: 步骤列表
        """
        for step in step_list:
            step['id'] = str(uuid.uuid4())
            if step['type'] == '条件分支':
                RPAGUI._regenerate_ids(step.get('true', []))
                RPAGUI._regenerate_ids(step.get('false', []))
            elif step['type'] in ('普通循环', '数据循环'):
                RPAGUI._regenerate_ids(step.get('body', []))

    def paste_steps(self) -> None:
        """粘贴剪贴板中的步骤，插入到选中步骤之后，ID 自动重编。

        粘贴规则：
        - 选中主流程步骤 → 插入到该步骤后面
        - 选中子流程容器 → 追加到子流程 body 末尾
        - 选中子流程中的步骤 → 插入到该步骤后面（子流程 body 内）
        - 选中条件分支/循环中的步骤 → 溯源到主流程父级之后
        - 没选中 → 追加到 self.data 末尾
        """
        self._save_undo_snapshot()
        if not self.clipboard:
            messagebox.showinfo("提示", "剪贴板为空，请先复制步骤")
            return

        # 深拷贝并重新生成 ID
        pasted = copy.deepcopy(self.clipboard)
        self._regenerate_ids(pasted)

        sel = self.tree.selection()
        lst = self.data
        idx = len(self.data)

        if sel:
            target_info = None
            for item_id in reversed(sel):
                info = self.tree_map.get(item_id)
                if info:
                    target_info = info
                    break

            if target_info:
                info_type = target_info.get('type')
                target_lst = target_info['list']

                if info_type == 'container':
                    # 容器节点（子流程的 📁 容器）→ 追加到容器末尾
                    lst = target_lst
                    idx = len(lst)

                elif target_lst is self.data:
                    # 主流程中的步骤 → 插入到该步骤后面
                    lst = self.data
                    idx = target_info['index'] + 1

                else:
                    # 子流程/条件分支/循环中的步骤
                    # 判断是否是子流程的 body
                    is_sub_body = False
                    for s in self.data:
                        if s.get('body') is target_lst and s.get('type') == '子流程':
                            is_sub_body = True
                            break

                    if is_sub_body:
                        # 子流程 body 中的步骤 → 插入到该步骤后面
                        lst = target_lst
                        idx = target_info['index'] + 1
                    else:
                        # 条件分支/循环中的步骤 → 溯源到主流程父级之后
                        parent_idx = -1
                        for i, s in enumerate(self.data):
                            if (s.get('true') is target_lst
                                    or s.get('false') is target_lst
                                    or s.get('body') is target_lst):
                                parent_idx = i
                                break
                        if parent_idx >= 0:
                            lst = self.data
                            idx = parent_idx + 1
                        else:
                            lst = self.data
                            idx = len(self.data)

        # 批量插入
        for i, step in enumerate(pasted):
            lst.insert(idx + i, step)

        self.refresh_tree()
        self.lbl_status.config(
            text=f"已粘贴 {len(pasted)} 个步骤", fg="green")

    def on_tree_right_click(self, event: tk.Event) -> None:
        """右键菜单：复制、粘贴、删除。"""
        # 选中右键点击的项
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)

        menu = tk.Menu(self.tree, tearoff=0)
        menu.add_command(label="剪切 (Ctrl+X)", command=self.cut_steps)
        menu.add_command(label="复制 (Ctrl+C)", command=self.copy_steps)
        menu.add_command(label="粘贴插入 (Ctrl+V)", command=self.paste_steps)
        menu.add_separator()
        menu.add_command(label="撤消 (Ctrl+Z)", command=self.undo)
        menu.add_separator()
        menu.add_command(label="删除 (Del)", command=self.delete_step)
        menu.add_separator()
        menu.add_command(label="导入流程", command=self.import_json)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ================================================================
    # 撤销功能
    # ================================================================

    def _save_undo_snapshot(self) -> None:
        """保存当前 self.data 的快照到撤销栈。"""
        self._modified = True
        self._redo_stack.clear()  # 新操作清空重做栈
        self._undo_stack.append(copy.deepcopy(self.data))
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)

    def undo(self) -> None:
        """撤销上一个操作，恢复到之前的状态。"""
        if not self._undo_stack:
            self.lbl_status.config(text="没有可撤销的操作", fg="gray")
            return
        self._redo_stack.append(copy.deepcopy(self.data))
        if len(self._redo_stack) > self._max_undo:
            self._redo_stack.pop(0)
        self.data = self._undo_stack.pop()
        self._editing_step_data = None
        self.refresh_tree()
        self.lbl_status.config(
            text=f"已撤销（剩余 {len(self._undo_stack)} 步可撤销）", fg="orange")

    def redo(self) -> None:
        """重做被撤销的操作。"""
        if not self._redo_stack:
            self.lbl_status.config(text="没有可重做的操作", fg="gray")
            return
        self._undo_stack.append(copy.deepcopy(self.data))
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        self.data = self._redo_stack.pop()
        self._editing_step_data = None
        self.refresh_tree()
        self.lbl_status.config(
            text=f"已重做（剩余 {len(self._redo_stack)} 步可重做）", fg="green")

    # 文件操作
    def new_file(self) -> None:
        """新建流程。"""
        if messagebox.askyesno("新建", "清空当前所有步骤？"):
            flow_name = simpledialog.askstring(
                "新建流程",
                "请输入流程名称（默认：未命名）",
                initialvalue="未命名")
            if flow_name is None:
                return
            if not flow_name.strip():
                flow_name = "未命名"

            self._save_undo_snapshot()
            self.current_flow_name = flow_name
            self.current_flow_dir = flow_name

            if not os.path.exists(self.current_flow_dir):
                os.makedirs(self.current_flow_dir)
                templates_dir = os.path.join(self.current_flow_dir, TEMPLATES_DIR)
                if not os.path.exists(templates_dir):
                    os.makedirs(templates_dir)

            self.core.templates_dir = os.path.join(
                self.current_flow_dir, TEMPLATES_DIR)
            self.data = []
            self._editing_step_data = None
            self._undo_stack.clear()
            self._redo_stack.clear()
            self.refresh_tree()

    def save_json(self) -> None:
        """保存流程到 JSON 文件。"""
        default_filename = os.path.join(
            self.current_flow_dir, f"{self.current_flow_name}.json")
        f = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            initialfile=f"{self.current_flow_name}.json",
            initialdir=self.current_flow_dir)
        if f:
            with open(f, 'w', encoding='utf-8') as file:
                json.dump(self.data, file, indent=2, ensure_ascii=False)
            self._modified = False
            self.log(f"\U0001f4be 保存成功: {f}")

    def load_json(self) -> None:
        """从 JSON 文件加载流程。"""
        f = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")])
        if f:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    self.data = json.load(file)

                self.current_flow_dir = os.path.dirname(f)
                self.current_flow_name = os.path.splitext(
                    os.path.basename(f))[0]

                templates_dir = os.path.join(
                    self.current_flow_dir, TEMPLATES_DIR)
                if not os.path.exists(templates_dir):
                    os.makedirs(templates_dir)
                self.core.templates_dir = templates_dir

                self._modified = False
                self._undo_stack.clear()
                self._redo_stack.clear()
                self.refresh_tree()
                self.log(f"\U0001f4c2 加载成功: {f}")
            except Exception as e:
                messagebox.showerror("错误", f"加载失败: {e}")

    def import_json(self) -> None:
        """从 JSON 文件导入流程步骤，插入到选中步骤之后。"""
        f = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")])
        if f:
            try:
                with open(f, 'r', encoding='utf-8') as file:
                    imported = json.load(file)

                if not isinstance(imported, list):
                    messagebox.showerror("错误", "导入失败：文件格式不正确")
                    return

                if not imported:
                    messagebox.showinfo("提示", "导入文件为空")
                    return

                # 重新生成所有导入步骤的 ID
                RPAGUI._regenerate_ids(imported)

                # 确定插入位置：选中步骤之后，逻辑与 paste_steps 一致
                sel = self.tree.selection()
                if sel:
                    insert_info = None
                    for item_id in reversed(sel):
                        info = self.tree_map.get(item_id)
                        if info and info.get('type') == 'step':
                            insert_info = info
                            break
                    if insert_info:
                        lst = self.data
                        idx = insert_info['index'] + 1
                    else:
                        lst = self.data
                        idx = len(self.data)
                else:
                    lst = self.data
                    idx = len(self.data)

                self._save_undo_snapshot()
                for i, step in enumerate(imported):
                    lst.insert(idx + i, step)
                self.refresh_tree()
                self.log(
                    f"\U0001f4e5 导入成功: {f}"
                    f"（已插入 {len(imported)} 个步骤）")
            except Exception as e:
                messagebox.showerror("错误", f"导入失败: {e}")

    # 工具方法
    def select_image_file(self, var: tk.StringVar) -> None:
        """选择图片文件。"""
        # 确保 templates_dir 存在
        templates_dir = self.core.templates_dir
        if not os.path.isabs(templates_dir):
            templates_dir = os.path.abspath(templates_dir)
        if not os.path.exists(templates_dir):
            os.makedirs(templates_dir)
        self.core.templates_dir = templates_dir

        fp = filedialog.askopenfilename(
            title="选择图片文件",
            initialdir=templates_dir,
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("所有文件", "*.*"),
            ])
        if fp:
            filename = os.path.basename(fp)
            dest_path = os.path.join(templates_dir, filename)
            # 如果文件不在 templates_dir，则复制过去
            if not os.path.exists(dest_path):
                import shutil
                try:
                    shutil.copy2(fp, dest_path)
                except Exception as e:
                    messagebox.showerror("错误", f"复制图片失败: {e}")
                    return
            var.set(filename)

    def select_file(self, var: tk.StringVar) -> None:
        """选择要打开的文件。"""
        fp = filedialog.askopenfilename(
            title="选择文件", filetypes=[("所有文件", "*.*")])
        if fp:
            var.set(fp)

    def select_data_file(self, var: tk.StringVar) -> None:
        """
        选择数据文件（CSV/Excel）。

        Args:
            var: 用于存储文件路径的 StringVar
        """
        fp = filedialog.askopenfilename(
            title="选择数据文件",
            filetypes=[
                ("CSV文件", "*.csv"),
                ("Excel文件", "*.xlsx *.xls"),
                ("所有文件", "*.*"),
            ])
        if fp:
            var.set(fp)
            # 清除文件字段缓存，以便重新读取新文件的字段
            self.file_fields_cache.pop(fp, None)

    def select_key(self, var: tk.StringVar) -> None:
        """使用 UIPickers 选择按键。"""
        UIPickers.select_key(
            self.root, lambda key: var.set(key))

    def capture_template(self) -> None:
        """使用 capture_area_tool 进行模板截图。"""
        def callback_func(filename: str) -> None:
            sel_type = self.cb_type.get()
            if sel_type in ["点击", "等待", "条件分支"]:
                if 'template' in self.param_vars:
                    self.param_vars['template'].set(filename)
            elif sel_type == "循环":
                if 'value' in self.param_vars:
                    self.param_vars['value'].set(filename)
            self.update_params_ui()

        UIPickers.capture_area_tool(
            self.root, 'template', callback_func,
            self.core.templates_dir)

    def start_window_spy(self, target_var: tk.StringVar) -> None:
        """使用 UIPickers 的窗口探测器。"""
        UIPickers.start_window_spy(
            self.root, lambda title: target_var.set(title))

    def start_coord_picker(self, target_var: Optional[tk.StringVar] = None) -> None:
        """使用 UIPickers 的坐标拾取工具。"""
        if target_var is None:
            if 'pos_var' in self.param_vars:
                target_var = self.param_vars['pos_var']
        if target_var is None:
            messagebox.showwarning("提示", "无法确定要填充的目标变量")
            return
        UIPickers.pick_coordinate(
            self.root, lambda coord_str: target_var.set(coord_str))

    def start_region_picker(
        self, target_var: Optional[tk.StringVar] = None,
    ) -> None:
        """使用 UIPickers 的区域选择工具。"""
        if target_var is None:
            if 'input_region_var' in self.param_vars:
                target_var = self.param_vars['input_region_var']
        if target_var is None:
            messagebox.showwarning("提示", "无法确定要填充的目标变量")
            return
        UIPickers.capture_area_tool(
            self.root, 'region',
            lambda region_str: target_var.set(region_str))

    def start_color_picker(self, target_var: tk.StringVar) -> None:
        """使用 UIPickers 的颜色拾取工具。"""
        UIPickers.pick_color(
            self.root, lambda color_str: target_var.set(color_str))

    def get_selected_step_params(self) -> Optional[Dict[str, Any]]:
        """
        获取选中步骤的参数，用于测试识别。

        Returns:
            步骤参数字典，未选中步骤或不支持的步骤类型时返回 None
        """
        sel = self.tree.selection()
        if not sel:
            return None
        info = self.tree_map.get(sel[0])
        if not info or info['type'] != 'step':
            return None
        step = info['data']
        step_type = step['type']
        p = step.get('params', {})

        # 所有步骤类型都支持测试识别，不限制类型
        params: Dict[str, Any] = {}

        # 图像模板参数
        if step_type in ['点击', '等待']:
            params['template'] = p.get('template', '')
        elif step_type == 'OCR':
            params['keyword'] = p.get('keyword', '')
            params['ocr_url'] = p.get('ocr_url', OCR_DEFAULT_URL)

        # 窗口参数
        if step_type == '窗口':
            params['bg_window_title'] = p.get('window_title', '')

        # 输入参数
        if step_type == '输入':
            params['text'] = p.get('text', '')

        # 按键参数
        if step_type == '按键':
            params['key'] = p.get('key', '')
            params['action'] = '按键'

        # 滚轮参数
        if step_type == '滚轮':
            params['direction'] = p.get('direction', '向下')
            params['clicks'] = p.get('clicks', 1)
            action_dir = '向上' if params['direction'] == '向上' else '向下'
            params['action'] = f'滚轮{action_dir}'

        # 通用参数
        params['confidence'] = p.get('confidence', DEFAULT_CONFIDENCE)
        params['find_nth'] = p.get('find_nth', 1)
        params['offset_x'] = p.get('offset_x', 0)
        params['offset_y'] = p.get('offset_y', 0)
        params['color_enable'] = p.get('color_enable', False)

        # 颜色参数
        if params['color_enable']:
            params['target_color'] = p.get('target_color', '')
            params['color_tolerance'] = p.get('color_tolerance', 10)

        # 区域参数
        region = p.get('input_region_var', '')
        if region:
            params['region'] = region

        # 后台模式参数
        params['use_bg'] = p.get('use_bg', False)
        if params['use_bg']:
            params['bg_window_title'] = p.get('bg_window_title', '')

        # 动作参数
        if step_type == '点击':
            action = p.get('click_action', '左键单击')
            params['action'] = action
        elif step_type == 'OCR':
            params['action'] = p.get('click_action', '仅识别')

        return params

    def test_recognition(self) -> None:
        """打开测试识别窗口，自动带入选中步骤的参数。"""
        step_params = self.get_selected_step_params()
        self.test_lab = TestLabWindow(
            self.root, self.core, self.core.templates_dir, step_params)
        self.test_lab.open()

    def find_window_by_title(self, title: str) -> Optional[int]:
        """
        根据窗口标题查找窗口句柄。

        Args:
            title: 窗口标题

Hello RPA            窗口句柄，未找到时返回 None
        """
        return _find_window_by_title(title)

    def find_label_location(
        self,
        step_list: List[Dict[str, Any]],
        label_name: str,
    ) -> Optional[Tuple[List[Dict[str, Any]], int]]:
        """
        递归查找标签位置。

        Args:
            step_list: 步骤列表
            label_name: 标签名称

        Returns:
            (列表, 索引) 元组，未找到时返回 None
        """
        for i, step in enumerate(step_list):
            if step['type'] == '标签' \
                    and step['params'].get('label_name') == label_name:
                return (step_list, i)
            if 'true' in step:
                res = self.find_label_location(step['true'], label_name)
                if res:
                    return res
            if 'false' in step:
                res = self.find_label_location(step['false'], label_name)
                if res:
                    return res
            if 'body' in step:
                res = self.find_label_location(step['body'], label_name)
                if res:
                    return res
        return None

    # 运行控制
    def start_run(self) -> None:
        """启动流程运行。"""
        if getattr(self, "worker", None) \
                and getattr(self.worker, "is_alive", lambda: False)():
            return
        self.stop_flag = False
        self._executing = True
        self.runtime_vars = {}
        self.runtime_bg_hwnd = None
        self.runtime_bg_title = ''
        try:
            self.txt_log.delete(1.0, tk.END)
        except Exception:
            pass
        self.stop_event.clear()
        snap = copy.deepcopy(self.data)

        self.worker = ExecutionEngine(
            snap, self.core, self.ui_queue, self.stop_event)
        try:
            self.worker.runtime_bg_hwnd = self.runtime_bg_hwnd
            self.worker.runtime_bg_title = self.runtime_bg_title
            self.worker.runtime_vars = self.runtime_vars
        except Exception:
            pass
        self.worker.start()

        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)

    def stop_run(self) -> None:
        """停止流程运行。"""
        self.stop_event.set()
        self.stop_flag = True
        self._executing = False
        self._restore_gui()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def _restore_gui(self) -> None:
        """恢复主窗口显示（从最小化恢复并置顶）。"""
        try:
            self.root.state('normal')
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _show_region_overlay_on_screen(self, region_var: tk.StringVar) -> None:
        """
        根据区域变量值在屏幕上绘制红色矩形框，用于可视化预览查找区域。

        支持两种值格式：
        - 直接坐标: "x1,y1,x2,y2"
        - 变量名: 从 self.runtime_vars 中解析

        Args:
            region_var: 保存区域值的 StringVar
        """
        value = region_var.get().strip()
        if not value or self._executing:
            return

        # 解析为 (x1, y1, x2, y2) 坐标
        try:
            if ',' in value:
                parts = [int(x.strip()) for x in value.split(',')]
                if len(parts) == 4:
                    x1, y1, x2, y2 = parts
                else:
                    return
            elif value in self.runtime_vars:
                resolved = self.runtime_vars[value]
                if isinstance(resolved, (tuple, list)) and len(resolved) == 4:
                    x1, y1, x2, y2 = map(int, resolved)
                elif isinstance(resolved, str) and ',' in resolved:
                    parts = [int(x.strip()) for x in resolved.split(',')]
                    if len(parts) == 4:
                        x1, y1, x2, y2 = parts
                    else:
                        return
                else:
                    return
            else:
                return
        except (ValueError, TypeError):
            return

        # 保证 x1<x2, y1<y2
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1

        width = x2 - x1
        height = y2 - y1
        if width <= 0 or height <= 0:
            return

        # 创建半透明覆盖层，绘制红色矩形
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes('-topmost', True)
        overlay.attributes('-alpha', 0.35)
        overlay.geometry(f"{width + 6}x{height + 6}+{x1 - 3}+{y1 - 3}")

        canvas = tk.Canvas(overlay, bg='black', highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_rectangle(
            3, 3, width + 3, height + 3,
            outline="red", width=3,
        )

        # 1.5秒后自动消失，或点击/ESC关闭
        overlay.after(1500, overlay.destroy)
        overlay.bind('<Button-1>', lambda e: overlay.destroy())
        overlay.bind('<Escape>', lambda e: overlay.destroy())

    # UI 日志轮询
    def show_dialog_gui_thread(self, message: str) -> str:
        """
        在主线程中显示超时对话框。

        Args:
            message: 提示消息

        Returns:
            "retry" 或 "skip"
        """
        result = {"action": "skip"}

        def show_dialog() -> None:
            dialog = tk.Toplevel(self.root)
            dialog.title("RPA超时")
            dialog.attributes('-topmost', True)
            dialog.resizable(False, False)

            # 设置对话框图标
            try:
                if self._icon_bitmap:
                    dialog.iconphoto(True, self._icon_bitmap)
            except Exception:
                pass

            # 根据消息行数动态计算窗口高度
            line_count = message.count('\n') + 1
            window_width = 420
            # 每行约 16px (9号字体) + padding: msg_label padding(50) + button_frame(60) + extra(40)
            window_height = max(220, min(line_count * 17, 600) + 50 + 60 + 40)
            screen_width = dialog.winfo_screenwidth()
            screen_height = dialog.winfo_screenheight()
            x = (screen_width // 2) - (window_width // 2)
            y = (screen_height // 2) - (window_height // 2)
            dialog.geometry(f"{window_width}x{window_height}+{x}+{y}")

            main_frame = ttk.Frame(dialog, padding="12")
            main_frame.pack(fill=tk.BOTH, expand=True)

            msg_frame = ttk.Frame(main_frame)
            msg_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 10))
            ttk.Label(
                msg_frame, text="\u26a0\ufe0f",
                font=('Segoe UI Emoji', 18),
            ).pack(side=tk.LEFT, padx=(0, 10), anchor=tk.N)
            ttk.Label(
                msg_frame, text=message, wraplength=360,
                justify=tk.LEFT, font=('Microsoft YaHei UI', 9),
            ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            button_frame = ttk.Frame(main_frame)
            button_frame.pack(fill=tk.X, pady=(0, 5))

            def on_retry() -> None:
                result["action"] = "retry"
                dialog.destroy()

            def on_skip() -> None:
                result["action"] = "skip"
                dialog.destroy()

            def on_closing() -> None:
                result["action"] = "skip"
                dialog.destroy()

            dialog.protocol("WM_DELETE_WINDOW", on_closing)

            retry_btn = tk.Button(
                button_frame, text="重试 (R)",
                font=('Microsoft YaHei UI', 9),
                padx=12, pady=6, relief=tk.GROOVE,
                command=on_retry)
            retry_btn.pack(side=tk.LEFT, padx=(0, 10))

            skip_btn = tk.Button(
                button_frame, text="跳过 (S)",
                font=('Microsoft YaHei UI', 9),
                padx=12, pady=6, relief=tk.GROOVE,
                command=on_skip)
            skip_btn.pack(side=tk.RIGHT, padx=(10, 0))

            dialog.bind('<R>', lambda e: on_retry())
            dialog.bind('<r>', lambda e: on_retry())
            dialog.bind('<S>', lambda e: on_skip())
            dialog.bind('<s>', lambda e: on_skip())
            dialog.bind('<Escape>', lambda e: on_skip())
            dialog.bind('<Return>', lambda e: on_retry())
            retry_btn.focus_set()

            dialog.wait_window()

        # 在主线程中同步显示对话框
        show_dialog()
        return result["action"]

    def show_pause_dialog_gui_thread(self, message: str) -> str:
        """
        在主线程中显示暂停对话框。

        Args:
            message: 提示消息

        Returns:
            'Yes' 或 'No'
        """
        result = {"continue": "No"}

        def show_dialog() -> None:
            dialog = tk.Toplevel(self.root)
            dialog.title("RPA自动化工具 - 暂停")
            dialog.attributes('-topmost', True)
            dialog.resizable(False, False)

            # 设置对话框图标
            try:
                if self._icon_bitmap:
                    dialog.iconphoto(True, self._icon_bitmap)
            except Exception:
                pass

            window_width = 380
            window_height = 180
            screen_width = dialog.winfo_screenwidth()
            screen_height = dialog.winfo_screenheight()
            x = (screen_width // 2) - (window_width // 2)
            y = (screen_height // 2) - (window_height // 2)
            dialog.geometry(f"{window_width}x{window_height}+{x}+{y}")

            main_frame = ttk.Frame(dialog, padding="20")
            main_frame.pack(fill=tk.BOTH, expand=True)

            ttk.Label(
                main_frame, text=message, wraplength=300,
                font=('Microsoft YaHei', 10),
            ).pack(pady=20)

            button_frame = ttk.Frame(main_frame)
            button_frame.pack(pady=10)

            def on_continue() -> None:
                result["continue"] = "Yes"
                dialog.destroy()

            def on_stop() -> None:
                result["continue"] = "No"
                dialog.destroy()

            def on_closing() -> None:
                result["continue"] = "No"
                dialog.destroy()

            dialog.protocol("WM_DELETE_WINDOW", on_closing)

            continue_btn = ttk.Button(
                button_frame, text="继续执行(C)",
                command=on_continue, width=15)
            continue_btn.pack(side=tk.LEFT, padx=10, ipady=8)

            stop_btn = ttk.Button(
                button_frame, text="停止运行(S)",
                command=on_stop, width=15)
            stop_btn.pack(side=tk.RIGHT, padx=10, ipady=8)

            dialog.bind('<C>', lambda e: on_continue())
            dialog.bind('<c>', lambda e: on_continue())
            dialog.bind('<S>', lambda e: on_stop())
            dialog.bind('<s>', lambda e: on_stop())
            dialog.bind('<Escape>', lambda e: on_stop())
            continue_btn.focus_set()

            dialog.wait_window()

        show_dialog()
        return result["continue"]

    def start_ui_poll(self) -> None:
        """启动 UI 消息轮询。"""
        def poll() -> None:
            try:
                while True:
                    ev = self.ui_queue.get_nowait()
                    ev_type = ev.get("type")

                    if ev_type == "log":
                        self._safe_call(self._handle_log_event, ev)

                    elif ev_type == "minimize_gui":
                        self._safe_call(lambda: self.root.state('iconic'))

                    elif ev_type == "show_gui":
                        self._safe_call(lambda: self._restore_gui())

                    elif ev_type == "done":
                        self._executing = False
                        try:
                            self.txt_log.insert(
                                tk.END, "\u2705 流程执行完成\n")
                            self.txt_log.see(tk.END)
                            self.btn_start.config(state=tk.NORMAL)
                            self.btn_stop.config(state=tk.DISABLED)
                        except Exception:
                            pass

                    elif ev_type == "timeout":
                        try:
                            timeout_msg = ev.get("msg", "节点执行超时")
                            user_choice = self.show_dialog_gui_thread(timeout_msg)

                            result_event = ev.get("result_event")
                            user_choice_dict = ev.get("user_choice")

                            if user_choice == "retry":
                                if user_choice_dict:
                                    user_choice_dict['action'] = "retry"
                                self.ui_queue.put(
                                    {"type": "timeout_confirmed"})
                            else:
                                if user_choice_dict:
                                    user_choice_dict['action'] = "skip"
                                self.ui_queue.put(
                                    {"type": "timeout_canceled"})

                            if result_event:
                                result_event.set()
                        except Exception as e:
                            self.txt_log.insert(
                                tk.END,
                                f"\u274c 超时提示处理异常: {e}\n")
                            self.txt_log.see(tk.END)
                            self.ui_queue.put(
                                {"type": "timeout_canceled"})
                            result_event = ev.get("result_event")
                            if result_event:
                                result_event.set()

                    elif ev_type == "pause":
                        try:
                            pause_msg = ev.get(
                                "msg", "流程已暂停，请手动确认...")
                            user_choice = self.show_pause_dialog_gui_thread(
                                pause_msg)

                            result_event = ev.get("result_event")
                            user_choice_dict = ev.get("user_choice")

                            if user_choice == 'Yes':
                                if user_choice_dict:
                                    user_choice_dict['continue'] = 'Yes'
                                self.ui_queue.put(
                                    {"type": "pause_confirmed"})
                            else:
                                if user_choice_dict:
                                    user_choice_dict['continue'] = 'No'
                                self.ui_queue.put(
                                    {"type": "pause_canceled"})

                            if result_event:
                                result_event.set()
                        except Exception as e:
                            self.txt_log.insert(
                                tk.END,
                                f"\u274c 暂停处理异常: {e}\n")
                            self.txt_log.see(tk.END)
                            result_event = ev.get("result_event")
                            if result_event:
                                result_event.set()

            except Empty:
                pass
            try:
                self.root.after(10, poll)
            except Exception:
                pass

        poll()

    # 日志
    def log(self, m: str) -> None:
        """
        向日志文本框追加消息。

        Args:
            m: 日志消息
        """
        def _log() -> None:
            self.txt_log.insert(tk.END, m + "\n")
            self.txt_log.see(tk.END)
        self.root.after(0, _log)

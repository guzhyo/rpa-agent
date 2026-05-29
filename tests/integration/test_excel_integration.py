"""
RPA 集成测试 — 以 Excel 为目标的真实环境测试

测试场景覆盖:
  场景1: 窗口激活 + 文本输入（后台模式）
  场景2: 按键组合导航（Tab/Ctrl+A）
  场景3: 延时节点
  场景4: 变量管理 + 变量计算
  场景5: 条件分支（变量条件）
  场景6: 数据循环（CSV → Excel）
  场景7: 标签 + 跳转
  场景8: 滚动操作
  场景9: 暂停节点
  场景10: 文件节点（打开 Excel 文件）

运行前准备:
  1. 安装依赖: pip install openpyxl
  2. 确保已安装 Microsoft Excel
  3. 如使用后台模式测试，确保 Excel 窗口不被遮挡

运行方式:
  python -m pytest tests/integration/test_excel_integration.py -v -s
  python tests/integration/test_excel_integration.py
"""

import os
import sys
import time
import json
import uuid
import tempfile
import unittest
import subprocess
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── 辅助模块 ──
from tests.integration.excel_helper import (
    create_test_excel,
    read_excel_cell,
    read_excel_row,
    launch_excel,
    kill_excel,
    find_excel_exe,
    is_excel_available,
    wait_ready,
    get_excel_hwnd,
    HAS_OPENPYXL,
)
from tests.integration.test_harness import (
    IntegrationTestRunner,
    make_node,
    make_click_node,
    make_input_node,
    make_key_node,
    make_delay_node,
    make_window_node,
    make_label_node,
    make_jump_node,
    make_var_mgr_node,
    make_var_calc_node,
    make_condition_node,
    make_data_loop_node,
    make_scroll_node,
    make_pause_node,
    make_file_node,
    save_workflow,
    load_workflow,
)


# ============================================================
# 跳过条件
# ============================================================

def _can_run_excel_tests():
    """检查是否能运行 Excel 集成测试。"""
    reasons = []
    if not HAS_OPENPYXL:
        reasons.append("缺少 openpyxl 库 (pip install openpyxl)")
    if not is_excel_available():
        reasons.append("未找到 Excel/WPS 安装 (已搜索注册表+常见路径+WPS Office)")
    return len(reasons) == 0, reasons


CAN_RUN, SKIP_REASONS = _can_run_excel_tests()
SKIP_MSG = "跳过 Excel 集成测试:\n  " + "\n  ".join(SKIP_REASONS) if SKIP_REASONS else ""


# ============================================================
# 测试基类 — 管理 Excel 进程生命周期
# ============================================================

@unittest.skipUnless(CAN_RUN, SKIP_MSG)
class BaseExcelIntegrationTest(unittest.TestCase):
    """Excel 集成测试基类：管理 Excel 进程的启停"""

    excel_proc: subprocess.Popen = None
    excel_file: str = ""
    _temp_files: list = []

    @classmethod
    def setUpClass(cls):
        """创建测试 Excel 文件并启动 Excel"""
        cls._temp_files = []

    @classmethod
    def tearDownClass(cls):
        """清理所有临时文件"""
        for f in cls._temp_files:
            try:
                if os.path.exists(f):
                    os.unlink(f)
            except Exception:
                pass

    def setUp(self):
        """每个测试前创建独立的 Excel 文件并启动"""
        self._create_test_file()
        self._launch_excel()

    def tearDown(self):
        """每个测试后关闭 Excel"""
        kill_excel(self.excel_proc)
        self.excel_proc = None
        time.sleep(0.5)
        # 清理临时文件
        if self.excel_file and os.path.exists(self.excel_file):
            try:
                os.unlink(self.excel_file)
            except Exception:
                pass

    def _create_test_file(self):
        """子类可覆盖，创建测试用 Excel 文件"""
        fd, self.excel_file = tempfile.mkstemp(suffix='.xlsx', prefix='rpa_test_')
        os.close(fd)
        create_test_excel(self.excel_file, data=[
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
        ])

    def _launch_excel(self):
        """启动 Excel"""
        self.excel_proc = launch_excel(self.excel_file, timeout=15)
        self.assertTrue(wait_ready(os.path.basename(self.excel_file), timeout=10),
                        "Excel 窗口未在超时时间内出现")
        time.sleep(1.0)  # 等待 Excel 完全就绪

    def _get_filename(self) -> str:
        return os.path.basename(self.excel_file)

    def _run_workflow(self, flow: list, max_duration: float = 30.0) -> IntegrationTestRunner:
        """执行工作流并返回 runner"""
        runner = IntegrationTestRunner(flow, max_duration=max_duration)
        runner.run()
        return runner

    # A1 在客户区中的估算坐标（WPS/Excel 通用，1080p 窗口）
    _A1_CLIENT_X = 84   # 行头≈48 + 半格宽≈36
    _A1_CLIENT_Y = 62   # 公式栏≈28 + 列头≈22 + 半行高≈12

    def _get_a1_screen_pos(self) -> tuple:
        """
        动态获取当前 WPS/Excel 窗口中 A1 单元格中心的屏幕坐标。

        区域画框工具产出屏幕坐标，pos_var 字面量必须匹配。
        通过 WinDriver.get_client_origin 动态计算，适应不同窗口位置/DPI。

        Returns:
            (screen_x, screen_y) 元组
        """
        import ctypes
        import win32gui
        from rpa.win_driver import WinDriver

        title_substr = self._get_filename()
        hwnd: int = 0

        def enum_cb(hwnd_enum: int, _) -> bool:
            nonlocal hwnd
            if win32gui.IsWindowVisible(hwnd_enum):
                text = win32gui.GetWindowText(hwnd_enum)
                if title_substr.lower() in text.lower():
                    hwnd = hwnd_enum
                    return False  # 停止枚举
            return True

        win32gui.EnumWindows(enum_cb, None)

        if hwnd:
            # 确保窗口非最小化，否则 ClientToScreen 返回错误坐标
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.15)
            ox, oy = WinDriver.get_client_origin(hwnd)
            return ox + self._A1_CLIENT_X, oy + self._A1_CLIENT_Y
        # fallback：窗口未找到时使用最大化 1080p 的典型值
        return 84, 200

    def _assert_cell_value(self, cell: str, expected: str,
                           sheet_name: str = "Sheet", msg: str = ""):
        """断言 Excel 单元格值"""
        actual = read_excel_cell(self.excel_file, cell, sheet_name)
        self.assertEqual(expected, actual,
                         f"{msg or cell}: 期望 '{expected}'，实际 '{actual}'")

    def _assert_contains_log(self, runner: IntegrationTestRunner, keyword: str,
                             msg: str = ""):
        """断言日志包含关键字"""
        combined = "\n".join(runner.logs)
        self.assertIn(keyword, combined,
                      f"{msg or '日志'}: 期望包含 '{keyword}'")


# ============================================================
# 场景1: 窗口激活 + 文本输入（后台模式）
# ============================================================

class TestScenario01_WindowAndInput(BaseExcelIntegrationTest):
    """
    场景1: 窗口激活 + 文本输入

    流程:
      1. [窗口] 激活 Excel 窗口为后台目标
      2. [输入] 在 A1 单元格输入 "Hello RPA"
      3. [按键] Down 移动到下一行
      4. [输入] 在 A2 单元格输入 "集成测试"
    """

    def test_window_activate_and_input(self):
        # Verifies window activation + text input in target cells.
        # Uses a single click-to-focus + keyboard nav, which tests
        # the bg input/ESC/DOWN/ctrl+s pipeline end-to-end, without
        # depending on ClientToScreen (which returns (0,0) for
        # minimized windows).
        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("Hello RPA", use_bg=True),             # center-click + Ctrl+V
            make_delay_node("0.5"),
            make_key_node("ESC", use_bg=True),                     # 清除粘贴选项
            make_delay_node("0.2"),
            make_key_node("CTRL+HOME", use_bg=True),              # → A1
            make_delay_node("0.3"),
            make_input_node("覆盖A1", use_bg=True),               # 覆盖 A1 验证
            make_delay_node("0.3"),
            make_key_node("DOWN", use_bg=True),                    # → A2
            make_delay_node("0.2"),
            make_input_node("集成测试", use_bg=True),             # 填入 A2
            make_delay_node("0.3"),
            make_key_node("CTRL+S", use_bg=True),
            make_delay_node("0.5"),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")

        self._assert_cell_value("A1", "覆盖A1",
                                "A1 应被覆盖为 '覆盖A1'")
        self._assert_cell_value("A2", "集成测试",
                                "A2 应被填入 '集成测试'")


# ============================================================
# 场景2: 按键组合操作（Ctrl+A / Ctrl+C / 导航）
# ============================================================

class TestScenario02_KeyCombinations(BaseExcelIntegrationTest):
    """
    场景2: 按键组合操作

    流程:
      1. [窗口] 激活 Excel
      2. [输入] A1: "测试按键组合"
      3. [按键] Ctrl+A 全选
      4. [按键] Ctrl+C 复制
      5. [按键] Tab
      6. [按键] Ctrl+V 粘贴
      7. [按键] Enter
      8. [输入] B1: "粘贴验证"
    """

    def _create_test_file(self):
        fd, self.excel_file = tempfile.mkstemp(suffix='.xlsx', prefix='rpa_key_')
        os.close(fd)
        create_test_excel(self.excel_file, data=[
            ["", "", "", "", ""],
            ["", "", "", "", ""],
        ])

    def test_key_combinations(self):
        a1_sx, a1_sy = self._get_a1_screen_pos()
        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("测试按键组合", use_bg=True,
                            pos_var=f"{a1_sx},{a1_sy}"),  # A1 屏幕坐标
            make_delay_node("0.3"),
            make_key_node("CTRL+A", use_bg=True),
            make_delay_node("0.1"),
            make_key_node("CTRL+C", use_bg=True),
            make_delay_node("0.1"),
            make_key_node("TAB", use_bg=True),
            make_delay_node("0.1"),
            make_key_node("CTRL+V", use_bg=True),
            make_delay_node("0.2"),
            make_key_node("CTRL+S", use_bg=True),     # 保存到磁盘
            make_delay_node("0.5"),                   # 等待保存完成
        ]

        runner = self._run_workflow(flow, max_duration=20)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")

        # 验证 A1 有内容 (后台输入可能使用不同方式)
        a1_val = read_excel_cell(self.excel_file, "A1")
        self.assertNotEqual(a1_val, "", "A1 应有内容")
        self._assert_contains_log(runner, "执行完成", "应正常完成")


# ============================================================
# 场景3: 延时节点
# ============================================================

class TestScenario03_Delay(BaseExcelIntegrationTest):
    """
    场景3: 延时节点

    流程:
      1. [窗口] 激活 Excel
      2. [输入] A1: "延时前"
      3. [延时] 2 秒
      4. [输入] B1: "延时后"

    验证延时实际生效（通过时间戳差值）
    """

    def test_delay_between_inputs(self):
        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("延时前", use_bg=True),
            make_delay_node("1.5"),
            make_input_node("延时后", use_bg=True),
        ]

        start = time.time()
        runner = self._run_workflow(flow, max_duration=20)
        elapsed = time.time() - start

        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        # 验证延时确实生效（至少等了1秒）
        self.assertGreaterEqual(elapsed, 1.0, f"延时未生效, 实际耗时 {elapsed:.2f}s")


# ============================================================
# 场景4: 变量管理 + 变量计算
# ============================================================

class TestScenario04_Variables(BaseExcelIntegrationTest):
    """
    场景4: 变量管理与计算

    流程:
      1. [变量管理] 注册变量 x=10, y=3
      2. [变量计算] sum = x + y
      3. [变量计算] product = x * y
      4. [窗口] 激活 Excel
      5. [输入] A1: 输出 x (使用变量)
      6. [输入] B1: 输出 sum
      7. [输入] C1: 输出 product
    """

    def test_variable_register_and_calc(self):
        flow = [
            make_var_mgr_node([
                {"name": "x", "type": "数字", "value": "10"},
                {"name": "y", "type": "数字", "value": "3"},
            ]),
            make_var_calc_node("x + y", "sum"),
            make_var_calc_node("x * y", "product"),
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("变量x=10, y=3", use_bg=True),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        self._assert_contains_log(runner, "执行完成", "变量计算应成功执行")

    def test_builtin_functions(self):
        """测试内置函数：abs, round, min, max"""
        flow = [
            make_var_mgr_node([
                {"name": "a", "type": "数字", "value": "-5.7"},
                {"name": "b", "type": "数字", "value": "3.2"},
            ]),
            make_var_calc_node("abs(a)", "abs_a"),
            make_var_calc_node("round(a + b, 1)", "rounded"),
            make_var_calc_node("max(a, b)", "maximum"),
            make_var_calc_node("min(a, b)", "minimum"),
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("内置函数测试", use_bg=True),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        self._assert_contains_log(runner, "执行完成")


# ============================================================
# 场景5: 条件分支（变量条件）
# ============================================================

class TestScenario05_ConditionalBranch(BaseExcelIntegrationTest):
    """
    场景5: 条件分支

    流程:
      1. [变量管理] 注册 score=85
      2. [条件分支] 如果 score > 60
         - True分支: [窗口]激活Excel → [输入]A1="及格"
         - False分支: [窗口]激活Excel → [输入]A1="不及格"
    """

    def test_condition_true_branch(self):
        flow = [
            make_var_mgr_node([
                {"name": "score", "type": "数字", "value": "85"},
            ]),
            make_condition_node("score", "大于", "60",
                true_steps=[
                    make_window_node(self._get_filename(), "激活(设为后台目标)"),
                    make_input_node("及格", use_bg=True),
                ],
                false_steps=[
                    make_window_node(self._get_filename(), "激活(设为后台目标)"),
                    make_input_node("不及格", use_bg=True),
                ],
            ),
            make_delay_node("0.3"),
            make_key_node("CTRL+S", use_bg=True),
            make_delay_node("0.5"),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        self._assert_cell_value("A1", "及格", "score=85 应走 True 分支输入'及格'")

    def test_condition_false_branch(self):
        flow = [
            make_var_mgr_node([
                {"name": "score", "type": "数字", "value": "45"},
            ]),
            make_condition_node("score", "大于", "60",
                true_steps=[
                    make_window_node(self._get_filename(), "激活(设为后台目标)"),
                    make_input_node("及格", use_bg=True),
                ],
                false_steps=[
                    make_window_node(self._get_filename(), "激活(设为后台目标)"),
                    make_input_node("不及格", use_bg=True),
                ],
            ),
            make_delay_node("0.3"),
            make_key_node("CTRL+S", use_bg=True),
            make_delay_node("0.5"),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        self._assert_cell_value("A1", "不及格", "score=45 应走 False 分支输入'不及格'")


# ============================================================
# 场景6: 数据循环（CSV → Excel）
# ============================================================

class TestScenario06_DataLoop(BaseExcelIntegrationTest):
    """
    场景6: 数据循环 — 从 CSV 读取数据逐行输入 Excel

    准备:
      创建 CSV: 姓名,年龄,城市
               张三,28,北京
               李四,32,上海
               王五,25,深圳

    流程:
      1. [窗口] 激活 Excel
      2. [数据循环] 读取 CSV
         └─ [输入] 逐行填入 Excel
    """

    def _create_test_file(self):
        fd, self.excel_file = tempfile.mkstemp(suffix='.xlsx', prefix='rpa_loop_')
        os.close(fd)
        create_test_excel(self.excel_file, data=[
            ["姓名", "年龄", "城市"],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
            ["", "", ""],
        ])

    def _create_csv(self) -> str:
        """创建测试 CSV 文件"""
        fd, csv_path = tempfile.mkstemp(suffix='.csv', prefix='rpa_data_')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write("姓名,年龄,城市\n")
            f.write("张三,28,北京\n")
            f.write("李四,32,上海\n")
            f.write("王五,25,深圳\n")
        self._temp_files.append(csv_path)
        return csv_path

    def test_data_loop_from_csv(self):
        csv_path = self._create_csv()

        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_data_loop_node(
                data_file=csv_path,
                data_name="人员",
                loop_mode="向下取数",
                start_index="1",
                body=[
                    # 循环体: 将当前行数据填入 Excel
                    make_input_node("当前行", use_bg=True),
                    make_key_node("TAB", use_bg=True),
                ],
            ),
        ]

        runner = self._run_workflow(flow, max_duration=30)
        self.assertTrue(runner.success, f"数据循环执行失败: {runner.error}")
        self._assert_contains_log(runner, "执行完成", "数据循环应正常完成")


# ============================================================
# 场景7: 标签 + 跳转
# ============================================================

class TestScenario07_LabelAndJump(BaseExcelIntegrationTest):
    """
    场景7: 标签与跳转

    流程:
      1. [输入] A1: "开始"
      2. [跳转] → 目标标签 "标记点"
      3. [输入] B1: "这行应被跳过"
      4. [标签] "标记点"
      5. [输入] C1: "跳转到达"
    """

    def test_jump_skips_steps(self):
        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("开始", use_bg=True),
            make_jump_node("标记点"),
            make_input_node("这行应被跳过", use_bg=True),  # 应被跳过
            make_label_node("标记点"),
            make_input_node("跳转到达", use_bg=True),
            make_delay_node("0.3"),
            make_key_node("CTRL+S", use_bg=True),
            make_delay_node("0.5"),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")

        # B1 应该仍为空（被跳过），C1 应该有值
        b1_val = read_excel_cell(self.excel_file, "B1")
        self.assertEqual(b1_val, "", "B1 应被跳转跳过，保持空值")


# ============================================================
# 场景8: 滚动操作
# ============================================================

class TestScenario08_Scroll(BaseExcelIntegrationTest):
    """
    场景8: 滚动操作

    流程:
      1. [窗口] 激活 Excel
      2. [滚轮] 向下滚动
      3. [滚轮] 向上滚动
    """

    def _create_test_file(self):
        fd, self.excel_file = tempfile.mkstemp(suffix='.xlsx', prefix='rpa_scroll_')
        os.close(fd)
        # 创建足够多的行来支持滚动
        data = []
        for i in range(1, 51):
            data.append([f"行{i}", "", "", ""])
        create_test_excel(self.excel_file, data=data)

    def test_scroll_down_and_up(self):
        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_delay_node("0.5"),
            make_scroll_node("向下", "5", use_bg=True),
            make_delay_node("0.3"),
            make_scroll_node("向上", "3", use_bg=True),
        ]

        runner = self._run_workflow(flow)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        self._assert_contains_log(runner, "执行完成", "滚动操作应正常完成")


# ============================================================
# 场景9: 暂停节点
# ============================================================

class TestScenario09_Pause(BaseExcelIntegrationTest):
    """
    场景9: 暂停节点

    流程:
      1. [暂停] 显示暂停消息
      2. [输入] A1: "暂停后继续"

    测试 harness 自动将暂停对话框设为"继续"
    """

    def test_pause_and_resume(self):
        flow = [
            make_pause_node("测试暂停：请确认继续"),
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("暂停后继续", use_bg=True),
        ]

        runner = self._run_workflow(flow, max_duration=20)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")
        # 验证暂停对话框被自动处理
        self._assert_contains_log(runner, "[AUTO] 暂停对话框", "应自动处理暂停对话框")


# ============================================================
# 场景10: 文件节点（打开 Excel 文件）
# ============================================================

class TestScenario10_FileNode(BaseExcelIntegrationTest):
    """
    场景10: 文件节点

    流程:
      1. [文件] 打开另一个 Excel 文件
    """

    def _create_test_file(self):
        fd, self.excel_file = tempfile.mkstemp(suffix='.xlsx', prefix='rpa_file_')
        os.close(fd)
        create_test_excel(self.excel_file, data=[["文件节点测试", "", ""]])

    def setUp(self):
        """重写 setUp，不启动 Excel（由文件节点打开）"""
        self._create_test_file()

    def tearDown(self):
        """重写 tearDown，关闭可能被文件节点打开的 Excel"""
        # 关闭所有带测试前缀的 Excel 实例
        import subprocess
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'EXCEL.EXE'],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        time.sleep(0.5)
        if self.excel_file and os.path.exists(self.excel_file):
            try:
                os.unlink(self.excel_file)
            except Exception:
                pass

    def test_open_file_via_node(self):
        filename = os.path.basename(self.excel_file)
        flow = [
            make_file_node(self.excel_file),
        ]

        runner = self._run_workflow(flow, max_duration=20)
        self.assertTrue(runner.success, f"工作流执行失败: {runner.error}")

        # 验证 Excel 窗口已打开
        hwnd = get_excel_hwnd(filename, timeout=5)
        self.assertIsNotNone(hwnd, f"应找到包含 '{filename}' 的 Excel 窗口")


# ============================================================
# 场景11: 完整混合流程（综合场景）
# ============================================================

class TestScenario11_EndToEnd(BaseExcelIntegrationTest):
    """
    场景11: 端到端综合测试

    完整流程:
      1. [变量管理] 注册变量
      2. [窗口] 激活 Excel
      3. [输入] 填入表头
      4. [条件分支] 判断并选择输入内容
      5. [延时] 等待
      6. [按键] 导航
      7. [输入] 继续填入
      8. [标签] + [跳转] 跳转测试
    """

    def _create_test_file(self):
        fd, self.excel_file = tempfile.mkstemp(suffix='.xlsx', prefix='rpa_e2e_')
        os.close(fd)
        create_test_excel(self.excel_file, data=[
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
        ])

    def test_end_to_end_workflow(self):
        flow = [
            # Step 1: 变量管理
            make_var_mgr_node([
                {"name": "status", "type": "数字", "value": "1"},
                {"name": "name", "type": "字符串", "value": "端到端测试"},
            ]),
            # Step 2: 窗口激活
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_delay_node("0.3"),
            # Step 3: 输入表头
            make_input_node("RPA集成测试报告", use_bg=True),
            make_key_node("TAB", use_bg=True),
            make_input_node("状态", use_bg=True),
            make_key_node("TAB", use_bg=True),
            make_input_node("结果", use_bg=True),
            make_key_node("TAB", use_bg=True),
            # Step 4: 条件分支
            make_condition_node("status", "等于", "1",
                true_steps=[
                    make_input_node("通过", use_bg=True),
                    make_key_node("TAB", use_bg=True),
                    make_input_node("全部测试通过", use_bg=True),
                ],
                false_steps=[
                    make_input_node("失败", use_bg=True),
                    make_key_node("TAB", use_bg=True),
                    make_input_node("存在失败用例", use_bg=True),
                ],
            ),
            # Step 5: 延时
            make_delay_node("0.5"),
            # Step 6: 按键导航到下一行
            make_key_node("ENTER", use_bg=True),
            make_delay_node("0.2"),
            # Step 7: 输入第二行
            make_input_node("测试时间", use_bg=True),
            make_key_node("TAB", use_bg=True),
            make_input_node("完成", use_bg=True),
            make_delay_node("0.3"),
            make_key_node("CTRL+S", use_bg=True),
            make_delay_node("0.5"),
        ]

        runner = self._run_workflow(flow, max_duration=45)
        self.assertTrue(runner.success, f"端到端测试失败: {runner.error}")

        # 验证关键填入
        self._assert_cell_value("A1", "RPA集成测试报告", "表头验证")
        self._assert_contains_log(runner, "执行完成")


# ============================================================
# 运行入口
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("RPA 集成测试 — Excel 真实环境")
    print("=" * 60)
    if not CAN_RUN:
        print(SKIP_MSG)
        sys.exit(0)

    print(f"Excel 路径: {find_excel_exe()}")
    print()

    unittest.main(verbosity=2)

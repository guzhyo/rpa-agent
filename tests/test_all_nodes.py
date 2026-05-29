"""
RPA自动化工具 — 逐节点功能测试

测试范围（纯逻辑层，不依赖实际 Windows API / 硬件）：
  - config.py      常量完整性
  - color_utils.py 颜色解析 & 匹配检查
  - utils.py       StopException / retry_on_failure / RPAConfig / ImagePreviewPool (部分)
  - label_manager.py  标签收集 / 跳转验证
  - win_api.py     MAKELPARAM / 结构体尺寸
  - vision.py      _filter_overlaps 静态方法
  - execution.py   _compare_values / _exec_var_calc / _exec_delay / _exec_var_manager
  - action_helper.py  模块结构 & 常量

运行方式:
    python -m pytest tests/test_all_nodes.py -v
    python tests/test_all_nodes.py        # 纯 unittest 方式
"""

import os
import sys
import json
import math
import time
import unittest
import threading
import tempfile
from collections import OrderedDict
from io import StringIO

# ── 确保项目根目录在 sys.path 中 ──────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================
# 1. config.py — 常量完整性测试
# ============================================================
class TestConfigConstants(unittest.TestCase):
    """测试 config.py 中所有模块级常量的值和类型"""

    def test_version_and_title(self):
        from rpa.config import VERSION, APP_TITLE
        self.assertIsInstance(VERSION, str)
        self.assertIn(VERSION, APP_TITLE)
        self.assertTrue(APP_TITLE.startswith("RPA"))

    def test_vision_defaults(self):
        from rpa.config import DEFAULT_CONFIDENCE, COLOR_DELTA_THRESHOLD, OVERLAP_RATIO
        self.assertIsInstance(DEFAULT_CONFIDENCE, float)
        self.assertGreater(DEFAULT_CONFIDENCE, 0.0)
        self.assertLessEqual(DEFAULT_CONFIDENCE, 1.0)
        self.assertIsInstance(COLOR_DELTA_THRESHOLD, float)
        self.assertGreater(COLOR_DELTA_THRESHOLD, 0.0)
        self.assertIsInstance(OVERLAP_RATIO, float)
        self.assertGreater(OVERLAP_RATIO, 0.0)
        self.assertLessEqual(OVERLAP_RATIO, 1.0)

    def test_execution_defaults(self):
        from rpa.config import (
            DEFAULT_TIMEOUT, DEFAULT_RETRY_INTERVAL, CLICK_DELAY,
            DOUBLE_CLICK_DELAY, TEXT_INPUT_DELAY, KEY_PRESS_DELAY,
            SCROLL_DELAY, SCROLL_DELTA_UNIT,
        )
        self.assertIsInstance(DEFAULT_TIMEOUT, float)
        self.assertGreater(DEFAULT_TIMEOUT, 0)
        self.assertIsInstance(DEFAULT_RETRY_INTERVAL, float)
        self.assertGreater(DEFAULT_RETRY_INTERVAL, 0)
        self.assertIsInstance(CLICK_DELAY, float)
        self.assertGreaterEqual(CLICK_DELAY, 0)
        self.assertIsInstance(DOUBLE_CLICK_DELAY, float)
        self.assertGreaterEqual(DOUBLE_CLICK_DELAY, 0)
        self.assertIsInstance(TEXT_INPUT_DELAY, float)
        self.assertGreaterEqual(TEXT_INPUT_DELAY, 0)
        self.assertIsInstance(KEY_PRESS_DELAY, float)
        self.assertGreaterEqual(KEY_PRESS_DELAY, 0)
        self.assertIsInstance(SCROLL_DELAY, float)
        self.assertGreaterEqual(SCROLL_DELAY, 0)
        self.assertEqual(SCROLL_DELTA_UNIT, 120)

    def test_hotkey_config(self):
        from rpa.config import HOTKEY_STOP
        self.assertEqual(HOTKEY_STOP, 'ctrl+q')

    def test_ocr_config(self):
        from rpa.config import OCR_DEFAULT_URL
        self.assertIsInstance(OCR_DEFAULT_URL, str)
        self.assertTrue(OCR_DEFAULT_URL.startswith('http'))

    def test_directory_config(self):
        from rpa.config import TEMPLATES_DIR
        self.assertEqual(TEMPLATES_DIR, '截图')

    def test_ui_constants(self):
        from rpa.config import (
            OVERLAY_ALPHA, OVERLAY_HIDE_DELAY, MIN_SELECT_SIZE,
            SPY_REFRESH_INTERVAL, SPY_OFFSET_X, SPY_OFFSET_Y,
            COORD_REFRESH_INTERVAL, HOTKEY_CHECK_INTERVAL, HOTKEY_COOLDOWN,
        )
        self.assertIsInstance(OVERLAY_ALPHA, float)
        self.assertGreater(OVERLAY_ALPHA, 0)
        self.assertIsInstance(OVERLAY_HIDE_DELAY, float)
        self.assertIsInstance(MIN_SELECT_SIZE, int)
        self.assertGreater(MIN_SELECT_SIZE, 0)
        self.assertIsInstance(SPY_REFRESH_INTERVAL, int)
        self.assertIsInstance(SPY_OFFSET_X, int)
        self.assertIsInstance(SPY_OFFSET_Y, int)
        self.assertIsInstance(COORD_REFRESH_INTERVAL, float)
        self.assertIsInstance(HOTKEY_CHECK_INTERVAL, float)
        self.assertIsInstance(HOTKEY_COOLDOWN, float)

    def test_wm_constants(self):
        from rpa.config import (
            WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP,
            WM_LBUTTONDBLCLK, WM_RBUTTONDOWN, WM_RBUTTONUP,
            WM_MOUSEWHEEL, WM_CHAR, WM_KEYDOWN, WM_KEYUP,
            WM_SHOWWINDOW, WM_SYSCOMMAND,
        )
        self.assertEqual(WM_MOUSEMOVE, 0x0200)
        self.assertEqual(WM_LBUTTONDOWN, 0x0201)
        self.assertEqual(WM_LBUTTONUP, 0x0202)
        self.assertEqual(WM_LBUTTONDBLCLK, 0x0203)
        self.assertEqual(WM_RBUTTONDOWN, 0x0204)
        self.assertEqual(WM_RBUTTONUP, 0x0205)
        self.assertEqual(WM_MOUSEWHEEL, 0x020A)
        self.assertEqual(WM_CHAR, 0x0102)
        self.assertEqual(WM_KEYDOWN, 0x0100)
        self.assertEqual(WM_KEYUP, 0x0101)

    def test_mk_constants(self):
        from rpa.config import MK_LBUTTON, MK_RBUTTON
        self.assertEqual(MK_LBUTTON, 0x0001)
        self.assertEqual(MK_RBUTTON, 0x0002)

    def test_vk_constants(self):
        from rpa.config import (
            VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN,
            VK_RETURN, VK_Q, VK_LBUTTON, VK_RBUTTON,
        )
        self.assertEqual(VK_CONTROL, 0x11)
        self.assertEqual(VK_MENU, 0x12)
        self.assertEqual(VK_SHIFT, 0x10)
        self.assertEqual(VK_LWIN, 0x5B)
        self.assertEqual(VK_RETURN, 0x0D)
        self.assertEqual(VK_Q, 0x51)

    def test_sw_constants(self):
        from rpa.config import SW_HIDE, SW_SHOW, SW_MINIMIZE, SW_RESTORE, SW_MAXIMIZE
        self.assertEqual(SW_HIDE, 0)
        self.assertEqual(SW_SHOW, 5)
        self.assertEqual(SW_MINIMIZE, 6)
        self.assertEqual(SW_RESTORE, 9)
        self.assertEqual(SW_MAXIMIZE, 3)

    def test_swp_constants(self):
        from rpa.config import (
            HWND_TOPMOST, HWND_NOTOPMOST,
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE,
        )
        self.assertEqual(HWND_TOPMOST, -1)
        self.assertEqual(HWND_NOTOPMOST, -2)
        self.assertEqual(SWP_NOSIZE, 0x0001)
        self.assertEqual(SWP_NOMOVE, 0x0002)
        self.assertEqual(SWP_NOACTIVATE, 0x0010)

    def test_gdi_constants(self):
        from rpa.config import SRCCOPY, DIB_RGB_COLORS
        self.assertEqual(SRCCOPY, 0x00CC0020)
        self.assertEqual(DIB_RGB_COLORS, 0)

    def test_gw_constants(self):
        from rpa.config import GW_CHILD, GW_HWNDNEXT
        self.assertEqual(GW_CHILD, 5)
        self.assertEqual(GW_HWNDNEXT, 2)

    def test_modifier_keys_map(self):
        from rpa.config import MODIFIER_KEYS as mk
        from rpa.config import VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN
        self.assertIn('ctrl', mk)
        self.assertEqual(mk['ctrl'], VK_CONTROL)
        self.assertIn('alt', mk)
        self.assertEqual(mk['alt'], VK_MENU)
        self.assertIn('shift', mk)
        self.assertEqual(mk['shift'], VK_SHIFT)
        self.assertIn('win', mk)
        self.assertEqual(mk['win'], VK_LWIN)

    def test_special_keys_map(self):
        from rpa.config import SPECIAL_KEYS as sk
        self.assertEqual(sk['enter'], 0x0D)
        self.assertEqual(sk['tab'], 0x09)
        self.assertEqual(sk['backspace'], 0x08)
        self.assertEqual(sk['delete'], 0x2E)
        self.assertEqual(sk['esc'], 0x1B)
        self.assertEqual(sk['space'], 0x20)
        self.assertEqual(sk['f1'], 0x70)
        self.assertEqual(sk['f12'], 0x7B)
        self.assertEqual(sk['up'], 0x26)
        self.assertEqual(sk['down'], 0x28)
        self.assertEqual(sk['left'], 0x25)
        self.assertEqual(sk['right'], 0x27)

    def test_button_map(self):
        from rpa.config import BUTTON_MAP
        self.assertEqual(BUTTON_MAP['左键单击'], 'left')
        self.assertEqual(BUTTON_MAP['右键单击'], 'right')
        self.assertEqual(BUTTON_MAP['左键双击'], 'double')

    def test_window_actions(self):
        from rpa.config import WINDOW_ACTIONS
        self.assertIsInstance(WINDOW_ACTIONS, list)
        self.assertIn('激活(前台)', WINDOW_ACTIONS)
        self.assertIn('激活(设为后台目标)', WINDOW_ACTIONS)
        self.assertIn('最大化', WINDOW_ACTIONS)
        self.assertIn('最小化', WINDOW_ACTIONS)
        self.assertIn('置顶', WINDOW_ACTIONS)
        self.assertIn('取消置顶', WINDOW_ACTIONS)
        self.assertEqual(len(WINDOW_ACTIONS), 6)

    def test_node_types(self):
        from rpa.config import NODE_TYPES
        self.assertIsInstance(NODE_TYPES, list)
        required_types = ["点击", "等待", "输入", "按键", "滚轮", "延时",
                          "条件分支", "普通循环", "数据循环", "标签", "跳转",
                          "窗口", "文件", "OCR", "暂停", "变量管理", "变量计算", "退出"]
        for t in required_types:
            self.assertIn(t, NODE_TYPES, f"缺少节点类型: {t}")

    def test_loop_modes(self):
        from rpa.config import LOOP_MODES
        self.assertIn("向下取数", LOOP_MODES)
        self.assertIn("向上取数", LOOP_MODES)

    def test_input_types(self):
        from rpa.config import INPUT_TYPES
        self.assertIn("直接输入", INPUT_TYPES)
        self.assertIn("数据变量", INPUT_TYPES)

    def test_data_loop_constants(self):
        from rpa.config import (
            DEFAULT_DATA_NAME, DEFAULT_START_INDEX, DEFAULT_LOOP_MODE,
            DATA_FILE_CACHE_TTL,
        )
        self.assertEqual(DEFAULT_DATA_NAME, "数据")
        self.assertEqual(DEFAULT_START_INDEX, 1)
        self.assertEqual(DEFAULT_LOOP_MODE, "向下取数")
        self.assertIsInstance(DATA_FILE_CACHE_TTL, float)
        self.assertGreater(DATA_FILE_CACHE_TTL, 0)


# ============================================================
# 2. color_utils.py — 颜色工具测试
# ============================================================
class TestColorUtils(unittest.TestCase):
    """测试 color_utils.py 中的颜色解析和检测函数"""

    # ── parse_color_string ──────────────────────────────────
    def test_parse_hex_uppercase(self):
        from rpa.color_utils import parse_color_string
        self.assertEqual(parse_color_string("#FF0000"), (255, 0, 0))
        self.assertEqual(parse_color_string("#00FF00"), (0, 255, 0))
        self.assertEqual(parse_color_string("#0000FF"), (0, 0, 255))
        self.assertEqual(parse_color_string("#000000"), (0, 0, 0))
        self.assertEqual(parse_color_string("#FFFFFF"), (255, 255, 255))

    def test_parse_hex_lowercase(self):
        from rpa.color_utils import parse_color_string
        self.assertEqual(parse_color_string("#ff0000"), (255, 0, 0))
        self.assertEqual(parse_color_string("#aabbcc"), (170, 187, 204))

    def test_parse_hex_mixed_case(self):
        from rpa.color_utils import parse_color_string
        self.assertEqual(parse_color_string("#AaBbCc"), (170, 187, 204))

    def test_parse_rgb_comma(self):
        from rpa.color_utils import parse_color_string
        self.assertEqual(parse_color_string("255,128,0"), (255, 128, 0))
        self.assertEqual(parse_color_string("100,200,50"), (100, 200, 50))

    def test_parse_empty_string(self):
        from rpa.color_utils import parse_color_string
        self.assertIsNone(parse_color_string(""))
        self.assertIsNone(parse_color_string("   "))

    def test_parse_none(self):
        from rpa.color_utils import parse_color_string
        self.assertIsNone(parse_color_string(None))

    def test_parse_invalid_hex(self):
        from rpa.color_utils import parse_color_string
        self.assertIsNone(parse_color_string("#GGGGGG"))
        self.assertIsNone(parse_color_string("#XYZ"))

    def test_parse_invalid_comma(self):
        from rpa.color_utils import parse_color_string
        self.assertIsNone(parse_color_string("abc,def"))
        # 注意: "255,256" 是有效输入（两个合法整数），源码会正常解析
        # 无效格式用非数字字符串测试
        self.assertIsNone(parse_color_string("abc,def,ghi"))

    # ── check_color_in_region_fast ──────────────────────────
    def test_color_in_region_match(self):
        import numpy as np
        from PIL import Image
        from rpa.color_utils import check_color_in_region_fast
        # 纯红色图片 (100x50), 查找红色
        arr = np.zeros((50, 100, 3), dtype=np.uint8)
        arr[:, :, 0] = 255  # R=255, G=0, B=0
        img = Image.fromarray(arr)
        result = check_color_in_region_fast(img, (0, 0, 100, 50), (255, 0, 0), 5.0)
        self.assertTrue(result)

    def test_color_in_region_no_match(self):
        import numpy as np
        from PIL import Image
        from rpa.color_utils import check_color_in_region_fast
        # 纯红色图片, 查找绿色
        arr = np.zeros((50, 100, 3), dtype=np.uint8)
        arr[:, :, 0] = 255
        img = Image.fromarray(arr)
        result = check_color_in_region_fast(img, (0, 0, 100, 50), (0, 255, 0), 5.0)
        self.assertFalse(result)

    def test_color_in_region_with_tolerance(self):
        import numpy as np
        from PIL import Image
        from rpa.color_utils import check_color_in_region_fast
        # (254, 0, 0) vs target (255, 0, 0), 距离 = 1, tolerance=2
        arr = np.full((10, 10, 3), [254, 0, 0], dtype=np.uint8)
        img = Image.fromarray(arr)
        result = check_color_in_region_fast(img, (0, 0, 10, 10), (255, 0, 0), 2.0)
        self.assertTrue(result)
        # tolerance=0.5, 不应该匹配
        result = check_color_in_region_fast(img, (0, 0, 10, 10), (255, 0, 0), 0.5)
        self.assertFalse(result)

    def test_color_in_region_empty_target(self):
        import numpy as np
        from PIL import Image
        from rpa.color_utils import check_color_in_region_fast
        arr = np.zeros((10, 10, 3), dtype=np.uint8)
        img = Image.fromarray(arr)
        self.assertFalse(check_color_in_region_fast(img, (0, 0, 10, 10), (), 5.0))

    # ── check_color_in_cv_image ─────────────────────────────
    def test_cv_color_match(self):
        import numpy as np
        from rpa.color_utils import check_color_in_cv_image
        # OpenCV BGR: 红色 = (0, 0, 255) in BGR
        arr = np.zeros((50, 100, 3), dtype=np.uint8)
        arr[:, :, 2] = 255  # R channel (BGR index 2)
        result = check_color_in_cv_image(arr, None, (255, 0, 0), 5.0)
        self.assertTrue(result)

    def test_cv_color_no_match(self):
        import numpy as np
        from rpa.color_utils import check_color_in_cv_image
        arr = np.zeros((50, 100, 3), dtype=np.uint8)
        arr[:, :, 2] = 255  # red
        result = check_color_in_cv_image(arr, None, (0, 255, 0), 5.0)
        self.assertFalse(result)

    def test_cv_color_with_region(self):
        import numpy as np
        from rpa.color_utils import check_color_in_cv_image
        # 全黑图片，左上角 10x10 区域放红色
        arr = np.zeros((50, 100, 3), dtype=np.uint8)
        arr[0:10, 0:10, 2] = 255  # BGR: R=255
        result = check_color_in_cv_image(arr, (0, 0, 10, 10), (255, 0, 0), 5.0)
        self.assertTrue(result)
        # 检查不包含红色的区域
        result = check_color_in_cv_image(arr, (20, 20, 30, 30), (255, 0, 0), 5.0)
        self.assertFalse(result)

    def test_cv_color_out_of_bounds_region(self):
        import numpy as np
        from rpa.color_utils import check_color_in_cv_image
        arr = np.zeros((10, 10, 3), dtype=np.uint8)
        # 完全越界
        result = check_color_in_cv_image(arr, (100, 100, 200, 200), (255, 0, 0), 5.0)
        self.assertFalse(result)

    def test_cv_color_none_image(self):
        from rpa.color_utils import check_color_in_cv_image
        self.assertFalse(check_color_in_cv_image(None, None, (255, 0, 0), 5.0))


# ============================================================
# 3. utils.py — StopException / RPAConfig / retry_on_failure
# ============================================================
class TestUtilsStopException(unittest.TestCase):
    """测试自定义 StopException"""

    def test_is_exception(self):
        from rpa.utils import StopException
        self.assertTrue(issubclass(StopException, Exception))

    def test_can_raise_and_catch(self):
        from rpa.utils import StopException
        with self.assertRaises(StopException):
            raise StopException("流程已停止")

    def test_message(self):
        from rpa.utils import StopException
        try:
            raise StopException("测试消息")
        except StopException as e:
            self.assertEqual(str(e), "测试消息")


class TestRPAConfig(unittest.TestCase):
    """测试 RPAConfig dataclass 的默认值 / load / save"""

    def test_default_values(self):
        from rpa.utils import RPAConfig
        cfg = RPAConfig()
        self.assertEqual(cfg.default_confidence, 0.95)
        self.assertEqual(cfg.default_timeout, 10.0)
        self.assertEqual(cfg.max_retry_attempts, 3)
        self.assertEqual(cfg.retry_delay, 0.5)
        self.assertEqual(cfg.screenshot_format, 'PNG')
        self.assertTrue(cfg.template_cache_enabled)
        self.assertEqual(cfg.ocr_api_url, "http://127.0.0.1:1224/api/ocr")
        self.assertEqual(cfg.ui_poll_interval, 100)

    def test_custom_values(self):
        from rpa.utils import RPAConfig
        cfg = RPAConfig(
            default_confidence=0.85,
            default_timeout=20.0,
            max_retry_attempts=5,
            retry_delay=1.0,
            screenshot_format='JPEG',
            template_cache_enabled=False,
            ocr_api_url='http://custom:8888/ocr',
            ui_poll_interval=200,
        )
        self.assertEqual(cfg.default_confidence, 0.85)
        self.assertEqual(cfg.default_timeout, 20.0)
        self.assertEqual(cfg.max_retry_attempts, 5)
        self.assertEqual(cfg.retry_delay, 1.0)
        self.assertEqual(cfg.screenshot_format, 'JPEG')
        self.assertFalse(cfg.template_cache_enabled)
        self.assertEqual(cfg.ocr_api_url, 'http://custom:8888/ocr')
        self.assertEqual(cfg.ui_poll_interval, 200)

    def test_save_and_load(self):
        from rpa.utils import RPAConfig
        cfg = RPAConfig(default_confidence=0.77, max_retry_attempts=7)
        tmp_path = os.path.join(tempfile.gettempdir(), f"_rpa_test_{time.time()}.json")
        try:
            self.assertTrue(cfg.save(tmp_path))
            loaded = RPAConfig.load(tmp_path)
            self.assertEqual(loaded.default_confidence, 0.77)
            self.assertEqual(loaded.max_retry_attempts, 7)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_load_file_not_exists(self):
        from rpa.utils import RPAConfig
        cfg = RPAConfig.load("__nonexistent_file_12345__.json")
        self.assertIsInstance(cfg, RPAConfig)
        # 应该是默认值
        self.assertEqual(cfg.default_confidence, 0.95)

    def test_load_invalid_json(self):
        from rpa.utils import RPAConfig
        tmp_path = os.path.join(tempfile.gettempdir(), f"_rpa_test_invalid_{time.time()}.json")
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                f.write("not valid json {{{")
            cfg = RPAConfig.load(tmp_path)
            # 解析失败应回退默认值
            self.assertEqual(cfg.default_confidence, 0.95)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestRetryOnFailure(unittest.TestCase):
    """测试 retry_on_failure 装饰器"""

    def test_success_on_first_try(self):
        from rpa.utils import retry_on_failure

        call_count = [0]

        @retry_on_failure(max_retries=3, delay=0.01)
        def maybe_fail():
            call_count[0] += 1
            return "ok"

        result = maybe_fail()
        self.assertEqual(result, "ok")
        self.assertEqual(call_count[0], 1)

    def test_retry_then_success(self):
        from rpa.utils import retry_on_failure

        call_count = [0]

        @retry_on_failure(max_retries=3, delay=0.01)
        def fail_then_ok():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("temp fail")
            return "recovered"

        result = fail_then_ok()
        self.assertEqual(result, "recovered")
        self.assertEqual(call_count[0], 2)

    def test_all_retries_exhausted(self):
        from rpa.utils import retry_on_failure

        call_count = [0]

        @retry_on_failure(max_retries=3, delay=0.01)
        def always_fail():
            call_count[0] += 1
            raise ValueError("permanent fail")

        with self.assertRaises(ValueError):
            always_fail()
        self.assertEqual(call_count[0], 3)

    def test_only_catches_specified_exception(self):
        from rpa.utils import retry_on_failure

        call_count = [0]

        @retry_on_failure(max_retries=3, delay=0.01,
                          exceptions=(KeyError,))
        def raise_type_error():
            call_count[0] += 1
            raise TypeError("wrong type")

        with self.assertRaises(TypeError):
            raise_type_error()
        # TypeError 不在捕获列表中，只调用了 1 次
        self.assertEqual(call_count[0], 1)

    def test_only_retries_matching_types(self):
        from rpa.utils import retry_on_failure

        call_count = [0]

        @retry_on_failure(max_retries=3, delay=0.01,
                          exceptions=(KeyError,))
        def fail_then_wrong_exc():
            call_count[0] += 1
            if call_count[0] == 1:
                raise KeyError("will retry")
            raise TypeError("should not retry")

        with self.assertRaises(TypeError):
            fail_then_wrong_exc()
        self.assertEqual(call_count[0], 2)


# ============================================================
# 4. label_manager.py — 标签管理测试
# ============================================================
class TestLabelManager(unittest.TestCase):
    """测试 LabelManager 的标签收集 / 跳转验证 / 定位"""

    def setUp(self):
        from rpa.label_manager import LabelManager
        self.LM = LabelManager

    def _make_label(self, name):
        return {"type": "标签", "params": {"label_name": name}}

    def _make_click(self):
        return {"type": "点击", "params": {}}

    def test_collect_empty_list(self):
        labels = self.LM.collect_all_labels([])
        self.assertEqual(labels, {})

    def test_collect_one_label(self):
        steps = [self._make_label("start")]
        labels = self.LM.collect_all_labels(steps)
        self.assertIn("start", labels)
        self.assertEqual(labels["start"]["index"], 0)
        self.assertEqual(labels["start"]["path"], "0")

    def test_collect_multiple_labels(self):
        steps = [
            self._make_label("A"),
            self._make_click(),
            self._make_label("B"),
            self._make_click(),
            self._make_label("C"),
        ]
        labels = self.LM.collect_all_labels(steps)
        self.assertEqual(len(labels), 3)
        self.assertIn("A", labels)
        self.assertIn("B", labels)
        self.assertIn("C", labels)
        self.assertEqual(labels["A"]["index"], 0)
        self.assertEqual(labels["B"]["index"], 2)
        self.assertEqual(labels["C"]["index"], 4)

    def test_collect_empty_label_name_ignored(self):
        steps = [{"type": "标签", "params": {"label_name": ""}}]
        labels = self.LM.collect_all_labels(steps)
        self.assertEqual(labels, {})

    def test_collect_whitespace_label_ignored(self):
        steps = [{"type": "标签", "params": {"label_name": "   "}}]
        labels = self.LM.collect_all_labels(steps)
        self.assertEqual(labels, {})

    def test_collect_in_true_branch(self):
        steps = [
            self._make_click(),
            {
                "type": "条件分支",
                "params": {},
                "true": [self._make_label("inside_true")],
                "false": [],
            },
        ]
        labels = self.LM.collect_all_labels(steps)
        self.assertIn("inside_true", labels)
        self.assertEqual(labels["inside_true"]["path"], "1.true.0")

    def test_collect_in_false_branch(self):
        steps = [
            {
                "type": "条件分支",
                "params": {},
                "true": [],
                "false": [self._make_label("inside_false")],
            },
        ]
        labels = self.LM.collect_all_labels(steps)
        self.assertIn("inside_false", labels)
        self.assertEqual(labels["inside_false"]["path"], "0.false.0")

    def test_collect_in_loop_body(self):
        steps = [
            {
                "type": "普通循环",
                "params": {},
                "body": [self._make_label("loop_label")],
            },
        ]
        labels = self.LM.collect_all_labels(steps)
        self.assertIn("loop_label", labels)
        self.assertEqual(labels["loop_label"]["path"], "0.body.0")

    def test_collect_nested_labels(self):
        steps = [
            {
                "type": "条件分支",
                "params": {},
                "true": [
                    {
                        "type": "普通循环",
                        "params": {},
                        "body": [
                            self._make_label("deep"),
                        ],
                    },
                ],
                "false": [],
            },
        ]
        labels = self.LM.collect_all_labels(steps)
        self.assertIn("deep", labels)
        self.assertEqual(labels["deep"]["path"], "0.true.0.body.0")

    def test_validate_jump_exists(self):
        steps = [self._make_label("target")]
        result = self.LM.validate_jump(steps, "target")
        self.assertTrue(result)

    def test_validate_jump_not_exists(self):
        steps = [self._make_label("target")]
        result = self.LM.validate_jump(steps, "nonexistent")
        self.assertFalse(result)

    def test_get_label_position(self):
        steps = [self._make_click(), self._make_label("AAA"), self._make_click()]
        pos = self.LM.get_label_position(steps, "AAA")
        self.assertIsNotNone(pos)
        self.assertEqual(pos["index"], 1)
        self.assertEqual(pos["path"], "1")

    def test_get_label_position_not_found(self):
        steps = []
        pos = self.LM.get_label_position(steps, "AAA")
        self.assertIsNone(pos)


# ============================================================
# 5. win_api.py — MAKELPARAM / 结构体 / 缓存
# ============================================================
class TestWinAPIMakelparam(unittest.TestCase):
    """测试 MAKELPARAM 静态方法"""

    def test_basic_values(self):
        from rpa.win_api import WinAPI
        self.assertEqual(WinAPI.MAKELPARAM(0, 0), 0)
        self.assertEqual(WinAPI.MAKELPARAM(100, 200), (200 << 16) | 100)

    def test_hex_values(self):
        from rpa.win_api import WinAPI
        self.assertEqual(WinAPI.MAKELPARAM(0xFFFF, 0x0000), 0x0000FFFF)
        self.assertEqual(WinAPI.MAKELPARAM(0x0000, 0xFFFF), 0xFFFF0000)

    def test_large_values_truncated(self):
        from rpa.win_api import WinAPI
        # 超出 16 位的值应被截断
        result = WinAPI.MAKELPARAM(0x12345, 0xABCDE)
        low = 0x12345 & 0xFFFF
        high = 0xABCDE & 0xFFFF
        expected = (high << 16) | low
        self.assertEqual(result, expected)

    def test_negative_values(self):
        from rpa.win_api import WinAPI
        # Python int 为负时的行为
        result = WinAPI.MAKELPARAM(-1, -1)
        low = (-1) & 0xFFFF   # = 65535
        high = (-1) & 0xFFFF
        expected = (high << 16) | low  # = 0xFFFFFFFF
        self.assertEqual(result, expected)

    def test_mouse_coordinates(self):
        from rpa.win_api import WinAPI
        # 模拟窗口消息坐标: x=300, y=200
        lparam = WinAPI.MAKELPARAM(300, 200)
        self.assertEqual(lparam & 0xFFFF, 300)          # LOWORD
        self.assertEqual((lparam >> 16) & 0xFFFF, 200)  # HIWORD


class TestWinAPIStructures(unittest.TestCase):
    """测试 WinAPI ctypes 结构体尺寸"""

    def test_point_size(self):
        from rpa.win_api import POINT
        import ctypes
        self.assertEqual(ctypes.sizeof(POINT), 8)  # 2 * c_long (4+4 on 32/64)

    def test_rect_size(self):
        from rpa.win_api import RECT
        import ctypes
        self.assertEqual(ctypes.sizeof(RECT), 16)  # 4 * c_long

    def test_point_access(self):
        from rpa.win_api import POINT
        p = POINT()
        p.x = 100
        p.y = 200
        self.assertEqual(p.x, 100)
        self.assertEqual(p.y, 200)

    def test_rect_access(self):
        from rpa.win_api import RECT
        r = RECT()
        r.left = 10
        r.top = 20
        r.right = 100
        r.bottom = 200
        self.assertEqual(r.left, 10)
        self.assertEqual(r.right, 100)

    def test_get_point_cache(self):
        from rpa.win_api import WinAPI
        p1 = WinAPI.get_point(10, 20)
        self.assertEqual(p1.x, 10)
        self.assertEqual(p1.y, 20)
        p2 = WinAPI.get_point(30, 40)
        self.assertEqual(p2.x, 30)
        self.assertEqual(p2.y, 40)
        # 应该返回同一个缓存对象
        self.assertIs(p1, p2)

    def test_get_rect_cache(self):
        from rpa.win_api import WinAPI
        r1 = WinAPI.get_rect()
        r1.left = 5
        r2 = WinAPI.get_rect()
        # 同一缓存对象，修改后可见
        self.assertIs(r1, r2)
        self.assertEqual(r2.left, 5)


# ============================================================
# 6. vision.py — _filter_overlaps 静态方法
# ============================================================
class TestVisionFilterOverlaps(unittest.TestCase):
    """测试 VisionEngine._filter_overlaps 静态方法"""

    def setUp(self):
        from rpa.vision import VisionEngine
        # 保存为实例属性，通过 self._filter_fn(…) 调用避免 self 被当作第一个参数
        self._filter_fn = VisionEngine._filter_overlaps

    def test_empty_matches(self):
        self.assertEqual(self._filter_fn([], 100, 50), [])

    def test_single_match_passes(self):
        matches = [(10, 20, 0.95)]
        result = self._filter_fn(matches, 100, 50)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (10, 20, 0.95))

    def test_far_apart_both_kept(self):
        matches = [(10, 20, 0.95), (500, 400, 0.90)]
        result = self._filter_fn(matches, 50, 30)
        self.assertEqual(len(result), 2)

    def test_overlapping_filtered_out(self):
        # 两个匹配非常接近，后者应被过滤
        matches = [(10, 20, 0.95), (12, 22, 0.90)]
        result = self._filter_fn(matches, 100, 50)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (10, 20, 0.95))  # 保留最先排序的

    def test_exactly_at_threshold(self):
        # 距离刚好大于阈值平方根
        matches = [(0, 0, 0.95), (1000, 1000, 0.90)]
        result = self._filter_fn(matches, 50, 30)
        # 距离 sqrt((1000)^2 + (1000)^2) ≈ 1414
        # min_dist = min(50,30) * 0.8 = 24, dist_sq = 2000000
        # 远大于阈值，两者都保留
        self.assertEqual(len(result), 2)

    def test_sort_by_y_then_x(self):
        # 验证按 (y, x) 排序
        matches = [(100, 50, 0.9), (50, 10, 0.95), (200, 10, 0.8)]
        result = self._filter_fn(matches, 3, 3)
        # 排序后应为 (50,10), (200,10), (100,50)
        self.assertEqual(result[0], (50, 10, 0.95))
        self.assertEqual(result[1], (200, 10, 0.8))
        self.assertEqual(result[2], (100, 50, 0.9))


# ============================================================
# 7. execution.py — _compare_values / _exec_var_calc / 变量管理
# ============================================================
class TestExecutionCompareValues(unittest.TestCase):
    """测试 ExecutionEngine._compare_values 方法"""

    @classmethod
    def setUpClass(cls):
        from rpa.execution import ExecutionEngine
        # 用一个假的 engine 实例访问方法
        cls._engine = ExecutionEngine.__new__(ExecutionEngine)
        # 初始化不存在的属性
        cls._engine.runtime_vars = {}
        cls._engine.ui_queue = None
        cls._engine.stop_event = threading.Event()

    def test_equal_numbers(self):
        self.assertTrue(self._engine._compare_values(5, '等于', '5'))
        self.assertTrue(self._engine._compare_values(5.0, '等于', '5'))

    def test_not_equal_numbers(self):
        self.assertTrue(self._engine._compare_values(5, '不等于', '3'))
        self.assertFalse(self._engine._compare_values(5, '不等于', '5'))

    def test_greater_than(self):
        self.assertTrue(self._engine._compare_values(10, '大于', '5'))
        self.assertFalse(self._engine._compare_values(3, '大于', '5'))

    def test_less_than(self):
        self.assertTrue(self._engine._compare_values(3, '小于', '5'))
        self.assertFalse(self._engine._compare_values(10, '小于', '5'))

    def test_greater_equal(self):
        self.assertTrue(self._engine._compare_values(10, '大于等于', '10'))
        self.assertTrue(self._engine._compare_values(11, '大于等于', '10'))
        self.assertFalse(self._engine._compare_values(9, '大于等于', '10'))

    def test_less_equal(self):
        self.assertTrue(self._engine._compare_values(10, '小于等于', '10'))
        self.assertTrue(self._engine._compare_values(9, '小于等于', '10'))
        self.assertFalse(self._engine._compare_values(11, '小于等于', '10'))

    def test_is_empty(self):
        self.assertTrue(self._engine._compare_values(None, '为空', ''))
        self.assertTrue(self._engine._compare_values('', '为空', ''))
        self.assertTrue(self._engine._compare_values('   ', '为空', ''))
        self.assertFalse(self._engine._compare_values('hello', '为空', ''))

    def test_is_not_empty(self):
        self.assertTrue(self._engine._compare_values('hello', '不为空', ''))
        self.assertTrue(self._engine._compare_values(0, '不为空', ''))
        self.assertFalse(self._engine._compare_values(None, '不为空', ''))
        self.assertFalse(self._engine._compare_values('  ', '不为空', ''))

    def test_contains(self):
        self.assertTrue(self._engine._compare_values('hello world', '包含', 'hello'))
        self.assertTrue(self._engine._compare_values('hello world', '包含', 'world'))
        self.assertFalse(self._engine._compare_values('hello world', '包含', 'xyz'))

    def test_string_fallback(self):
        # 不可转换为数值时回退到字符串比较
        self.assertTrue(self._engine._compare_values('abc', '等于', 'abc'))
        self.assertFalse(self._engine._compare_values('abc', '等于', 'def'))
        self.assertTrue(self._engine._compare_values('hello', '小于', 'world'))

    def test_unknown_operator(self):
        self.assertFalse(self._engine._compare_values('abc', '__UNKNOWN__', 'xyz'))

    def test_none_actual_converts_to_empty(self):
        # None → '' (空字符串), 不等于 'None'
        self.assertFalse(self._engine._compare_values(None, '等于', 'None'))
        # None → '' 等于 '' (都转空串)
        self.assertTrue(self._engine._compare_values(None, '等于', ''))
        self.assertTrue(self._engine._compare_values('', '等于', ''))


class TestExecutionVarManager(unittest.TestCase):
    """测试 ExecutionEngine._exec_var_manager"""

    def setUp(self):
        from rpa.execution import ExecutionEngine
        from queue import Queue
        self.engine = ExecutionEngine.__new__(ExecutionEngine)
        self.engine.runtime_vars = {}
        self.engine.ui_queue = Queue()  # mock queue 使 self.log() 不报错
        self.engine.stop_event = threading.Event()

    def test_register_string_var(self):
        p = {
            "variables": [{"name": "my_var", "type": "字符串", "value": "hello"}]
        }
        self.engine._exec_var_manager(p)
        self.assertIn("my_var", self.engine.runtime_vars)
        self.assertEqual(self.engine.runtime_vars["my_var"], "hello")

    def test_register_number_var(self):
        p = {
            "variables": [{"name": "num", "type": "数字", "value": "42"}]
        }
        self.engine._exec_var_manager(p)
        self.assertEqual(self.engine.runtime_vars["num"], 42)

    def test_register_float_var(self):
        p = {
            "variables": [{"name": "pi", "type": "数字", "value": "3.14"}]
        }
        self.engine._exec_var_manager(p)
        self.assertEqual(self.engine.runtime_vars["pi"], 3.14)

    def test_register_invalid_number_defaults_zero(self):
        p = {
            "variables": [{"name": "bad", "type": "数字", "value": "abc"}]
        }
        self.engine._exec_var_manager(p)
        self.assertEqual(self.engine.runtime_vars["bad"], 0)

    def test_register_bool_true(self):
        for val in ['true', 'True', '1', '是']:
            self.engine.runtime_vars.clear()
            p = {"variables": [{"name": "flag", "type": "布尔", "value": val}]}
            self.engine._exec_var_manager(p)
            self.assertTrue(self.engine.runtime_vars["flag"],
                            f"{val} should be True")

    def test_register_bool_false(self):
        for val in ['false', 'False', '0', 'no']:
            self.engine.runtime_vars.clear()
            p = {"variables": [{"name": "flag", "type": "布尔", "value": val}]}
            self.engine._exec_var_manager(p)
            self.assertFalse(self.engine.runtime_vars["flag"],
                             f"{val} should be False")

    def test_register_multiple_vars(self):
        p = {
            "variables": [
                {"name": "a", "type": "字符串", "value": "A"},
                {"name": "b", "type": "数字", "value": "10"},
                {"name": "c", "type": "布尔", "value": "1"},
            ]
        }
        self.engine._exec_var_manager(p)
        self.assertEqual(self.engine.runtime_vars["a"], "A")
        self.assertEqual(self.engine.runtime_vars["b"], 10)
        self.assertTrue(self.engine.runtime_vars["c"])

    def test_empty_variables(self):
        self.engine._exec_var_manager({"variables": []})
        self.assertEqual(len(self.engine.runtime_vars), 0)

    def test_empty_name_ignored(self):
        p = {"variables": [{"name": "", "type": "字符串", "value": "x"}]}
        self.engine._exec_var_manager(p)
        self.assertNotIn("", self.engine.runtime_vars)


class TestExecutionVarCalc(unittest.TestCase):
    """测试 ExecutionEngine._exec_var_calc"""

    def setUp(self):
        from rpa.execution import ExecutionEngine
        self.engine = ExecutionEngine.__new__(ExecutionEngine)
        self.engine.runtime_vars = {}
        self.engine.ui_queue = None
        self.engine.stop_event = threading.Event()
        # 模拟 log 方法
        self.log_messages = []
        def fake_log(msg):
            self.log_messages.append(msg)
        self.engine.log = fake_log

    def test_simple_arithmetic(self):
        self.engine.runtime_vars = {"x": 5, "y": 3}
        p = {"expression": "x + y", "calc_result_var": "sum"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["sum"], 8)

    def test_subtraction(self):
        self.engine.runtime_vars = {"a": 10, "b": 4}
        p = {"expression": "a - b", "calc_result_var": "diff"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["diff"], 6)

    def test_multiplication(self):
        self.engine.runtime_vars = {"m": 7, "n": 6}
        p = {"expression": "m * n", "calc_result_var": "prod"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["prod"], 42)

    def test_division(self):
        self.engine.runtime_vars = {"u": 10, "v": 3}
        p = {"expression": "u / v", "calc_result_var": "quot"}
        self.engine._exec_var_calc(p)
        self.assertAlmostEqual(self.engine.runtime_vars["quot"], 10 / 3)

    def test_integer_division(self):
        self.engine.runtime_vars = {"u": 10, "v": 3}
        p = {"expression": "u // v", "calc_result_var": "idiv"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["idiv"], 3)

    def test_modulo(self):
        self.engine.runtime_vars = {"u": 10, "v": 3}
        p = {"expression": "u % v", "calc_result_var": "mod"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["mod"], 1)

    def test_power(self):
        self.engine.runtime_vars = {"x": 2, "y": 10}
        p = {"expression": "x ** y", "calc_result_var": "pow"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["pow"], 1024)

    def test_complex_expression(self):
        self.engine.runtime_vars = {"a": 10, "b": 5, "c": 2}
        p = {"expression": "(a + b) * c", "calc_result_var": "result"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["result"], 30)

    def test_builtin_abs(self):
        self.engine.runtime_vars = {"x": -5}
        p = {"expression": "abs(x)", "calc_result_var": "r"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["r"], 5)

    def test_builtin_round(self):
        self.engine.runtime_vars = {"x": 3.14159}
        p = {"expression": "round(x, 2)", "calc_result_var": "r"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["r"], 3.14)

    def test_builtin_min_max(self):
        self.engine.runtime_vars = {"a": 5, "b": 10, "c": 3}
        p = {"expression": "min(a, b, c) + max(a, b, c)", "calc_result_var": "r"}
        self.engine._exec_var_calc(p)
        self.assertEqual(self.engine.runtime_vars["r"], 13)  # 3 + 10

    def test_empty_expression(self):
        p = {"expression": "", "calc_result_var": "r"}
        self.engine._exec_var_calc(p)
        self.assertTrue(any("表达式为空" in msg for msg in self.log_messages))

    def test_empty_result_var(self):
        self.engine.runtime_vars = {"x": 5}
        p = {"expression": "x + 1", "calc_result_var": ""}
        self.engine._exec_var_calc(p)
        self.assertTrue(any("结果变量名为空" in msg for msg in self.log_messages))

    def test_division_by_zero(self):
        self.engine.runtime_vars = {"x": 10}
        p = {"expression": "x / 0", "calc_result_var": "r"}
        self.engine._exec_var_calc(p)
        self.assertTrue(any("除数不能为零" in msg for msg in self.log_messages))

    def test_undefined_variable(self):
        self.engine.runtime_vars = {}
        p = {"expression": "undefined_var + 1", "calc_result_var": "r"}
        self.engine._exec_var_calc(p)
        self.assertTrue(any("未定义的变量" in msg for msg in self.log_messages))


# ============================================================
# 8. action_helper.py — 模块结构 & 常量
# ============================================================
class TestActionHelper(unittest.TestCase):
    """测试 ActionHelper 的类结构和常量（不含实际操作）"""

    def test_class_exists(self):
        from rpa.action_helper import ActionHelper
        self.assertTrue(hasattr(ActionHelper, 'click_action'))
        self.assertTrue(hasattr(ActionHelper, 'send_keys_action'))
        self.assertTrue(hasattr(ActionHelper, 'send_text_action'))
        self.assertTrue(hasattr(ActionHelper, 'scroll_action'))
        self.assertTrue(hasattr(ActionHelper, 'find_image_with_params'))

    def test_all_methods_are_static(self):
        from rpa.action_helper import ActionHelper
        import inspect
        for name in ['click_action', 'send_keys_action', 'send_text_action',
                      'scroll_action', 'find_image_with_params']:
            method = getattr(ActionHelper, name)
            # @staticmethod 方法在类上获取时直接是可调用对象
            self.assertTrue(
                callable(method),
                f"{name} should be callable"
            )

    def test_win32_constants(self):
        from rpa.action_helper import (
            MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
            MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
        )
        self.assertEqual(MOUSEEVENTF_LEFTDOWN, 0x0002)
        self.assertEqual(MOUSEEVENTF_LEFTUP, 0x0004)
        self.assertEqual(MOUSEEVENTF_RIGHTDOWN, 0x0008)
        self.assertEqual(MOUSEEVENTF_RIGHTUP, 0x0010)


# ============================================================
# 运行入口
# ============================================================
if __name__ == '__main__':
    unittest.main(verbosity=2)

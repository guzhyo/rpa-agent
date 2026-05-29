"""
RPA自动化工具 - 视觉引擎层

提供核心的图像识别和匹配功能：
- 支持前台（pyautogui 截图）和后台（PrintWindow）两种模式
- 支持颜色敏感匹配（LAB Delta E 校验）
- 支持重叠结果过滤
- 支持查找第 N 个匹配项
"""

import os
from typing import Optional, Tuple, Union

import cv2
import numpy as np
import pyautogui

from rpa.win_driver import WinDriver
from rpa.config import (
    DEFAULT_CONFIDENCE, COLOR_DELTA_THRESHOLD, OVERLAP_RATIO,
    TEMPLATES_DIR,
)


class VisionEngine:
    """
    视觉引擎类，负责图像模板匹配。

    使用 OpenCV 的 matchTemplate 进行模板匹配，
    可选启用 LAB 色彩空间的 Delta E 校验以提高匹配精度。
    """

    def __init__(self, templates_dir: str = TEMPLATES_DIR) -> None:
        """
        初始化视觉引擎。

        Args:
            templates_dir: 模板图片目录路径
        """
        self.templates_dir: str = templates_dir
        self.confidence: float = DEFAULT_CONFIDENCE

    def find_image(
        self,
        template_name: str,
        hwnd: Optional[int] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
        find_nth: int = 1,
        return_similarity: bool = False,
        color_sensitive: bool = True,
        debug: bool = False,
    ) -> Union[
        Optional[Tuple[int, int]],
        Tuple[Optional[Tuple[int, int]], Optional[float]],
    ]:
        """
        在屏幕或窗口中查找模板图片。

        Args:
            template_name: 模板图片文件名（可省略 .png 后缀）
            hwnd: 窗口句柄，提供时使用后台模式截图
            region: 搜索区域 (x1, y1, x2, y2)
                   - 后台模式: 相对于窗口客户区坐标
                   - 前台模式: 屏幕坐标
            find_nth: 查找第 n 个匹配项（从 1 开始）
            return_similarity: 是否同时返回匹配相似度
            color_sensitive: 是否启用颜色敏感匹配（LAB Delta E 校验）
            debug: 是否输出调试信息

        Returns:
            - return_similarity=False: 返回 (x, y) 或 None
            - return_similarity=True: 返回 ((x, y), similarity) 或 (None, None)
            - 后台模式返回客户区坐标，前台模式返回屏幕坐标
        """
        # 参数校验
        if not template_name:
            return (None, None) if return_similarity else None

        if not template_name.endswith('.png'):
            template_name += '.png'

        path = os.path.join(self.templates_dir, template_name)
        if not os.path.exists(path):
            return (None, None) if return_similarity else None

        # 读取模板图片
        template = cv2.imdecode(
            np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if template is None:
            return (None, None) if return_similarity else None

        h_t, w_t = template.shape[:2]
        offset_x, offset_y = 0, 0

        # 获取截图
        scr = self._capture_screenshot(hwnd, region)
        if scr is None:
            return (None, None) if return_similarity else None

        # 计算偏移量
        if hwnd:
            offset_x, offset_y = (region[0], region[1]) if region else (0, 0)
        else:
            if region:
                offset_x, offset_y = region[0], region[1]

        # 模板尺寸检查
        if h_t > scr.shape[0] or w_t > scr.shape[1]:
            return (None, None) if return_similarity else None

        # 执行模板匹配
        matches = self._match_template(
            scr, template, h_t, w_t, color_sensitive, debug
        )

        # 过滤重叠结果
        filtered = self._filter_overlaps(matches, w_t, h_t)

        # 返回第 N 个匹配结果
        if len(filtered) >= find_nth:
            res_pt = filtered[find_nth - 1]
            center_x = res_pt[0] + w_t // 2 + offset_x
            center_y = res_pt[1] + h_t // 2 + offset_y
            if return_similarity:
                return ((center_x, center_y), res_pt[2])
            return (center_x, center_y)

        return (None, None) if return_similarity else None

    def _capture_screenshot(
        self,
        hwnd: Optional[int],
        region: Optional[Tuple[int, int, int, int]],
    ) -> Optional[np.ndarray]:
        """
        获取截图（BGR 格式）。

        Args:
            hwnd: 窗口句柄，None 为前台模式
            region: 搜索区域

        Returns:
            BGR 格式的 numpy 数组，失败返回 None
        """
        if hwnd:
            return WinDriver.capture_window(hwnd, region)

        # 前台模式
        if region:
            x1, y1, x2, y2 = region
            scr_pil = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
        else:
            scr_pil = pyautogui.screenshot()

        return cv2.cvtColor(np.array(scr_pil), cv2.COLOR_RGB2BGR)

    def _match_template(
        self,
        scr: np.ndarray,
        template: np.ndarray,
        h_t: int,
        w_t: int,
        color_sensitive: bool,
        debug: bool,
    ) -> list:
        """
        执行模板匹配并返回候选匹配列表。

        Args:
            scr: 截图（BGR）
            template: 模板图片（BGR）
            h_t: 模板高度
            w_t: 模板宽度
            color_sensitive: 是否启用颜色敏感匹配
            debug: 是否输出调试信息

        Returns:
            匹配列表，每个元素为 (x, y, similarity)
        """
        if color_sensitive:
            # 颜色敏感模式：同时匹配颜色和形状
            res_color = cv2.matchTemplate(
                scr, template, cv2.TM_CCOEFF_NORMED
            )
            scr_gray = cv2.cvtColor(scr, cv2.COLOR_BGR2GRAY)
            tpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            res_shape = cv2.matchTemplate(
                scr_gray, tpl_gray, cv2.TM_CCOEFF_NORMED
            )
            loc = np.where(
                (res_color >= self.confidence)
                & (res_shape >= self.confidence)
            )
            res = res_color
        else:
            # 仅形状匹配
            scr_gray = cv2.cvtColor(scr, cv2.COLOR_BGR2GRAY)
            tpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            res = cv2.matchTemplate(
                scr_gray, tpl_gray, cv2.TM_CCOEFF_NORMED
            )
            loc = np.where(res >= self.confidence)

        # 收集匹配结果
        matches: list = []
        for pt in zip(*loc[::-1]):
            sim = float(res[pt[1]][pt[0]])

            if color_sensitive:
                # LAB Delta E 平均值校验
                match_region = scr[pt[1]:pt[1] + h_t, pt[0]:pt[0] + w_t]
                if match_region.shape[:2] != template.shape[:2]:
                    continue

                tpl_lab = cv2.cvtColor(
                    template, cv2.COLOR_BGR2LAB
                ).astype(np.int32)
                pat_lab = cv2.cvtColor(
                    match_region, cv2.COLOR_BGR2LAB
                ).astype(np.int32)
                dist = np.sqrt(np.clip(
                    np.sum((pat_lab - tpl_lab) ** 2, axis=2), 0, None
                ))

                if dist.mean() > COLOR_DELTA_THRESHOLD:
                    continue

            matches.append((pt[0], pt[1], sim))

        return matches

    @staticmethod
    def _filter_overlaps(
        matches: list,
        w_t: int,
        h_t: int,
    ) -> list:
        """
        过滤重叠的匹配结果。

        按位置排序后，移除与已有结果距离过近的匹配项。

        Args:
            matches: 匹配列表，每个元素为 (x, y, similarity)
            w_t: 模板宽度
            h_t: 模板高度

        Returns:
            过滤后的匹配列表
        """
        if not matches:
            return []

        matches.sort(key=lambda p: (p[1], p[0]))
        min_dist_sq = (min(w_t, h_t) * OVERLAP_RATIO) ** 2

        filtered: list = []
        for m in matches:
            if all(
                (m[0] - f[0]) ** 2 + (m[1] - f[1]) ** 2 > min_dist_sq
                for f in filtered
            ):
                filtered.append(m)

        return filtered

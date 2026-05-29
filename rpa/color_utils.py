"""
RPA自动化工具 - 颜色匹配辅助模块

提供颜色解析和匹配功能：
- parse_color_string: 解析颜色字符串为 RGB 元组
- check_color_in_region_fast: 在 PIL 图像中检查颜色
- check_color_in_cv_image: 在 OpenCV 图像中检查颜色
"""

from typing import Optional, Tuple

import numpy as np


def parse_color_string(color_str: str) -> Optional[Tuple[int, int, int]]:
    """
    解析颜色字符串为 RGB 元组。

    支持两种格式：
    - '#RRGGBB' 十六进制格式
    - 'R,G,B' 逗号分隔的十进制格式

    Args:
        color_str: 颜色字符串

    Returns:
        (r, g, b) 元组，解析失败时返回 None
    """
    if not color_str:
        return None
    try:
        color_str = color_str.strip()
        if color_str.startswith('#'):
            hex_color = color_str.lstrip('#')
            return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        elif ',' in color_str:
            return tuple(map(int, color_str.split(',')))
    except (ValueError, IndexError):
        pass
    return None


def check_color_in_region_fast(
    screenshot_pil: object,
    region: Tuple[int, int, int, int],
    target_color_rgb: Tuple[int, int, int],
    tolerance: float,
) -> bool:
    """
    使用 NumPy 高效检查 PIL 图像区域内是否存在匹配颜色。

    Args:
        screenshot_pil: PIL.Image 对象
        region: (x1, y1, x2, y2) 相对于截图的坐标
        target_color_rgb: (r, g, b) 目标颜色元组
        tolerance: 颜色偏差（欧几里得距离）

    Returns:
        True 表示区域内存在匹配颜色，False 表示不存在
    """
    if not target_color_rgb:
        return False

    try:
        img_crop = screenshot_pil.crop(region)
        img_np = np.array(img_crop)

        # 确保是 RGB 格式
        if len(img_np.shape) == 3 and img_np.shape[2] >= 3:
            img_np = img_np[:, :, :3]
        else:
            return False

        # 向量化计算颜色距离（避免开方，比较距离平方）
        target_color_np = np.array(target_color_rgb, dtype=int)
        distances_sq = np.sum(
            (img_np.astype(int) - target_color_np) ** 2, axis=2
        )

        return bool(np.any(distances_sq <= tolerance ** 2))
    except Exception:
        return False


def check_color_in_cv_image(
    cv_image: object,
    region: Optional[Tuple[int, int, int, int]],
    target_color_rgb: Tuple[int, int, int],
    tolerance: float,
) -> bool:
    """
    在 OpenCV BGR 图像中检查颜色。

    Args:
        cv_image: OpenCV BGR 格式图像（numpy 数组）
        region: (x1, y1, x2, y2) 相对于图像的坐标，None 则检查整个图像
        target_color_rgb: (r, g, b) 目标颜色元组
        tolerance: 颜色偏差（欧几里得距离）

    Returns:
        True 表示区域内存在匹配颜色，False 表示不存在
    """
    if not target_color_rgb or cv_image is None:
        return False

    try:
        if region:
            x1, y1, x2, y2 = region
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(cv_image.shape[1], x2)
            y2 = min(cv_image.shape[0], y2)
            if x2 <= x1 or y2 <= y1:
                return False
            img_crop = cv_image[y1:y2, x1:x2]
        else:
            img_crop = cv_image

        # OpenCV 是 BGR 格式，转换目标颜色为 BGR
        target_bgr = np.array(
            [target_color_rgb[2], target_color_rgb[1], target_color_rgb[0]],
            dtype=int,
        )

        # 计算颜色距离
        distances_sq = np.sum(
            (img_crop.astype(int) - target_bgr) ** 2, axis=2
        )

        return bool(np.any(distances_sq <= tolerance ** 2))
    except Exception:
        return False

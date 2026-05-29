"""
RPA自动化工具 - 优化工具类模块

提供各种优化工具类和辅助函数：
- StopException: 自定义停止异常
- retry_on_failure: 重试装饰器
- ImagePreviewPool: 图片对象池（LRU缓存）
- RPAConfig: 配置管理类
- gdi_context: GDI资源上下文管理器
"""

import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple, Type

import ctypes
from ctypes import wintypes

from rpa.config import (
    OCR_DEFAULT_URL, DEFAULT_TIMEOUT, DEFAULT_RETRY_INTERVAL,
)

# 配置日志（仅输出到控制台）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# 自定义异常类
# ============================================================

class StopException(Exception):
    """
    用户主动停止流程的自定义异常。

    当用户通过全局热键或其他方式主动停止流程时抛出此异常，
    用于区分正常执行结束和用户主动中断。
    """
    pass


# ============================================================
# 重试装饰器
# ============================================================

def retry_on_failure(
    max_retries: int = 3,
    delay: float = 0.5,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    log_func: Optional[Callable[[str], None]] = None
) -> Callable:
    """
    失败重试装饰器。

    当装饰的函数抛出指定异常时，自动进行重试，直到成功或达到最大重试次数。

    Args:
        max_retries: 最大重试次数，默认为3次
        delay: 每次重试之间的延迟时间（秒），默认为0.5秒
        exceptions: 需要捕获并重试的异常类型元组，默认为所有Exception
        log_func: 可选的日志记录函数，用于输出重试日志

    Returns:
        装饰器函数

    Example:
        @retry_on_failure(max_retries=3, delay=0.5, exceptions=(OSError, cv2.error))
        def capture_window(hwnd):
            # 可能失败的截图操作
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        if log_func:
                            log_func(f"{func.__name__} 失败: {e}")
                        logger.error(
                            f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}"
                        )
                        raise
                    time.sleep(delay)
            return None  # 永远不会执行到这里
        return wrapper
    return decorator


# ============================================================
# 图片对象池管理器
# ============================================================

class ImagePreviewPool:
    """
    ImageTk 对象池，使用 LRU 缓存策略避免内存泄漏。

    用于缓存缩略图对象，减少重复创建 ImageTk.PhotoImage 的开销，
    同时通过 LRU 策略限制缓存大小，防止内存无限增长。

    Attributes:
        pool: OrderedDict 实现的 LRU 缓存
        max_size: 缓存最大容量
        _lock: 线程锁，保证线程安全
    """

    def __init__(self, max_size: int = 20):
        """
        初始化图片对象池。

        Args:
            max_size: 缓存最大容量，默认为20
        """
        self.pool: OrderedDict = OrderedDict()
        self.max_size = max_size
        self._lock = threading.Lock()

    def get_thumbnail(
        self,
        image_path: str,
        size: Tuple[int, int] = (80, 40)
    ) -> Optional[Any]:
        """
        获取缩略图，使用对象池缓存。

        如果缓存中已存在对应路径和尺寸的缩略图，直接返回缓存对象；
        否则创建新的缩略图并加入缓存。

        Args:
            image_path: 图片文件的完整路径
            size: 缩略图尺寸 (width, height)，默认为 (80, 40)

        Returns:
            ImageTk.PhotoImage 对象，失败时返回 None
        """
        from PIL import Image, ImageTk

        cache_key = (image_path, size)
        with self._lock:
            if cache_key in self.pool:
                # LRU: 移动到末尾表示最近使用
                self.pool.move_to_end(cache_key)
                return self.pool[cache_key]

            # 检查是否需要清理最旧的项目
            if len(self.pool) >= self.max_size:
                self.pool.popitem(last=False)

            # 创建新的缩略图
            try:
                img = Image.open(image_path)
                img.thumbnail(size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.pool[cache_key] = photo
                return photo
            except Exception as e:
                logger.warning(f"创建缩略图失败 {image_path}: {e}")
                return None

    def clear(self) -> None:
        """清空对象池，释放所有缓存的图片对象。"""
        with self._lock:
            self.pool.clear()


# 全局图片对象池实例
image_pool = ImagePreviewPool()


# ============================================================
# 配置管理类
# ============================================================

@dataclass
class RPAConfig:
    """
    RPA工具配置管理类。

    使用 dataclass 定义所有配置参数，支持从 JSON 文件加载和保存。
    包含图像识别、超时、OCR等各方面的默认配置。

    Attributes:
        default_confidence: 默认图像识别相似度阈值
        default_timeout: 默认超时时间（秒）
        max_retry_attempts: 最大重试次数
        retry_delay: 重试延迟（秒）
        screenshot_format: 截图保存格式
        template_cache_enabled: 是否启用模板缓存
        ocr_api_url: OCR服务API地址
        ui_poll_interval: UI轮询间隔（毫秒）
    """

    default_confidence: float = 0.95
    default_timeout: float = DEFAULT_TIMEOUT
    max_retry_attempts: int = 3
    retry_delay: float = DEFAULT_RETRY_INTERVAL
    screenshot_format: str = 'PNG'
    template_cache_enabled: bool = True
    ocr_api_url: str = OCR_DEFAULT_URL
    ui_poll_interval: int = 100  # UI轮询间隔(ms)

    @classmethod
    def load(cls, config_file: str = "rpa_config.json") -> "RPAConfig":
        """
        从 JSON 文件加载配置。

        如果文件不存在或解析失败，返回默认配置。

        Args:
            config_file: 配置文件路径，默认为 "rpa_config.json"

        Returns:
            RPAConfig 实例
        """
        import os
        import json

        try:
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return cls(**data)
        except Exception as e:
            logger.warning(f"加载配置文件失败: {e}")
        return cls()

    def save(self, config_file: str = "rpa_config.json") -> bool:
        """
        保存配置到 JSON 文件。

        Args:
            config_file: 配置文件路径，默认为 "rpa_config.json"

        Returns:
            保存成功返回 True，失败返回 False
        """
        import json

        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False


# 加载全局配置实例
config = RPAConfig.load()


# ============================================================
# GDI资源上下文管理器
# ============================================================

@contextmanager
def gdi_context(hwnd: int):
    """
    GDI资源上下文管理器，确保资源正确释放。

    使用 with 语句管理 GDI 资源（HDC、Bitmap等）的生命周期，
    确保即使在异常情况下也能正确释放资源，避免内存泄漏。

    Args:
        hwnd: 窗口句柄

    Yields:
        Tuple[hdc, mfc_dc, bitmap, old_obj, width, height]: GDI资源元组

    Example:
        with gdi_context(hwnd) as (hdc, mfc_dc, bitmap, old_obj, w, h):
            # 执行GDI操作
            WinAPI.user32.PrintWindow(hwnd, mfc_dc, flags)
    """
    # 延迟导入 WinAPI 避免循环依赖
    from rpa.win_api import WinAPI

    hdc = None
    mfc_dc = None
    bitmap = None
    old_obj = None
    success = False

    try:
        if not hwnd or not WinAPI.user32.IsWindow(hwnd):
            raise ValueError("无效的窗口句柄")

        # 获取窗口客户区尺寸
        rect = WinAPI.get_rect()
        WinAPI.user32.GetClientRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top

        if w <= 0 or h <= 0:
            raise ValueError("窗口尺寸无效")

        # 创建GDI资源
        hdc = WinAPI.user32.GetDC(hwnd)
        mfc_dc = WinAPI.gdi32.CreateCompatibleDC(hdc)
        bitmap = WinAPI.gdi32.CreateCompatibleBitmap(hdc, w, h)
        old_obj = WinAPI.gdi32.SelectObject(mfc_dc, bitmap)

        success = True
        yield hdc, mfc_dc, bitmap, old_obj, w, h

    except Exception as e:
        logger.error(f"GDI上下文初始化失败: {e}")
        if success:
            yield hdc, mfc_dc, bitmap, old_obj, 0, 0

    finally:
        # 确保资源被正确释放（后进先出）
        try:
            if old_obj and mfc_dc:
                WinAPI.gdi32.SelectObject(mfc_dc, old_obj)
            if bitmap:
                WinAPI.gdi32.DeleteObject(bitmap)
            if mfc_dc:
                WinAPI.gdi32.DeleteDC(mfc_dc)
            if hdc:
                WinAPI.user32.ReleaseDC(hwnd, hdc)
        except Exception as e:
            logger.warning(f"GDI资源释放时出错: {e}")


# ============================================================
# OCR 识别工具函数
# ============================================================

def ocr_find_keyword(
    screenshot,
    keyword: str,
    ocr_url: str = OCR_DEFAULT_URL,
    nth: int = 1,
) -> Optional[Tuple[int, int, int, int]]:
    """
    在截图中使用 OCR 查找关键字，返回文字区域的边界框。

    Args:
        screenshot: PIL.Image 截图对象
        keyword: 要查找的关键字
        ocr_url: OCR 服务地址
        nth: 查找第几个匹配项（从1开始）

    Returns:
        边界框 (x1, y1, x2, y2) 或 None
    """
    try:
        import base64
        import io
        import requests
    except ImportError:
        return None

    try:
        # 转换为 base64
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        # 调用 OCR API
        response = requests.post(
            ocr_url,
            json={
                "base64": b64,
                "options": {
                    "ocr.language": "models/config_chinese.txt"
                },
            },
            timeout=10,
        )
        ocr_result = response.json()

        # 查找第N个包含关键字的文本
        count = 0
        for item in ocr_result.get("data", []):
            text = item.get("text", "")
            if keyword in text:
                count += 1
                if count == nth:
                    box = item.get("box", [])
                    if len(box) >= 4:
                        return (int(box[0][0]), int(box[0][1]),
                                int(box[2][0]), int(box[2][1]))

    except Exception:
        pass

    return None


def ocr_get_texts(
    screenshot,
    ocr_url: str = OCR_DEFAULT_URL,
) -> list:
    """
    在截图中使用 OCR 获取所有文字。

    Args:
        screenshot: PIL.Image 截图对象
        ocr_url: OCR 服务地址

    Returns:
        文字列表，每个元素包含 text 和 box
    """
    try:
        import base64
        import io
        import requests
    except ImportError:
        return []

    try:
        # 转换为 base64
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        # 调用 OCR API
        response = requests.post(
            ocr_url,
            json={
                "base64": b64,
                "options": {
                    "ocr.language": "models/config_chinese.txt"
                },
            },
            timeout=10,
        )
        ocr_result = response.json()
        return ocr_result.get("data", [])

    except Exception:
        return []

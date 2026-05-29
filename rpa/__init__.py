"""
RPA自动化工具

一款基于 Python + Tkinter 开发的 Windows 平台 RPA（机器人流程自动化）工具。
"""

__version__ = "0.8.2"
__author__ = "RPA Team"
__license__ = "MIT"

from rpa.vision import VisionEngine
from rpa.win_driver import WinDriver
from rpa.action_helper import ActionHelper

__all__ = [
    "VisionEngine",
    "WinDriver",
    "ActionHelper",
    "__version__",
]

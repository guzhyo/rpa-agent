"""RPA 自动化工具 - 入口文件"""

import tkinter as tk
import sys
import os
import tempfile
from rpa.gui import RPAGUI
from rpa.icon_data import ICON_DATA
from io import BytesIO


def main() -> None:
    """程序入口函数。"""
    root = tk.Tk()
    root.title("RPA 自动化工具")
    root.geometry("800x600")
    
    # 设置窗口和任务栏图标
    icon_path = None
    is_frozen = getattr(sys, 'frozen', False)
    try:
        from PIL import Image
        from rpa.win_api import WinAPI
        import win32gui
        import win32con
        
        icon_img = Image.open(BytesIO(ICON_DATA))
        
        # 打包环境用临时文件
        if is_frozen:
            fd, icon_path = tempfile.mkstemp(suffix='.ico')
            os.close(fd)
        else:
            icon_path = "icon.ico"
        
        icon_img.save(icon_path, format='ICO')
        root.iconbitmap(icon_path)
        
        # 设置任务栏图标
        hwnd = root.winfo_id()
        hicon = win32gui.LoadImage(
            None, icon_path, win32con.IMAGE_ICON, 0, 0,
            win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        if hicon:
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, hicon)
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, hicon)
    except Exception:
        pass
    finally:
        # 清理打包环境临时ICO文件
        if is_frozen and icon_path:
            try:
                os.unlink(icon_path)
            except OSError:
                pass
    
    root.update()  # 确保窗口先显示
    app = RPAGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()

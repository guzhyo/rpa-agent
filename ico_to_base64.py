"""
ICO转Base64工具
运行此脚本可将icon.ico转换为base64编码
"""

import base64
import os

def convert_ico_to_base64(ico_path: str, output_path: str = None) -> str:
    """将ICO文件转换为base64字符串"""
    with open(ico_path, 'rb') as f:
        data = f.read()
    b64_data = base64.b64encode(data).decode('utf-8')
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(b64_data)
        print(f"已保存到: {output_path}")
    
    return b64_data

def generate_icon_module(ico_path: str, output_path: str = 'rpa/icon_data.py') -> None:
    """生成图标数据模块"""
    b64_data = convert_ico_to_base64(ico_path)
    
    content = f'''"""RPA自动化工具 - 图标数据模块"""

import base64

ICON_DATA = base64.b64decode(
    "{b64_data}"
)
'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"图标模块已生成: {output_path}")

if __name__ == '__main__':
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(script_dir, 'icon.ico')
    
    if os.path.exists(ico_path):
        print(f"正在转换: {ico_path}")
        generate_icon_module(ico_path)
    else:
        print(f"找不到 icon.ico: {ico_path}")

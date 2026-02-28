import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib
import os

def setup_chinese_font(font_path=None):
    """
    Matplotlib 中文字体配置（稳定版）
    """
    if font_path is None:
        # Windows 默认中文字体
        font_path = r"C:\Windows\Fonts\msyh.ttc"  # 微软雅黑

    if not os.path.exists(font_path):
        raise FileNotFoundError(f"字体文件不存在: {font_path}")

    font_prop = fm.FontProperties(fname=font_path)

    matplotlib.rcParams['font.family'] = font_prop.get_name()
    matplotlib.rcParams['axes.unicode_minus'] = False

    print(f"✅ 已使用中文字体: {font_prop.get_name()}")
    return font_prop

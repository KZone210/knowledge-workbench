# -*- coding: utf-8 -*-
"""生成知识工作台应用图标：kb_icon.ico（快捷方式/应用图标）+ kb_icon.png（托盘）。
用法：python tools/make_icon.py   （产物输出到项目根目录）"""
import os
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 256


def lerp_rgb(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def make_icon():
    # 1) 垂直渐变背景（iOS 蓝 #0A84FF → 深蓝）
    top, bottom = (10, 132, 255), (0, 84, 190)
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        grad.putpixel((0, y), lerp_rgb(top, bottom, y / (S - 1)))
    grad = grad.resize((S, S))
    grad_rgba = grad.convert("RGBA")

    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = rounded_mask(S, radius=int(S * 0.223))
    icon.paste(grad_rgba, (0, 0), mask)

    d = ImageDraw.Draw(icon)

    # 2) 中央白色圆角文档
    doc_box = (78, 62, 180, 200)
    d.rounded_rectangle(doc_box, radius=20, fill=(255, 255, 255, 255))
    # 3) 文档上的"内容线"（深蓝，圆头）
    line_col = (0, 96, 210, 235)
    for y, w in [(98, 82), (128, 102), (158, 70)]:
        x0 = 97
        d.rounded_rectangle([x0, y - 6, x0 + w, y + 6], radius=6, fill=line_col)

    # 4) 右下绿色徽章 + 白色对勾（"知识入库成功"之意）
    cx, cy, r = 204, 184, 32
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(52, 199, 89, 255))
    # 外圈白描边增强小尺寸辨识
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 200), width=4)
    d.line([(cx - 15, cy + 1), (cx - 4, cy + 12), (cx + 16, cy - 10)],
           fill=(255, 255, 255, 255), width=11, joint="curve")

    return icon


def main():
    icon = make_icon()
    png_path = os.path.join(BASE, "kb_icon.png")
    ico_path = os.path.join(BASE, "kb_icon.ico")
    icon.save(png_path)
    icon.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已生成: {png_path}")
    print(f"已生成: {ico_path}")


if __name__ == "__main__":
    main()

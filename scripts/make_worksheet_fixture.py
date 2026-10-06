"""生成拍照功能的测试夹具（合成的练习页，无版权）：

python scripts/make_worksheet_fixture.py

输出到 frontend/e2e/fixtures/：
- worksheet.png      清晰的练习页（四年级小数的性质）
- worksheet_blur.png 同一页，画质很差（模糊 + 变暗）——应触发"请核对"
- english.png        一页英文短文——不是数学，应被友好拒绝
夹具提交入库；本脚本需要系统里有中文字体（Windows 的微软雅黑），只在重新生成时运行。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "e2e" / "fixtures"
FONT = Path("C:/Windows/Fonts/msyh.ttc")


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def page(lines: list[tuple[int, int, str, int]]) -> Image.Image:
    """lines: (x, y, text, size)。"""
    im = Image.new("RGB", (1200, 1600), (250, 249, 244))
    d = ImageDraw.Draw(im)
    for x, y, text, size in lines:
        d.text((x, y), text, fill=(24, 24, 28), font=font(size))
    d.line([(80, 1500), (1120, 1500)], fill=(120, 130, 150), width=2)
    return im


def worksheet() -> Image.Image:
    rows: list[tuple[int, int, str, int]] = [
        (80, 70, "第二周 · 周二", 36),
        (80, 130, "小数的性质（练习）", 56),
        (80, 270, "1. 化简各数。", 44),
    ]
    first = ["0.70=", "0.6050=", "3.800=", "20.90=", "10.00=", "2.090="]
    for i, t in enumerate(first):
        rows.append((110 + (i % 3) * 360, 350 + (i // 3) * 90, t, 44))
    rows.append((80, 580, "2. 在○里填上“>”“<”或“=”。", 44))
    cmp_ = ["0.8 ○ 0.58", "4.90 ○ 4.900", "1.45 ○ 1.5", "6 ○ 6.00"]
    for i, t in enumerate(cmp_):
        rows.append((110 + (i % 2) * 520, 660 + (i // 2) * 90, t, 44))
    rows.append((80, 900, "3. 食堂运来大米10.7吨，第一个月吃掉3.3吨，", 44))
    rows.append((80, 970, "    第二个月吃掉3.4吨，还剩多少吨？", 44))
    rows.append((80, 1090, "4. 把下面各数改写成两位小数。", 44))
    rows.append((110, 1170, "5.6=        8=        3.2700=", 44))
    return page(rows)


def english() -> Image.Image:
    lines = [
        "Unit 3  A Day at the Farm",
        "",
        "Tom and Lily went to a farm on Saturday.",
        "They saw cows, sheep and many little ducks.",
        "'Look at the ducks!' said Lily.",
        "Tom took a lot of photos.",
        "",
        "Read and answer.",
        "1. Where did Tom and Lily go?",
        "2. What did they see on the farm?",
    ]
    return page([(80, 120 + i * 110, t, 46) for i, t in enumerate(lines)])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    w = worksheet()
    w.save(OUT / "worksheet.png")
    bad = ImageEnhance.Brightness(w.filter(ImageFilter.GaussianBlur(7))).enhance(0.45)
    bad.save(OUT / "worksheet_blur.png")
    english().save(OUT / "english.png")
    print(f"已生成：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

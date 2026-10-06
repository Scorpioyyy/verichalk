"""生成拍照评测的变体集与负例集（eval/specs/perceive.md §2）。确定性（固定种子），输出到 eval/datasets/photos/（不入库）。

python scripts/make_photo_variants.py

- 变体：对 `raw/` 里每张真实照片施加扰动（旋转、透视、模糊、低光、反光、遮挡、缩小、手写笔迹），沿用源图标注。
- 负例：合成的"不该出题"的图（英文页、语文通知页、初中数学页、空白、全黑、全是手写、严重模糊的数学页、风景渐变、缩略图）。
  负例里的文字页由本脚本用系统字体绘制，不含教辅版权内容。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "eval" / "datasets" / "photos"
LONG_SIDE = 2400
FONT_CJK = Path("C:/Windows/Fonts/msyh.ttc")


def load(path: Path) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
    return im


def rotate(im: Image.Image, rng: random.Random) -> Image.Image:
    ang = rng.choice([-1, 1]) * rng.uniform(6, 12)
    return im.rotate(ang, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(58, 42, 30))


def _coeffs(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    return list(np.linalg.solve(np.array(a, float), np.array(b, float)))


def perspective(im: Image.Image, rng: random.Random) -> Image.Image:
    w, h = im.size
    s = rng.choice([-1, 1])
    k = 0.09
    dst = [(0, 0), (w, 0), (w, h), (0, h)]
    off = [(s * k * w, k * h * 0.5), (-s * k * w * 0.2, 0), (-s * k * w * 0.2, 0), (s * k * w, -k * h * 0.5)]
    src = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(dst, off, strict=True)]
    out = im.transform((w, h), Image.Transform.PERSPECTIVE, _coeffs(src, dst), Image.Resampling.BICUBIC, fillcolor=(58, 42, 30))
    return out


def blur(im: Image.Image, rng: random.Random) -> Image.Image:
    return im.filter(ImageFilter.GaussianBlur(rng.uniform(3.0, 4.5) * im.width / 2400))


def dark(im: Image.Image, rng: random.Random) -> Image.Image:
    out = ImageEnhance.Brightness(im).enhance(rng.uniform(0.28, 0.4))
    arr = np.asarray(out).astype(float)
    noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 4, arr.shape)
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype("uint8"))


def glare(im: Image.Image, rng: random.Random) -> Image.Image:
    w, h = im.size
    g = Image.radial_gradient("L").resize((int(w * 0.9), int(h * 0.55)))
    mask = ImageOps.invert(g).point(lambda v: int(v * 0.95))
    white = Image.new("RGB", g.size, (255, 255, 255))
    out = im.copy()
    out.paste(white, (rng.randint(0, w // 3), rng.randint(0, h // 2)), mask)
    return out


def occlude(im: Image.Image, rng: random.Random) -> Image.Image:
    """遮挡：角落里一根手指（肤色椭圆）+ 一块阴影。"""
    w, h = im.size
    out = im.copy()
    d = ImageDraw.Draw(out, "RGBA")
    cx, cy = rng.choice([(0.92, 0.95), (0.05, 0.9)])
    d.ellipse([cx * w - 0.12 * w, cy * h - 0.07 * h, cx * w + 0.12 * w, cy * h + 0.07 * h], fill=(214, 168, 140, 255))
    shade = Image.new("RGBA", out.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    sd.polygon([(0, 0), (w * 0.5, 0), (0, h * 0.35)], fill=(0, 0, 0, 70))
    return Image.alpha_composite(out.convert("RGBA"), shade).convert("RGB")


def small(im: Image.Image, rng: random.Random) -> Image.Image:
    del rng
    im = im.copy()
    im.thumbnail((800, 800), Image.Resampling.LANCZOS)
    return im


def squiggle(d: ImageDraw.ImageDraw, rng: random.Random, x: float, y: float, size: float, color: tuple[int, int, int]) -> None:
    """一团像手写数字的笔迹：几段随机折线。"""
    for _ in range(rng.randint(2, 4)):
        pts = [(x + rng.uniform(0, size), y + rng.uniform(-size * 0.5, size * 0.5)) for _ in range(rng.randint(3, 5))]
        d.line(pts, fill=color, width=max(3, int(size / 7)), joint="curve")


def handwriting(im: Image.Image, rng: random.Random) -> Image.Image:
    """叠加学生作答：蓝黑色笔迹，落在页面中下部的空白处（模拟括号里、算式后写答案）。"""
    w, h = im.size
    out = im.copy()
    d = ImageDraw.Draw(out)
    color = rng.choice([(20, 40, 150), (30, 30, 40)])
    for _ in range(rng.randint(10, 16)):
        squiggle(d, rng, rng.uniform(0.15, 0.8) * w, rng.uniform(0.3, 0.88) * h, rng.uniform(0.03, 0.05) * w, color)
    return out


def hard(im: Image.Image, rng: random.Random) -> Image.Image:
    """很差的照片：缩到 640px 再强压缩 + 轻微模糊（数字边缘糊在一起，用来检验"看不清要标出来"）。"""
    del rng
    im = im.copy()
    im.thumbnail((640, 640), Image.Resampling.LANCZOS)
    import io

    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=25)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB").filter(ImageFilter.GaussianBlur(1.3))


VARIANTS = {
    "rot": rotate,
    "persp": perspective,
    "blur": blur,
    "dark": dark,
    "glare": glare,
    "occl": occlude,
    "small": small,
    "hand": handwriting,
}


def make_variants() -> list[Path]:
    out_dir = BASE / "variants"
    out_dir.mkdir(parents=True, exist_ok=True)
    raws = sorted((BASE / "raw").glob("p*.jpg"))
    names = list(VARIANTS)
    written: list[Path] = []
    for i, p in enumerate(raws):
        im = load(p)
        for j in range(2):  # 每张源图 2 种扰动，轮换着取，让每种扰动都落在多张图上
            kind = names[(i * 2 + j) % len(names)]
            rng = random.Random(f"{p.stem}-{kind}")
            v = VARIANTS[kind](im, rng)
            dst = out_dir / f"{p.stem}~{kind}.jpg"
            v.save(dst, "JPEG", quality=88)
            written.append(dst)
    for page in ("p009", "p014", "p033", "p044", "p079"):  # 压力集：每张源图再加一张很差的照片
        dst = out_dir / f"{page}~hard.jpg"
        hard(load(BASE / "raw" / f"{page}.jpg"), random.Random(page)).save(dst, "JPEG", quality=60)
        written.append(dst)
    return written


# ---- 负例 ----
def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FONT_CJK.exists():
        return ImageFont.truetype(str(FONT_CJK), size, index=0)
    return ImageFont.load_default(size)


def text_page(lines: list[str], size: int = 56, w: int = 1600, h: int = 2200) -> Image.Image:
    im = Image.new("RGB", (w, h), (250, 249, 245))
    d = ImageDraw.Draw(im)
    f = font(size)
    y = 140
    for ln in lines:
        d.text((120, y), ln, fill=(25, 25, 25), font=f)
        y += int(size * 1.9)
    return im


ENGLISH = [
    "Unit 3  A Day at the Farm",
    "",
    "Tom and Lily went to a farm on Saturday.",
    "They saw cows, sheep and many little ducks.",
    "'Look at the ducks!' said Lily. 'They are swimming",
    "in the pond.' Tom took a lot of photos.",
    "",
    "Read and answer.",
    "1. Where did Tom and Lily go?",
    "2. What did they see on the farm?",
    "3. What did Tom do?",
]
NOTICE = [
    "关于举办春季运动会的通知",
    "",
    "各班同学：",
    "学校定于下周五上午八点在操场举行春季运动会，",
    "请各班按时集合，穿好运动服，注意安全。",
    "比赛项目包括：50 米跑、跳绳、立定跳远和接力赛。",
    "",
    "语文阅读：请同学们回家后阅读《草房子》第三章，",
    "并写一段 200 字左右的读后感。",
]
JUNIOR_MATH = [
    "第二十一章  一元二次方程  课后练习",
    "",
    "1. 解方程：x² - 5x + 6 = 0",
    "2. 已知关于 x 的方程 x² + mx + 4 = 0 有两个相等的实数根，求 m 的值。",
    "3. 已知函数 y = 2x + 1，当 x = 3 时，求 y 的值；",
    "   并求该函数图象与坐标轴围成的三角形面积。",
    "4. 在直角三角形中，两直角边分别为 5 和 12，用勾股定理求斜边。",
    "5. 因式分解：x² - 9y²",
]


def make_negatives() -> list[Path]:
    out_dir = BASE / "negatives"
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random("neg")
    raws = sorted((BASE / "raw").glob("p*.jpg"))
    written: list[Path] = []

    def save(name: str, im: Image.Image) -> None:
        dst = out_dir / f"{name}.jpg"
        im.convert("RGB").save(dst, "JPEG", quality=90)
        written.append(dst)

    save("neg_english", text_page(ENGLISH))
    save("neg_notice", text_page(NOTICE))
    save("neg_junior_math", text_page(JUNIOR_MATH))
    blank = Image.new("RGB", (1600, 2200), (247, 246, 242))
    arr = np.asarray(blank).astype(float) + np.random.default_rng(1).normal(0, 2.5, (2200, 1600, 3))
    save("neg_blank", Image.fromarray(np.clip(arr, 0, 255).astype("uint8")))
    save("neg_black", Image.new("RGB", (1600, 2200), (6, 6, 8)))
    hw = Image.new("RGB", (1600, 2200), (250, 249, 245))
    d = ImageDraw.Draw(hw)
    for _ in range(40):
        squiggle(d, rng, rng.uniform(0.08, 0.85) * 1600, rng.uniform(0.08, 0.9) * 2200, rng.uniform(50, 90), (25, 40, 150))
    save("neg_handwriting_only", hw)
    sky = Image.new("RGB", (1600, 1200))
    sd = ImageDraw.Draw(sky)
    for y in range(1200):
        t = y / 1200
        sd.line([(0, y), (1600, y)], fill=(int(255 * (1 - t) + 40 * t), int(160 * (1 - t) + 70 * t), int(80 * (1 - t) + 140 * t)))
    sd.ellipse([1000, 500, 1300, 800], fill=(255, 220, 120))
    sd.polygon([(0, 1200), (500, 700), (1000, 1200)], fill=(40, 60, 50))
    save("neg_scenery", sky)
    if raws:
        base = load(raws[len(raws) // 2])
        save("neg_blur_math", base.filter(ImageFilter.GaussianBlur(28)))
        save("neg_dark_math", ImageEnhance.Brightness(base).enhance(0.04))
        thumb = base.copy()
        thumb.thumbnail((120, 160))
        save("neg_tiny", thumb)
    return written


def main() -> int:
    if not (BASE / "raw").exists():
        print("缺少 eval/datasets/photos/raw/（真实照片）", file=sys.stderr)
        return 1
    v = make_variants()
    n = make_negatives()
    print(f"变体 {len(v)} 张，负例 {len(n)} 张 → {BASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

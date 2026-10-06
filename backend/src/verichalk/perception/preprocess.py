"""图片预处理：按 EXIF 转正 → 限制长边 → 统一为 JPEG → 质量指标与拍摄建议。

确定性、无模型调用。**只在极端情况下拒绝**（几乎全黑 / 全白 / 过小）：质量门槛放太严会误拒真实照片（eval/specs/perceive.md P6），
其余问题只生成"拍摄建议"（教师语言），由视觉模型与教师决定能不能用。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from ..core.errors import VerichalkError
from ..domain.perception import ImageQuality

MIN_SIDE = 200  # 短边小于此值：无法读题
DEFAULT_MAX_SIDE = 1600
JPEG_QUALITY = 88
_STAT_SIDE = 800  # 质量指标在缩小后的灰度图上算：与原始分辨率无关，也更快

# 阈值经变体集校准（eval/specs/perceive.md §5）
SHARPNESS_LOW = 1000.0  # 真实清晰照片 ≥ 1480；模糊 / 压缩变体 ≤ 860
LOW_RES = 600  # 短边小于它：分辨率偏低
BRIGHTNESS_DARK = 70.0
BRIGHTNESS_BRIGHT = 235.0
CONTRAST_FLAT = 22.0
EXTREME_DARK = 12.0
EXTREME_BRIGHT = 250.0


class PrepareError(VerichalkError):
    code = "image_unusable"

    def __init__(self, message: str, *, user_message: str) -> None:
        super().__init__(message, user_message=user_message)


@dataclass
class Prepared:
    data: bytes  # 送给模型的 JPEG
    quality: ImageQuality
    reject: str = ""  # 非空：确定性判定不可用（教师语言的原因）
    mime: str = field(default="image/jpeg")


def measure(img: Image.Image) -> tuple[float, float, float]:
    """返回 (清晰度, 平均亮度, 对比度)。清晰度 = 边缘滤波响应的方差（缩到固定尺寸后计算）。"""
    g = ImageOps.grayscale(img)
    g.thumbnail((_STAT_SIDE, _STAT_SIDE))
    stat = ImageStat.Stat(g)
    edges = g.filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).var[0]), float(stat.mean[0]), float(stat.stddev[0])


def _hints(sharp: float, bright: float, contrast: float, short_side: int) -> list[str]:
    out: list[str] = []
    if sharp < SHARPNESS_LOW:
        out.append("照片有些模糊，建议稳住手机、对好焦再拍一张")
    if short_side < LOW_RES:
        out.append("照片分辨率偏低，建议靠近一点、整页拍清楚")
    if bright < BRIGHTNESS_DARK:
        out.append("光线偏暗，建议开灯或到亮一点的地方拍")
    elif bright > BRIGHTNESS_BRIGHT:
        out.append("照片过亮或有反光，建议避开灯光直射")
    elif contrast < CONTRAST_FLAT:
        out.append("字迹与纸张对比不明显，建议调整光线后重拍")
    return out


def prepare(data: bytes, *, max_side: int = DEFAULT_MAX_SIDE, resize: bool = True) -> Prepared:
    """读取图片字节，返回可送入视觉模型的 JPEG 与质量指标。`resize=False` 只转 JPEG（消融用）。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as e:
        raise PrepareError(
            "无法解码图片", user_message="这个文件不是有效的图片，请上传 JPG / PNG / WebP。"
        ) from e
    ow, oh = img.size
    img = ImageOps.exif_transpose(img).convert("RGB")
    if resize and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    sharp, bright, contrast = measure(img)
    hints = _hints(sharp, bright, contrast, min(img.size))
    quality = ImageQuality(
        width=img.width,
        height=img.height,
        orig_width=ow,
        orig_height=oh,
        sharpness=round(sharp, 1),
        brightness=round(bright, 1),
        contrast=round(contrast, 1),
        hints=hints,
        poor=sharp < SHARPNESS_LOW
        or bright < BRIGHTNESS_DARK
        or min(img.size) < LOW_RES
        or (
            contrast < CONTRAST_FLAT and bright <= BRIGHTNESS_BRIGHT
        ),  # 过亮只提示，不强制核对（白底清晰页很常见）
    )
    reject = ""
    if min(img.size) < MIN_SIDE:
        reject = "图片太小，看不清上面的字。请重新拍一张完整的页面。"
    elif bright < EXTREME_DARK:
        reject = "照片几乎是全黑的，请开灯后重新拍一张。"
    elif bright > EXTREME_BRIGHT and contrast < CONTRAST_FLAT:
        reject = "照片几乎是一片空白，请对准有题目的页面重新拍一张。"
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return Prepared(data=buf.getvalue(), quality=quality, reject=reject)


def thumbnail_jpeg(data: bytes, max_side: int = 720) -> bytes:
    """缩略图（聊天里的照片预览）：转正、限长边、JPEG。"""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def to_data_uri(p: Prepared) -> str:
    return f"data:{p.mime};base64,{base64.b64encode(p.data).decode('ascii')}"

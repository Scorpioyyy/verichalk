"""字体：PDF 模板的字体回退栈，以及"内容里的字符有没有字形"的检查（X3）。

Typst 遇到没有字形的字符不报错也不警告，只会画出豆腐块，所以在导出前自己查：对内容里每个非 ASCII 字符，
字体栈里至少要有一个字体覆盖它。服务器镜像必须装中文字体（Docker 里 `fonts-noto-cjk`），否则这里会给出警告。
"""

from __future__ import annotations

from functools import lru_cache

import typst
from fontTools.ttLib import TTFont

# 拉丁 / 数字用 New Computer Modern（Typst 内置），汉字依次回退；数学用 New Computer Modern Math
TEXT_FONTS = (
    "New Computer Modern",
    "Noto Serif CJK SC",
    "Noto Serif SC",
    "Source Han Serif SC",
    "SimSun",
    "Songti SC",
    "STSong",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Microsoft YaHei",
)
MATH_FONT = "New Computer Modern Math"


@lru_cache(maxsize=4)
def _installed(font_dirs: tuple[str, ...]) -> dict[str, list[tuple[str, int]]]:
    fonts = typst.Fonts(font_paths=list(font_dirs)) if font_dirs else typst.Fonts()
    out: dict[str, list[tuple[str, int]]] = {}
    for f in fonts.fonts():
        if f.path:
            out.setdefault(f.family, []).append((str(f.path), f.index))
    return out


@lru_cache(maxsize=64)
def _cmap(path: str, index: int) -> frozenset[int]:
    try:
        font = TTFont(path, fontNumber=index, lazy=True)
        return frozenset((font.getBestCmap() or {}).keys())
    except Exception:
        return frozenset()


def _covered(font_dirs: tuple[str, ...]) -> frozenset[int]:
    installed = _installed(font_dirs)
    cps: set[int] = set()
    seen: set[tuple[str, int]] = set()
    for fam in (*TEXT_FONTS, MATH_FONT):
        for path, idx in installed.get(fam, [])[:3]:  # 同族取前几个字重即可（字形覆盖基本一致）
            if (path, idx) not in seen:
                seen.add((path, idx))
                cps |= _cmap(path, idx)
    return frozenset(cps)


def has_cjk_font(font_dirs: tuple[str, ...] = ()) -> bool:
    installed = _installed(font_dirs)
    return any(f in installed for f in TEXT_FONTS if f != "New Computer Modern")


def missing_glyphs(text: str, font_dirs: tuple[str, ...] = ()) -> list[str]:
    """内容里没有任何字体覆盖的字符（去重、按出现顺序）。空白与 ASCII 不查。"""
    covered = _covered(font_dirs)
    out: list[str] = []
    for ch in text:
        cp = ord(ch)
        if cp < 0x80 or ch.isspace() or cp in covered or ch in out:
            continue
        out.append(ch)
    return out

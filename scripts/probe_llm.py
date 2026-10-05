"""M1 探针：对候选模型实测 TTFT、总时长、输出速度、缓存命中行为与图片输入，写入 eval/reports/m1_probe.md。

用法：python scripts/probe_llm.py [--profile cn|intl] [--repeat 3]
需要对应 profile 的 DASHSCOPE_* 环境变量。费用极小（每个模型约十次短调用）。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from verichalk.core.config import LLMMode, Profile, Settings  # noqa: E402
from verichalk.core.errors import VerichalkError  # noqa: E402
from verichalk.domain.llm import ChatMessage, Role  # noqa: E402
from verichalk.llm import LLMRequest, build_gateway  # noqa: E402

TEXT_MODELS = [
    ("qwen3.8-flash", False),
    ("qwen3.7-flash", False),
    ("qwen3.8-max", False),
    ("qwen3.7-plus", False),
    ("deepseek-v4.1-flash", False),
    ("deepseek-v4-pro", False),
    ("deepseek-v4.1-flash", True),
    ("qwen3.8-flash", True),
]
VISION_MODELS = [
    "qwen3.8-flash",
    "qwen3-vl-plus",
    "qwen3-vl-flash",
    "qwen3.8-omni-flash",
    "qwen-vl-ocr-2025-11-20",
]
SHORT = "用一句话说明什么是平行四边形。"
LONG_PREFIX = "你是小学数学命题助手。规则：" + "".join(
    f"第{i}条：题目必须符合北师大版课程标准，数值精确，不得出现超纲概念，答案唯一。"
    for i in range(120)
)


def sample_image() -> str:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 260), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 34)
    d.text((20, 20), "4. 小明有 3.6 元，买一支铅笔用去 1.25 元，", font=f, fill="black")
    d.text((20, 80), "还剩多少元？  (    ) 元", font=f, fill="black")
    d.text(
        (20, 150),
        "5. 在三角形 ABC 中，∠A = 56°，∠B = 74°，求 ∠C。",
        font=f,
        fill="black",
    )
    b = io.BytesIO()
    img.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="intl", choices=["cn", "intl"])
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    settings = Settings(profile=Profile(args.profile), llm_mode=LLMMode.live)
    gw = build_gateway(settings)
    lines = [
        f"## profile = {args.profile}",
        "",
        "### 文本模型（短提示，非流式场景的延迟代理）",
        "",
        "| 模型 | 思考 | TTFT p50 (ms) | 总时长 p50 (ms) | 输出 tok/s | 备注 |",
        "|---|---|---|---|---|---|",
    ]

    def ov(model: str, thinking: bool, role: Role = Role.fast):
        gw.registry = gw.registry.override(
            role,
            model=model,
            thinking=thinking,
            max_tokens=300 if not thinking else 1200,
        )

    for model, thinking in TEXT_MODELS:
        ov(model, thinking)
        ttfts, totals, speeds, note = [], [], [], ""
        for _ in range(args.repeat):
            try:
                r = await gw.complete(
                    LLMRequest(
                        role=Role.fast,
                        messages=[ChatMessage(role="user", content=SHORT)],
                        purpose="probe",
                    )
                )
                ttfts.append(r.ttft_ms or 0)
                totals.append(r.total_ms)
                gen_ms = max(r.total_ms - (r.ttft_ms or 0), 1)
                speeds.append(r.usage.completion_tokens / (gen_ms / 1000))
            except VerichalkError as e:
                note = f"{e.code}"
                break
        if ttfts:
            lines.append(
                f"| {model} | {'是' if thinking else '否'} | {statistics.median(ttfts):.0f} | "
                f"{statistics.median(totals):.0f} | {statistics.median(speeds):.0f} | {note} |"
            )
        else:
            lines.append(
                f"| {model} | {'是' if thinking else '否'} | — | — | — | {note} |"
            )

    lines += [
        "",
        "### 缓存行为（同一长前缀连续调用 3 次；前缀约 2.8k token）",
        "",
        "| 模型 | 第 1 次 cached/prompt | 第 2 次 | 第 3 次 | 第 2 次 TTFT (ms) | 第 1 次 TTFT (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for model in [
        "qwen3.8-flash",
        "qwen3.8-max",
        "qwen3.7-plus",
        "deepseek-v4.1-flash",
    ]:
        ov(model, False)
        cells, tt = [], []
        tag = f"{time.time():.0f}"  # 避免命中上一次探针留下的缓存
        for i in range(3):
            try:
                r = await gw.complete(
                    LLMRequest(
                        role=Role.fast,
                        purpose="probe-cache",
                        messages=[
                            ChatMessage(
                                role="system", content=f"[{tag}-{model}] " + LONG_PREFIX
                            ),
                            ChatMessage(
                                role="user", content=f"请只回复“好”。（第{i}次）"
                            ),
                        ],
                    )
                )
                cells.append(f"{r.usage.cached_tokens}/{r.usage.prompt_tokens}")
                tt.append(f"{r.ttft_ms:.0f}" if r.ttft_ms else "—")
            except VerichalkError as e:
                cells.append(e.code)
                tt.append("—")
        lines.append(
            f"| {model} | {cells[0]} | {cells[1]} | {cells[2]} | {tt[1]} | {tt[0]} |"
        )

    lines += [
        "",
        "### 图片输入（合成的练习册题目图，转写）",
        "",
        "| 模型 | 总时长 (ms) | 转写含「3.6」「1.25」「56」「74」 | 备注 |",
        "|---|---|---|---|",
    ]
    uri = sample_image()
    for model in VISION_MODELS:
        ov(model, False, Role.vision)
        try:
            r = await gw.complete(
                LLMRequest(
                    role=Role.vision,
                    purpose="probe-vision",
                    messages=[
                        ChatMessage(
                            role="user",
                            content=[
                                {"type": "image_url", "image_url": {"url": uri}},
                                {
                                    "type": "text",
                                    "text": "逐题转写图中文字，保留题号，只输出转写结果。",
                                },
                            ],
                        )
                    ],
                )
            )
            ok = all(x in r.text for x in ("3.6", "1.25", "56", "74"))
            lines.append(f"| {model} | {r.total_ms:.0f} | {'是' if ok else '否'} | |")
        except VerichalkError as e:
            lines.append(f"| {model} | — | — | {e.code} |")

    out = ROOT / "eval" / "reports" / f"m1_probe_{args.profile}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    header = f"# M1 模型探针（{time.strftime('%Y-%m-%d')}）\n\n本机网络下的实测，仅作基线参考；每次部署环境不同，数值会变。\n\n"
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())

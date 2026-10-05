"""图片上传：校验、落盘、登记。上传内容只作为数据处理，永远不会被当作指令或代码执行。"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from ..core.config import Settings
from ..core.errors import InvalidRequest
from ..core.ids import new_id
from ..domain.run import Attachment

ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_FILES = 4
MAX_PIXELS = 40_000_000


async def save_uploads(settings: Settings, session_id: str, files: list[UploadFile]) -> list[Attachment]:
    if len(files) > MAX_FILES:
        raise InvalidRequest(
            f"一次最多上传 {MAX_FILES} 张图片", user_message=f"一次最多上传 {MAX_FILES} 张图片。"
        )
    out: list[Attachment] = []
    root = settings.data_path / "uploads" / session_id
    for f in files:
        data = await f.read()
        if len(data) > settings.upload_max_mb * 2**20:
            raise InvalidRequest(
                "图片过大", user_message=f"图片不能超过 {settings.upload_max_mb}MB，请压缩后重试。"
            )
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
            fmt = (Image.open(io.BytesIO(data)).format or "").lower()
            w, h = Image.open(io.BytesIO(data)).size
        except (UnidentifiedImageError, OSError, SyntaxError) as e:
            raise InvalidRequest(
                "不是有效的图片", user_message="这个文件不是有效的图片，请上传 JPG / PNG / WebP。"
            ) from e
        mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(fmt)
        if mime is None or mime not in ALLOWED:
            raise InvalidRequest(f"不支持的图片格式：{fmt}", user_message="暂只支持 JPG / PNG / WebP 图片。")
        if w * h > MAX_PIXELS:
            raise InvalidRequest("图片像素过大", user_message="图片分辨率过大，请压缩后重试。")
        att_id = new_id("att")
        path = root / f"{att_id}{ALLOWED[mime]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        out.append(
            Attachment(
                id=att_id,
                filename=Path(f.filename or "image").name[:80],
                mime=mime,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                session_id=session_id,
                path=str(path.relative_to(settings.data_path)),
                ts=time.time(),
            )
        )
    return out

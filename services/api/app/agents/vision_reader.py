"""扫描件视觉转录：页面图交给视觉模型抄成文字，再交给既有规则解析。

定位：这是「网页版能读图」的等价能力，不是工程判定的替代品。
- 只负责把页面上的字抄下来，不做解释、不做取舍；
- 输出当候选文本，字段仍需人工确认；
- 模型不可用或未配置时明确报错，不伪造文字。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Callable

MAX_SIDE = 1600
LAST_ERRORS: list[str] = []
MAX_IMAGE_BYTES = 12 * 1024 * 1024

PROMPT = (
    "这是工程计算书的一页扫描图。请把页面上的文字按阅读顺序原样抄写：\n"
    "1) 表格每行输出一行，列之间用 | 分隔；\n"
    "2) 数字、单位、编号必须原样保留，不要四舍五入、不要补全；\n"
    "3) 看不清的字用 □ 代替，不要猜；\n"
    "4) 不要解释、不要总结、不要加标题；\n"
    "5) 必须从页面第一行抄到最后一行，表格有几行抄几行，不得省略或提前结束。\n"
    "页面没有文字就返回空字符串。"
)


def _model() -> str:
    return (os.getenv("PITGUARD_LLM_MODEL_VISION", "").strip()
            or os.getenv("PITGUARD_LLM_MODEL_FAST", "").strip())


def available() -> bool:
    return (os.getenv("PITGUARD_LLM_ENABLE", "0") == "1"
            and bool(os.getenv("PITGUARD_LLM_API_KEY", "").strip())
            and bool(_model()))


def _endpoint() -> str:
    base = os.getenv("PITGUARD_LLM_BASE_URL", "https://api.deepseek.com/v1").strip().rstrip("/")
    return f"{base}/chat/completions"


def _shrink(image_bytes: bytes, mime: str) -> tuple[bytes, str]:
    """压到视觉模型舒服的尺寸；不转换时保持原 MIME，避免声明与实际不符。"""
    if len(image_bytes) <= 1_500_000:
        return image_bytes, mime
    try:
        from PIL import Image
    except Exception:
        return image_bytes, mime
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        return buf.getvalue(), "image/jpeg"


def read_image(image_bytes: bytes, mime: str = "image/png", *,
               transport: Callable[[str, bytes, str], bytes] | None = None) -> str:
    """把一张页面图抄成文字。失败抛 ValueError，由上层决定是否回退。"""
    if not image_bytes:
        raise ValueError("页面图为空")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("单页图片超过 12 MB，请先压缩")
    if not available():
        raise ValueError("未配置视觉模型，无法读取扫描页")
    payload_bytes, mime = _shrink(image_bytes, mime)
    data_url = f"data:{mime};base64,{base64.b64encode(payload_bytes).decode()}"
    payload = json.dumps({
        "model": _model(),
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
    }).encode("utf-8")
    key = os.getenv("PITGUARD_LLM_API_KEY", "").strip()
    endpoint = _endpoint()
    request = urllib.request.Request(endpoint, data=payload, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    raw = b""
    last_error = ""
    for attempt in range(3):
        try:
            if transport:
                raw = transport(endpoint, payload, key)
            else:
                with opener.open(request, timeout=90) as reply:
                    raw = reply.read(400_001)
            break
        except Exception as exc:
            status = getattr(exc, "code", None)
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:160]
            except Exception:
                detail = ""
            last_error = f"{type(exc).__name__}{f' {status}' if status else ''} {detail}".strip()
            if status == 429 and attempt < 2:
                time.sleep(4 * (attempt + 1))
                continue
            raise ValueError(f"视觉模型调用失败：{last_error}") from exc
    if not raw:
        raise ValueError(f"视觉模型无响应：{last_error or '空响应'}")
    try:
        choice = json.loads(raw)["choices"][0]
        content = choice["message"]["content"]
    except Exception as exc:
        raise ValueError("视觉模型返回结构不合法") from exc
    # 有的兼容实现把 content 返回成块数组（[{type:text,text:...}]），统一拼成字符串。
    if isinstance(content, list):
        pieces = [part.get("text", "") for part in content if isinstance(part, dict)]
        content = "".join(pieces)
    if not isinstance(content, str):
        finish = choice.get("finish_reason")
        raise ValueError(f"视觉模型未返回文本（finish_reason={finish}）")
    # 空白切片是正常情况（该段没有字），返回空串由调用方跳过。
    return content.strip()


def document_pages(data: bytes) -> list[str]:
    """逐页取内嵌页面图并转录；没有图的页返回空串。

    扫描件通常「一页一张图」；矢量绘制的页面取不到图，这里如实返回空，
    由上层决定是否报「需要 OCR」。
    """
    from pypdf import PdfReader

    LAST_ERRORS.clear()
    digest = hashlib.sha256(data).hexdigest()[:16]
    cache = Path("runtime") / "vision-cache" / f"{digest}.json"
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(cached.get("pages"), list):
                return [str(item) for item in cached["pages"]]
        except Exception:
            pass
    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        images = sorted(page.images, key=lambda item: len(item.data), reverse=True)
        if not images:
            pages.append("")
            continue
        image = images[0]
        mime = "image/jpeg" if str(image.name).lower().endswith((".jpg", ".jpeg")) else "image/png"
        chunks: list[str] = []
        try:
            for part, part_mime in _slices(image.data, mime):
                chunks.append(read_image(part, part_mime))
        except ValueError as exc:
            LAST_ERRORS.append(f"第{page_index}页: {exc}")
            chunks = []
        pages.append("\n".join(chunk for chunk in chunks if chunk.strip()))
    if any(page.strip() for page in pages):
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"pages": pages}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return pages


def _slices(image_bytes: bytes, mime: str) -> list[tuple[bytes, str]]:
    """长页切成上下若干段（带重叠），避免模型抄到一半就停。"""
    try:
        from PIL import Image
    except Exception:
        return [(image_bytes, mime)]
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            width, height = img.size
            if height <= 1400:
                return [(image_bytes, mime)]
            count = max(2, min(4, round(height / 1100)))
            step = height // count
            overlap = int(step * 0.08)
            parts: list[tuple[bytes, str]] = []
            for index in range(count):
                top = max(0, index * step - (overlap if index else 0))
                bottom = min(height, (index + 1) * step + overlap)
                buf = io.BytesIO()
                img.crop((0, top, width, bottom)).save(buf, format="JPEG", quality=88)
                parts.append((buf.getvalue(), "image/jpeg"))
            return parts
    except Exception:
        return [(image_bytes, mime)]

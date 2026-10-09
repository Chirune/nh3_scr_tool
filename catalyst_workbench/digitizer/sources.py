"""Local image/PDF loading with reproducible source metadata.

PDF pages are rendered as complete pages. Caption candidates are text hints only;
this module does not locate figures or infer their scientific meaning.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import threading
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
import pypdfium2 as pdfium


MAX_PIXELS = 20_000_000
MAX_PAGE_TEXT = 20_000
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDFIUM_LOCK = threading.Lock()


class SourceError(ValueError):
    """A human-readable, local source loading error."""


def _source_path(path: str | Path) -> Path:
    try:
        candidate = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SourceError("找不到该文件，请重新选择本地图片或 PDF。") from exc
    if not candidate.is_file():
        raise SourceError("请选择一个文件，不能选择文件夹。")
    if candidate.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES | {".pdf"}:
        raise SourceError("暂不支持此文件类型。请选择 PDF、PNG、JPEG、TIFF、BMP 或 WebP。")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(2 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SourceError("无法读取文件，请检查文件权限，或把文件复制到本地后重试。") from exc
    return digest.hexdigest()


def _check_source_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise SourceError("图片尺寸无效，无法读取。")
    if width * height > MAX_PIXELS:
        raise SourceError("原始图片超过 2000 万像素。为避免内存不足，请先缩小图片或导入原始 PDF。")


def _checked_limit(max_dimension: int) -> int:
    if isinstance(max_dimension, bool) or not isinstance(max_dimension, int) or max_dimension < 1:
        raise SourceError("图像最大边长必须为正整数。")
    return max_dimension


def _image_size(width: int, height: int, limit: int) -> tuple[int, int, float]:
    scale = min(1.0, limit / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))
    out_width = max(1, int(math.floor(width * scale)))
    out_height = max(1, int(math.floor(height * scale)))
    return out_width, out_height, scale


def _caption_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    for line in lines:
        cleaned = re.sub(r"\s+", " ", line).strip()
        if re.match(r"^(?:Fig(?:ure)?\.?\s*[A-Za-z]?\d+[A-Za-z]?\b|图\s*\d+)", cleaned, re.I):
            if cleaned not in candidates:
                candidates.append(cleaned[:1500])
    return candidates[:30]


def inspect_source(path: str | Path) -> dict[str, Any]:
    """Return local source identity and dimensions without rendering a PDF."""
    source = _source_path(path)
    metadata: dict[str, Any] = {
        "kind": "pdf" if source.suffix.lower() == ".pdf" else "image",
        "path": str(source),
        "sha256": _sha256(source),
        "size_bytes": source.stat().st_size,
    }
    if metadata["kind"] == "pdf":
        try:
            with PDFIUM_LOCK:
                document = pdfium.PdfDocument(str(source))
                try:
                    metadata["page_count"] = len(document)
                    if metadata["page_count"] < 1:
                        raise SourceError("PDF 没有可读取的页面。")
                finally:
                    document.close()
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError("无法打开 PDF。文件可能已损坏、受密码保护，或并非真正的 PDF。") from exc
    else:
        try:
            with Image.open(source) as opened:
                _check_source_size(*opened.size)
                raw_width, raw_height = opened.size
                # An orientation that swaps axes can be checked without decoding.
                orientation = opened.getexif().get(274, 1)
                width, height = (raw_height, raw_width) if orientation in {5, 6, 7, 8} else opened.size
                metadata.update({
                    "page_count": 1, "image_width": width, "image_height": height,
                    "raw_image_width": raw_width, "raw_image_height": raw_height,
                    "image_format": opened.format,
                    "image_frame_count": getattr(opened, "n_frames", 1),
                    "exif_orientation": orientation,
                })
        except SourceError:
            raise
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise SourceError("无法打开图片，文件可能已损坏或格式不正确。") from exc
    return metadata


def render_source(path: str | Path, page: int = 1, max_dimension: int = 3600) -> tuple[Image.Image, dict[str, Any]]:
    """Render one source page to an independent RGB PIL image.

    Pages are one-based. Large PDF renders are capped at 20 million pixels.
    Images retain their size unless they exceed max_dimension; EXIF orientation
    is applied and transparency is composited over white. TIFF imports its first
    frame only, explicitly noted in metadata. Returned objects own their pixels.
    """
    limit = _checked_limit(max_dimension)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise SourceError("页码应为从 1 开始的整数。")
    info = inspect_source(path)
    if page > info["page_count"]:
        raise SourceError(f"页码超出范围：该文件共 {info['page_count']} 页。")
    source = Path(info["path"])
    metadata: dict[str, Any] = {
        "source_path": str(source), "source_sha256": info["sha256"],
        "source_kind": info["kind"], "page": page, "page_count": info["page_count"],
        "page_text": "", "figure_caption_candidates": [],
        "caption_note": "图注仅为页面文字候选，未经自动图像定位或人工确认。",
    }
    if info["kind"] == "image":
        try:
            with Image.open(source) as opened:
                oriented = ImageOps.exif_transpose(opened)
                try:
                    width, height = oriented.size
                    out_width, out_height, scale = _image_size(width, height, limit)
                    if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                        rgba = oriented.convert("RGBA")
                        try:
                            rgb = Image.new("RGB", rgba.size, "white")
                            rgb.paste(rgba, mask=rgba.getchannel("A"))
                        finally:
                            rgba.close()
                    else:
                        rgb = oriented.convert("RGB")
                    if rgb.size != (out_width, out_height):
                        resized = rgb.resize((out_width, out_height), Image.Resampling.LANCZOS)
                        rgb.close()
                        rgb = resized
                finally:
                    oriented.close()
            metadata.update({
                "image_width": rgb.width, "image_height": rgb.height,
                "original_image_width": width, "original_image_height": height,
                "raw_image_width": info["raw_image_width"], "raw_image_height": info["raw_image_height"],
                "render_scale": scale, "render_scale_x": rgb.width / width,
                "render_scale_y": rgb.height / height, "image_frame_count": info["image_frame_count"],
                "frame_note": "仅导入第一帧。" if info["image_frame_count"] > 1 else "",
            })
            return rgb, metadata
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError("图片像素读取失败，请尝试重新保存为 PNG 后导入。") from exc

    try:
        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(str(source))
            try:
                pdf_page = document[page - 1]
                try:
                    width_points, height_points = pdf_page.get_size()
                    if width_points <= 0 or height_points <= 0:
                        raise SourceError("PDF 页面尺寸无效。")
                    # 1 PDF point = 1/72 inch; cap by dimension AND pixel count.
                    scale = min(limit / max(width_points, height_points), math.sqrt(MAX_PIXELS / (width_points * height_points)))
                    # PDFium rounds up pixel dimensions; keep its allocation within cap.
                    while math.ceil(width_points * scale) * math.ceil(height_points * scale) > MAX_PIXELS:
                        scale *= 0.999
                    text_error = ""
                    try:
                        text_page = pdf_page.get_textpage()
                        try:
                            page_text = text_page.get_text_bounded()[:MAX_PAGE_TEXT]
                        finally:
                            text_page.close()
                    except Exception:
                        page_text = ""
                        text_error = "本页文本层读取失败；仍可读取图像。"
                    bitmap = pdf_page.render(scale=scale)
                    try:
                        borrowed = bitmap.to_pil()
                        try:
                            rgb = borrowed.convert("RGB").copy()
                        finally:
                            borrowed.close()
                    finally:
                        bitmap.close()
                finally:
                    pdf_page.close()
            finally:
                document.close()
        metadata.update({
            "image_width": rgb.width, "image_height": rgb.height,
            "page_width_points": width_points, "page_height_points": height_points,
            "render_scale": scale, "render_dpi": scale * 72,
            "page_text": page_text,
            "text_note": text_error or ("未提取到文本层；未执行 OCR。" if not page_text.strip() else "来自 PDF 文本层，未执行 OCR。"),
            "figure_caption_candidates": _caption_candidates(page_text),
        })
        return rgb, metadata
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("PDF 页面读取失败，请检查文件完整性，或把目标页另存为图片后导入。") from exc

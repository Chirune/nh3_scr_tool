"""Windows built-in OCR, executed locally without changing system settings."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image


def _recognize(path):
    shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not shell.is_file():
        raise ValueError("本机没有可用的 Windows 本地文字识别入口。")
    source = Path(__file__).with_name("windows_ocr.ps1").read_text(encoding="utf-8")
    literal = str(Path(path).resolve()).replace("'", "''")
    command = "& {\n" + source + "\n} -ImagePath '" + literal + "'"
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    output = result.stdout.decode("utf-8-sig", errors="replace").strip()
    try:
        response = json.loads(output)
    except Exception:
        raise ValueError("本地文字识别没有返回可用结果：" + result.stderr.decode("utf-8", errors="replace")[:300]) from None
    if result.returncode or not response.get("available"):
        raise ValueError("本地文字识别未完成：" + str(response.get("error", "未知错误")))
    return response


def recognize_image(path, include_rotated=True):
    """Return line and word boxes in the original crop's pixel coordinates."""
    path = Path(path).resolve()
    with Image.open(path) as source:
        image = source.convert("RGB").copy()
    width, height = image.size
    responses = [(0, _recognize(path))]
    if include_rotated:
        with tempfile.TemporaryDirectory(prefix="local_figure_ocr_") as folder:
            for angle in (90, -90):
                rotated = image.rotate(angle, expand=True)
                rotated_path = Path(folder) / f"rotation_{angle}.png"
                rotated.save(rotated_path)
                rotated.close()
                responses.append((angle, _recognize(rotated_path)))
    image.close()

    def original_box(box, angle):
        left, top, right, bottom = map(float, box)
        if angle == 90:
            return [width-bottom, left, width-top, right]
        if angle == -90:
            return [top, height-right, bottom, height-left]
        return [left, top, right, bottom]

    lines = []
    for angle, response in responses:
        for raw_line in response.get("lines", []):
            words = [{"text": word["text"], "bbox": original_box(word["bbox"], angle)} for word in raw_line.get("words", [])]
            if not words:
                continue
            boxes = [word["bbox"] for word in words]
            lines.append({"text": raw_line["text"], "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)], "words": words,
                          "orientation": angle, "source": "windows_local_ocr", "requires_review": True})
    return {"status": "ready", "lines": lines, "width": width, "height": height,
            "language": responses[0][1].get("language"),
            "warnings": ["OCR 字符识别可能有误；刻度、单位和图例需要核对。"]}

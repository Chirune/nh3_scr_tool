"""Local coordinate calibration and conservative colour tracing for graph images.

This module never fetches remote content and does not interpolate missing points.
Coordinates and regions use the original image, with its origin at the top left.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image


def _finite(value: Any, label: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label}必须是有效数字，不能是是/否值。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}必须是有效数字。") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label}不能是空值、无穷大或非数字。")
    return number


def _point(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 2:
        raise ValueError(f"{label}需要两个像素坐标，例如 [100, 200]。")
    return [_finite(value[0], f"{label}横坐标"), _finite(value[1], f"{label}纵坐标")]


def validate_calibration(cal: dict) -> dict:
    """Return a validated copy with ``x``, ``y`` and a ``warnings`` list.

    First-version calibration treats the axes as horizontal and vertical.
    Small clicking offsets are allowed; substantial rotation is rejected rather
    than silently transformed with an incorrect model. Log values must be the
    actual positive values printed on the ticks, not their logarithms.
    """
    if not isinstance(cal, dict):
        raise ValueError("请先完成横轴和纵轴的四点标定。")
    result: dict[str, Any] = {}
    warnings: list[str] = []
    for key, label, main_coordinate in (("x", "横轴", 0), ("y", "纵轴", 1)):
        axis = cal.get(key)
        if not isinstance(axis, dict):
            raise ValueError(f"缺少{label}标定，请分别选择两个刻度并填写刻度值。")
        missing = [name for name in ("p1", "p2", "v1", "v2") if name not in axis]
        if missing:
            raise ValueError(f"{label}标定尚未完成：缺少 {', '.join(missing)}。")
        p1 = _point(axis["p1"], f"{label}第一个点")
        p2 = _point(axis["p2"], f"{label}第二个点")
        v1 = _finite(axis["v1"], f"{label}第一个刻度值")
        v2 = _finite(axis["v2"], f"{label}第二个刻度值")
        scale = str(axis.get("scale", "linear")).strip().lower()
        if scale not in ("linear", "log10"):
            raise ValueError(f"{label}刻度类型只能选择 linear（线性）或 log10（对数）。")
        if v1 == v2:
            raise ValueError(f"{label}两个刻度值不能相同，请选择两个不同的刻度。")
        if scale == "log10" and (v1 <= 0 or v2 <= 0):
            raise ValueError(f"{label}使用对数刻度，两个刻度值都必须大于 0。")
        main_delta = abs(p2[main_coordinate] - p1[main_coordinate])
        cross_delta = abs(p2[1 - main_coordinate] - p1[1 - main_coordinate])
        if main_delta < 1e-9:
            direction = "横向" if key == "x" else "纵向"
            raise ValueError(f"{label}两个点的{direction}位置相同，无法换算。请重新选择刻度位置。")
        if cross_delta > 0.15 * main_delta:
            raise ValueError(f"{label}标定点偏斜明显。第一版要求图中横轴水平、纵轴垂直；请重新选点或先校正图片。")
        if cross_delta > max(1.0, 0.02 * main_delta):
            warnings.append(f"{label}两个标定点有轻微偏移；请核对是否落在同一条坐标轴上。")
        if main_delta < 20:
            warnings.append(f"{label}两个标定点仅相距 {main_delta:g} 像素，建议选择间隔更远的刻度以减小误差。")
        result[key] = {
            "p1": p1, "p2": p2, "v1": v1, "v2": v2, "scale": scale,
            "name": str(axis.get("name", "")), "unit": str(axis.get("unit", "")),
        }
    result["warnings"] = warnings
    return result


def _axis_value(pixel: float, axis: dict, coordinate: int) -> float:
    a, b = axis["p1"][coordinate], axis["p2"][coordinate]
    t = (pixel - a) / (b - a)
    if axis["scale"] == "log10":
        v1, v2 = math.log10(axis["v1"]), math.log10(axis["v2"])
        try:
            value = 10.0 ** (v1 + t * (v2 - v1))
        except OverflowError as exc:
            raise ValueError("取点结果超出可计算范围，请检查对数轴刻度和选点位置。") from exc
        if value <= 0:
            raise ValueError("对数轴取点结果过小，无法可靠表示，请检查标定和选点位置。")
    else:
        value = axis["v1"] + t * (axis["v2"] - axis["v1"])
    if not math.isfinite(value):
        raise ValueError("取点结果不是有限数值，请检查刻度和选点位置。")
    return value


def pixel_to_data(px: float, py: float, cal: dict) -> dict:
    """Convert an original-image point into data coordinates.

    Pixel resolution is the data span from pixel - 0.5 to pixel + 0.5. It is
    a local sampling scale, NOT a complete measurement uncertainty estimate.
    Points outside the calibration span are mathematically extrapolated; the
    caller should keep the selected point within the actual plot area.
    """
    px = _finite(px, "取点横坐标")
    py = _finite(py, "取点纵坐标")
    normalized = validate_calibration(cal)
    out: dict[str, float] = {}
    for key, pixel, coordinate in (("x", px, 0), ("y", py, 1)):
        axis = normalized[key]
        out[key] = _axis_value(pixel, axis, coordinate)
        out[f"{key}_pixel_resolution"] = abs(
            _axis_value(pixel + 0.5, axis, coordinate)
            - _axis_value(pixel - 0.5, axis, coordinate)
        )
    return out


def _positive_integer(value: Any, label: str) -> int:
    number = _finite(value, label)
    if number < 1 or not number.is_integer():
        raise ValueError(f"{label}必须是大于或等于 1 的整数。")
    return int(number)


def trace_color_curve(
    image: Image.Image,
    roi: list[int],
    color: list[int],
    tolerance: float = 45,
    step: int = 5,
    max_thickness: int = 20,
) -> dict:
    """Conservatively trace a single coloured curve inside a selected rectangle.

    ``roi`` is [left, top, right, bottom], right/bottom excluded. At each sampled
    column, precisely one contiguous coloured run must be found. A column with
    separated runs or excessive thickness is skipped; missing points are never
    filled in. Returned points remain in original-image pixel coordinates.

    Restrict the ROI to the plot, excluding the legend and unrelated series.
    This helper is not intended for scatter plots, bars, grey/black curves,
    multiple curves of the same colour, or non-functional/vertical curves.
    """
    if not isinstance(image, Image.Image):
        raise ValueError("请先打开一张有效图片。")
    if not isinstance(roi, (list, tuple, np.ndarray)) or len(roi) != 4:
        raise ValueError("请框选绘图区，区域需要左、上、右、下四个像素坐标。")
    raw = [_finite(value, "绘图区边界") for value in roi]
    if raw[2] <= raw[0] or raw[3] <= raw[1]:
        raise ValueError("绘图区为空或方向相反，请从左上角向右下角重新框选。")
    left = max(0, math.floor(raw[0]))
    top = max(0, math.floor(raw[1]))
    right = min(image.width, math.ceil(raw[2]))
    bottom = min(image.height, math.ceil(raw[3]))
    if right <= left or bottom <= top:
        raise ValueError("框选区域没有覆盖图片，请在图片内重新选择绘图区。")
    if not isinstance(color, (list, tuple, np.ndarray)) or len(color) != 3:
        raise ValueError("目标颜色需要 R、G、B 三个通道，请在目标曲线上重新吸取颜色。")
    rgb = [_finite(value, "颜色通道") for value in color]
    if any(value < 0 or value > 255 for value in rgb):
        raise ValueError("颜色通道必须在 0 到 255 之间。")
    if max(rgb) - min(rgb) < 25:
        raise ValueError("所选颜色接近白色、灰色或黑色，容易与背景、文字和坐标轴混淆；第一版请改用人工取点，或选择颜色更明显的曲线。")
    tolerance = _finite(tolerance, "颜色容差")
    if tolerance <= 0 or tolerance > 150:
        raise ValueError("颜色容差应大于 0 且不超过 150，建议先使用 45。")
    step = _positive_integer(step, "取样间隔")
    max_thickness = _positive_integer(max_thickness, "最大线宽")
    effective_step = max(step, math.ceil((right - left) / 1000))
    warnings: list[str] = []
    if raw[0] < 0 or raw[1] < 0 or raw[2] > image.width or raw[3] > image.height:
        warnings.append("绘图区部分超出图片，已只处理图片内的区域。")
    if effective_step != step:
        warnings.append(f"绘图区较宽，取样间隔已调整为 {effective_step} 像素，最多生成 1000 个候选点。")
    if tolerance > 80:
        warnings.append("颜色容差较大，可能混入其他曲线；请仔细核对原图中的取点位置。")
    crop = image.crop((left, top, right, bottom))
    if "A" in crop.getbands() or crop.mode == "P" and "transparency" in crop.info:
        rgba = crop.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        crop = Image.alpha_composite(white, rgba).convert("RGB")
    else:
        crop = crop.convert("RGB")
    pixels = np.asarray(crop, dtype=np.float32)
    target = np.asarray(rgb, dtype=np.float32)
    points: list[dict[str, Any]] = []
    missing: list[int] = []
    ambiguous: list[int] = []
    too_thick: list[int] = []
    columns = list(range(0, right - left, effective_step))
    for local_x in columns:
        diff = pixels[:, local_x, :] - target
        rows = np.flatnonzero(np.sum(diff * diff, axis=1) <= tolerance * tolerance)
        px = left + local_x
        if not len(rows):
            missing.append(px)
            continue
        if np.any(np.diff(rows) > 1):
            ambiguous.append(px)
            continue
        if len(rows) > max_thickness:
            too_thick.append(px)
            continue
        points.append({"px": px, "py": top + float((int(rows[0]) + int(rows[-1])) / 2), "method": "color_trace"})
    if missing:
        warnings.append(f"{len(missing)} 个取样列未找到目标颜色，已跳过，未补点。")
    if ambiguous:
        warnings.append(f"{len(ambiguous)} 个取样列出现多个分离的同色段，可能存在同色曲线、标记或图例，已跳过。")
    if too_thick:
        warnings.append(f"{len(too_thick)} 个取样列的同色段超过最大线宽，可能是竖线、误差棒或重叠区域，已跳过。")
    if len(points) < 3:
        warnings.append("可恢复的候选点少于 3 个；请检查绘图区、目标颜色和线宽，必要时改用人工取点。")
    warnings.append("这些点是根据图片颜色恢复的候选位置；请叠加原图核对，不能当作作者提供的原始数据。")
    return {
        "points": points,
        "warnings": warnings,
        "stats": {
            "roi": [left, top, right, bottom],
            "color": rgb,
            "tolerance": tolerance,
            "requested_step": step,
            "effective_step": effective_step,
            "sampled_columns": len(columns),
            "returned_points": len(points),
            "missing_columns": len(missing),
            "ambiguous_columns": len(ambiguous),
            "too_thick_columns": len(too_thick),
            "missing_column_positions": missing,
            "ambiguous_column_positions": ambiguous,
            "too_thick_column_positions": too_thick,
            "retained_fraction": len(points) / len(columns) if columns else 0.0,
        },
    }

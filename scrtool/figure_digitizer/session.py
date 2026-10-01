"""Local, traceable scientific figure digitizing sessions. No network calls."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
from .digitize import pixel_to_data, validate_calibration
from .sources import render_source

SCHEMA_VERSION = "figure-digitizer/1.0"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def save_session(session):
    session["updated_at"] = _now()
    run_dir = Path(session["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    destination = run_dir / "session.json"
    _json_write(destination, session)
    return destination


def new_session(source_path, page=1, output_root=None):
    image, metadata = render_source(source_path, page=page)
    root = Path(output_root) if output_root else Path(__file__).resolve().parents[2] / "outputs" / "figure_sessions"
    root.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = (root / name).resolve()
    run_dir.mkdir()
    image_path = run_dir / "source.png"
    image.save(image_path)
    session = {
        "schema_version": SCHEMA_VERSION, "session_id": name,
        "created_at": _now(), "run_dir": str(run_dir),
        "image_path": str(image_path), "image_file": "source.png",
        "image_sha256": _hash(image_path), "source_metadata": metadata,
        "calibration": None, "calibration_id": None, "calibration_history": [],
        "points": [], "roi": None, "figure_label": "", "chart_type": "xy",
        "notes": "", "exports": [],
        "disclosure": "本工具恢复的是图像近似读数，不是作者原始实验数据。pixel_resolution 仅表示一个像素跨度对应的数据分辨率，不是置信区间或实验误差。",
    }
    save_session(session)
    return session, image


def load_session(path_file_or_directory):
    path = Path(path_file_or_directory)
    if path.is_dir():
        path = path / "session.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    if session.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("该文件不是本工具支持的读数记录。")
    folder = path.resolve().parent
    # Only use the saved image inside this session, including when moved.
    image_file = Path(session.get("image_file", "source.png"))
    if image_file.name != str(image_file):
        raise ValueError("读数记录中的图片文件名无效。")
    image_path = folder / image_file
    if _hash(image_path) != session.get("image_sha256"):
        raise ValueError("保存的原图已发生变化，无法确认读数对应的图像。请重新导入。")
    with Image.open(image_path) as original:
        image = original.convert("RGB").copy()
    session["run_dir"] = str(folder)
    session["image_path"] = str(image_path)
    return session, image


def _invalidate_review(session):
    session["reviewed"] = False
    for point in session["points"]:
        point["review_status"] = "unreviewed"


def set_calibration(session, cal):
    normalized = validate_calibration(cal)
    # Prepare everything before mutating the session.
    new_points = copy.deepcopy(session["points"])
    cal_id = uuid.uuid4().hex[:12]
    for point in new_points:
        point.update(pixel_to_data(point["px"], point["py"], normalized))
        point["calibration_id"] = cal_id
        point["review_status"] = "unreviewed"
    session["calibration"] = normalized
    session["calibration_id"] = cal_id
    session["calibration_history"].append({"id": cal_id, "saved_at": _now(), "calibration": copy.deepcopy(normalized)})
    session["points"] = new_points
    _invalidate_review(session)
    save_session(session)
    return session


def add_points(session, points, series_label, sample_label="", category="", method="manual"):
    if not session.get("calibration"):
        raise ValueError("请先标定坐标轴，再读取数据。")
    with Image.open(session["image_path"]) as img:
        width, height = img.size
    pending = []
    for item in points:
        px, py = float(item["px"]), float(item["py"])
        if not all(math.isfinite(v) for v in (px, py)) or not (0 <= px < width and 0 <= py < height):
            raise ValueError("读数点必须位于图片内。")
        if session.get("roi"):
            left, top, right, bottom = session["roi"]
            if not (min(left,right) <= px <= max(left,right) and min(top,bottom) <= py <= max(top,bottom)):
                raise ValueError("读数点在已选绘图区之外，请核对是否点到了其他子图；必要时重新框选绘图区。")
        point = {
            "point_id": uuid.uuid4().hex[:12], "px": px, "py": py,
            "series_label": str(series_label), "sample_label": str(sample_label),
            "category": str(item.get("category", category)),
            "method": str(item.get("method", method)), "review_status": "unreviewed",
            "calibration_id": session["calibration_id"], "added_at": _now(),
            "trace_id": str(item.get("trace_id", "")),
        }
        point.update(pixel_to_data(px, py, session["calibration"]))
        if not all(math.isfinite(point[key]) for key in ("x", "y", "x_pixel_resolution", "y_pixel_resolution")):
            raise ValueError("坐标换算结果超出范围，请检查坐标轴标定。")
        pending.append(point)
    _invalidate_review(session)
    session["points"].extend(pending)
    save_session(session)
    return session


def delete_last_point(session):
    if session["points"]:
        session["points"].pop()
        _invalidate_review(session)
        save_session(session)
    return session


def _safe_cell(value):
    # Spreadsheet formulas must never be executable when a user opens CSV.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _warnings(session):
    warnings = []
    if not session.get("roi"):
        warnings.append("尚未框选绘图区；请逐点核对读数是否来自同一子图。")
    else:
        left, top, right, bottom = session["roi"]
        if any(not (min(left,right) <= p["px"] <= max(left,right) and min(top,bottom) <= p["py"] <= max(top,bottom)) for p in session["points"]):
            warnings.append("部分既有读数点落在当前绘图区之外，需要重新核对。")
    if not session.get("figure_label", "").strip():
        warnings.append("尚未填写图号/子图号。")
    axes = ("y",) if session.get("chart_type") == "bar" else ("x", "y")
    for axis in axes:
        definition = session["calibration"][axis]
        if not definition.get("name"):
            warnings.append(f"{axis.upper()} 轴物理量名称为空。")
        if not definition.get("unit"):
            warnings.append(f"{axis.upper()} 轴单位为空；无量纲请明确填写“无量纲”。")
    if any(not p["sample_label"].strip() for p in session["points"]):
        warnings.append("部分读数尚未关联样品标识。")
    if any(not p["series_label"].strip() or "待确认" in p["series_label"] for p in session["points"]):
        warnings.append("部分曲线名称尚待确认。")
    if session.get("chart_type") == "bar" and any(not p["category"].strip() for p in session["points"]):
        warnings.append("部分柱尚未填写类别。")
    if any(p["review_status"] != "user_reviewed" for p in session["points"]):
        warnings.append("存在尚未人工核对的读数。")
    warnings.append("图中读数仍需与论文的样品、反应条件和正文/表格关联；本工具不自动判定可用于机器学习。")
    return warnings


def export_session(session, metadata=None):
    if not session.get("calibration") or not session.get("points"):
        raise ValueError("请完成标定并添加至少一个读数点后再导出。")
    if _hash(session["image_path"]) != session["image_sha256"]:
        raise ValueError("保存的原图已发生变化，请重新导入后读数，不能使用旧标定导出。")
    metadata = metadata or {}
    for key in ("figure_label", "notes", "roi", "chart_type", "doi"):
        if key in metadata:
            session[key] = metadata[key]
    if session.get("chart_type") not in ("xy", "bar"):
        raise ValueError("不支持的图表类型。")
    reviewed = bool(metadata.get("reviewed", False))
    session["reviewed"] = reviewed
    for point in session["points"]:
        point["review_status"] = "user_reviewed" if reviewed else "unreviewed"
    session["warnings"] = _warnings(session)
    root = Path(session["run_dir"])
    # New snapshots preserve previously exported evidence, even after recalibration.
    export_dir = root / ("export_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4])
    export_dir.mkdir()
    cal, source = session["calibration"], session["source_metadata"]
    columns = ["point_id", "doi", "source_file", "source_sha256", "page", "figure_label", "chart_type",
               "series_label", "sample_label", "category", "x_name", "x_unit", "x_value", "y_name", "y_unit", "y_value",
               "pixel_x", "pixel_y", "x_pixel_resolution", "y_pixel_resolution", "x_scale", "y_scale",
               "method", "trace_id", "review_status", "calibration_id", "value_origin", "ml_ready", "notes"]
    with (export_dir / "读数数据.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for p in session["points"]:
            bar = session["chart_type"] == "bar"
            row = {
                "point_id": p["point_id"], "doi": session.get("doi", ""),
                "source_file": Path(source["source_path"]).name,
                "source_sha256": source["source_sha256"], "page": source.get("page", ""),
                "figure_label": session["figure_label"], "chart_type": session["chart_type"],
                "series_label": p["series_label"], "sample_label": p["sample_label"], "category": p["category"],
                "x_name": "" if bar else cal["x"]["name"], "x_unit": "" if bar else cal["x"]["unit"],
                "x_value": "" if bar else p["x"], "y_name": cal["y"]["name"], "y_unit": cal["y"]["unit"], "y_value": p["y"],
                "pixel_x": p["px"], "pixel_y": p["py"],
                "x_pixel_resolution": "" if bar else p["x_pixel_resolution"], "y_pixel_resolution": p["y_pixel_resolution"],
                "x_scale": "" if bar else cal["x"]["scale"], "y_scale": cal["y"]["scale"],
                "method": p["method"], "trace_id": p.get("trace_id", ""), "review_status": p["review_status"], "calibration_id": p["calibration_id"],
                "value_origin": "image_digitized_approximate", "ml_ready": "requires_scientific_context_review",
                "notes": session.get("notes", ""),
            }
            writer.writerow({k: _safe_cell(v) for k, v in row.items()})
    with Image.open(session["image_path"]) as original:
        evidence = original.convert("RGB").copy()
    draw = ImageDraw.Draw(evidence)
    radius = max(3, round(max(evidence.size) / 500))
    for number, p in enumerate(session["points"], 1):
        x, y = p["px"], p["py"]
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), outline="magenta", width=2)
        if len(session["points"]) <= 80:
            draw.text((x+radius+2, y-radius), str(number), fill="magenta")
    for axis in ("x", "y"):
        for key in ("p1", "p2"):
            x, y = cal[axis][key]
            draw.line((x-8,y,x+8,y), fill="blue", width=2)
            draw.line((x,y-8,x,y+8), fill="blue", width=2)
            draw.text((x+9,y+2), axis.upper()+key[-1], fill="blue")
    if session.get("roi"):
        left, top, right, bottom = session["roi"]
        draw.rectangle((min(left,right),min(top,bottom),max(left,right),max(top,bottom)), outline="orange", width=2)
    evidence.save(export_dir / "原图与读数标记.png")
    evidence.close()
    exported = {"at": _now(), "directory": export_dir.name, "point_count": len(session["points"]), "reviewed": reviewed}
    session["exports"].append(exported)
    session["last_export_dir"] = str(export_dir)
    save_session(session)
    snapshot = copy.deepcopy(session)
    snapshot["image_file"] = "../source.png"
    _json_write(export_dir / "读数与溯源.json", snapshot)
    description = "\n".join([
        "论文图片读数结果（近似值）", "",
        "读数数据.csv：可用 Excel 打开。原图与读数标记.png：品红色为读数点，蓝色为标定点，橙色为选区。",
        "读数与溯源.json：本次导出时的完整标定、像素位置、来源及核对状态。",
        "重新打开编辑请载入上一级目录的 session.json；每次导出均保留独立快照。",
        "", session["disclosure"],
        "颜色辅助取点是对图中像素的采样，多个采样点不等于多个独立实验。",
        "柱图的 x_value 留空；类别存于 category，pixel_x 仅是图上位置。",
        "未读取或推算误差条、实验重复次数或作者未给出的数值。", "",
        "本次检查提示：", *["- " + warning for warning in session["warnings"]],
    ])
    (export_dir / "请先阅读.txt").write_text(description, encoding="utf-8-sig")
    return root

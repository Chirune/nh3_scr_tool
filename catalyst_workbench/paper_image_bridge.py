"""Read-only import of current, attributable figure exports into one paper.

``usable`` means that this image reading was checked and its source is still
current. It never means that the row is an independent experiment or is ready
for machine learning. Chemical sample/condition matching belongs to the paper
workbench. This module never updates a batch, session, export, or review flag.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image
from digitizer.digitize import pixel_to_data, validate_calibration


_BATCH_SCHEMA = "screened-pdf-figures/1.0"
_SESSION_SCHEMA = "figure-digitizer/1.0"
_CURRENT_FIELDS = (
    "points", "calibration", "calibration_id", "doi", "figure_label",
    "chart_type", "notes", "roi", "source_metadata", "reviewed",
    "session_id", "image_sha256",
)
_APPROXIMATION = (
    "图像近似读数，保留像素位置供核对；不是作者原始实验数据。"
    "像素分辨率不是实验误差；曲线采样点不等于独立实验。"
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("记录不是有效的对象。")
    return result


def _number(value) -> float:
    if isinstance(value, bool):
        raise ValueError("读数中存在非数值。")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("读数中存在无穷大或无效数值。")
    return number


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _axis_name(value) -> str:
    name = _text(value)
    generic = {"x", "y", "x axis", "y axis", "横轴", "纵轴", "横轴指标",
               "纵轴指标", "待核对", "未识别", "unknown"}
    return "" if name.casefold() in generic else name


def _confirmed_label(value) -> bool:
    text = _text(value)
    return bool(text) and not any(marker in text for marker in ("待确认", "待核对", "未命名", "未识别"))


def _validate_readings(snapshot, image_size):
    """Reject stale/corrupt values rather than trusting matching file hashes.

    File hashes preserve the source chain, while recomputing from pixels checks
    the scientific contents of an export. No values or review flags are changed.
    """
    calibration = validate_calibration(snapshot.get("calibration"))
    calibration_id = snapshot.get("calibration_id")
    if not calibration_id:
        raise ValueError("读数缺少标定版本，请重新标定并导出。")
    roi = snapshot.get("roi")
    if roi is not None:
        if not isinstance(roi, (list, tuple)) or len(roi) != 4:
            raise ValueError("读数绘图区记录无效，请重新框选后导出。")
        left, top, right, bottom = map(_number, roi)
        left, right = sorted((left, right))
        top, bottom = sorted((top, bottom))
        if left == right or top == bottom:
            raise ValueError("读数绘图区为空，请重新框选后导出。")
    for point in snapshot["points"]:
        if point.get("calibration_id") != calibration_id:
            raise ValueError("读数的标定版本不一致，请重新标定并导出。")
        px, py = _number(point.get("px")), _number(point.get("py"))
        if not (0 <= px < image_size[0] and 0 <= py < image_size[1]):
            raise ValueError("读数点位于原图之外，请重新核对后导出。")
        if roi is not None and not (left <= px <= right and top <= py <= bottom):
            raise ValueError("读数点位于当前绘图区之外，请核对是否来自其他子图。")
        expected = pixel_to_data(px, py, calibration)
        for field, value in expected.items():
            if not math.isclose(_number(point.get(field)), value, rel_tol=1e-9, abs_tol=0.0):
                raise ValueError("导出数值或像素分辨率与保存的像素与标定不一致，请重新标定并导出。")


def _file(value, root: Path, previous: Path) -> Path:
    if not value:
        raise ValueError("来源文件路径缺失。")
    path = Path(value)
    if path.is_absolute():
        try:
            path = root / path.relative_to(previous)
        except ValueError:
            pass
    else:
        path = root / path
    return path.resolve()


def _check_hash(path: Path, expected, label: str, cache: dict) -> str:
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"{label}缺少来源校验记录，请重新核对来源。")
    key = str(path)
    if key not in cache:
        cache[key] = _digest(path)
    if cache[key] != expected:
        raise ValueError(f"{label}已变化，旧读数不进入本篇数据；请重新读取并审核。")
    return cache[key]


def _validate_source(batch, paper, figure, session, snapshot, root, previous, cache):
    """Verify the entire current PDF -> page/crop -> session image chain."""
    source = snapshot.get("source_metadata") or {}
    context = source.get("figure_context") or {}
    if (context.get("figure_id") != figure.get("figure_id")
            or context.get("figure_revision") != figure.get("revision")
            or context.get("batch_id") != batch.get("batch_id")):
        raise ValueError("导出来源与本次批次、图号或图框版本不匹配。")
    for candidate in (figure.get("record_id"), context.get("record_id")):
        if paper.get("record_id") and candidate != paper["record_id"]:
            raise ValueError("读数所关联的论文记录不匹配。")
    if source.get("page") != figure.get("page"):
        raise ValueError("读数所关联的论文页码不匹配。")
    source_hash = figure.get("source_sha256")
    if source.get("source_sha256") != source_hash or paper.get("source_sha256") != source_hash:
        raise ValueError("读数与当前论文 PDF 的校验记录不匹配。")
    source_path = _file(figure.get("local_pdf"), root, previous)
    _check_hash(source_path, source_hash, "来源 PDF", cache)
    for candidate in (paper.get("local_pdf"), source.get("source_path")):
        _check_hash(_file(candidate, root, previous), source_hash, "来源 PDF", cache)
    for path_key, hash_key, label in (
        ("page_image_path", "page_image_sha256", "整页原图"),
        ("crop_path", "crop_sha256", "候选裁图"),
    ):
        if context.get(hash_key) != figure.get(hash_key):
            raise ValueError(f"{label}与导出时的来源记录不匹配。")
        _check_hash(_file(figure.get(path_key), root, previous), figure.get(hash_key), label, cache)
    if session.get("image_sha256") != snapshot.get("image_sha256"):
        raise ValueError("当前读图原图与导出时不同。")
    return source, context, source_path


def _point_item(point, snapshot, figure, paper_id, path, export_dir, source,
                context, source_path, snapshot_hash):
    chart = snapshot.get("chart_type", "xy")
    axis = "x" if chart == "bar_horizontal" else "y"
    calibration = snapshot.get("calibration") or {}
    x_axis, y_axis = calibration.get("x") or {}, calibration.get("y") or {}
    numeric = x_axis if axis == "x" else y_axis
    metric, unit = _axis_name(numeric.get("name")), _text(numeric.get("unit"))
    value, px, py = _number(point.get(axis)), _number(point.get("px")), _number(point.get("py"))
    warnings = [_APPROXIMATION]
    checked = snapshot.get("reviewed") is True and point.get("review_status") == "user_reviewed"
    if not checked:
        warnings.append("读图结果尚未逐点人工核对；请在读图窗口核验后重新导出。")
    if figure.get("review_status") != "keep":
        warnings.append("候选图尚未标记保留，请核对图框与图注。")
    context_complete = bool(metric and unit)
    if not metric:
        warnings.append("数值轴的物理量名称未确认，暂不能使用。")
    if not unit:
        warnings.append("数值单位未确认；无量纲也需明确标注。")
    conditions = {}
    extras = {}
    if chart == "xy":
        x_name, x_unit, x_value = _axis_name(x_axis.get("name")), _text(x_axis.get("unit")), _number(point.get("x"))
        conditions = {"x_name": x_name, "x_unit": x_unit, "x_value": x_value}
        extras.update(conditions)
        if not x_name:
            context_complete = False
            warnings.append("横轴的物理量名称未确认，暂不能使用；未将其推断为反应温度。")
        if not x_unit:
            context_complete = False
            warnings.append("横轴单位未确认。")
    elif chart in ("bar", "bar_horizontal") and not _confirmed_label(point.get("category")):
        context_complete = False
        warnings.append("柱图类别名称未确认，像素位置不作为科学变量。")
    if not _confirmed_label(point.get("series_label")):
        context_complete = False
        warnings.append("系列名称尚未确认，请核对图例及系列对应关系。")
    sample = _text(point.get("sample_label"))
    if not sample:
        warnings.append("尚未关联论文中的样品标识。")
    else:
        warnings.append("样品名称作为关联候选保留；仍需核对正文中的配方和条件。")
    warnings.append("读图核验不等于可直接建模；本篇汇总仍需核对样品、单位、条件和预测任务。")
    source_ref = {
        "figure_id": figure["figure_id"], "figure_revision": figure["revision"],
        "figure_label": snapshot.get("figure_label", figure.get("figure_label", "")),
        "batch_id": context.get("batch_id"), "paper_id": paper_id,
        "session_id": snapshot.get("session_id"), "session_path": str(path),
        "export_dir": str(export_dir), "export_snapshot": str(export_dir / "读数与溯源.json"),
        "export_snapshot_sha256": snapshot_hash, "point_id": point.get("point_id", ""),
        "source_pdf": str(source_path), "source_sha256": source["source_sha256"],
        "source_pdf_sha256": source["source_sha256"], "image_path": str(path.parent / "source.png"),
        "image_sha256": snapshot["image_sha256"], "crop_sha256": figure["crop_sha256"],
        "page_image_sha256": figure["page_image_sha256"], "page": source.get("page"),
        "figure_bbox": figure.get("bbox"), "bbox_coordinate_system": context.get("bbox_coordinate_system"),
        "point_pixels": [px, py], "crop_to_page": context.get("crop_to_page"),
        "automatic_subfigure": context.get("automatic_subfigure"),
        "calibration_id": point.get("calibration_id"), "method": point.get("method", ""),
        "pixel_resolution": point.get(f"{axis}_pixel_resolution"),
        "csv_sha256": snapshot["exports"][-1]["csv_sha256"],
    }
    if point.get("bar_bbox") is not None:
        source_ref["bar_bbox_pixels"] = point["bar_bbox"]
    transform = context.get("crop_to_page")
    if isinstance(transform, dict):
        source_ref["page_pixel_x"] = _number(transform.get("offset_x", 0)) + px * _number(transform.get("scale_x", 1))
        source_ref["page_pixel_y"] = _number(transform.get("offset_y", 0)) + py * _number(transform.get("scale_y", 1))
    point_key = point.get("point_id") or hashlib.sha256(json.dumps(point, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    identity = [snapshot.get("session_id"), snapshot_hash, point_key]
    evidence_id = "img_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    series = _text(point.get("series_label"))
    label = _text(snapshot.get("figure_label")) or _text(figure.get("figure_label")) or "图号待确认"
    return {
        "evidence_id": evidence_id, "branch": "image", "paper_id": paper_id,
        "page": source.get("page"), "quote": f"{label} · {series or '系列待确认'} · 像素点 ({px:g}, {py:g})",
        "metric": metric, "value": value, "unit": unit, "operator": "eq", "value_high": None,
        "sample_label": sample, "sample_label_status": "candidate", "series_label": series,
        "category": _text(point.get("category")), "conditions": conditions, "kind": "absolute",
        "review_status": "reviewed" if checked else "unreviewed",
        "usable": bool(checked and context_complete and figure.get("review_status") == "keep"),
        "source_ref": source_ref, "warnings": warnings,
        "value_origin": "image_curve_samples_approximate" if "curve_sample" in _text(point.get("method")) or _text(point.get("method")) == "color_trace" else "image_digitized_approximate",
        "ml_ready": False,
        "chart_type": chart, **extras,
    }


def read_image_evidence(batch_path: str | Path, paper_id: str) -> dict:
    """Return current figure evidence and waiting reasons for exactly one paper.

    Invalid/missing/stale exports are skipped, not silently replaced by older
    snapshots. Unreviewed valid exports remain visible as unusable candidates.
    Repeated references to one session are read only once. Different sessions
    or figures are never averaged or treated as duplicate experiments here.
    """
    result = {"items": [], "warnings": []}
    try:
        file = Path(batch_path)
        if file.is_dir():
            file = file / "batch.json"
        file = file.resolve()
        batch = _json(file)
        if batch.get("schema_version") != _BATCH_SCHEMA:
            raise ValueError("请选择本工具的图片批次 batch.json。")
        root, previous = file.parent, Path(batch.get("run_dir") or file.parent)
        papers = [p for p in batch.get("papers", []) if p.get("paper_id") == paper_id]
        if len(papers) != 1:
            raise ValueError("当前图片批次未找到唯一对应的论文，请从该论文重新进入读图。")
        paper = papers[0]
        figures = {f["figure_id"]: f for f in batch.get("figures", []) if f.get("paper_id") == paper_id}
        grouped = {}
        for reference in batch.get("reading_sessions", []):
            if reference.get("figure_id") not in figures:
                continue
            path = _file(reference.get("session_path"), root, previous)
            grouped.setdefault(path, []).append(reference)
        if not grouped:
            result["warnings"].append("本篇尚无关联的读图项目；请审核图片、生成数值并在读图窗口核验导出。")
        cache, seen = {}, set()
        for path, references in grouped.items():
            prefix = path.parent.name + "："
            try:
                if any(r.get("superseded_by") for r in references):
                    raise ValueError("已有更新的读数项目；旧读数不重复导入。")
                ref = references[0]
                if any((r.get("figure_id"), r.get("figure_revision")) != (ref.get("figure_id"), ref.get("figure_revision")) for r in references):
                    raise ValueError("同一读图项目的图号或版本引用冲突。")
                figure = figures[ref["figure_id"]]
                if figure.get("review_status") == "exclude":
                    raise ValueError("图片已排除，旧读数不进入本篇数据。")
                if ref.get("figure_revision") != figure.get("revision"):
                    raise ValueError("图框、图号或图型已经修改；旧版本读数不导入。")
                session = _json(path)
                if session.get("schema_version") != _SESSION_SCHEMA:
                    raise ValueError("读图项目格式不受支持。")
                if session.get("superseded_by"):
                    raise ValueError("此读图项目已由新项目替代。")
                exports = session.get("exports") or []
                if not exports:
                    raise ValueError("尚无数值导出；请在读图窗口生成、核验后导出。")
                export_dir = (path.parent / exports[-1]["directory"]).resolve()
                if export_dir.parent != path.parent:
                    raise ValueError("导出目录不在当前读图项目中。")
                snapshot_path = export_dir / "读数与溯源.json"
                snapshot = _json(snapshot_path)
                if snapshot.get("schema_version") != _SESSION_SCHEMA or not snapshot.get("exports"):
                    raise ValueError("导出快照格式不完整。")
                if snapshot["exports"][-1] != exports[-1]:
                    raise ValueError("最近导出记录与快照不一致，请重新核验并导出。")
                if any(session.get(key) != snapshot.get(key) for key in _CURRENT_FIELDS):
                    raise ValueError("读数项目存在未导出的修改，旧快照已过期；请核验后重新导出。")
                if snapshot.get("chart_type", "xy") not in ("xy", "bar", "bar_horizontal"):
                    raise ValueError("该图型的读数尚未接入本篇汇总。")
                source, context, source_path = _validate_source(batch, paper, figure, session, snapshot, root, previous, cache)
                _check_hash(path.parent / "source.png", snapshot.get("image_sha256"), "读图原图", cache)
                _check_hash(export_dir / "读数数据.csv", exports[-1].get("csv_sha256"), "导出数值表", cache)
                if not snapshot.get("points"):
                    raise ValueError("本次导出没有数值点。")
                with Image.open(path.parent / "source.png") as original:
                    _validate_readings(snapshot, original.size)
                point_ids = [p.get("point_id") for p in snapshot["points"] if p.get("point_id")]
                if len(point_ids) != len(set(point_ids)):
                    raise ValueError("导出中存在重复点编号，请在读图窗口重新核验。")
                snapshot_hash = _digest(snapshot_path)
                pending = [_point_item(p, snapshot, figure, paper_id, path, export_dir, source, context, source_path, snapshot_hash)
                           for p in snapshot["points"]]
                for item in pending:
                    if item["evidence_id"] not in seen:
                        seen.add(item["evidence_id"])
                        result["items"].append(item)
                if not all(p["usable"] for p in pending):
                    result["warnings"].append(prefix + "已载入图像候选值，其中有尚未完成读图核验、指标、单位或系列类别确认的项目。")
            except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
                result["warnings"].append(prefix + str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["warnings"].append(str(exc))
    return result

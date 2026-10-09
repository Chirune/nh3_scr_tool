"""Automatic, local chart reading with explicit uncertainty and traceable exports."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import uuid

try:
    from .auto_axes import auto_calibrate
    from .auto_curves import extract_curve_series
    from .auto_trace import trace_series
    from .auto_panels import independent_frames, read_panels
    from .native_ocr import recognize_image
    from .axis_ocr import recognize_axis_ticks, replace_tick_observations, recognize_axis_titles
    from .auto_symbols import find_symbol_legend, extract_symbol_series
    from .auto_multishape import find_multishape_legend, extract_multishape_series
    from .auto_bars import bar_geometry, read_bars, magnified_bar_text
    from .chart_catalog import classify_chart
    from .session import load_session, save_session, set_calibration, add_points, export_session
except ImportError:
    from auto_axes import auto_calibrate
    from auto_curves import extract_curve_series
    from auto_trace import trace_series
    from auto_panels import independent_frames, read_panels
    from native_ocr import recognize_image
    from axis_ocr import recognize_axis_ticks, replace_tick_observations, recognize_axis_titles
    from auto_symbols import find_symbol_legend, extract_symbol_series
    from auto_multishape import find_multishape_legend, extract_multishape_series
    from auto_bars import bar_geometry, read_bars, magnified_bar_text
    from chart_catalog import classify_chart
    from session import load_session, save_session, set_calibration, add_points, export_session


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _register_project(session, original_path, register=True):
    context = session.get("source_metadata", {}).get("figure_context", {})
    if not context.get("batch_id"):
        return
    batch_path = Path(original_path).resolve().parent.parent.parent / "batch.json"
    if not batch_path.is_file():
        return
    parent = str(Path(__file__).resolve().parent.parent)
    if parent not in sys.path:
        sys.path.append(parent)
    from pipeline import load_batch, save_batch
    batch = load_batch(batch_path)
    if batch.get("batch_id") != context["batch_id"]:
        raise ValueError("自动读数与图片批次不对应，请重新进入当前图。")
    figure = next((f for f in batch["figures"] if f["figure_id"] == context.get("figure_id")), None)
    if not figure or figure.get("revision") != context.get("figure_revision") or figure.get("review_status") == "exclude":
        raise ValueError("当前图已修改范围或排除，请对新范围重新识别。")
    if not register:
        return
    path = str(Path(session["run_dir"]) / "session.json")
    if not any(ref.get("session_path") == path for ref in batch.get("reading_sessions", [])):
        batch.setdefault("reading_sessions", []).append({"session_path": path,
            "figure_id": context["figure_id"], "figure_revision": context["figure_revision"],
            "created_at": datetime.now(timezone.utc).isoformat(), "mode": "automatic"})
    # Keep older exports for audit, but a repeated automatic reading of the
    # same crop must not double the data in the batch's current summary.
    panel=context.get('automatic_subfigure',{})
    current_panel=panel.get('plot_bbox_in_parent',panel.get('bbox'))
    for ref in batch.get('reading_sessions',[]):
        if ref.get('session_path')==path or ref.get('figure_id')!=context['figure_id'] or ref.get('figure_revision')!=context['figure_revision']:continue
        try:
            previous=json.loads(Path(ref['session_path']).read_text(encoding='utf8'))
            prior_context=previous.get('source_metadata',{}).get('figure_context',{})
            prior_panel=prior_context.get('automatic_subfigure',{})
            same=prior_panel.get('plot_bbox_in_parent',prior_panel.get('bbox'))==current_panel
            automatic=previous.get('automatic_extraction') and previous.get('points') and not previous.get('reviewed') and all(p.get('method','').startswith('auto') and p.get('review_status')!='user_reviewed' for p in previous['points'])
            if same and automatic: ref['superseded_by']=path
        except (OSError,ValueError,KeyError):pass
    save_batch(batch)


def auto_extract_session(session_path, split_panels=True):
    session_path = Path(session_path).resolve()
    original, image = load_session(session_path)
    report_path = Path(original["run_dir"]) / ("自动识别_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4] + ".json")
    report = {"status": "running", "source_session": str(session_path), "count": 0,
              "scope": "Automatically recovered approximate image readings. All values and OCR labels require review; no original experiment data is claimed."}

    def unfinished(message, status="unsupported"):
        report.update({"status": status, "count": 0, "message": message})
        reasons = report.get("axes", {}).get("reason_codes", [])
        next_action = report.get("axes", {}).get("next_action")
        report["reason_codes"] = reasons
        if next_action:
            report["next_action"] = next_action
        _write(report_path, report)
        return {"status": status, "count": 0, "message": message, "reason": message,
                "report_path": str(report_path), "session_path": str(session_path), "reason_codes": reasons,
                "next_action": next_action}

    try:
        _register_project(original, session_path, register=False)
        axes = auto_calibrate(original, image=image, use_local_ocr=False)
        if "source_pdf_identity_changed" in axes.get("reason_codes", []):
            report["axes"] = axes
            return unfinished("源 PDF 已改变，请重新导入并扫描，未生成数值。", "needs_review")
        frames = independent_frames(image)
        if split_panels and len(frames)>1 and axes.get('status')!='refuse_numeric':
            return read_panels(original,image,session_path,frames,auto_extract_session,_register_project)
        if axes.get("status") == "refuse_numeric":
            report["axes"] = axes
            return unfinished("这张图被识别为示意图或非性能数据图，未生成性能数值。" + "；".join(axes.get("reasons", [])))
        ocr = None
        context=original.get('source_metadata',{}).get('figure_context',{})
        geometry=bar_geometry(image,frames)
        bars=read_bars(image,axes.get('visible_text_lines',[]),context.get('caption',''),geometry) if geometry else None
        if (bars and bars.get('status')!='ready') or (not bars and axes.get("status") != "ready"):
            ocr = recognize_image(original["image_path"], include_rotated=True)
            axes = auto_calibrate(original, image=image, extra_text_lines=ocr["lines"])
            if bars:bars=read_bars(image,axes.get('visible_text_lines',[]),context.get('caption',''),geometry)
        if bars:
            if bars.get('status')!='ready':
                enlarged=magnified_bar_text(image);report['bar_magnified_ocr']=enlarged
                original_pdf_lines=[v for v in axes.get('visible_text_lines',[]) if v.get('source','').startswith('pdf')]
                if enlarged.get('lines'):
                    retried=read_bars(image,original_pdf_lines+enlarged['lines'],context.get('caption',''),geometry)
                    if retried.get('status')=='ready':bars=retried
            report['bars']=bars
            if bars.get('status')!='ready':
                report['axes']=bars
                return unfinished('柱图尚未完整识别，未生成数值。'+'；'.join(bars.get('reasons',[])),'needs_review')
            axes=bars
        elif original.get('chart_type') in ('bar','bar_horizontal'):
            return unfinished('未识别到可分离的实心柱体。请核对是否为堆叠、空心、斜线填充或双轴图；本次未生成柱图数值。','needs_review')
        if not bars and axes.get('status') == 'needs_review' and len(frames)==1:
            refined=recognize_axis_ticks(original['image_path'],frames[0])
            report['axis_tile_ocr']=refined
            observations=replace_tick_observations((ocr or {}).get('lines',[]),refined)
            axes=auto_calibrate(original,image=image,extra_text_lines=observations)
            if axes.get('status')=='needs_review':
                wider=recognize_axis_ticks(original['image_path'],frames[0],wide=True)
                report['wide_axis_tile_ocr']=wider
                alternative=auto_calibrate(original,image=image,extra_text_lines=replace_tick_observations((ocr or {}).get('lines',[]),wider))
                if alternative.get('status')=='ready':axes=alternative
        if not bars and axes.get('status')=='ready' and any(
                v.get('fit_method')=='ocr_tick_consensus' for v in axes.get('axis_evidence',{}).values()):
            try:
                focused=recognize_axis_titles(original['image_path'],axes['plot_bbox'],axes.get('ticks',{}))
            except Exception as exc:
                focused={'lines':[],'warning':'轴名称补充识别未完成：'+str(exc)}
            report['axis_title_ocr']=focused
            if focused.get('lines'):
                alternative=auto_calibrate(original,image=image,extra_text_lines=axes['visible_text_lines']+focused['lines'])
                if alternative.get('status')=='ready':axes=alternative
        report["axes"] = axes
        if ocr:
            report["ocr"] = ocr
        if axes.get("status") != "ready":
            if ocr and "insufficient_visible_tick_text" in axes.get("reason_codes", []) and "normalized_profile_without_numeric_y" not in axes.get("reason_codes", []):
                axes["reasons"] = ["本地 OCR 已执行，但识别出的数字不足以建立横、纵轴标定。请核对原图是否提供了数字刻度。"]
            return unfinished("尚不能可靠识别坐标，未生成数值。" + "；".join(axes.get("reasons", [])), "needs_review")
        curves = bars or extract_curve_series(image, axes["plot_bbox"], axes.get("visible_text_lines", []),
                                              calibration=axes["calibration"])
        traced={} if bars else trace_series(image,axes['plot_bbox'],axes.get('visible_text_lines',[]),axes['calibration'])
        # A few inset markers or only one colour must not take precedence over
        # the main plot's labelled continuous strokes or hollow/solid series.
        marker_named=sum(s.get('label_basis')=='visible_legend_text' for s in curves.get('series',[]))
        trace_named=sum(str(s.get('label_basis','')).startswith('visible_legend') for s in traced.get('series',[]))
        if traced.get('series') and (not curves.get('series') or trace_named>marker_named):
            curves=traced
        elif traced.get('skipped_series'):
            # Never let the simpler colour extractor silently merge a colour
            # that the legend explicitly assigns to multiple symbol styles.
            curves=traced
        shared=context.get('shared_symbol_legend')
        local=None if bars else find_symbol_legend(image,[axes['plot_bbox']],axes.get('visible_text_lines',[]))
        symbols=None if bars else local or shared
        if symbols:
            # Once the legend specifies two same-ink shapes, colour-only
            # tracing must not silently merge their two series.
            curves=extract_symbol_series(image,axes['plot_bbox'],symbols,
                local.get('legend_bbox') if local else shared.get('local_legend_bbox'),axes.get('visible_text_lines',[]))
            if shared and not local:
                curves['warnings'].append('系列名称沿用该组合图的共用图例；适用关系仍待核对。')
        multishape=None if bars else find_multishape_legend(image,[axes['plot_bbox']],axes.get('visible_text_lines',[]))
        if multishape:
            curves=extract_multishape_series(image,axes['plot_bbox'],multishape,axes.get('visible_text_lines',[]))
        report["curves"] = curves
        series = [item for item in curves.get("series", []) if item.get("points_px") or item.get("points")]
        if not series:
            return unfinished("坐标已识别，但没有可靠分离数据系列，未生成数值。" + "；".join(curves.get("warnings", [])), "needs_review")

        # Existing manual/automatic points and exported snapshots stay in their project.
        working = copy.deepcopy(original)
        isolated = bool(original.get("points") or original.get("exports"))
        if isolated:
            root = Path(original["run_dir"]).parent / ("auto_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
            root.mkdir()
            shutil.copy2(original["image_path"], root / "source.png")
            working.update({"session_id": root.name, "run_dir": str(root), "image_path": str(root / "source.png"),
                            "points": [], "exports": [], "calibration": None, "calibration_id": None, "calibration_history": []})
        working["chart_type"] = axes.get('chart_type','xy')
        working['chart_profile']=classify_chart(context.get('caption',''),' '.join(axes['calibration'][a].get('name','') for a in ('x','y')))
        working["roi"] = list(axes["plot_bbox"])
        working["reviewed"] = False
        working["automatic_extraction"] = {"report_path": str(report_path), "axes": axes,
            "series_evidence": [{k: v for k, v in item.items() if k not in ("points", "points_px")} for item in series],
            "isolated_from_previous_readings": isolated}
        working["notes"] = original.get("notes", "") + "\n程序自动识别坐标、图例和图像点位；候选数值及OCR标签待核对。"
        set_calibration(working, axes["calibration"])
        for item in series:
            label = str(item.get("label") or item.get("name") or "未命名系列（待核对）")
            points = item.get("points_px") or item.get("points")
            sample='' if bars else context.get('automatic_subfigure',{}).get('sample_label') or label
            add_points(working, points, label, sample_label=sample,
                       method=item.get("method", "auto_detected_marker"))
        warnings = list(dict.fromkeys(list(axes.get("axis_metadata_warnings", [])) + list(curves.get("warnings", []))))
        if context.get('automatic_subfigure',{}).get('sample_label_requires_review'):
            warnings.append('样品名称取自子图标题 OCR，字符 I/1、O/0 等可能混淆；请对照原图核对。')
        working["automatic_extraction"]["warnings"] = warnings
        _register_project(working, session_path, register=False)
        export_session(working, metadata={"reviewed": False})
        final_path = save_session(working)
        _register_project(working, session_path)
        export_dir = Path(working["run_dir"]) / working["exports"][-1]["directory"]
        report.update({"status": "partial_success" if curves.get('skipped_series') or curves.get('requires_category_review') else "success", "count": len(working["points"]),
                       "session_path": str(final_path), "export_dir": str(export_dir),
                       "series_count": len(series), "requires_review": True, "warnings": warnings,
                       "message": "已读取柱体数值；部分类别名称未识别，需要补齐类别与样品对应关系。" if curves.get('requires_category_review') else
                           "已按图例点形导出可分离的数据点；部分重叠或点形不清的区域未输出，请对照核对图查看缺口。" if multishape and curves.get('skipped_series') else
                           "已自动生成待核对的图像近似数值，坐标和图例由程序识别。"})
        _write(report_path, report)
        return {k: report[k] for k in ("status", "count", "session_path", "export_dir", "message", "warnings", "series_count")} | {"report_path": str(report_path)}
    except Exception as exc:
        return unfinished("自动识别未完成：" + str(exc), "error")
    finally:
        image.close()

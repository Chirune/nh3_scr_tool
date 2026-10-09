"""Offline, per-series review artifacts; no inferred labels or new readings."""
from __future__ import annotations

import csv
from html import escape
import json
from pathlib import Path
import statistics

from PIL import Image, ImageDraw, ImageFont

COLORS = ("#146C94", "#D35400", "#267D46", "#9B3FA7", "#C63849", "#6B6B00",
          "#006E75", "#86553B", "#4249AA", "#595959", "#BD2982", "#667B9C")


def _safe(value):
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value


def _num(value):
    return f"{value:.6g}"


def _unresolved(value):
    return not str(value).strip() or any(s in str(value) for s in ("待确认", "待核对", "未命名", "未识别"))


def series_records(session):
    groups = {}
    for row_number, point in enumerate(session["points"], 1):
        key = (str(point.get("series_label", "")), str(point.get("sample_label", "")))
        groups.setdefault(key, []).append((row_number, point))
    rows = []
    cal = session["calibration"]
    chart = session.get("chart_type", "xy")
    relevant_axes = ("y",) if chart == "bar" else ("x",) if chart == "bar_horizontal" else ("x", "y")
    for index, ((label, sample), entries) in enumerate(groups.items(), 1):
        points = [point for _, point in entries]
        methods = sorted({point.get("method", "") for point in points})
        sampled = any("curve_sample" in method or method == "color_trace" for method in methods)
        warnings = []
        if _unresolved(label):
            warnings.append("系列名称需要对照图例确认")
        if _unresolved(sample):
            warnings.append("样品名称需要和正文匹配")
        if any(not str(cal[axis].get("unit", "")).strip() for axis in relevant_axes):
            warnings.append("坐标单位尚未确认")
        if any(_unresolved(cal[axis].get("name", "")) for axis in relevant_axes):
            warnings.append("坐标物理量尚未确认")
        if chart in ("bar", "bar_horizontal") and any(_unresolved(p.get("category", "")) for p in points):
            warnings.append("柱体类别需要确认")
        reviewed = session.get("reviewed") is True and all(p.get("review_status") == "user_reviewed" for p in points)
        if not reviewed:
            warnings.append("这些读数尚未人工核对")
        if sampled:
            warnings.append("曲线采样行数不等于独立实验数")
        warnings.append("本篇仍需核对样品、配方、条件与预测任务")
        axes = {}
        for axis in relevant_axes:
            values = [p[axis] for p in points]
            axes[axis] = {"name": cal[axis].get("name", ""), "unit": cal[axis].get("unit", ""),
                          "min": min(values), "max": max(values),
                          "pixel_resolution_median": statistics.median(p[axis + "_pixel_resolution"] for p in points)}
        rows.append({"series_id": f"S{index:02d}", "series_label": label, "sample_label_candidate": sample,
                     "color": COLORS[(index-1) % len(COLORS)], "point_count": len(points),
                     "methods": methods, "value_origin": "image_curve_samples_approximate" if sampled else "image_digitized_approximate",
                     "review_status": "user_reviewed" if reviewed else "unreviewed", "ml_ready": False,
                     "axes": axes, "warnings": warnings,
                     "point_ids": [p["point_id"] for p in points], "csv_row_numbers": [n+1 for n, _ in entries],
                     "entries": entries})
    return rows


def _font(size):
    for name in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for char in str(text):
        if line and draw.textlength(line + char, font=font) > width:
            lines.append(line)
            line = char
        else:
            line += char
    return lines + ([line] if line else [])


def _summary_image(session, rows, folder):
    with Image.open(session["image_path"]) as source:
        original = source.convert("RGB")
    scale = min(1.6, 1100 / original.width, 960 / original.height)
    rendered = original.resize((round(original.width * scale), round(original.height * scale)), Image.Resampling.LANCZOS)
    original.close()
    left, top = 24, 95
    text_left = left + rendered.width + 28
    sidebar_width = 570
    font, small, title = _font(19), _font(16), _font(27)
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    row_layout = []
    for row in rows:
        text = f"{row['series_id']}  {row['series_label'] or '系列待确认'}"
        labels = _wrap(scratch, text, font, sidebar_width - 28)
        summary = f"{row['point_count']} 行近似读数 · {'曲线采样' if row['value_origin'].startswith('image_curve') else '可见标记/柱体/人工点'}"
        bounds = [f"{a.upper()} {_num(v['min'])}—{_num(v['max'])} {v['unit'] or '[单位待核对]'}" for a, v in row['axes'].items()]
        row_layout.append((row, labels, summary, bounds, 30 + len(labels)*25 + len(bounds)*22 + 24))
    out = Image.new("RGB", (text_left + sidebar_width + 24, max(top + rendered.height + 92, top + sum(r[-1] for r in row_layout) + 60)), "#F4F7FA")
    out.paste(rendered, (left, top))
    draw = ImageDraw.Draw(out)
    draw.text((24, 20), "原图与逐系列候选数值匹配", fill="#14394B", font=title)
    state = "已人工核对图像读数；科研上下文仍需审核" if session.get('reviewed') is True else "自动/人工取点候选，尚未人工核对"
    draw.text((24, 60), f"{session.get('figure_label') or '图号待确认'} · {state} · 彩色空心圈仅用于核对", fill="#6B4A16", font=small)
    for row in rows:
        for _, p in row['entries']:
            x, y = left + p['px']*scale, top + p['py']*scale
            radius = max(3, min(6, 3*scale))
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), outline=row['color'], width=2)
    y = top
    for row, labels, summary, bounds, height in row_layout:
        draw.rounded_rectangle((text_left, y, text_left + sidebar_width, y + height - 8), radius=10, fill="white")
        draw.rectangle((text_left, y+8, text_left+5, y+height-16), fill=row['color'])
        yy=y+8
        for label in labels:
            draw.text((text_left+15,yy),label,font=font,fill=row['color']);yy+=25
        draw.text((text_left+15,yy),summary,font=small,fill="#333333");yy+=24
        for bound in bounds:
            draw.text((text_left+15,yy),bound,font=small,fill="#555555");yy+=22
        draw.text((text_left+15,yy),"图例、单位、样品对应关系仍需核对",font=small,fill="#7A4B16")
        y += height
    draw.text((24,out.height-45), "图像恢复的近似值；无补点、无实验次数推断。像素分辨率不是实验误差或置信区间。", fill="#4B5563", font=small)
    out.save(folder / "图例与数值匹配.png")
    rendered.close();out.close()


def write_series_review(session, export_dir):
    folder = Path(export_dir)
    rows = series_records(session)
    columns = ["系列编号", "系列名称候选", "样品名称候选", "近似读数行数", "X指标", "X单位", "X最小值", "X最大值", "Y指标", "Y单位", "Y最小值", "Y最大值", "取点方式", "审核状态", "可直接进入机器学习", "待核对事项", "DOI", "页码", "图号", "源PDF校验", "标定版本", "点编号", "CSV行号（含表头）"]
    with (folder / "系列核对清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream);writer.writerow(columns)
        for row in rows:
            x, y = row["axes"].get("x", {}), row["axes"].get("y", {})
            values = [row['series_id'],row['series_label'],row['sample_label_candidate'],row['point_count'],
                      x.get('name',''),x.get('unit',''),x.get('min',''),x.get('max',''),
                      y.get('name',''),y.get('unit',''),y.get('min',''),y.get('max',''),
                      '; '.join(row['methods']),row['review_status'],False,'；'.join(row['warnings']),session.get('doi',''),
                      session.get('source_metadata',{}).get('page',''),session.get('figure_label',''),
                      session.get('source_metadata',{}).get('source_sha256',''),session['calibration_id'],
                      ';'.join(row['point_ids']),';'.join(map(str,row['csv_row_numbers']))]
            writer.writerow([_safe(v) for v in values])
    _summary_image(session, rows, folder)
    with Image.open(session["image_path"]) as img:
        width,height=img.size
    # SVG uses the original pixel coordinate system. Toggling never resamples
    # points and never draws connecting lines across missing/overlapping areas.
    groups=[];options=['<option value="all">全部系列</option>'];cards=[]
    for row in rows:
        sid=row['series_id'];color=row['color']
        options.append(f'<option value="{sid}">{sid} {escape(row["series_label"])}</option>')
        circles=[];table=[]
        for number,p in row['entries']:
            x_value = _num(p['x']) if 'x' in row['axes'] else '类别轴，见 category'
            y_value = _num(p['y']) if 'y' in row['axes'] else '类别轴，见 category'
            tooltip=f"CSV 第 {number+1} 行 | {p['point_id']} | X={x_value}; Y={y_value} | 像素 ({p['px']:.3f}, {p['py']:.3f})"
            circles.append(f'<circle cx="{p["px"]}" cy="{p["py"]}" r="4" fill="none" stroke="{color}" stroke-width="1.7"><title>{escape(tooltip)}</title></circle>')
            table.append('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in [number+1,p['point_id'],x_value,y_value,f"{p['px']:.3f}, {p['py']:.3f}"] )+'</tr>')
        groups.append(f'<g data-series="{sid}">'+''.join(circles)+'</g>')
        cards.append(f'<section data-series="{sid}"><h2 style="color:{color}">{sid} {escape(row["series_label"])}</h2><p>{row["point_count"]} 行；样品候选：{escape(row["sample_label_candidate"] or "未填写")}；状态：{escape(row["review_status"])}</p><p>'+escape('；'.join(row['warnings']))+'</p><details><summary>查看数值与点编号</summary><table><tr><th>CSV行</th><th>点编号</th><th>X</th><th>Y</th><th>原图像素</th></tr>'+''.join(table)+'</table></details></section>')
    html='''<!doctype html><meta charset="utf-8"><title>逐系列核对</title><style>body{font:16px/1.7 "Microsoft YaHei",sans-serif;color:#233642;background:#f4f7fa;max-width:1260px;margin:24px auto;padding:0 20px}h1{font-size:30px}section,.notice{background:white;padding:18px;border-radius:10px;margin:16px 0}select{font:inherit;padding:8px;max-width:95%}svg{background:white;max-width:100%;height:auto;border:1px solid #cbd5e1}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:5px;border:1px solid #d8e0e6}a{color:#12647a}.hidden{display:none}</style><h1>原图、图例与数值逐系列核对</h1><p class="notice">这是从图像恢复的近似数值。先核对系列与原图，再核对轴名、单位、样品和条件。曲线采样行数不是独立实验数；不补齐遮挡点。是否完成科研审核由论文工作台另外判定。</p>'''
    html+=f'<p>DOI：{escape(str(session.get("doi", "")))} · 第 {escape(str(session.get("source_metadata", {}).get("page", "")))} 页 · {escape(session.get("figure_label", ""))}</p><p><a href="读数数据.csv">读数 CSV</a>　<a href="系列核对清单.csv">系列核对清单</a>　<a href="图例与数值匹配.png">总览图</a></p><label>显示系列：<select id="series">'+''.join(options)+'</select></label>'
    html+=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}"><image href="../source.png" width="{width}" height="{height}"/>'+''.join(groups)+'</svg>'+''.join(cards)
    html+='''<script>document.getElementById('series').addEventListener('change',function(){document.querySelectorAll('[data-series]').forEach(n=>n.classList.toggle('hidden',this.value!=='all'&&n.dataset.series!==this.value));});</script>'''
    (folder / "逐系列核对.html").write_text(html, encoding="utf-8")
    return rows

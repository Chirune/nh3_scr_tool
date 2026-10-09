"""Connect screened PDFs, candidate figures and auditable local digitizing."""
from __future__ import annotations
import csv
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import uuid

from PIL import Image
from intake import load_screened_input, load_pdf_folder
from locate import locate_pdf
from candidate_policy import caption_status, selection_report, split_candidates

SCHEMA = "screened-pdf-figures/1.0"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    os.replace(temporary,path)


def csv_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")):
        return "'"+value
    return value


def write_csv(path, rows, fields):
    with Path(path).open("w",encoding="utf-8-sig",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k:csv_cell(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v) for k,v in row.items()})


def save_batch(batch):
    batch["updated_at"] = now()
    root = Path(batch["run_dir"])
    root.mkdir(parents=True,exist_ok=True)
    path = root / "batch.json"
    # Automatic reading can add a separate project while this window stays open.
    # Preserve those references when an older in-memory figure list is saved.
    if path.is_file() and batch.get("batch_id"):
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("batch_id") == batch["batch_id"]:
            references = batch.setdefault("reading_sessions", [])
            known = {str(item.get("session_path")) for item in references}
            for reference in stored.get("reading_sessions", []):
                reference=dict(reference)
                for key in ('session_path','superseded_by'):
                    if reference.get(key): reference[key]=_rebase(reference[key],Path(stored.get('run_dir',root)),root)
                if str(reference.get("session_path")) not in known:
                    references.append(reference)
                    known.add(str(reference.get("session_path")))
                elif reference.get('superseded_by'):
                    for current in references:
                        if current.get('session_path')==reference.get('session_path') and not current.get('superseded_by'):
                            current['superseded_by']=reference['superseded_by']
    # Keep raw regions in the batch so older projects, manual edits and reading
    # references remain addressable. Only numbered captions enter the main queue.
    numbered, unmatched = split_candidates(batch)
    batch["figure_selection"] = selection_report(batch)
    write_json(path,batch)
    fields = ["figure_id","paper_id","doi","title","page","figure_label","caption","chart_type","locator_status","review_status","bbox","bbox_coordinate_system","crop_path","quality_flags","revision"]
    write_csv(root / "候选图清单.csv",numbered,fields)
    write_csv(root / "未识别图号_查漏清单.csv",[dict(f,caption_filter_reason=caption_status(f)) for f in unmatched],fields+["caption_filter_reason"])
    write_json(root / "图号筛选统计.json",batch["figure_selection"])
    write_csv(root / "等待获取或复核.csv",batch["waiting"],["record_id","doi","title","reason_code","reason","local_pdf"])
    return path


def build_batch(input_path, output_root=None, input_kind="screening", include_review=False, progress=None, cancel_event=None, only_record_ids=None):
    def emit(message):
        if progress:
            progress(str(message))
    if input_kind not in ("screening","folder"):
        raise ValueError("输入方式只能为筛选结果或本地 PDF 文件夹。")
    intake = load_screened_input(input_path,include_review=include_review) if input_kind == "screening" else load_pdf_folder(input_path)
    if only_record_ids is not None:
        wanted=set(only_record_ids)
        intake['papers']=[p for p in intake['papers'] if p.get('record_id') in wanted]
        intake['waiting']=[p for p in intake.get('waiting',[]) if p.get('record_id') in wanted]
    root = Path(output_root) if output_root else Path(__file__).parent.parent / "结果"
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = (root / batch_id).resolve()
    run_dir.mkdir(parents=True)
    batch = {"schema_version":SCHEMA,"batch_id":batch_id,"run_dir":str(run_dir),"created_at":now(),"input_kind":input_kind,
             "input_summary":{k:v for k,v in intake.items() if k not in ("papers","waiting")},
             "papers":intake["papers"],"figures":[],"waiting":intake.get("waiting",[]),"errors":[],"reading_sessions":[],"status":"running",
             "scope":"自动查找候选图及图注，接入本地坐标、图例和标记点识别；生成的是待核对图像近似读数，不是已核实实验数据。"}
    save_batch(batch)
    for index,paper in enumerate(batch["papers"],1):
        if cancel_event is not None and cancel_event.is_set():
            batch["status"] = "cancelled"
            break
        paper_id = "paper_"+hashlib.sha256(str(paper.get("record_id",index)).encode()).hexdigest()[:12]+f"_{index:03d}"
        paper["paper_id"] = paper_id
        emit(f"扫描论文 {index}/{len(batch['papers'])}：{paper.get('title') or Path(paper['local_pdf']).name}")
        try:
            source_hash = digest(paper["local_pdf"])
            if paper.get("source_sha256") and source_hash != paper["source_sha256"]:
                raise ValueError("PDF 在接收后发生变化，已停止该论文，请重新导入。")
            result = locate_pdf(paper["local_pdf"],run_dir / "papers" / paper_id,progress=emit,cancel_event=cancel_event)
            if result.get("source_sha256") != source_hash or digest(paper["local_pdf"]) != source_hash:
                raise ValueError("PDF 在扫描期间发生变化，该论文候选不进入批次。")
            paper["source_sha256"] = source_hash
            paper["pages"] = result.get("pages",[])
            paper["scan_warnings"] = result.get("warnings",[])
            paper["scan_status"] = "cancelled" if cancel_event is not None and cancel_event.is_set() else "scanned"
            for number,candidate in enumerate(result.get("figures",[]),1):
                figure = dict(candidate)
                figure.update({"figure_id":paper_id+f"_fig_{number:03d}","paper_id":paper_id,"record_id":paper.get("record_id",""),
                               "doi":paper.get("doi",""),"title":paper.get("title",""),"local_pdf":paper["local_pdf"],"source_sha256":source_hash,
                               "review_status":"unreviewed","revision":1,"review_history":[],"screening_status":paper.get("screening_status","unknown")})
                figure.setdefault("quality_flags",[])
                figure['download_source_url']=paper.get('download_source_url','')
                figure['download_identity_validation']=paper.get('download_identity_validation','')
                figure.setdefault("figure_label","")
                figure.setdefault("caption","")
                figure.setdefault("chart_type","unknown")
                figure["original_bbox"] = list(figure["bbox"])
                for key in ("page_image_path","crop_path"):
                    figure[key] = str(Path(figure[key]).resolve())
                figure["page_image_sha256"] = digest(figure["page_image_path"])
                figure["crop_sha256"] = digest(figure["crop_path"])
                if paper.get("requires_review"):
                    figure["quality_flags"].append("文献本身仍需人工确认研究范围。")
                batch["figures"].append(figure)
        except Exception as exc:
            paper["scan_status"] = "failed"
            batch["errors"].append({"paper_id":paper_id,"record_id":paper.get("record_id"),"error":str(exc)})
            emit(f"该论文暂未完成：{exc}")
        save_batch(batch)
    if batch["status"] == "running":
        batch["status"] = "cancelled" if cancel_event is not None and cancel_event.is_set() else "complete"
    save_batch(batch)
    selection = batch["figure_selection"]
    emit(f"有图号图片 {selection['numbered_candidate_count']} 张；无图号暂存查漏 {selection['unmatched_candidate_count']} 张；等待或待复核 {len(batch['waiting'])} 篇；处理失败 {len(batch['errors'])} 篇。")
    return batch


def _rebase(value, previous, current):
    if not value:
        return value
    try:
        relative = Path(value).relative_to(previous)
    except ValueError:
        return value
    return str(current / relative)


def load_batch(file_or_dir):
    path = Path(file_or_dir)
    if path.is_dir():
        path = path / "batch.json"
    batch = json.loads(path.read_text(encoding="utf-8-sig"))
    if batch.get("schema_version") != SCHEMA:
        raise ValueError("请选择本工具生成的 batch.json。")
    old, current = Path(batch["run_dir"]), path.resolve().parent
    batch["run_dir"] = str(current)
    for figure in batch["figures"]:
        for key in ("page_image_path","crop_path"):
            figure[key] = _rebase(figure[key],old,current)
    for paper in batch["papers"]:
        for page in paper.get("pages",[]):
            for key in ("image_path","page_image_path"):
                if page.get(key):
                    page[key] = _rebase(page[key],old,current)
    for session in batch.get("reading_sessions",[]):
        session["session_path"] = _rebase(session["session_path"],old,current)
        if session.get('superseded_by'):
            session['superseded_by']=_rebase(session['superseded_by'],old,current)
    return batch


def get_figure(batch,figure_id):
    try:
        return next(f for f in batch["figures"] if f["figure_id"] == figure_id)
    except StopIteration:
        raise ValueError("未找到所选候选图。") from None


def update_figure(batch,figure_id,decision="review",bbox=None,figure_label=None,chart_type=None):
    figure = get_figure(batch,figure_id)
    if decision not in ("keep","exclude","review"):
        raise ValueError("请选择保留、排除或待复核。")
    if chart_type is not None and chart_type not in ("xy","bar","bar_horizontal","unknown","non_numeric"):
        raise ValueError("图型设置不受支持。")
    if digest(figure["page_image_path"]) != figure["page_image_sha256"]:
        raise ValueError("整页原图已被替换，请重新扫描 PDF。")
    changed = False
    old_revision = figure["revision"]
    previous = {key:figure.get(key) for key in ("bbox","figure_label","chart_type","review_status")}
    if bbox is not None:
        if len(bbox) != 4 or not all(math.isfinite(float(v)) for v in bbox):
            raise ValueError("图框必须包含四个有限数值。")
        with Image.open(figure["page_image_path"]) as page:
            left,top,right,bottom = [float(v) for v in bbox]
            if not (0 <= left < right <= page.width and 0 <= top < bottom <= page.height):
                raise ValueError("图框必须位于整页图片内，且有有效宽度和高度。")
            box = [math.floor(left),math.floor(top),math.ceil(right),math.ceil(bottom)]
            if box != figure["bbox"]:
                changed = True
                figure["bbox"] = box
                figure["locator_status"] = "manually_located"
                figure["bbox_method"] = "manual_page_rectangle"
                crop = page.crop(box).convert("RGB")
                crop_path = Path(figure["crop_path"]).parent / (figure_id+f"_r{old_revision+1}.png")
                crop.save(crop_path)
                crop.close()
                figure["crop_path"] = str(crop_path)
                figure["crop_sha256"] = digest(crop_path)
    if figure_label is not None and figure_label != figure.get("figure_label"):
        figure["figure_label"] = str(figure_label)
        changed = True
    if chart_type is not None and chart_type != figure.get("chart_type"):
        figure["chart_type"] = chart_type
        changed = True
    if changed:
        figure["revision"] += 1
    figure["review_status"] = decision
    figure["review_history"].append({"at":now(),"previous":previous,"decision":decision,"revision":figure["revision"]})
    save_batch(batch)
    return batch


def create_subfigure(batch,figure_id):
    original = get_figure(batch,figure_id)
    figure = copy.deepcopy(original)
    new_id = figure_id+"_sub_"+uuid.uuid4().hex[:6]
    figure.update({"figure_id":new_id,"parent_figure_id":figure_id,"parent_figure_revision":original["revision"],"revision":1,"review_status":"review","review_history":[],
                   "figure_label":original.get("figure_label","")+"（子图待框选）"})
    figure["quality_flags"] = list(figure.get("quality_flags",[]))+["manual_subfigure_pending"]
    batch["figures"].append(figure)
    save_batch(batch)
    return new_id


def create_reading_session(batch,figure_id,output_root=None):
    figure = get_figure(batch,figure_id)
    if figure["review_status"] == "exclude":
        raise ValueError("该图已排除；如需读数，请先修改人工判断。")
    if figure["chart_type"] == "non_numeric":
        raise ValueError("该图被标为本版不读数；请先核对其是否为曲线、散点或普通柱图。")
    if digest(figure["crop_path"]) != figure["crop_sha256"] or digest(figure["page_image_path"]) != figure["page_image_sha256"]:
        raise ValueError("候选裁图或整页图已发生变化，请重新扫描或重新框选。")
    from digitizer.session import new_session,save_session
    session,image = new_session(figure["crop_path"],output_root=output_root or Path(batch["run_dir"])/"readings")
    meta = session["source_metadata"]
    crop_meta = dict(meta)
    width,height = image.size
    image.close()
    left,top,right,bottom = figure["bbox"]
    context = {"figure_id":figure_id,"figure_revision":figure["revision"],"batch_id":batch["batch_id"],"record_id":figure["record_id"],
               "bbox":figure["bbox"],"bbox_coordinate_system":"rendered_page_pixels","crop_to_page":{"offset_x":left,"offset_y":top,"scale_x":(right-left)/width,"scale_y":(bottom-top)/height},
               "page_width":figure.get("page_width"),"page_height":figure.get("page_height"),
               "figure_review_status":figure["review_status"],"screening_status":figure["screening_status"],"locator_status":figure["locator_status"],
               "page_image_sha256":figure["page_image_sha256"],"crop_sha256":figure["crop_sha256"],"caption":figure["caption"],"quality_flags":figure["quality_flags"]}
    context.update(download_source_url=figure.get('download_source_url',''),download_identity_validation=figure.get('download_identity_validation',''))
    meta.update({"source_path":figure["local_pdf"],"source_sha256":figure["source_sha256"],"source_kind":"pdf_figure_crop","page":figure["page"],
                 "figure_context":context,"crop_source_metadata":crop_meta,"page_text":figure["caption"],"figure_caption_candidates":[figure["caption"]] if figure["caption"] else []})
    session["doi"] = figure["doi"]
    session["figure_label"] = figure["figure_label"]
    session["chart_type"] = figure['chart_type'] if figure['chart_type'] in ('bar','bar_horizontal') else 'xy'
    session["notes"] = "来自批量 PDF 候选图；需人工核对坐标、图例、样品与条件。\n图注："+figure["caption"]
    path = save_session(session)
    batch["reading_sessions"].append({"session_path":str(path),"figure_id":figure_id,"figure_revision":figure["revision"],"created_at":now()})
    save_batch(batch)
    return path


def open_digitizer(batch,figure_id,output_root=None,auto_read=False):
    path = create_reading_session(batch,figure_id,output_root)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    runtime = pythonw if pythonw.is_file() else Path(sys.executable)
    command = [str(runtime),"-B",str(Path(__file__).parent / "digitizer" / "app.py"),"--load-session",str(path)]
    if auto_read:
        command.append("--auto-read")
    kwargs = {"cwd":str(Path(__file__).parent / "digitizer")}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen(command,**kwargs)
    return path


def collect_readings(batch):
    # Merge references created by the automatic worker before collecting from
    # the still-open workbench. Existing figure decisions remain authoritative.
    save_batch(batch)
    rows,skipped = [],[]
    fields = []
    seen = set()
    for ref in batch.get("reading_sessions",[]):
        path = Path(ref["session_path"])
        if ref.get('superseded_by'):
            skipped.append({'session_path':str(path),'reason':'已有更新的自动读数；旧导出保留用于核对，本次汇总不重复计入。','superseded_by':ref['superseded_by']})
            continue
        if str(path) in seen:
            continue
        seen.add(str(path))
        try:
            figure = get_figure(batch,ref["figure_id"])
            if figure["review_status"] == "exclude" or figure["revision"] != ref["figure_revision"]:
                raise ValueError("图已排除或图框/图号/图型已修改，旧读数不自动汇入。")
            session = json.loads(path.read_text(encoding="utf-8"))
            if not session.get("exports"):
                raise ValueError("尚未在读数窗口导出数值。")
            export_dir = path.parent / session["exports"][-1]["directory"]
            if export_dir.resolve().parent != path.parent.resolve():
                raise ValueError("导出目录不在当前读数项目中。")
            snapshot = json.loads((export_dir / "读数与溯源.json").read_text(encoding="utf-8"))
            if digest(path.parent / "source.png") != snapshot["image_sha256"]:
                raise ValueError("读数原图已被替换。")
            context = snapshot.get("source_metadata",{}).get("figure_context",{})
            if context.get("figure_id") != figure["figure_id"] or context.get("figure_revision") != figure["revision"]:
                raise ValueError("读数来源与当前候选图不匹配。")
            if digest(figure["crop_path"]) != figure["crop_sha256"] or digest(figure["page_image_path"]) != figure["page_image_sha256"]:
                raise ValueError("候选图来源文件已变化，暂不汇入。")
            expected_csv_hash = snapshot.get("exports", [{}])[-1].get("csv_sha256")
            if not expected_csv_hash or digest(export_dir / "读数数据.csv") != expected_csv_hash:
                raise ValueError("导出 CSV 被修改或缺少校验记录，请在读数窗口重新核对并导出。")
            compared = ("points","calibration","doi","figure_label","chart_type","notes","roi","source_metadata","reviewed")
            unexported = any(session.get(key) != snapshot.get(key) for key in compared)
            with (export_dir / "读数数据.csv").open(encoding="utf-8-sig",newline="") as stream:
                reader = csv.DictReader(stream)
                for name in reader.fieldnames or []:
                    if name not in fields:
                        fields.append(name)
                for row in reader:
                    row.update({"batch_id":batch["batch_id"],"figure_id":figure["figure_id"],"figure_review_status":figure["review_status"],
                                "reading_session":str(path),"export_snapshot":str(export_dir),"screening_status":figure["screening_status"],
                                "current_session_has_unexported_changes":unexported})
                    rows.append(row)
        except Exception as exc:
            skipped.append({"session_path":str(path),"reason":str(exc)})
    extras = ["batch_id","figure_id","figure_review_status","reading_session","export_snapshot","screening_status","current_session_has_unexported_changes"]
    fields.extend(key for key in extras if key not in fields)
    destination = Path(batch["run_dir"])/("汇总_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:4])
    destination.mkdir()
    target = destination/"图片读数汇总.csv"
    write_csv(target,rows,fields)
    write_json(destination/"汇总说明.json",{"row_count":len(rows),"skipped":skipped,"scope":"只汇总每个读数项目最近一次主动导出的快照；不合并同名系列，不去重实验点，不判定机器学习可用性。人工状态保持原样。"})
    batch["last_collection"] = {"path":str(target),"row_count":len(rows),"skipped":len(skipped)}
    save_batch(batch)
    return target

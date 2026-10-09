"""Offline bridge from literature_gateway screening records to local PDFs.

No downloads, browser actions, shell commands, OCR or scientific extraction take
place here. An admitted PDF is a processable source, not validated research data.
Schema checked against literature_gateway/engine.py and rules.py: decisions are
target/review/non_target; profiles are cuzn/scr_ammonia; local PDF identity uses
local_path/local_sha256. downloaded_fulltext currently represents PMC XML.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

from pypdf import PdfReader


DECISIONS = frozenset({"target", "review", "non_target"})
PROFILES = frozenset({"cuzn", "scr_ammonia"})
INTAKE_VERSION = "figure-intake/1.0"


class IntakeError(ValueError):
    """An actionable, local input error."""


def _text(value):
    return "" if value is None else str(value)


def _local_path(value, base=None):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise IntakeError("未提供本地文件路径。")
    raw = str(value)
    if "\x00" in raw:
        raise IntakeError("本地路径含无效字符。")
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw.strip(), re.I):
        raise IntakeError("这里需要已经下载的本地文件，不能使用网址、DOI 链接或 file:// 地址。")
    if raw.startswith(("\\\\", "//")):
        raise IntakeError("离线接收不读取网络共享或设备路径，请先复制到本机文件夹。")
    if re.match(r"^[A-Za-z]:[^/\\]", raw):
        raise IntakeError("请使用完整本地路径，不能使用仅指定盘符的相对路径。")
    candidate = Path(raw)
    if not candidate.is_absolute() and base is not None:
        candidate = Path(base) / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise IntakeError("找不到本地文件或文件夹，请核对下载位置。") from exc
    if str(resolved).startswith(("\\\\", "//")):
        raise IntakeError("该路径实际指向网络共享，请先复制到本机文件夹。")
    return resolved


def _hash_file(path):
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except (OSError, ValueError) as exc:
        raise IntakeError("无法读取本地文件，请检查权限或重新下载。") from exc
    return digest.hexdigest()


def _canonical_doi(value):
    raw = unquote(_text(value)).strip()
    cleaned = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", raw, flags=re.I)
    cleaned = cleaned.rstrip(".,;，。；")
    if re.fullmatch(r"10\.\d{4,9}/[^\s<>\x00-\x1f]+", cleaned, re.I):
        return cleaned.lower()
    return ""


def _base_record(record, index, profile, source_id):
    raw_doi = _text(record.get("doi"))
    doi = _canonical_doi(raw_doi)
    raw_warnings = record.get("warnings") or []
    warnings = [_text(item) for item in raw_warnings] if isinstance(raw_warnings, list) else [_text(raw_warnings)]
    if not doi:
        warnings.append("DOI 缺失或格式待核对；不会把 DOI 猜测成文件路径。")
    screening = record.get("screening") if isinstance(record.get("screening"), dict) else {}
    record_id = _text(record.get("id")) or "missing-id-" + hashlib.sha256(f"{source_id}:{index}".encode("utf-8")).hexdigest()[:18]
    if not record.get("id"):
        warnings.append("原记录没有 id，已生成仅用于本次接收定位的记录号。")
    return {
        "record_id": record_id,
        "doi": doi,
        "doi_raw": raw_doi,
        "doi_status": "unverified_against_pdf" if doi else "missing_or_invalid",
        "title": _text(record.get("title")),
        "profile": profile,
        "screening_status": "unknown",
        "screening_reason": _text(screening.get("reason")),
        "manual_decision": _text(record.get("manual_decision")).strip(),
        "manual_note": _text(record.get("manual_note")),
        "auto_decision": _text(screening.get("decision")),
        "stored_effective_decision": _text(record.get("effective_decision")).strip(),
        "source_record_index": index,
        "local_pdf": "",
        "source_sha256": "",
        "warnings": warnings,
        "requires_review": True,
        "download_source_url":record.get('pdf_acquisition',{}).get('source_url',''),
        "download_identity_validation":record.get('pdf_acquisition',{}).get('identity_check',''),
        "pdf_acquisition_status":record.get('pdf_acquisition',{}).get('status',''),
    }


def _wait(item, reason_code, reason, **extra):
    return {**item, "reason_code": reason_code, "reason": reason, **extra}


def _inspect_pdf(raw_path, base, expected_hash=""):
    """Return (metadata, reason_code, reason), never silently accept changes."""
    try:
        path = _local_path(raw_path, base=base)
        if not path.is_file():
            return {}, "not_a_file", "指定路径不是文件，请选择已经下载的 PDF。"
        if path.suffix.lower() != ".pdf":
            return {}, "not_pdf", "已有文件不是 PDF；XML、网页和 DOI 清单不能作为图片 PDF 处理。"
        actual_hash = _hash_file(path)
        identity = {"local_pdf": str(path), "source_sha256": actual_hash}
        expected = _text(expected_hash).strip().lower()
        if expected:
            identity["expected_source_sha256"] = expected
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                return identity, "invalid_expected_hash", "筛选记录中的 local_sha256 格式无效，需核对原始文件记录后再接收。"
            if expected != actual_hash:
                return identity, "source_changed", "本地 PDF 与筛选时的文件摘要不一致，可能已被修改或替换；请人工确认并重新筛选，未自动使用。"
        identity["source_hash_status"] = "matches_record" if expected else "newly_computed"
        with path.open("rb") as stream:
            head = stream.read(1024)
            if not re.search(br"%PDF-\d\.\d", head):
                return identity, "invalid_pdf", "文件没有有效 PDF 文件头，可能是被保存为 .pdf 的网页或错误响应。"
            stream.seek(0)
            try:
                reader = PdfReader(stream, strict=False)
                if reader.is_encrypted and not reader.decrypt(""):
                    return identity, "password_required", "PDF 需要密码，当前离线接收无法读取；请提供可正常打开的本地副本。"
                page_count = len(reader.pages)
                if page_count < 1:
                    return identity, "empty_pdf", "PDF 没有页面，不能进行图片读取。"
            except Exception:
                return identity, "invalid_pdf", "PDF 结构无法读取，可能已损坏、未下载完整或受到密码保护。"
        identity.update(page_count=page_count, pdf_validation="pdf_header_and_structure_checked")
        return identity, "", ""
    except IntakeError as exc:
        return {}, "local_file_unavailable", str(exc)
    except (OSError, ValueError) as exc:
        return {}, "local_file_unavailable", f"本地文件无法读取：{type(exc).__name__}；请检查路径和文件权限。"


def _result(kind, run_path="", run_hash=""):
    return {
        "intake_version": INTAKE_VERSION,
        "input_kind": kind,
        "papers": [], "waiting": [], "excluded": [], "excluded_count": 0,
        "source_run_path": run_path, "source_run_sha256": run_hash,
        "warnings": [],
        "scope_note": "只接收可读取的本地 PDF；入队不代表图中数据、论文身份或实验结论已核实。",
    }


def load_screened_input(run_json, include_review=False) -> dict:
    """Read a gateway run without executing or importing gateway code.

    Explicit manual decisions take priority. Missing/unknown decisions wait for
    review; they are never inferred from automatic screening or a DOI. Relative
    paths resolve against the actual run.json directory, not its old run_dir.
    Non-target records are counted in excluded_count and preserved in excluded.
    """
    if not isinstance(include_review, bool):
        raise IntakeError("include_review 必须明确为 True 或 False。")
    path = _local_path(run_json)
    if path.is_dir():
        path = _local_path(path / "run.json")
    if not path.is_file():
        raise IntakeError("请选择模块一保存的 run.json 文件。")
    try:
        raw = path.read_bytes()
        run_hash = hashlib.sha256(raw).hexdigest()
        run = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntakeError("无法读取筛选结果 JSON，请选择模块一保存的 run.json。") from exc
    if not isinstance(run, dict) or not isinstance(run.get("records"), list) or run.get("profile") not in PROFILES:
        raise IntakeError("这不是支持的模块一筛选记录；需要 records 列表和 cuzn / scr_ammonia 研究方向。")
    result = _result("screened_run", str(path), run_hash)
    result.update(profile=run["profile"], include_review=include_review, input_record_count=len(run["records"]))
    links = {}
    links_path = path.parent / "local_pdf_links.json"
    if links_path.is_file():
        try:
            if links_path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("关联清单过大")
            link_data = json.loads(links_path.read_text(encoding="utf-8-sig"))
            if link_data.get("schema_version") != "local-pdf-links/1.0":
                raise ValueError("关联清单格式不匹配")
            for link in link_data.get("links", []):
                if isinstance(link, dict) and link.get("record_id") and link.get("doi") and link.get("local_sha256"):
                    links[(_text(link["record_id"]), _canonical_doi(link["doi"]))] = link
            result["pdf_links_path"] = str(links_path)
            result["pdf_links_sha256"] = _hash_file(links_path)
        except (OSError, ValueError, TypeError) as exc:
            result["warnings"].append("本地 PDF 关联清单暂不可用：" + str(exc))
    for index, record in enumerate(run["records"]):
        if not isinstance(record, dict):
            item = _base_record({}, index, run["profile"], run_hash)
            result["waiting"].append(_wait(item, "invalid_record", "该条筛选记录不是有效对象，请在模块一核对。"))
            continue
        item = _base_record(record, index, run["profile"], run_hash)
        manual = item["manual_decision"]
        effective = item["stored_effective_decision"]
        if manual and manual not in DECISIONS:
            result["waiting"].append(_wait(item, "unknown_manual_decision", "人工决定不是 target / review / non_target，需先核对，未按自动结果放行。"))
            continue
        decision = manual or effective
        item["screening_status"] = decision or "unknown"
        item["effective_decision"] = decision
        if manual and manual != effective:
            item["warnings"].append("人工决定与保存的 effective_decision 不一致，接收时以明确的人工决定为准。")
        if decision == "non_target":
            result["excluded_count"] += 1
            result["excluded"].append(_wait(item, "excluded_by_screening", "该文献已被人工或筛选结果排除，不进入图片处理。"))
            continue
        if decision not in DECISIONS:
            result["waiting"].append(_wait(item, "unknown_screening_decision", "缺少有效的最终筛选决定，需在模块一确认保留或待复核。"))
            continue
        if decision == "review" and not include_review:
            result["waiting"].append(_wait(item, "screening_review_required", "该文献仍待复核；默认不进入图片处理。"))
            continue
        if decision == "review":
            item["warnings"].append("用户选择包含待复核文献；本条尚未通过相关性确认，不得视为已保留数据。")
        linked = links.get((_text(record.get("id")), item["doi"]))
        if not record.get("local_path") and linked:
            record = dict(record, local_path=linked.get("local_path"), local_sha256=linked["local_sha256"])
            item["pdf_link_origin"] = "downloaded_pdf_sidecar"
            item["download_source_url"] = linked.get("source_url", "")
            item["download_identity_validation"] = linked.get("doi_validation", "")
        raw_path = record.get("local_path")
        if not raw_path:
            acquisition=record.get('pdf_acquisition',{})
            if acquisition.get('message'):
                result['waiting'].append(_wait(item,'pdf_'+str(acquisition.get('status','not_acquired')),acquisition['message']))
                continue
            downloaded = _text(record.get("downloaded_fulltext"))
            if downloaded:
                result["waiting"].append(_wait(item, "fulltext_not_local_pdf", "目前仅记录了 downloaded_fulltext 全文，模块一该字段为 XML；需要取得并关联本地 PDF 后再读图。", downloaded_fulltext=downloaded))
            else:
                result["waiting"].append(_wait(item, "pdf_not_downloaded", "已取得论文记录或 DOI，但尚未关联本地 PDF；请先由全文获取模块下载。"))
            continue
        identity, reason_code, reason = _inspect_pdf(raw_path, path.parent, record.get("local_sha256"))
        item.update(identity)
        if reason_code:
            result["waiting"].append(_wait(item, reason_code, reason, supplied_local_path=_text(raw_path)))
            continue
        if not record.get("local_sha256"):
            item["warnings"].append("筛选记录未提供文件摘要；已计算当前 PDF 的 SHA256，但尚不能证明它就是筛选时的原文件。")
        if not item["title"]:
            item["title"] = Path(item["local_pdf"]).stem
        item["requires_review"] = decision != "target" or not item["doi"] or not record.get("local_sha256")
        result["papers"].append(item)
    return result


def load_pdf_folder(folder, recursive=False) -> dict:
    """Admit local PDFs for technical processing, explicitly NOT screened.

    The default is the directly selected folder only; recursion is opt-in.
    Duplicate copies are retained as distinct source records rather than being
    silently merged. Every admitted record requires scientific review.
    """
    if not isinstance(recursive, bool):
        raise IntakeError("recursive 必须明确为 True 或 False。")
    directory = _local_path(folder)
    if not directory.is_dir():
        raise IntakeError("请选择存放 PDF 的本地文件夹。")
    try:
        candidates = directory.rglob("*") if recursive else directory.iterdir()
        paths = sorted((p for p in candidates if p.suffix.lower() == ".pdf" and p.is_file()), key=lambda p: str(p).casefold())
    except OSError as exc:
        raise IntakeError("无法列出该文件夹，请检查读取权限。") from exc
    result = _result("pdf_folder")
    result.update(source_folder=str(directory), recursive=recursive, profile="", input_record_count=len(paths))
    result["warnings"].append("文件夹导入没有经过模块一相关性筛选，所有论文均标为 not_screened，需人工核对。")
    if not paths:
        result["warnings"].append("所选范围没有 PDF；默认只读取文件夹直接下一层。")
    for index, path in enumerate(paths):
        relative = path.relative_to(directory).as_posix()
        record_id = "local-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:18]
        item = {
            "record_id": record_id, "doi": "", "doi_raw": "", "doi_status": "missing_or_invalid",
            "title": path.stem, "profile": "", "screening_status": "not_screened",
            "effective_decision": "not_screened", "manual_decision": "", "manual_note": "",
            "source_record_index": index, "relative_pdf_path": relative,
            "local_pdf": "", "source_sha256": "", "requires_review": True,
            "warnings": ["未经过相关性筛选，需人工确认研究方向和文献身份。", "未自动推断 DOI，请核对原文后补充。"],
        }
        identity, reason_code, reason = _inspect_pdf(path, directory)
        item.update(identity)
        if reason_code:
            result["waiting"].append(_wait(item, reason_code, reason))
        else:
            result["papers"].append(item)
    manifest = [{"record_id": p["record_id"], "local_pdf": p["local_pdf"], "sha256": p["source_sha256"], "status": p.get("reason_code", "readable")} for p in result["papers"] + result["waiting"]]
    result["intake_manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return result

"""Persistent, local, human-reviewed workflow on top of the existing extractors."""
from __future__ import annotations

import argparse
import contextlib
import io
import shutil
from datetime import datetime
from pathlib import Path

from .abstract_import import collect_files
from .cli import run_extract, run_export
from .core import FIELDS, make_record, read_json, uid, write_csv, write_json
from .features import build_features


def stamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S_%f')


ISSUES = {
    'missing_catalyst': '未找到对应催化剂', 'catalyst_not_in_block': '催化剂名称未在对应原文中找到',
    'non_scalar_or_missing': '数值缺失，或为范围、±、不等式', 'unknown_unit': '单位未识别',
    'out_of_range': '数值超出指标范围', 'secondary_source_document': '来源被识别为综述（二手数据）',
    'unclassified_document': '尚未确认文档为原始研究', 'cross_source_conflict': '不同来源数值冲突',
    'missing_doi': '缺 DOI', 'missing_abstract': '缺摘要', 'abstract_conflict': '摘要来源冲突',
    'short_or_truncated_abstract': '摘要较短或疑似截断',
}
PROPERTIES = {
    'temperature': '反应温度', 'pressure': '反应压力', 'ghsv': '空速',
    'no_conversion': 'NO 转化率', 'nox_conversion': 'NOx 转化率',
    'nh3_conversion': 'NH3 转化率', 'n2_selectivity': 'N2 选择性', 'n2o_concentration': 'N2O 浓度',
    't50': 'T50', 't90': 'T90', 'cu_content': 'Cu 含量', 'loading': '负载量',
    'si_al_ratio': 'Si/Al 比', 'cu_al_ratio': 'Cu/Al 比', 'bet_surface_area': '比表面积',
    'active_site_type': '活性中心类型', 'active_site_distance': '活性中心距离',
    'bond_distance': '键长', 'coordination_number': '配位数', 'activation_energy': '活化能',
    'apparent_activation_energy': '表观活化能', 'acid_amount': '酸量',
    'si_content': 'Si 含量', 'al_content': 'Al 含量', 'isolated_cu_content': '孤立 Cu 含量',
    'cu_per_cage': '每个笼的 Cu 数量', 'isolated_cu_al_ratio': '孤立 Cu/Al 比',
    'isolated_cu_per_cage': '每个笼的孤立 Cu 数量', 'anr': 'NH3/NOx 进料比',
    'critical_anr': '临界 NH3/NOx 比', 'hydrothermal_temperature': '水热合成温度',
    'hydrothermal_time': '水热合成时间', 'no_inlet': 'NO 入口浓度', 'no2_inlet': 'NO2 入口浓度',
    'nh3_inlet': 'NH3 入口浓度', 'o2_inlet': 'O2 入口浓度', 'h2o_inlet': '水入口浓度',
    'so2_inlet': 'SO2 入口浓度', 'time_on_stream': '反应持续时间', 'pore_volume': '孔容',
    'pore_diameter': '孔径', 'crystallite_size': '晶粒尺寸', 'acid_site_type': '酸位点类型',
    'mo_oxidation_state': 'Mo 氧化态', 'turnover_frequency': '周转频率（TOF）',
    'calcination_temperature': '焙烧温度', 'calcination_time': '焙烧时间',
}


def issue_label(code):
    if code.startswith('condition_'):
        for prop in FIELDS:
            prefix='condition_'+prop+'_'
            if code.startswith(prefix):
                return PROPERTIES.get(prop,prop)+'：'+ISSUES.get(code[len(prefix):],code[len(prefix):])
    return ISSUES.get(code, code)


def questions(record):
    value = f"{record.get('raw_value', '')} {record.get('raw_unit', record.get('unit', ''))}"
    lines = [f"1. 原文中的样品是否确实是“{record.get('catalyst') or '尚未找到'}”？",
             f"2. {PROPERTIES.get(record['property'], record['property'])}是否对应 {value}？单位和指标含义是否正确？"]
    temp = record.get('conditions', {}).get('temperature', {})
    if record['category'] == 'performance' and record['property'] not in {'t50', 't90'}:
        lines.append(f"3. 对应的反应温度是否为 {temp['value']} {temp['unit']}？" if temp.get('value') is not None
                     else '3. 尚未关联反应温度；这一性能值暂不能进入性能训练表。')
    lines.append('请确认这是作者自己的数据，且样品、条件与数值属于同一实验。')
    if record.get('issues'):
        lines.append('待解决：' + '；'.join(issue_label(c) for c in record['issues']))
    return '\n'.join(lines)


class Project:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.file = self.folder / 'project.json'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = read_json(self.file) if self.file.exists() else {
            'schema': 'nh3scr-workbench-v1', 'papers': [], 'attachments': {}, 'runs': {},
            'history': [], 'last_export': None}
        if self.state.get('schema') != 'nh3scr-workbench-v1':
            raise ValueError('这不是本工具保存的工作项目。请选择包含 project.json 的项目文件夹。')
        self.save()

    def save(self):
        self.state['updated_at'] = datetime.now().isoformat()
        temporary = self.file.with_suffix('.new')
        write_json(temporary, self.state)
        temporary.replace(self.file)

    def paper(self, identity):
        return next(r for r in self.state['papers'] if r['record_id'] == identity)

    def import_abstracts(self, paths):
        # Reuse the original collection when adding files; preserve expert decisions.
        paths = list(paths)
        if self.state['papers']:
            saved = self.folder / 'abstracts_before_import.json'
            write_json(saved, self.state['papers'])
            paths.insert(0, saved)
        rows, errors = collect_files(paths)
        if not rows:
            raise ValueError('未读到论文题录。请导入摘要 JSON、HTML 或 Zotero 导出文件。')
        old = {r['record_id']: r for r in self.state['papers']}
        for row in rows:
            row['upstream_record_id'] = row.get('upstream_record_id') or row['record_id']
            row['record_id'] = uid('workbench-paper', row['doi']) if row.get('doi') else uid('workbench-paper', row['title'], row['upstream_record_id'])
            previous = old.get(row['record_id'], {})
            for key in ('human_decision', 'human_reviewer', 'human_note'):
                row.pop(key, None)
                if key in previous:
                    row[key] = previous[key]
            # Changed or conflicting evidence requires a new human decision.
            if previous and (row['abstract'] != previous['abstract'] or row.get('abstract_alternatives') != previous.get('abstract_alternatives')):
                row.pop('human_decision', None)
        self.state['papers'] = rows
        from .scoring import score_record
        for row in rows:
            row.update(score_record(row, self.state.get('score_config')))
        self._store_browser_captures(paths)
        self.state['last_export'] = None
        write_json(self.folder / 'import_errors.json', errors)
        self.save()
        return len(rows), errors

    def _store_browser_captures(self, paths):
        """Keep browser-visible article HTML outside project.json, matched to a paper."""
        from bs4 import BeautifulSoup
        from .abstract_import import doi as normalize_doi

        by_doi = {normalize_doi(r.get('doi')): r['record_id'] for r in self.state['papers'] if r.get('doi')}
        by_title = {}
        for row in self.state['papers']:
            by_title.setdefault(row['title'].casefold().strip(), []).append(row['record_id'])
        def local_file(value):
            if not isinstance(value, str) or not value:
                return None
            try:
                candidate = Path(value)
                return candidate if candidate.is_file() and candidate.stat().st_size <= 100 * 1024 * 1024 else None
            except OSError:
                return None
        captures = self.state.setdefault('captures', {})
        for path in paths:
            path = Path(path)
            if path.suffix.lower() != '.json':
                continue
            try:
                payload = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get('schema') != 'nh3scr-browser-capture-v2':
                continue
            for item in payload.get('items', []):
                if not isinstance(item, dict):
                    continue
                capture_doi = normalize_doi(item.get('doi'))
                identity = by_doi.get(capture_doi)
                if not identity and not capture_doi:
                    matches = by_title.get(str(item.get('title') or '').casefold().strip(), [])
                    identity = matches[0] if len(matches) == 1 else None
                if not identity:
                    continue
                capture = dict(captures.get(identity, {}))
                raw = item.get('fulltext_html')
                if isinstance(raw, str) and len(raw) >= 1000:
                    soup = BeautifulSoup(raw, 'html.parser')
                    for node in soup.select('script, iframe, object, embed, form'):
                        node.decompose()
                    for node in soup.find_all(True):
                        for attr in list(node.attrs):
                            if attr.lower().startswith('on'):
                                del node.attrs[attr]
                    if len(soup.get_text(' ', strip=True)) >= 1000:
                        target = self.folder / 'captures' / identity / 'page.html'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(str(soup), encoding='utf-8')
                        capture['path'] = str(target.relative_to(self.folder))
                from .harvest import is_valid_pdf
                pdf = local_file(item.get('downloaded_pdf_path'))
                if pdf and is_valid_pdf(pdf):
                    target = self.folder / 'captures' / identity / 'browser.pdf'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pdf, target)
                    capture['pdf_path'] = str(target.relative_to(self.folder))
                supplements = []
                raw_supplements = item.get('downloaded_supplement_paths')
                for index, raw_path in enumerate(raw_supplements if isinstance(raw_supplements, list) else []):
                    source = local_file(raw_path)
                    if not source or (source.suffix.lower() == '.pdf' and not is_valid_pdf(source)):
                        continue
                    target = self.folder / 'captures' / identity / 'supplement' / f'{index:02d}_{source.name}'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    supplements.append(str(target.relative_to(self.folder)))
                if supplements:
                    capture['supplement_paths'] = supplements
                figures = []
                raw_figures = item.get('downloaded_figure_paths')
                image_types = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}
                for index, raw_path in enumerate(raw_figures if isinstance(raw_figures, list) else []):
                    source = local_file(raw_path)
                    if not source or source.suffix.lower() not in image_types:
                        continue
                    target = self.folder / 'captures' / identity / 'figures' / f'{index:02d}_{source.name}'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    figures.append(str(target.relative_to(self.folder)))
                if figures:
                    capture['figure_paths'] = figures
                if (capture.get('path') or capture.get('pdf_path') or
                        capture.get('supplement_paths') or capture.get('figure_paths')):
                    capture['source_url'] = item.get('landing_page_url') or item.get('abstract_url') or ''
                    capture['captured_at'] = item.get('captured_at') or ''
                    captures[identity] = capture

    def attach_browser_capture(self, identity):
        """Promote a captured page only after the reader has kept the paper."""
        if self.paper(identity).get('human_decision') != 'target':
            raise ValueError('先在第 2 步人工确认保留这篇论文。')
        capture = self.state.get('captures', {}).get(identity)
        if not capture:
            raise ValueError('这篇论文还没有可用的浏览器全文采集。请在可见正文的网页重新使用插件。')
        source = None
        if not any(item['role'] == 'primary' for item in self.state['attachments'].get(identity, [])):
            from .harvest import is_valid_pdf
            pdf = self.folder / capture['pdf_path'] if capture.get('pdf_path') else None
            html = self.folder / capture['path'] if capture.get('path') else None
            source = pdf if pdf and is_valid_pdf(pdf) else html if html and html.exists() else None
            if source:
                self.attach(identity, [source], 'primary')
        self._attach_capture_supplements(identity, capture)
        if not source and not capture.get('supplement_paths') and not capture.get('figure_paths'):
            raise ValueError('浏览器采集文件不存在，或这篇论文已经有正文文件。')
        return source

    def _attach_capture_supplements(self, identity, capture):
        existing = {item['original'] for item in self.state['attachments'].get(identity, [])}
        supported = {'.pdf', '.html', '.htm', '.txt', '.md', '.csv', '.tsv', '.xlsx',
                     '.json', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}
        paths = [self.folder / path for path in
                 capture.get('supplement_paths', []) + capture.get('figure_paths', [])]
        paths = [path for path in paths if path.is_file() and path.suffix.lower() in supported
                 and str(path) not in existing]
        if paths:
            self.attach(identity, paths, 'supplement')

    def search_online(self, query, sources, *, limit=20, email=None, openalex_key=None,
                      elsevier_key=None, elsevier_insttoken=None, springer_key=None):
        """Use the existing multi-source harvester without downloading before review."""
        query = query.strip()
        if not query:
            raise ValueError('请输入检索词。')
        from .harvest import ALLOWED_SOURCES, run_harvest
        sources = list(dict.fromkeys(sources))
        if not sources or set(sources) - ALLOWED_SOURCES:
            raise ValueError('请选择至少一个可用的检索来源。')
        output = self.folder / 'searches' / stamp()
        args = argparse.Namespace(query=[query], sources=sources, limit=limit,
                                  from_year=None, to_year=None, email=email,
                                  openalex_key=openalex_key, elsevier_key=elsevier_key,
                                  elsevier_insttoken=elsevier_insttoken,
                                  springer_key=springer_key, local_papers=[], records=None,
                                  public_abstracts=True, abstract_page_limit=5,
                                  screen_engine='none', download=False, max_downloads=0,
                                  max_file_mb=100, timeout=20, delay=0, output=str(output))
        with contextlib.redirect_stdout(io.StringIO()):
            run_harvest(args)
        found = read_json(output / 'records.json')
        errors = read_json(output / 'errors.json')
        if found:
            total, import_errors = self.import_abstracts([output / 'records.json'])
            errors.extend(import_errors)
        else:
            total = len(self.state['papers'])
        self.state['history'].append({'action': 'online_search', 'query': query,
                                      'sources': sources, 'found': len(found),
                                      'time': datetime.now().isoformat()})
        self.save()
        return {'found': len(found), 'total': total, 'errors': errors, 'output': str(output)}

    def score_papers(self, config):
        from .scoring import score_record, validate_config
        config = validate_config(config)
        scored = [dict(r, **score_record(r, config)) for r in self.state['papers']]
        self.state.update(papers=scored, score_config=config, last_export=None)
        self.state['history'].append({'action': 'score_weights', 'config': config, 'time': datetime.now().isoformat()})
        write_csv(self.folder / '文献评分排序.csv', sorted(scored, key=lambda r: -r['priority_score']),
                  ['doi', 'title', 'priority_score', 'score_basis', 'score_components', 'score_config_id',
                   'human_decision', 'reason'])
        self.save()

    def claims(self):
        rows = []
        for identity in self.state['runs']:
            if self.paper(identity).get('human_decision') != 'target':
                continue
            file = self.run_path(identity) / 'semantic_candidates.json'
            if file.exists():
                rows.extend(read_json(file))
        return rows

    def review_claims(self, ids, decision, reviewer, note=''):
        if decision not in {'approve', 'reject'} or not reviewer.strip():
            raise ValueError('请填写核对人并选择通过或排除。')
        ids = set(ids)
        selected = [r for r in self.claims() if r['claim_id'] in ids]
        if not ids or len(selected) != len(ids):
            raise ValueError('所选语义候选已变化，请刷新后重选。')
        for row in selected:
            if row['evidence'] not in self.source(row['paper_id'], row['block_id'])['text']:
                raise ValueError('语义原句与来源不一致。')
        for identity in {r['paper_id'] for r in selected}:
            path = self.run_path(identity) / 'semantic_candidates.json'
            rows = read_json(path)
            for row in rows:
                if row['claim_id'] in ids:
                    row.update(review_status='approved' if decision == 'approve' else 'rejected',
                               reviewer=reviewer, review_note=note, training_eligible=False)
            write_json(path, rows)
            write_csv(path.with_suffix('.csv'), rows)
        self.state['history'].append({'action': 'semantic_review', 'claims': sorted(ids),
                                     'decision': decision, 'reviewer': reviewer, 'note': note, 'time': datetime.now().isoformat()})
        self.state['last_export'] = None
        self.save()

    def export_claims(self):
        output = self.folder / 'semantic_exports' / stamp()
        write_json(output / '语义候选.json', self.claims())
        write_csv(output / '语义候选.csv', self.claims())
        return output

    def import_figure(self, identity, snapshot_path, y_property, x_property=None, *,
                      context_block_id=None, conditions=None):
        from .figure_import import build_figure_records, file_hash, snapshot_image
        if self.paper(identity).get('human_decision') != 'target' or identity not in self.state['runs']:
            raise ValueError('先确认论文为原始研究，并完成一次正文提取。')
        snapshot_path = Path(snapshot_path).resolve()
        snapshot = read_json(snapshot_path)
        doi = snapshot.get('doi')
        from .literature import normalized_doi
        if doi and normalized_doi(doi) != normalized_doi(self.paper(identity).get('doi')):
            raise ValueError('图片快照 DOI 与所选论文不同。')
        source_sha = snapshot.get('source_metadata', {}).get('source_sha256')
        attached = next((self.folder / a['path'] for a in self.state['attachments'].get(identity, [])
                         if file_hash(self.folder / a['path']) == source_sha), None)
        if attached is None:
            raise ValueError('图片原始文件尚未绑定到这篇论文。请使用第 3 步已添加的论文文件读图。')
        source_rows = read_json(self.run_path(identity) / 'workflow_sources.json')
        context = (self.source(identity, context_block_id) if context_block_id else
                   next((b for b in source_rows if b['source_id'] == source_sha and b.get('kind') != 'digitized_curve'), {}))
        context = dict(context, doi=self.paper(identity).get('doi'))
        dtype = next((b.get('document_type', 'unknown') for b in source_rows if b['source_id'] == source_sha and b.get('kind') != 'digitized_curve'), 'unknown')
        options = dict(source_path=attached, context=context, conditions=conditions, document_type=dtype)
        # Validate everything before storing evidence or changing the active extraction.
        records, _ = build_figure_records(snapshot_path, identity, y_property, x_property, **options)
        old = read_json(self.run_path(identity) / 'reviewed.json')
        seen = {r['record_id'] for r in old}
        if all(r['record_id'] in seen for r in records):
            return 0
        evidence_dir = self.folder / 'figure_evidence' / identity / file_hash(snapshot_path)[:20]
        export_dir = evidence_dir / 'export'
        export_dir.mkdir(parents=True, exist_ok=True)
        stored = export_dir / '读数与溯源.json'
        shutil.copy2(snapshot_path, stored)
        image = snapshot_image(snapshot_path, snapshot)
        # The snapshot's relative image_file is ../source.png in the delivered reader format.
        relative = snapshot.get('image_file', '../source.png')
        saved_image = (export_dir / relative).resolve()
        if evidence_dir.resolve() not in saved_image.parents:
            raise ValueError('读数快照的原图路径不在证据目录中。')
        saved_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image, saved_image)
        overlay = snapshot_path.parent / '原图与读数标记.png'
        if overlay.exists():
            shutil.copy2(overlay, export_dir / overlay.name)
        records, blocks = build_figure_records(stored, identity, y_property, x_property, **options)
        for row in records + blocks:
            row['doi'] = self.paper(identity).get('doi')
        new = [r for r in records if r['record_id'] not in seen]
        old.extend(new)
        source_index = {b['block_id']: b for b in source_rows}
        source_index.update({b['block_id']: b for b in blocks})
        write_json(self.run_path(identity) / 'reviewed.json', old)
        write_json(self.run_path(identity) / 'workflow_sources.json', list(source_index.values()))
        report = read_json(self.run_path(identity) / 'run_report.json')
        report['figure_imported'] = report.get('figure_imported', 0) + len(new)
        report['candidates'] = len(old)
        write_json(self.run_path(identity) / 'run_report.json', report)
        self.state['history'].append({'action': 'figure_import', 'paper': identity, 'snapshot': str(stored),
                                     'records': [r['record_id'] for r in new], 'time': datetime.now().isoformat()})
        self.state['last_export'] = None
        self.save()
        return len(new)

    def decide_papers(self, identities, decision, reviewer, note=''):
        if decision not in {'target', 'non_target', 'review'} or not reviewer.strip():
            raise ValueError('请填写核对人，并选择保留、不相关或待定。')
        for identity in identities:
            row = self.paper(identity)
            row.update(human_decision=decision, human_reviewer=reviewer.strip(), human_note=note)
            self.state['history'].append({'time': datetime.now().isoformat(), 'paper': identity,
                                          'action': 'paper_decision', 'decision': decision, 'reviewer': reviewer, 'note': note})
        self.state['last_export'] = None
        self.save()

    def attach(self, identity, paths, role):
        if self.paper(identity).get('human_decision') != 'target':
            raise ValueError('先在第 2 步人工确认保留这篇原始研究论文。')
        if role not in {'primary', 'supplement', 'data'}:
            raise ValueError('请选择正文、补充材料或原始数据表。')
        linked = self.state['attachments'].setdefault(identity, [])
        for path in paths:
            path = Path(path).resolve()
            if path.suffix.lower() not in {'.pdf', '.html', '.htm', '.txt', '.md', '.csv', '.tsv', '.xlsx', '.json', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'}:
                raise ValueError('支持 PDF、HTML、文本、CSV、Excel 和 MinerU JSON。')
            if any(x['original'] == str(path) and x['role'] == role for x in linked):
                continue
            dest = self.folder / 'materials' / identity / role / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(stamp() + '_' + dest.name)
            shutil.copy2(path, dest)
            linked.append({'original': str(path), 'path': str(dest.relative_to(self.folder)), 'role': role})
        self.state['last_export'] = None
        self.save()

    def acquire_primary(self, identity, *, email=None, openalex_key=None, elsevier_key=None,
                        elsevier_insttoken=None, springer_key=None, skip_semantic=False):
        """Find a PDF by DOI and attach it only to the selected, approved paper."""
        paper = self.paper(identity)
        if paper.get('human_decision') != 'target':
            raise ValueError('先在第 2 步人工确认保留这篇论文。')
        if any(item['role'] == 'primary' for item in self.state['attachments'].get(identity, [])):
            return {'status': 'already_attached', 'source': None, 'url': None,
                    'errors': [], 'path': None}
        from .fulltext import acquire_pdf, publisher_route
        from .harvest import is_valid_pdf
        destination = self.folder / 'downloads' / identity / 'primary.pdf'
        capture = self.state.get('captures', {}).get(identity, {})
        browser_pdf = self.folder / capture['pdf_path'] if capture.get('pdf_path') else None
        prefetched = self.state.get('prefetch', {}).get(identity, {})
        cached = self.folder / prefetched['path'] if prefetched.get('path') else None
        if browser_pdf and is_valid_pdf(browser_pdf):
            destination = browser_pdf
            result = {'status': 'cached_pdf', 'path': str(browser_pdf),
                      'source': 'Chrome 插件下载', 'url': capture.get('source_url'),
                      'errors': [], 'message': '已从浏览器下载文件中添加正文，请核对标题和 DOI。'}
        elif cached and is_valid_pdf(cached):
            destination = cached
            result = {'status': 'cached_pdf', 'path': str(cached),
                      'source': prefetched.get('source') or '批量预取',
                      'url': prefetched.get('url'), 'errors': [],
                      'message': '已从批量预取材料中添加正文，请核对论文身份。'}
        else:
            result = acquire_pdf(paper, destination, email=email, openalex_key=openalex_key,
                                 elsevier_key=elsevier_key, elsevier_insttoken=elsevier_insttoken,
                                 springer_key=springer_key, skip_semantic=skip_semantic)
        if result.get('path'):
            self.attach(identity, [destination], 'primary')
            if browser_pdf and destination == browser_pdf:
                self._attach_capture_supplements(identity, capture)
        self.state.setdefault('acquisition', {})[identity] = {
            'status': result['status'], 'source': result.get('source'),
            'url': result.get('url'), 'errors': result.get('errors', []),
            'message': result.get('message', ''),
            'doi': paper.get('doi'), 'journal': paper.get('journal'),
            'publisher_route': publisher_route(paper),
            'time': datetime.now().isoformat(),
        }
        self.state['history'].append({'action': 'acquire_primary', 'paper': identity,
                                      'status': result['status'], 'time': datetime.now().isoformat()})
        self.save()
        return result

    def prefetch_screened(self, *, limit=15, progress=None, should_stop=None, **credentials):
        """Download promising unreviewed papers without admitting them as source material."""
        if not 1 <= limit <= 100:
            raise ValueError('每批预取数量需为 1–100 篇。')
        from .fulltext import acquire_pdf
        candidates = sorted((r for r in self.state['papers']
            if r.get('effective_decision') == 'target'
            and r.get('human_decision') not in {'target', 'non_target'}
            and r['record_id'] not in self.state.get('prefetch', {})),
            key=lambda r: -r.get('priority_score', 0))[:limit]
        if not candidates:
            raise ValueError('没有尚未尝试的初筛相关论文。可先在线检索或核对现有论文。')
        report = self.folder / '批量预取_最近一次.csv'
        columns = ['doi', 'title', 'result', 'source', 'message']
        rows = []
        for index, paper in enumerate(candidates, 1):
            if should_stop and should_stop():
                break
            identity = paper['record_id']
            destination = self.folder / 'prefetch' / identity / 'primary.pdf'
            try:
                result = acquire_pdf(paper, destination, skip_semantic=True, **credentials)
                state = '已预取待核对' if result.get('path') else '未获取'
                message = result.get('message') or ''
                source = result.get('source') or ''
                url = result.get('url')
                path = str(destination.relative_to(self.folder)) if result.get('path') else None
            except Exception as exc:
                from .harvest import safe_error
                state, source, message, url, path = '处理失败', '', safe_error(exc), None, None
            self.state.setdefault('prefetch', {})[identity] = {
                'status': state, 'path': path, 'source': source, 'url': url,
                'message': message, 'time': datetime.now().isoformat()}
            self.save()
            rows.append({'doi': paper.get('doi', ''), 'title': paper.get('title', ''),
                         'result': state, 'source': source, 'message': message})
            write_csv(report, rows, columns)
            if progress:
                progress(index, len(candidates), paper.get('title', ''), state)
        return {'rows': rows, 'report': str(report), 'stopped': len(rows) < len(candidates),
                'downloaded': sum(r['result'] == '已预取待核对' for r in rows),
                'failed': sum(r['result'] != '已预取待核对' for r in rows)}

    def acquire_many(self, identities, *, progress=None, should_stop=None, **credentials):
        """Try every kept paper in order; one provider failure does not stop the batch."""
        identities = list(dict.fromkeys(identities))
        if not identities:
            raise ValueError('没有待获取正文的已保留论文。')
        for identity in identities:
            if self.paper(identity).get('human_decision') != 'target':
                raise ValueError('批量获取只接受已人工保留的论文。')
        report = self.folder / '批量正文获取_最近一次.csv'
        rows = []
        columns = ['doi', 'title', 'result', 'source', 'message']
        for index, identity in enumerate(identities, 1):
            if should_stop and should_stop():
                break
            paper = self.paper(identity)
            try:
                result = self.acquire_primary(identity, skip_semantic=True, **credentials)
                state = ('已存在' if result['status'] == 'already_attached' else
                         '已获取' if result.get('path') else '未获取')
                message = result.get('message') or ('已绑定正文。' if state == '已存在' else '')
                source = result.get('source') or ''
            except Exception as exc:
                from .harvest import safe_error
                state, source, message = '处理失败', '', safe_error(exc)
            rows.append({'doi': paper.get('doi', ''), 'title': paper.get('title', ''),
                         'result': state, 'source': source, 'message': message})
            write_csv(report, rows, columns)
            if progress:
                progress(index, len(identities), paper.get('title', ''), state)
        return {'rows': rows, 'report': str(report), 'stopped': len(rows) < len(identities),
                'downloaded': sum(r['result'] == '已获取' for r in rows),
                'existing': sum(r['result'] == '已存在' for r in rows),
                'failed': sum(r['result'] not in {'已获取', '已存在'} for r in rows)}

    def extract(self, identities):
        identities = list(identities)
        # Check the whole selection before starting a batch.
        for identity in identities:
            if self.paper(identity).get('human_decision') != 'target':
                raise ValueError('只能提取已经人工保留的论文。')
            if not self.state['attachments'].get(identity):
                raise ValueError('所选论文中有尚未添加材料的论文。请先添加正文、补充材料或原始数据表。')
        reports = []
        for identity in identities:
            if self.paper(identity).get('human_decision') != 'target':
                raise ValueError('只能提取已经人工保留的论文。')
            attached = self.state['attachments'].get(identity, [])
            if not attached:
                raise ValueError('这篇论文还没有正文、补充材料或原始数据表。先添加文件。')
            output = self.folder / 'extractions' / identity / stamp()
            args = argparse.Namespace(input=str(self.folder / 'materials' / identity), output=str(output),
                    engine='rules', config=None, mapping=None, manifest=None, paper_id=identity,
                    max_chars=24000, reaction='NH3-SCR')
            with contextlib.redirect_stdout(io.StringIO()):
                run_extract(args)
            # Explicitly linked supplements inherit the primary-paper identity, never a guessed filename identity.
            records = read_json(output / 'candidates.json')
            sources = read_json(output / 'sources.json')
            for row in records + sources:
                row['doi'] = self.paper(identity).get('doi')
            for attachment in attached:
                if attachment['role'] != 'supplement':
                    continue
                file = str((self.folder / attachment['path']).resolve())
                for row in records + sources:
                    if row['source_file'] == file and row.get('document_type') in {'unknown', 'supplementary_information'}:
                        row.update(document_type='supplementary_information', training_eligible=True)
                        if 'issues' in row:
                            row['issues'] = [x for x in row['issues'] if x != 'unclassified_document']
            write_json(output / 'reviewed.json', records)
            write_json(output / 'workflow_sources.json', sources)
            self.state['runs'][identity] = str(output.relative_to(self.folder))
            reports.append(read_json(output / 'run_report.json'))
            self.state['last_export'] = None
            self.save()
        self.state['last_export'] = None
        self.save()
        return reports

    def run_path(self, identity):
        return self.folder / self.state['runs'][identity]

    def candidates(self):
        return [row for identity in self.state['runs'] if self.paper(identity).get('human_decision') == 'target'
                for row in read_json(self.run_path(identity) / 'reviewed.json')]

    def missing_blocks(self):
        return [dict(row, paper_id=identity) for identity in self.state['runs']
                if self.paper(identity).get('human_decision') == 'target'
                for row in read_json(self.run_path(identity) / 'review_queue.json')]

    def source(self, identity, block_id):
        return next(r for r in read_json(self.run_path(identity) / 'workflow_sources.json') if r['block_id'] == block_id)

    def review(self, ids, decision, reviewer, note=''):
        if decision not in {'approve', 'reject'} or not reviewer.strip():
            raise ValueError('请填写核对人并选择通过或排除。')
        identities = set(ids)
        selected = [r for r in self.candidates() if r['record_id'] in identities]
        if len(selected) != len(identities):
            raise ValueError('所选数据已变化，请刷新后重选。')
        if decision == 'approve':
            for row in selected:
                if row['issues'] or row['value'] is None or not row['catalyst']:
                    raise ValueError('这条数据还有待解决的问题：' + '；'.join(issue_label(c) for c in row['issues']) + '。请先修正或暂时排除。')
        for identity in {r['paper_id'] for r in selected}:
            path = self.run_path(identity) / 'reviewed.json'
            rows = read_json(path)
            for row in rows:
                if row['record_id'] in identities:
                    row.update(review_status='approved' if decision == 'approve' else 'rejected', reviewer=reviewer, review_note=note)
                    self.state['history'].append({'time': datetime.now().isoformat(), 'action': 'measurement_review',
                                                 'record': row['record_id'], 'decision': decision, 'reviewer': reviewer, 'note': note})
            write_json(path, rows)
        self.state['last_export'] = None
        self.save()

    def manual_record(self, identity, block_id, catalyst, prop, raw, unit, evidence,
                      reviewer, temperature='', temperature_evidence='', temperature_unit='degC', old_id=None):
        if not reviewer.strip():
            raise ValueError('请填写核对人。')
        block = self.source(identity, block_id)
        path = self.run_path(identity) / 'reviewed.json'
        rows = read_json(path)
        previous = next((r for r in rows if r['record_id'] == old_id), None) if old_id else None
        if old_id and previous is None:
            raise ValueError('原记录已变化，无法修正。')
        if not str(raw).strip() or str(raw).strip() not in evidence:
            raise ValueError('数值必须出现在你填写的原文证据中。请照原文录入，不要填推测值。')
        conditions = dict(previous.get('conditions', {})) if previous else {}
        if temperature:
            if str(temperature) not in temperature_evidence:
                raise ValueError('温度数值必须出现在温度证据原文中。')
            conditions['temperature'] = {'raw_value': temperature, 'unit': temperature_unit, 'evidence': temperature_evidence}
        record = make_record(block, catalyst, prop, raw, unit, evidence, conditions, method='curated_annotation',
                             experiment_id=previous.get('experiment_id') if previous else None)
        record.update(annotation_author=reviewer, annotation_note='Manually entered/corrected in the source-preview workbench')
        if any(r['record_id'] == record['record_id'] and r['record_id'] != old_id for r in rows):
            raise ValueError('这条补录数据已经存在。')
        rows = [r for r in rows if r['record_id'] != old_id]
        rows.append(record)
        self.state['history'].append({'time': datetime.now().isoformat(), 'action': 'manual_observation',
                                      'previous_record': previous, 'new_record': record, 'reviewer': reviewer})
        write_json(path, rows)
        self.state['last_export'] = None
        self.save()
        return record

    def confirm_primary_document(self, identity, source_file, reviewer, evidence):
        if not reviewer.strip() or len(evidence.strip()) < 12:
            raise ValueError('请填写核对人，并复制能证明这是原始研究的原文（至少 12 字符）。')
        path = self.run_path(identity)
        sources = read_json(path / 'workflow_sources.json')
        selected = [b for b in sources if b['source_file'] == source_file]
        if not selected or not any(evidence in b['text'] for b in selected):
            raise ValueError('证明原始研究的文字必须出现在这份文件的原文中。')
        if any(b.get('document_type') == 'review' for b in selected):
            raise ValueError('这份文件被识别为综述，不能用此按钮直接改为原始实验。请先核查论文类型。')
        rows = read_json(path / 'reviewed.json')
        for row in rows + sources:
            if row['source_file'] == source_file:
                row.update(document_type='research_article', training_eligible=True, document_type_confidence=None,
                           document_type_review={'reviewer':reviewer, 'evidence':evidence, 'method':'manual'})
                if 'issues' in row:
                    row['issues'] = [x for x in row['issues'] if x != 'unclassified_document']
        write_json(path / 'reviewed.json', rows)
        write_json(path / 'workflow_sources.json', sources)
        self.state['history'].append({'action':'confirm_primary_document', 'reviewer':reviewer,
                                     'source_file':source_file, 'evidence':evidence, 'time':datetime.now().isoformat()})
        self.state['last_export'] = None
        self.save()

    def note_missing(self, identity, block_id, reviewer, note):
        self.source(identity, block_id)
        if not reviewer.strip() or not note.strip():
            raise ValueError('请填写核对人与这页需要补提的内容。')
        self.state.setdefault('block_notes', {})[identity + ':' + block_id] = {'reviewer':reviewer, 'note':note}
        self.save()

    def export(self):
        rows = self.candidates()
        if not any(r['review_status'] == 'approved' for r in rows):
            raise ValueError('还没有通过审核的数据。先在第 4 步核对原文并通过至少一条记录。')
        output = self.folder / 'exports' / stamp()
        output.mkdir(parents=True)
        write_json(output / 'reviewed.json', rows)
        with contextlib.redirect_stdout(io.StringIO()):
            run_export(argparse.Namespace(input=str(output / 'reviewed.json'), output=str(output)))
            feature_rows = build_features(rows, output)
        report = read_json(output / 'export_report.json')
        report.update(feature_rows=len(feature_rows), pending=sum(r['review_status']=='pending' for r in rows),
                      papers=len({r['paper_id'] for r in rows}),
                      unresolved_features=len(read_json(output / 'ambiguous_features.json')),
                      machine_learning_ready=False)
        write_json(output / 'workflow_report.json', report)
        for original,label in [('reviewed_measurements.csv','已审核观测.csv'),('ml_long.csv','性能数据长表.csv'),
                               ('ml_features.csv','样品特征表.csv'),('withheld.csv','暂缓导出记录.csv')]:
            shutil.copy2(output / original,output / label)
        write_csv(output / '论文审核清单.csv', self.state['papers'])
        write_json(output / '语义候选.json', self.claims())
        write_csv(output / '语义候选.csv', self.claims())
        report['semantic_candidates'] = len(self.claims())
        write_json(output / 'workflow_report.json', report)
        self.state['last_export'] = str(output.relative_to(self.folder))
        self.save()
        return output, report

"""Local, article-centred evidence and handoff layer; does not train a model."""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

from pypdf import PdfReader
from intake import load_screened_input
from pipeline import load_batch, build_batch, digest

SCHEMA='paper-evidence-workspace/1.0'
TASKS={
    'pending': {'title':'先整理证据，预测目标待确认','metric':'','units':[], 'conditions':[]},
    'scr_conversion': {'title':'NH₃-SCR：在给定条件下预测 NO 转化率','metric':'NO conversion','units':['%','fraction'],
                       'conditions':['temperature_C','feed_description']},
    'scr_nox_conversion': {'title':'NH₃-SCR：在给定条件下预测 NOₓ 总转化率','metric':'NOx conversion','units':['%','fraction'],
                           'conditions':['temperature_C','feed_description']},
    'scr_selectivity': {'title':'NH₃-SCR：在给定条件下预测 N₂ 选择性','metric':'N2 selectivity','units':['%','fraction'],
                        'conditions':['temperature_C','feed_description']},
    'nh3_adsorption_energy': {'title':'NH₃ 吸附：预测指定表面和位点的 DFT 吸附能','metric':'NH3 adsorption energy','units':['eV','kJ/mol'],
                              'conditions':['surface_site','calculation_method']},
    'cuzn_co2_conversion': {'title':'CuZn 制甲醇：预测 CO₂ 转化率','metric':'CO2 conversion','units':['%','fraction'],
                      'conditions':['temperature_C','pressure_kPa','feed_description']},
    'cuzn_methanol_selectivity': {'title':'CuZn 制甲醇：预测甲醇选择性','metric':'methanol selectivity','units':['%','fraction'],
                      'conditions':['temperature_C','pressure_kPa','feed_description']},
    'cuzn_co_selectivity': {'title':'CuZn 制甲醇：预测 CO 选择性','metric':'CO selectivity','units':['%','fraction'],
                      'conditions':['temperature_C','pressure_kPa','feed_description']},
    'cuzn_methanol_yield': {'title':'CuZn 制甲醇：预测甲醇收率','metric':'methanol yield','units':['%','fraction'],
                      'conditions':['temperature_C','pressure_kPa','feed_description']},
    'cuzn_methanol_sty': {'title':'CuZn 制甲醇：预测甲醇时空收率','metric':'methanol STY','units':['g/kgcat/h'],
                      'conditions':['temperature_C','pressure_kPa','feed_description']},
    'cuzn_tof': {'title':'CuZn 制甲醇：预测周转频率 TOF','metric':'TOF','units':['h^-1'],
                      'conditions':['temperature_C','pressure_kPa','feed_description']},
}
CONDITION_LABELS={'temperature_C':'反应温度（°C）','pressure_kPa':'压力（kPa）','space_velocity_h_inv':'空速（h⁻¹）',
    'flow_rate_ml_min':'气体流量（mL/min）','catalyst_mass_g':'催化剂用量（g）','feed_description':'进料组成及浓度',
    'surface_site':'表面 / 晶面 / 吸附位点','calculation_method':'计算方法及能量定义','other':'其他条件'}
NUMERIC_CONDITIONS={'temperature_C','pressure_kPa','space_velocity_h_inv','flow_rate_ml_min','catalyst_mass_g'}
CONDITION_LABELS.update({
    'feed_NO_ppm':'进料NO（ppm；未知留空）', 'feed_NO2_ppm':'进料NO₂（ppm；未知留空）',
    'feed_NH3_ppm':'进料NH₃（ppm；未知留空）', 'feed_O2_vol_pct':'进料O₂（vol.%）',
    'feed_H2O_vol_pct':'进料水蒸气（vol.%；无水须有依据才填0）', 'feed_SO2_ppm':'进料SO₂（ppm；无硫须有依据才填0）',
    'h2_co2_molar_ratio':'进料H₂/CO₂摩尔比（CuZn任务）', 'reaction_time_h':'本性能点的反应/运行时间（h）'})
NUMERIC_CONDITIONS.update({'feed_NO_ppm','feed_NO2_ppm','feed_NH3_ppm','feed_O2_vol_pct',
    'feed_H2O_vol_pct','feed_SO2_ppm','h2_co2_molar_ratio','reaction_time_h'})
RECORD_FIELDS=('task_id','sample_label','experiment_id','composition','metric','value','value_high','unit','operator',
               'conditions','evidence_ids','assertion_scope','measurement_type','notes')
SEMANTIC_USES={'pending':'待决定如何采纳','qualitative':'保留为定性结论','explanation':'保留为机理 / 解释',
               'comparison':'保留为比较关系','constraint':'保留为比值 / 计算约束',
               'condition':'保留为条件 / 适用范围','durability':'保留为耐久性 / 失活证据','quality':'保留为误差 / 检测限信息',
               'background':'仅作背景 / 展望','exclude':'不采用'}


def now():return datetime.now(timezone.utc).isoformat()


def stable_id(*parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True,default=str).encode('utf8')).hexdigest()[:24]


def _write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex[:8]+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    os.replace(tmp,path)


def workspace_path(project):return Path(project['run_dir'])/'paper.json'


def save_project(project, check_revision=True):
    path=workspace_path(project)
    if check_revision and path.is_file():
        disk=json.loads(path.read_text(encoding='utf8'))
        if disk.get('revision')!=project.get('revision'):
            raise ValueError('本篇论文已被另一个窗口更新。请刷新后再保存，避免覆盖别人的审核。')
    project['revision']=int(project.get('revision',0))+1
    project['updated_at']=now()
    _write(path,project)
    return path


def verify_source(project):
    article=project['article']
    if digest(article['local_pdf'])!=article['source_sha256']:
        raise ValueError('原 PDF 已改变；本篇旧证据暂不导出，请重新接收并建立新论文档案。')


def load_project(path):
    value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if value.get('schema_version')!=SCHEMA:raise ValueError('请选择单篇论文工作台的 paper.json。')
    value['run_dir']=str(Path(path).resolve().parent)
    verify_source(value)
    return value


def list_papers(screening_run=None,batch_path=None):
    if batch_path:
        batch=load_batch(batch_path)
        papers=[dict(p,batch_path=str(Path(batch_path).resolve())) for p in batch.get('papers',[])]
        return papers,batch.get('waiting',[])
    intake=load_screened_input(screening_run)
    papers=[dict(p,screening_run=str(Path(screening_run).resolve())) for p in intake['papers']]
    return papers,intake.get('waiting',[])


def open_paper(paper,output_root):
    article=copy.deepcopy(paper)
    actual=digest(article['local_pdf'])
    if article.get('source_sha256') and actual!=article['source_sha256']:raise ValueError('接收的 PDF 已变化。')
    article['source_sha256']=actual
    key=stable_id(article.get('doi','').lower(),actual)
    root=Path(output_root).resolve()/key
    if (root/'paper.json').is_file():
        project=load_project(root/'paper.json')
        # A new batch may refer to the same immutable PDF. Preserve evidence
        # and reviews; only refresh the routing metadata.
        for field in ('batch_path','paper_id','screening_run','record_id','local_pdf'):
            if article.get(field):project['article'][field]=article[field]
        save_project(project)
        return project
    project={'schema_version':SCHEMA,'project_id':key,'run_dir':str(root),'revision':0,'created_at':now(),
             'article':article,'task_id':'scr_conversion' if article.get('profile')=='scr_ammonia' else 'pending',
             'pages':[],'evidence':[],'records':[],'events':[],'exports':[],
             'branch_status':{'text':'not_started','semantic':'not_started','image':'not_started'},
             'scope':'候选证据与人工核对框架；未训练模型，未把候选数据声明为机器学习可用数据。'}
    save_project(project)
    return project


def _event(project,action,detail,reviewer=''):
    project['events'].append({'at':now(),'action':action,'reviewer':reviewer,'detail':copy.deepcopy(detail)})


def set_task(project,task_id):
    if task_id not in TASKS:raise ValueError('未知的预测任务。')
    project['task_id']=task_id
    _event(project,'set_research_task',task_id)
    save_project(project)


def extract_document(project):
    """Retain PDF page text, raw table passages, and linked semantic candidates."""
    verify_source(project)
    from paper_semantics import semantic_candidates
    from paper_numeric import numeric_facts
    from paper_relations import relation_candidates
    from paper_semantic_facets import enrich_candidates
    from paper_semantic_audit import source_span_issues
    reader=PdfReader(project['article']['local_pdf'])
    if len(reader.pages)>1000:raise ValueError('单篇页数超过本框架处理上限，请先核对是否选中了合订本。')
    old={e['evidence_id']:e for e in project['evidence']}
    pages=[];new=[];flags=[]
    source_hash=project['article']['source_sha256'];references_started=False
    for number,page in enumerate(reader.pages,1):
        text=page.extract_text() or ''
        if len(text)>300000:
            text=text[:300000];flags.append(f'第 {number} 页超过文字上限，本次只读前 300000 字符。')
        pages.append({'page':number,'text':text,'text_sha256':hashlib.sha256(text.encode('utf8')).hexdigest(),
                      'status':'text_ready' if len(text.strip())>=20 else 'needs_ocr'})
        if len(text.strip())<20:flags.append(f'第 {number} 页文字层不足，等待扫描页 OCR；不表示本页没有数据。')
        for match in re.finditer(r'(?im)^\s*(?:table\s*\d+[a-z]?|表\s*\d+)\b[^\n]*(?:\n[^\n]*){0,4}',text):
            new.append({'evidence_id':stable_id(source_hash,'table',number,match.start(),match.end()),
                'branch':'text','kind':'table_passage','page':number,'quote':match.group(),
                'start':match.start(),'end':match.end(),'review_status':'unreviewed','usable':False,
                'source_ref':{'source_sha256':source_hash,'page':number,'extraction':'pdf_text_layer'},
                'warnings':['这是表格文字候选段，尚未恢复行列和表头；请对照 PDF 录入或核对单元格。']})
        for item in numeric_facts(text):
            start,end=item.get('start'),item.get('end');quote=item.get('evidence_quote','')
            if not isinstance(start,int) or not isinstance(end,int) or not quote or text[start:end]!=quote:
                flags.append(f'第 {number} 页一个数值候选没有准确原文位置，已跳过。');continue
            eid=stable_id(source_hash,'numeric',number,start,end,item.get('metric'),item.get('value'),item.get('value_high'),item.get('operator'))
            new.append({**item,'evidence_id':eid,'branch':'text','page':number,'quote':quote,'usable':False,
                        'source_ref':{'source_sha256':source_hash,'page':number,'extraction':'local_numeric_rules',
                                      'start':start,'end':end}})
        precise=semantic_candidates(text)
        relation_text='' if references_started else text
        if re.search(r'(?im)^\s*(?:References|Bibliography|参考文献)\s*$',text):references_started=True
        expanded=relation_candidates(relation_text,existing=precise) if relation_text else []
        candidates=enrich_candidates(relation_text,[*precise,*expanded]) if relation_text else precise
        for item in candidates:
            # Reject mismatched quotations even when a future parser/provider
            # returns plausible JSON. Evidence must exist on the cited page.
            start,end=item.get('start'),item.get('end')
            quote=item.get('evidence_quote','')
            if not isinstance(start,int) or not isinstance(end,int) or not quote or text[start:end]!=quote:
                flags.append(f'第 {number} 页一个语义候选没有准确原文位置，已跳过。');continue
            if source_span_issues(item,text):
                flags.append(f'第 {number} 页一个语义候选的关系或条件引文未通过原文核验，已跳过。');continue
            identity=[source_hash,'semantic',number,start,end,item.get('kind'),item.get('metric'),item.get('operator')]
            if item.get('semantic_id_key'):identity.append(item['semantic_id_key'])
            eid=stable_id(*identity)
            new.append({**item,'evidence_id':eid,'branch':'semantic','page':number,'quote':quote,'usable':False,
                        'source_ref':{'source_sha256':source_hash,'page':number,'extraction':item.get('extraction_method') or item.get('candidate_origin') or 'local_semantic_rules',
                                      'start':start,'end':end}})
    kept=[e for e in project['evidence'] if e['branch']=='image' or e.get('manual_capture')]
    seen={e['evidence_id'] for e in kept}
    for item in new:
        if item['evidence_id'] in seen:continue
        seen.add(item['evidence_id'])
        prior=old.get(item['evidence_id'])
        if prior:
            sensitive=('quote','kind','metric','value','value_high','unit','operator','sample_label','reference_sample','conditions','assertion_scope','uncertainty','relations','context_evidence','quantity_role','field_key','source_table','assertion_mode','unresolved_subject','semantic_facets','semantic_roles','conditions_mentions','assertion_status')
            baseline=dict(prior)
            if prior.get('human_annotation'):baseline.update(prior.get('parser_interpretation',{}))
            if all(baseline.get(k)==item.get(k) for k in sensitive):
                for key in ('review_status','reviewer','reviewed_at','review_note','human_annotation','parser_interpretation','semantic_adoption','previous_interpretation'):
                    if key in prior:item[key]=prior[key]
                if prior.get('human_annotation'):
                    for key in ('sample_label','reference_sample','assertion_scope'):item[key]=prior.get(key,'')
            elif prior.get('review_status') in ('reviewed','excluded') or prior.get('human_annotation') or prior.get('semantic_adoption') or prior.get('previous_interpretation'):
                item['previous_interpretation']=copy.deepcopy(prior)
                item['review_status']='unreviewed'
                item.setdefault('warnings',[]).append('语义解析结果已更新，旧判断保留在审核历史；本条需重新核对。')
        kept.append(item)
    # Human-reviewed or annotated evidence must survive parser migrations,
    # even before a unified record has been created from it.
    referenced={eid for record in project['records'] for eid in record.get('evidence_ids',[])}
    for eid,item in old.items():
        has_human_work=bool(item.get('human_annotation') or item.get('semantic_adoption') or item.get('reviewer') or item.get('previous_interpretation') or item.get('review_status') in ('reviewed','excluded','stale'))
        if eid not in seen and (eid in referenced or has_human_work) and item not in kept:
            legacy=copy.deepcopy(item);legacy.update(stale=True,review_status='stale',usable=False)
            legacy.setdefault('warnings',[]).append('重读后该证据已被替换，原始内容保留；请重新关联当前证据。')
            kept.append(legacy)
    project.update(pages=pages,evidence=kept,text_warnings=flags,numeric_extraction_version='scientific-numeric/1.0',
                   semantic_extraction_version='scientific-relations/2.0')
    project['branch_status'].update(text='partial' if flags else 'ready',semantic='candidates_need_review')
    _event(project,'extract_document',{'pages':len(pages),'semantic_candidates':sum(e['branch']=='semantic' for e in kept),
        'numeric_facts':sum(e.get('kind') in ('numeric_fact','table_value') and not e.get('stale') for e in kept),
        'numeric_extraction_version':project['numeric_extraction_version'],'semantic_extraction_version':project['semantic_extraction_version']})
    save_project(project)
    return project


def add_text_evidence(project,page,start,end,kind='text_passage'):
    text=next((p['text'] for p in project['pages'] if p['page']==page),'')
    if not 0<=start<end<=len(text):raise ValueError('请选择原文中的连续文字。')
    eid=stable_id(project['article']['source_sha256'],'manual_text',page,start,end)
    existing=next((e for e in project['evidence'] if e['evidence_id']==eid),None)
    if existing:return existing
    item={'evidence_id':eid,'branch':'text','kind':kind,'page':page,'start':start,'end':end,
          'quote':text[start:end],'review_status':'unreviewed','usable':False,'manual_capture':True,
          'source_ref':{'source_sha256':project['article']['source_sha256'],'page':page,'start':start,'end':end},'warnings':[]}
    project['evidence'].append(item);_event(project,'capture_text',eid);save_project(project)
    return item


def review_evidence(project,evidence_id,decision,reviewer):
    if decision not in ('reviewed','excluded','unreviewed'):raise ValueError('未知核对状态。')
    if not reviewer.strip():raise ValueError('请在顶部填写审核人姓名或组员代号。')
    item=next(e for e in project['evidence'] if e['evidence_id']==evidence_id)
    if item.get('stale'):raise ValueError('旧版本证据已过期，请选择重读后的当前证据核对。')
    if item['branch']=='image':raise ValueError('图片读数请在读图窗口对照原图核对并重新导出，再在这里刷新。')
    if decision!='excluded' and item.get('semantic_adoption',{}).get('purpose')=='exclude':
        raise ValueError('这句话已决定不采用；若要恢复，请在“标注如何采纳”中修改决定。')
    item['review_status']=decision;item['reviewer']=reviewer.strip();item['reviewed_at']=now()
    _event(project,'review_evidence',{'id':evidence_id,'decision':decision},reviewer);save_project(project)


def set_semantic_adoption(project,evidence_id,purpose,reviewer,note,confirm=False):
    """Record a human use decision; no relation becomes an absolute target."""
    if purpose not in SEMANTIC_USES:raise ValueError('请选择一种有效采纳方式。')
    if not reviewer.strip():raise ValueError('请填写审核人 / 组员代号。')
    if purpose!='pending' and not note.strip():raise ValueError('请写明为何采用或不采用，保留复核依据。')
    item=next(e for e in project['evidence'] if e['evidence_id']==evidence_id)
    if item['branch']!='semantic' or item.get('stale'):raise ValueError('请选择当前有效的语义候选。')
    before=copy.deepcopy(item)
    item['semantic_adoption']={'purpose':purpose,'note':note.strip(),'reviewer':reviewer.strip(),'at':now()}
    item['review_status']='excluded' if purpose=='exclude' else ('reviewed' if confirm else 'unreviewed')
    item['reviewer']=reviewer.strip();item['reviewed_at']=now();item['usable']=False
    _event(project,'set_semantic_adoption',{'before':before,'after':item},reviewer)
    save_project(project)
    return item


def annotate_semantics(project,evidence_id,updates,reviewer,note):
    """Record human entity/role corrections while preserving the raw numbers."""
    if not reviewer.strip() or not note.strip():raise ValueError('请填写审核人和修改依据，便于复核。')
    item=next(e for e in project['evidence'] if e['evidence_id']==evidence_id)
    if item['branch']!='semantic' or item.get('stale'):raise ValueError('只能补充当前语义候选的归属。')
    if updates.get('assertion_scope','unknown') not in ('unknown','current_study','prior_work'):raise ValueError('结果归属无效。')
    before=copy.deepcopy(item)
    if 'parser_interpretation' not in item:
        item['parser_interpretation']={key:copy.deepcopy(item.get(key)) for key in ('sample_label','reference_sample','assertion_scope','conditions')}
    for key in ('sample_label','reference_sample','assertion_scope'):
        if key in updates:item[key]=str(updates[key]).strip()
    item['human_annotation']={'reviewer':reviewer.strip(),'at':now(),'note':note.strip(),
                              'fields':{key:item.get(key) for key in ('sample_label','reference_sample','assertion_scope')}}
    item['review_status']='unreviewed';item['review_note']=note.strip()
    for record in project['records']:
        if evidence_id in record.get('evidence_ids',[]) and record.get('review_status')=='reviewed':
            record['review_status']='draft';record['reviewer']=''
    _event(project,'annotate_semantics',{'before':before,'after':copy.deepcopy(item)},reviewer);save_project(project)


def refresh_images(project):
    from paper_image_bridge import read_image_evidence
    verify_source(project)
    article=project['article']
    if not article.get('batch_path'):
        project['image_warnings']=['本篇还没有关联图片批次，先打开本篇图片审核。']
        save_project(project)
        return project
    result=read_image_evidence(article['batch_path'],article['paper_id'])
    valid_items=[]
    for item in result['items']:
        if item.get('source_ref',{}).get('source_sha256')!=article['source_sha256']:
            result['warnings'].append('有图片证据来自另一份 PDF，已拒绝并标记旧关联过期；请重新关联本篇图片批次。')
        else:valid_items.append(item)
    # Preserve removed evidence for audit, mark stale and block its records.
    current={v['evidence_id']:v for v in valid_items}
    for e in project['evidence']:
        if e['branch']=='image' and e['evidence_id'] not in current:
            e['usable']=False;e['stale']=True;e['review_status']='stale'
    for e in current.values():
        previous=next((i for i,v in enumerate(project['evidence']) if v['evidence_id']==e['evidence_id']),None)
        if previous is None:project['evidence'].append(e)
        else:project['evidence'][previous]=e
    project['image_warnings']=list(dict.fromkeys(result['warnings']))
    project['branch_status']['image']='ready' if current else 'waiting_for_readings'
    _event(project,'refresh_image_evidence',{'points':len(current),'warnings':len(result['warnings'])})
    save_project(project)
    return project


def ensure_image_batch(project,image_output_root,progress=None):
    article=project['article'];verify_source(project)
    if article.get('batch_path') and Path(article['batch_path']).is_file():
        batch=load_batch(article['batch_path'])
        match=next((p for p in batch['papers'] if p['paper_id']==article['paper_id']),None)
        if not match or match.get('source_sha256')!=article['source_sha256']:
            raise ValueError('已关联图片批次中的论文与当前 PDF 不一致，请从正确批次重新打开本篇。')
        return batch
    if not article.get('screening_run'):raise ValueError('缺少板块1筛选记录，请从板块1进入，或打开已有图片批次。')
    # Reuse a prior batch with this exact PDF, retaining all manual reviews.
    root=Path(image_output_root)
    candidates=sorted(root.glob('*/batch.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:120]
    for path in candidates:
        try:
            batch=load_batch(path)
            found=next((p for p in batch['papers'] if p.get('source_sha256')==article['source_sha256'] and p.get('scan_status')=='scanned'),None)
            if found:
                article.update(batch_path=str(path.resolve()),paper_id=found['paper_id']);save_project(project);return batch
        except (ValueError,OSError,KeyError):continue
    batch=build_batch(article['screening_run'],output_root=image_output_root,only_record_ids=[article['record_id']],progress=progress)
    found=next((p for p in batch['papers'] if p.get('record_id')==article['record_id']),None)
    if not found:raise ValueError('本篇未进入可读 PDF 队列，请回到板块1核对当前保留状态。')
    article.update(batch_path=str(Path(batch['run_dir'])/'batch.json'),paper_id=found['paper_id'])
    save_project(project)
    return batch


def finite_number(value,blank=True):
    if value is None or str(value).strip()=='':
        if blank:return None
        raise ValueError('请填写数值。')
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):
        raise ValueError('数值应为数字，不能用布尔值或列表替代。')
    number=float(value)
    if not math.isfinite(number):raise ValueError('数值必须是有限数字，缺失值请留空。')
    return number


def record_from_evidence(project,evidence_ids):
    entries=[e for e in project['evidence'] if e['evidence_id'] in evidence_ids]
    if not entries:raise ValueError('先选中一条原文、语义或图片证据。')
    if len(entries)!=1:raise ValueError('先从一条数值证据建立记录，再关联同一实验的补充证据。')
    item=entries[0]
    if item.get('kind') in ('numeric_fact','table_value') and item.get('quantity_role')!='performance':
        raise ValueError('这条是实验条件、材料属性或其他结果，不能当成当前性能标签。请使用“补充条件 / 特征”。')
    conditions=copy.deepcopy(item.get('conditions',{}))
    original_conditions=copy.deepcopy(conditions)
    if item.get('branch')=='image' and re.search(r'temperature|温度',str(item.get('x_name','')),re.I) and item.get('x_unit') in ('°C','℃'):
        conditions['temperature_C']=item.get('x_value')
        for key in ('x_name','x_value','x_unit'):conditions.pop(key,None)
    temperature=conditions.get('temperature')
    if (isinstance(temperature,dict) and temperature.get('unit') in ('°C','℃')
            and temperature.get('operator','eq')=='eq' and temperature.get('value_high') is None):
        conditions['temperature_C']=temperature.get('value');conditions.pop('temperature')
    return {'record_id':uuid.uuid4().hex,'task_id':project['task_id'],'sample_label':item.get('sample_label',''),
        'experiment_id':'','composition':'','metric':item.get('metric') or TASKS[project['task_id']]['metric'],
        'value':item.get('value'),'value_high':item.get('value_high'),'unit':item.get('unit',''),
        'operator':item.get('operator','eq'),
        'conditions':conditions,'evidence_ids':list(evidence_ids),
        'evidence_roles':{eid:'value' for eid in evidence_ids},
        'source_conditions':original_conditions,
        'preparation':'','characterization':'','missing_reason':'','features':{},'sample_mapping_note':'',
        'assertion_scope':item.get('assertion_scope','unknown'),'measurement_type':'unknown',
        'review_status':'draft','reviewer':'','notes':''}


def _checked_record(project,record,reviewer='',confirm=False):
    item=copy.deepcopy(record)
    if item.get('task_id') not in TASKS:raise ValueError('请选择一个有效任务。')
    if not item.get('evidence_ids'):raise ValueError('统一记录必须关联原文或图像证据。')
    known={e['evidence_id'] for e in project['evidence']}
    if any(i not in known for i in item['evidence_ids']):raise ValueError('证据不属于本篇论文。')
    item['evidence_roles']={eid:item.get('evidence_roles',{}).get(eid,'value') for eid in item['evidence_ids']}
    if any(role not in ('value','context') for role in item['evidence_roles'].values()):raise ValueError('证据用途只能是数值来源或补充条件。')
    for field in ('value','value_high'):item[field]=finite_number(item.get(field))
    for key in NUMERIC_CONDITIONS:
        if key in item.get('conditions',{}):item['conditions'][key]=finite_number(item['conditions'][key])
    from paper_encoding import FEATURE_SCHEMA, feature_issues
    for key,spec in FEATURE_SCHEMA.items():
        if spec.get('kind')=='numeric' and key in item.get('features',{}):
            item['features'][key]=finite_number(item['features'][key])
    feature_errors=feature_issues(item)
    if feature_errors:raise ValueError('；'.join(feature_errors))
    if item.get('operator')=='range' and item['value'] is not None and item['value_high'] is not None and item['value']>item['value_high']:
        raise ValueError('区间下限不能大于上限。')
    if confirm and not reviewer.strip():raise ValueError('请填写审核人姓名或组员代号。')
    if confirm and not str(item.get('sample_mapping_note') or '').strip():
        normalize=lambda value:re.sub(r'\s+',' ',str(value or '').strip()).casefold()
        for eid in item['evidence_ids']:
            if item['evidence_roles'].get(eid,'value')!='value':continue
            evidence=next(e for e in project['evidence'] if e['evidence_id']==eid)
            source_name=evidence.get('sample_label') or (evidence.get('series_label') if evidence['branch']=='image' else '')
            if source_name and normalize(source_name)!=normalize(item.get('sample_label')):
                raise ValueError('记录样品名与原句 / 图例不同，请填写“图例与样品对应依据”，说明它们为何属于同一样品。')
    return item


def put_record(project,record,reviewer='',confirm=False):
    item=_checked_record(project,record,reviewer,confirm)
    previous=next((r for r in project['records'] if r['record_id']==item['record_id']),None)
    if previous:project['records'].remove(previous)
    item.update(review_status='reviewed' if confirm else 'draft',reviewer=reviewer.strip() if confirm else '',updated_at=now())
    project['records'].append(item)
    _event(project,'save_unified_record',{'before':previous,'after':item},reviewer if confirm else '')
    save_project(project);return item


def put_image_records(project,evidence_ids,template,reviewer='',confirm=False):
    """Use one reviewed series template; retain each point's own coordinates."""
    ids=list(dict.fromkeys(evidence_ids))
    entries=[next((e for e in project['evidence'] if e['evidence_id']==eid),None) for eid in ids]
    if not entries or any(not e or e['branch']!='image' or not e.get('usable') or e.get('stale') for e in entries):
        raise ValueError('批量整理只接收已在读图窗口核验并导出的当前图点。')
    groups={(e.get('source_ref',{}).get('session_id'),e.get('series_label','')) for e in entries}
    if len(groups)!=1:raise ValueError('请只选择同一个读图项目、同一条系列的点，分别填写各样品的共同条件。')
    planned=[]
    existing={eid for r in project['records'] if r.get('review_status')!='excluded' for eid in r.get('evidence_ids',[]) if r.get('evidence_roles',{}).get(eid,'value')=='value'}
    if any(e['evidence_id'] in existing for e in entries):raise ValueError('所选图点已有统一记录，请编辑已有记录，避免批量重复建立。')
    for evidence in entries:
        item=record_from_evidence(project,[evidence['evidence_id']])
        for key in ('task_id','sample_label','sample_mapping_note','experiment_id','composition','preparation','characterization','features','assertion_scope','measurement_type','notes','missing_reason','metric'):
            if key in template:item[key]=copy.deepcopy(template[key])
        point_conditions=item['conditions'];item['conditions']=copy.deepcopy(template.get('conditions',{}))
        item['conditions'].update(point_conditions)
        # y, raw unit and any mapped x condition always come from this point.
        checked=_checked_record(project,item,reviewer,confirm)
        checked.update(review_status='reviewed' if confirm else 'draft',reviewer=reviewer.strip() if confirm else '',updated_at=now())
        planned.append(checked)
    project['records'].extend(planned)
    _event(project,'create_records_from_reviewed_series',{'evidence_ids':ids,'record_ids':[r['record_id'] for r in planned],
        'common_template':template,'point_values_preserved':True},reviewer if confirm else '')
    save_project(project);return planned


def attach_context(project,record_id,evidence_id,reviewer):
    """Attach material/conditions/interpretation without creating a new label."""
    if not reviewer.strip():raise ValueError('请填写审核人 / 组员代号。')
    record=next(r for r in project['records'] if r['record_id']==record_id)
    if record['review_status']=='excluded':raise ValueError('该记录已不采用，请选择其他记录。')
    evidence=next(e for e in project['evidence'] if e['evidence_id']==evidence_id)
    if evidence['branch']=='image':raise ValueError('图片的数值证据请建立记录后核对；相同记录可合并证据。')
    before=copy.deepcopy(record)
    roles=record.setdefault('evidence_roles',{eid:'value' for eid in record['evidence_ids']})
    if evidence_id not in record['evidence_ids']:
        record['evidence_ids'].append(evidence_id);roles[evidence_id]='context'
    record['review_status']='draft';record['reviewer']=''
    _event(project,'attach_context_evidence',{'before':before,'record_id':record_id,'evidence_id':evidence_id},reviewer)
    save_project(project)


def exclude_record(project,record_id,reviewer):
    if not reviewer.strip():raise ValueError('请填写审核人。')
    record=next(r for r in project['records'] if r['record_id']==record_id)
    record['review_status']='excluded';record['reviewer']=reviewer.strip()
    _event(project,'exclude_record',record_id,reviewer);save_project(project)


def _identity(record):
    if not all(record.get(k) for k in ('sample_label','experiment_id','metric')):return None
    # Raw plot coordinates remain evidence provenance. They must not prevent
    # duplicate detection against the same test transcribed from prose.
    conditions={k:v.strip() if isinstance(v,str) else v for k,v in record.get('conditions',{}).items()
                if v is not None and str(v).strip()}
    for key in NUMERIC_CONDITIONS:
        if key in conditions:conditions[key]=finite_number(conditions[key])
    return (record['task_id'],record['sample_label'].strip(),record['experiment_id'].strip(),record['metric'].strip(),
            json.dumps(conditions,sort_keys=True,ensure_ascii=False))


def conflicts(project):
    groups={}
    for r in project['records']:
        if r['review_status']=='excluded':continue
        key=_identity(r)
        if key:groups.setdefault(key,[]).append(r)
    problems={}
    for records in groups.values():
        if len(records)<2:continue
        signatures={(r.get('operator'),r.get('value'),r.get('value_high'),r.get('unit')) for r in records}
        message='同一样品、实验和条件存在不同数值或单位；核对后保留正确记录，另一条标为不采用，并保留原因。' if len(signatures)>1 else '同一实验记录重复；请合并证据后只保留一条，避免文字和图片重复计数。'
        for r in records:problems[r['record_id']]=message
    return problems


def merge_equal_records(project,record_ids,reviewer):
    records=[r for r in project['records'] if r['record_id'] in record_ids and r['review_status']!='excluded']
    if len(records)<2 or not reviewer.strip():raise ValueError('选择至少两条记录，并填写审核人。')
    first=records[0]
    if not _identity(first) or any(_identity(r)!=_identity(first) for r in records):raise ValueError('只有同一样品、同一实验和相同条件的记录才能合并证据。')
    compare=lambda r:(r.get('operator'),r.get('value'),r.get('value_high'),r.get('unit'))
    if any(compare(r)!=compare(first) for r in records):raise ValueError('数值或单位不一致，不能自动平均或选择；请先核对。')
    original=copy.deepcopy(records)
    first['evidence_ids']=list(dict.fromkeys(e for r in records for e in r['evidence_ids']))
    roles={}
    for r in original:
        for eid in r['evidence_ids']:
            role=r.get('evidence_roles',{}).get(eid,'value')
            if roles.get(eid)!='value':roles[eid]=role
    first['evidence_roles']=roles
    first['review_status']='draft';first['reviewer']=''
    for r in records[1:]:r['review_status']='excluded';r['merged_into']=first['record_id']
    _event(project,'merge_equal_evidence',{'before':original,'kept_record':first['record_id']},reviewer);save_project(project)


def record_issues(project,record):
    issues=[];task=TASKS[record['task_id']]
    if record['review_status']!='reviewed':issues.append('统一记录尚未人工确认')
    if record['task_id']=='pending':issues.append('预测目标未确定')
    for field,label in [('sample_label','样品名称'),('experiment_id','实验/测试编号'),('composition','材料组成或表面结构'),('metric','指标'),('unit','单位')]:
        if not str(record.get(field,'')).strip():issues.append(label+'未填写')
    if record.get('assertion_scope')!='current_study':issues.append('尚未确认是本研究结果；引用他人工作不能当本篇实验')
    expected_type='dft' if record['task_id']=='nh3_adsorption_energy' else 'experiment'
    if record.get('measurement_type')!=expected_type:issues.append('测量/计算类型与任务不匹配或待确认')
    if record.get('operator')!='eq':issues.append('区间或不等式保留在关系/约束表，不作为精确单点标签')
    if record.get('value') is None:issues.append('没有绝对数值')
    if record.get('value_high') is not None and record.get('operator')=='eq':issues.append('明确单值不应同时保留区间上限，请核对数值形式')
    if record.get('value') is not None and record.get('unit') in ('%','fraction'):
        maximum=100 if record['unit']=='%' else 1
        if not 0<=record['value']<=maximum:issues.append('性能比例超出 0～'+str(maximum)+'，请核对原值与定义；未自动截断')
    if task['units'] and record.get('unit') not in task['units']:issues.append('单位尚未按本任务明确为 '+ ' / '.join(task['units']))
    if task['metric'] and record.get('metric')!=task['metric']:issues.append('指标与当前任务不一致；先统一定义或另建任务')
    for condition in task['conditions']:
        value=record.get('conditions',{}).get(condition)
        if value is None or str(value).strip()=='':issues.append(CONDITION_LABELS[condition]+'未报告或未核对')
    if record['task_id'].startswith('scr_'):
        c=record.get('conditions',{})
        if c.get('space_velocity_h_inv') is None and not all(c.get(k) is not None for k in ('flow_rate_ml_min','catalyst_mass_g')):
            issues.append('缺少空速，或缺少气体流量与催化剂用量；不能比较不同接触条件')
    value_sources=[]
    for eid in record['evidence_ids']:
        e=next((e for e in project['evidence'] if e['evidence_id']==eid),None)
        if not e or e.get('stale') or e.get('review_status')=='stale':issues.append('关联证据已过期或缺失');continue
        if e.get('source_ref',{}).get('source_sha256')!=project['article']['source_sha256']:
            issues.append('关联证据不属于当前源 PDF');continue
        if e['branch']=='image' and not e.get('usable'):issues.append('图片读数尚未在读图窗口核验并导出，或来源状态无效')
        if e['branch']!='image' and e.get('review_status')!='reviewed':issues.append('关联原文/语义证据尚未确认')
        if record.get('evidence_roles',{}).get(eid,'value')=='context':continue
        value_sources.append(eid)
        if e.get('kind') in ('numeric_fact','table_value') and e.get('quantity_role')!='performance':
            issues.append('条件、材料属性或其他结果不能作为本任务的性能数值来源')
        if e['branch']=='semantic':
            if e.get('kind')!='absolute' or e.get('operator')!='eq':
                issues.append('语义证据含倍数、区间、边界、近似、否定或展望，不能直接变成精确绝对值标签')
            from paper_semantic_audit import assertion_modes
            if set(assertion_modes(e)).intersection(('modal','future','future_or_modal','hypothetical','conditional','recommended','required','recommended_or_required','negated')):
                issues.append('数值原句带否定、假设或预期限定；不能改作本篇已完成实验的精确标签')
            if e.get('assertion_scope')=='prior_work':issues.append('数值原句引用他人研究，不能改标为本篇实测结果')
            if e.get('uncertainty'):issues.append('语义证据含测量误差；请保留误差定义，不能作为无误差单值直接编码')
    if not value_sources:issues.append('缺少直接支持数值的证据；补充条件不能单独充当数值来源')
    conflict=conflicts(project).get(record['record_id'])
    if conflict:issues.append(conflict)
    return list(dict.fromkeys(issues))


def _csv(path,rows,fields):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for row in rows:
            cleaned={}
            for k,v in row.items():
                if isinstance(v,(dict,list)):v=json.dumps(v,ensure_ascii=False)
                if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')):v="'"+v
                cleaned[k]=v
            writer.writerow(cleaned)


def export_handoff(project):
    """Freeze a coding input packet; no one-hot encoding, training or scoring."""
    verify_source(project)
    if project['article'].get('batch_path'):refresh_images(project)
    folder=Path(project['run_dir'])/('待编码包_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:4])
    folder.mkdir()
    passed=[];waiting=[]
    for record in project['records']:
        if record['review_status']=='excluded':continue
        issues=record_issues(project,record)
        row=dict(record,issues=issues,doi=project['article'].get('doi',''),source_sha256=project['article']['source_sha256'],
                 split_group=project['article'].get('doi') or project['article']['source_sha256'],
                 stage3_status='ready_for_standardization' if not issues else 'needs_review',ml_ready=False)
        (waiting if issues else passed).append(row)
    relations=[e for e in project['evidence'] if e['branch']=='semantic' and (e.get('kind')!='absolute' or e.get('operator')!='eq' or e.get('semantic_facets'))]
    packet={'schema_version':'stage3-evidence-handoff/1.0','created_at':now(),'article':project['article'],
        'source_workspace':{'path':str(workspace_path(project)),'project_id':project['project_id']},
        'records_ready_for_standardization':passed,'records_waiting_for_review':waiting,'semantic_relations':relations,
        'evidence':project['evidence'],'review_events':project['events'],
        'encoding_contract':{'row_grain':'一个样品在一个明确实验/计算条件下的一个指标；一篇论文可有多行',
            'group_split_key':'DOI，缺失时使用源PDF SHA256；同一论文的数据留在同一划分组',
            'feature_roles':{'X':['组成/结构','制备/处理','反应或计算条件'],'y':['该任务定义的性能指标']},
            'missing_values':'保留 null 与未报告原因，禁止用 0 替代缺失',
            'unit_policy':'先核对定义与单位再换算；小数转化率与百分数有区别',
            'training_policy':'按任务分别构建数据集；划分后仅在训练集拟合编码、填补、缩放与特征选择',
            'leakage_policy':'同一实验的正文/表格/图像是多份证据，不是多条独立样本；不把目标衍生量作为输入',
            'raw_text_policy':'组成、制备、表征原文仍待拆分字段；包含性能结论的整句不能直接作为类别输入',
            'relation_policy':'倍率、趋势、机理解释、展望和否定独立保存；本版不由它们推算绝对标签',
            'implemented':'结构化交接包；第三板块可执行字段标准化与编码，尚未训练或验证预测模型'},
        'scope':'核验后的标准化候选，不代表完整数据集或模型已验证；图像值仍为近似读数。'}
    _write(folder/'待编码包.json',packet)
    fields=['record_id','doi','task_id','sample_label','sample_mapping_note','experiment_id','composition','preparation','characterization','metric','operator','value','value_high','unit',
            'conditions','features','evidence_ids','evidence_roles','reviewer','missing_reason','split_group','stage3_status','ml_ready','issues']
    _csv(folder/'可进入标准化的记录.csv',passed,fields)
    _csv(folder/'待补齐与冲突记录.csv',waiting,fields)
    _csv(folder/'语义关系_不作绝对值标签.csv',relations,['evidence_id','page','kind','quote','metric','operator','value','unit','sample_label','reference_sample',
        'assertion_scope','relations','semantic_facets','semantic_roles','conditions_mentions','assertion_status','context_evidence','semantic_adoption','review_status','warnings'])
    summary={'directory':str(folder),'ready_count':len(passed),'waiting_count':len(waiting),'relation_count':len(relations),
             'at':now(),'packet_sha256':digest(folder/'待编码包.json')}
    project['exports'].append(summary);save_project(project)
    return summary

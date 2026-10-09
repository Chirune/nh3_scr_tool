"""Import team-collected paper identities and manual decisions, not labels."""
from pathlib import Path
import hashlib
import json
import shutil
import sys


def import_project(path, output_root):
    path=Path(path).resolve()
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict) or value.get('schema')!='nh3scr-workbench-v1' or not isinstance(value.get('papers'),list):
        raise ValueError('请选择组员采集工作台保存的 project.json。')
    ids=[p.get('record_id') for p in value['papers'] if isinstance(p,dict)]
    if len(ids)!=len(value['papers']) or not all(isinstance(i,str) and i for i in ids) or len(ids)!=len(set(ids)):
        raise ValueError('原项目论文编号缺失或重复，需先在原工具核对。')
    sys.path.insert(0,str(Path(__file__).parent/'gateway'))
    import engine
    from rules import screen_record
    run=engine._new_run('scr_ammonia',output_root,'team_project_import')
    run['upstream_project']={'schema':value['schema'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'path':str(path),'scope':'论文身份、人工筛选决定与唯一有效正文 PDF；不迁移数值审核为训练标签'}
    for original in value['papers']:
        identity=original['record_id']
        decision=original.get('human_decision')
        decision=decision if decision in {'target','review','non_target'} else 'review'
        item={key:original.get(key,'') for key in ['title','abstract','doi','year','journal','publisher','url','document_type']}
        item.update(id=identity,doi=engine.canonical_doi(item['doi']),sources=['team_workbench'],
            source_ids={'team_record_id':identity},source_records=[],warnings=[],fulltext_candidates=[],
            manual_decision=decision,effective_decision=decision,manual_note=original.get('human_note',''),
            manual_reviewer=original.get('human_reviewer',''))
        item['screening']=screen_record(item,'scr_ammonia')
        item['route']=engine.source_route(item)
        if not original.get('human_decision'):item['warnings'].append('原项目尚无人工决定；导入后继续待复核。')
        attachments=value.get('attachments',{}).get(identity,[])
        primary=[a for a in attachments if isinstance(a,dict) and a.get('role')=='primary' and str(a.get('path','')).lower().endswith('.pdf')]
        if len(primary)==1:
            source=(path.parent/primary[0]['path']).resolve()
            if source.is_relative_to(path.parent) and source.is_file() and source.read_bytes()[:5]==b'%PDF-':
                sha=hashlib.sha256(source.read_bytes()).hexdigest()
                saved=Path(run['run_dir'])/'imported_primary'/f'{sha}.pdf'
                saved.parent.mkdir(exist_ok=True)
                if not saved.exists():shutil.copyfile(source,saved)
                item.update(local_path=str(saved),local_sha256=sha,
                    pdf_acquisition={'status':'available','message':'从组员项目接收唯一正文 PDF；论文身份沿用原项目人工关联。'})
            else:item['warnings'].append('正文 PDF 缺失、越出原项目目录或文件格式无效；未绑定。')
        elif len(primary)>1:item['warnings'].append('原项目有多个正文 PDF；未自动选择，需核对后导入。')
        else:item['warnings'].append('原项目没有正文 PDF，HTML 或补充材料未冒充正文。')
        run['records'].append(item)
    run['summary'].update(raw_count=len(run['records']),duplicates_merged=0)
    engine._save(run)
    return Path(run['run_dir'])/'run.json'

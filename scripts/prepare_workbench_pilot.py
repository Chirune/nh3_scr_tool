"""Prepare the user's existing Cu-CHA PDF, with every decision left for the user."""
import argparse
import contextlib
import io
import shutil
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pypdf import PdfReader
from scrtool.cli import run_extract
from scrtool.core import read_json, write_json
from scrtool.workbench import Project, stamp


def prepare(pdf, folder):
    pdf=Path(pdf).resolve(); folder=Path(folder).resolve()
    if (folder/'project.json').exists():
        raise ValueError('该测试项目已经存在，不覆盖已有的审核工作。')
    original=PdfReader(pdf).pages[0].extract_text()
    paper_doi='10.1038/s41467-026-72879-7'
    assert paper_doi in original
    start=original.index('Ammonia')
    end=original.index('The selective catalytic reduction',start)
    abstract=original[start:end].strip()
    assert len(abstract)>600 and abstract.endswith('systems.')
    metadata=folder/'来自本地PDF首页的摘要.json'
    write_json(metadata,[{'doi':paper_doi,'title':'Insights into the mechanisms of NH3 inhibition on Cu-CHA SCR catalysts',
                         'abstract':abstract,'abstract_source':'local_pdf_page1', 'abstract_locator':'page:1',
                         'abstract_is_full':True,'year':2026,'journal':'Nature Communications',
                         'landing_page_url':'https://doi.org/'+paper_doi,'source_pdf':str(pdf)}])
    project=Project(folder); project.import_abstracts([metadata])
    identity=project.state['papers'][0]['record_id']
    destination=folder/'materials'/identity/'primary'/pdf.name
    destination.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(pdf,destination)
    output=folder/'extractions'/identity/stamp()
    args=argparse.Namespace(input=str(destination),output=str(output),engine='rules',config=None,mapping=None,
                            manifest=None,paper_id=identity,max_chars=24000,reaction='NH3-SCR')
    with contextlib.redirect_stdout(io.StringIO()):run_extract(args)
    records=read_json(output/'candidates.json')
    for record in records:
        record['doi']=paper_doi
    assert all(r['review_status']=='pending' for r in records)
    write_json(output/'reviewed.json',records)
    sources=read_json(output/'sources.json')
    for source in sources:
        source['doi']=paper_doi
    write_json(output/'workflow_sources.json',sources)
    project.state['attachments'][identity]=[{'original':str(pdf),'path':str(destination.relative_to(folder)),'role':'primary'}]
    project.state['runs'][identity]=str(output.relative_to(folder))
    project.state['pilot_note']='已预先读取本地真实论文。论文未人工确认，全部数值未审核；确认保留后才显示候选数据。'
    project.state['history'].append({'action':'prepare_local_pilot_without_human_approval','source_file':str(pdf),'run':str(output.relative_to(folder))})
    project.save()
    assert not project.paper(identity).get('human_decision') and not project.candidates()
    report=read_json(output/'run_report.json')
    (folder/'先看这里.md').write_text(
        '# 本地论文测试\n\n已预先导入你“论文”文件夹中的 Cu-CHA 真实论文和首页摘要。未替你批准任何论文或数值。\n\n'
        '1. 打开工作台，在上方填写核对人。\n'
        '2. 第 2 步选中这一篇，看右侧摘要，确认后点“保留：原始实验研究”。\n'
        '3. 正文已绑定、首轮提取已完成，可直接到第 4 步选记录，看具体问题和原文。PDF 页签中双击图片可放大。\n'
        '4. 图片曲线可点“打开当前页图片读数”，按刻度标定并取点。语义候选在独立页签中核对。\n'
        '5. 按原文通过、排除或修正；再到第 5 步导出。\n\n'
        f'本轮规则得到 {report["candidates"]} 条候选，{report["unresolved_blocks"]} 个内容块未自动提取。'
        f'另收集 {report.get("semantic_candidates",0)} 条待审核语义候选。'
        '这不代表整篇论文的数据已经提取完整。可在“未提取页”查看漏提。\n',encoding='utf-8')
    write_json(folder/'首轮读取报告.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('pdf'); parser.add_argument('folder')
    args=parser.parse_args()
    print(prepare(args.pdf,args.folder))

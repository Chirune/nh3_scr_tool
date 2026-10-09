"""Generate explicitly synthetic data locally. No papers or results are bundled."""
from pathlib import Path
import hashlib
import sys
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def create_demo(output_root):
    sys.path.insert(0,str(Path(__file__).parent/'gateway'))
    import engine
    from rules import screen_record
    run=engine._new_run('scr_ammonia',output_root,'synthetic_tutorial')
    folder=Path(run['run_dir']);pdf=folder/'SYNTHETIC_NOT_EXPERIMENTAL.pdf'
    writer=PdfWriter()
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    ref=writer._add_object(font)
    page=writer.add_blank_page(width=600,height=800)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):ref})})
    commands=[]
    def text(x,y,line,size=11):
        line=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        commands.append(f'0 0 0 rg BT /F1 {size} Tf {x} {y} Td ({line}) Tj ET')
    for i,line in enumerate([
        'SYNTHETIC TUTORIAL ONLY - NOT EXPERIMENTAL RESULTS',
        'NH3-SCR example: Cu/CeO2 catalyst S1',
        'NO conversion was 90% at 300 C.',
        'Feed: 500 ppm NO, 500 ppm NH3, 5% O2 in N2.',
        'Space velocity was 40000 h-1.',
        'Compared with S0, the NO conversion of S1 increased by 10%.',
        'The activity may increase significantly after optimization.',
        'This document contains invented values for learning the interface.'
    ]):text(38,760-i*24,line)
    commands.append('0 0 0 RG 1 w 90 230 m 90 490 l 470 490 l 470 230 l 90 230 l S')
    for t in [100,200,300,400]:
        x=90+(t-100)*380/300
        commands.append(f'{x} 230 m {x} 225 l S');text(x-10,210,str(t))
    for value in [0,20,40,60,80,100]:
        y=230+value*2.6
        commands.append(f'90 {y} m 85 {y} l S');text(58,y-3,str(value))
    text(220,185,'Temperature (C)');text(90,511,'NO conversion (%)')
    points=[(100,20),(200,60),(300,90),(400,95)]
    commands.append('0.05 0.4 0.8 RG 2 w')
    for i,(x,y) in enumerate(points):
        commands.append(f'{90+(x-100)*380/300} {230+y*2.6} '+('m' if i==0 else 'l'))
    commands.append('S')
    text(50,145,'Figure 1. Synthetic NO conversion curve of catalyst S1.')
    text(50,120,'Values were invented. Do not include this tutorial in research data.')
    stream=DecodedStreamObject();stream.set_data('\n'.join(commands).encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.add_metadata({'/Title':'SYNTHETIC TUTORIAL - NOT EXPERIMENTAL RESULTS'})
    with pdf.open('wb') as out:writer.write(out)
    record={'id':'synthetic_tutorial_s1','title':'合成教学：NH3-SCR 操作练习（非科研数据）',
        'doi':'','abstract':'Synthetic example of ammonia selective catalytic reduction. Not experimental results.',
        'sources':['synthetic_tutorial'],'source_ids':{},'source_records':[],
        'local_path':str(pdf),'local_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
        'manual_decision':'target','effective_decision':'target','manual_note':'仅用于界面教学，不得用于科研训练',
        'warnings':['SYNTHETIC: All values invented.'],'fulltext_candidates':[],
        'pdf_acquisition':{'status':'available','message':'本机生成的合成演示 PDF，无需联网下载。'},
        'data_origin':'synthetic_tutorial_not_research'}
    record['screening']=screen_record(record,'scr_ammonia');record['route']=engine.source_route(record)
    run['records']=[record];run['summary'].update(raw_count=1,duplicates_merged=0)
    engine._save(run)
    return folder/'run.json'

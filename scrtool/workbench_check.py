"""Software acceptance fixtures, clearly separated from research data."""
from pathlib import Path
from .core import read_json, write_json
from .workbench import Project


def verify(output,sample,real_pdf=None,app_factory=None):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    real=Project(output/'abstracts_only')
    count,errors=real.import_abstracts([sample])
    assert count>=1 and not errors
    assert not any(r.get('human_decision') for r in real.state['papers'])
    fixture=output/'软件验收输入_不是科研数据.json'
    write_json(fixture,[{'doi':'10.9999/software-test','title':'软件验收示例，非科研论文：NH3-SCR',
                        'abstract':'NH3-SCR catalysts were prepared and experimentally tested. NOx conversion and selectivity were measured at different temperatures.'}])
    project=Project(output/'synthetic_software_test')
    project.import_abstracts([fixture]); rid=project.state['papers'][0]['record_id']
    project.decide_papers([rid],'target','软件验收（非科研审核）')
    table=output/'软件验收数据_非科研.csv'
    table.write_text('catalyst,temperature [K],nox_conversion [%],loading [wt%]\nTEST_SAMPLE_NOT_REAL,473.15,90,5\n',encoding='utf-8')
    project.attach(rid,[table],'data'); reports=project.extract([rid])
    rows=project.candidates(); performance=next(r for r in rows if r['property']=='nox_conversion')
    assert abs(performance['conditions']['temperature']['value']-200)<1e-8
    assert 'TEST_SAMPLE_NOT_REAL' in project.source(rid,performance['block_id'])['text']
    try:project.export()
    except ValueError:pass
    else:raise AssertionError('Pending observations were exported')
    project.review([r['record_id'] for r in rows],'approve','软件验收（非科研审核）')
    export,report=Project(project.folder).export()
    assert report['ml_rows']==1 and not report['machine_learning_ready']
    from pypdf import PdfReader, PdfWriter
    blank=output/'验收用空白页.pdf'
    writer=PdfWriter(); writer.add_blank_page(width=400,height=500)
    with blank.open('wb') as handle:writer.write(handle)
    assert len(PdfReader(blank).pages)==1
    # Exercise the frozen EXE's DOI-acquisition-to-project path without a network call.
    from . import fulltext
    original_acquire = fulltext.acquire_pdf
    def fixture_acquire(record, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(blank.read_bytes())
        return {'status':'downloaded_pdf','path':str(destination),'source':'software_fixture',
                'url':'https://example.invalid/software-fixture.pdf','errors':[]}
    try:
        fulltext.acquire_pdf = fixture_acquire
        acquired = project.acquire_primary(rid)
    finally:
        fulltext.acquire_pdf = original_acquire
    assert acquired['status']=='downloaded_pdf'
    assert any(item['role']=='primary' for item in Project(project.folder).state['attachments'][rid])
    # Imported by the app entry point, including inside the frozen EXE.
    from workbench_preview import render_page
    preview=render_page(real_pdf or blank,'page:1',output/'page_preview')
    assert preview and preview.stat().st_size>100
    result={'abstract_import':count,'explicit_paper_confirmation':True,'source_link':True,
            'auto_fulltext_attach':True,
            'pending_export_blocked':True,'kelvin_conversion':True,'review_resume_export':True,
            'pdf_read_and_render':True,'preview_path':str(preview),'software_fixture_only':True}
    from PIL import Image
    from .figure_digitizer.session import new_session, set_calibration, add_points, export_session
    from .figure_import import build_figure_records
    from .semantics import semantic_records
    from .scoring import score_record
    image_source=output/'软件验收图片_非科研.png'
    Image.new('RGB',(400,300),'white').save(image_source)
    session,image=new_session(image_source,output_root=output/'figure_fixture')
    set_calibration(session,{
        'x':dict(p1=[20,260],p2=[380,260],v1=473.15,v2=673.15,name='temperature',unit='K'),
        'y':dict(p1=[20,260],p2=[20,20],v1=0,v2=100,name='NOx conversion',unit='%')})
    add_points(session,[dict(px=200,py=44)],'SOFTWARE_TEST',sample_label='SOFTWARE_TEST')
    export_session(session,{'figure_label':'Fig.TEST','reviewed':False})
    figure_records,_=build_figure_records(Path(session['last_export_dir'])/'读数与溯源.json',
                                        'SOFTWARE_TEST','nox_conversion','temperature')
    assert figure_records[0]['review_status']=='pending'
    assert abs(figure_records[0]['conditions']['temperature']['value']-300)<1e-8
    assert abs(figure_records[0]['value']-90)<1e-8
    claim_source=dict(project.source(rid,performance['block_id']),paper_id='SOFTWARE_TEST',
                      text='NOx conversion increased tenfold compared with sample B.')
    claims=list(semantic_records(claim_source))
    assert claims[0]['reported_change']['amount']==10 and claims[0]['absolute_value'] is None
    assert not claims[0]['training_eligible']
    assert score_record({'title':'NH3-SCR catalyst','abstract':''})['priority_score']>0
    pdf_session,pdf_image=new_session(real_pdf or blank,output_root=output/'figure_pdf_fixture')
    pdf_image.close()
    result.update(figure_import_pending=True,figure_kelvin_conversion=True,semantic_not_absolute=True,
                  configurable_literature_score=True,figure_pdf_render=True)
    if app_factory:
        import time
        import tkinter as tk
        root=tk.Tk(); root.withdraw()
        app=app_factory(root)
        try:
            app.project=Project(project.folder)
            app.record_filter.set('全部'); app.refresh()
            index=next(i for i,r in enumerate(app.records) if r['property']=='nox_conversion')
            app.record_table.selection_set(str(index)); app.preview_record()
            root.update()
            source=app.project.source(rid,performance['block_id'])
            assert app.original.text.get('1.0','end-1c')==source['text']
            assert app.original.text.tag_ranges('evidence')
            prompt=app.original.prompt.get('1.0','end-1c')
            assert '90' in prompt and '200' in prompt and '同一实验' in prompt
            app.original.show({'source_file':str(Path(real_pdf or blank).resolve()),'locator':'page:1','text':'软件验收：原始页预览'},'软件验收：原始页预览',cache=output/'ui_preview')
            deadline=time.monotonic()+12
            while not app.original.image_path and time.monotonic()<deadline:
                root.update(); time.sleep(0.03)
            assert app.original.image_path and app.original.picture.cget('image'), app.original.page_status.get()
            from .figure_digitizer.app import DigitizerApp
            from workbench_figures import import_dialog
            window=tk.Toplevel(root); window.withdraw()
            reader=DigitizerApp(window); reader.set_session((session,image))
            import_dialog(app,Path(session['last_export_dir'])/'读数与溯源.json',rid)
            app.score_weights(); root.update()
            assert len(app.review_tabs.tabs())==3
            result['gui_figure_mapping_scoring_semantics']=True
            result['gui_record_selection_and_highlight']=True
            result['gui_pdf_page_display']=True
        finally:
            app.close()
    write_json(output/'acceptance.json',result)
    return result

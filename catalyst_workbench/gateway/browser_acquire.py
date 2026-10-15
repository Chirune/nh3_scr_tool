"""User-authorized, visible Chrome acquisition for one exact paper per job."""
from pathlib import Path
import argparse
import html
import ipaddress
import json
import re
import time
from urllib.parse import urlsplit

from engine import canonical_doi
from pdf_fetch import inspect_pdf, MAX_PDF_BYTES, mdpi_asset, FetchError


def web_url(value):
    p=urlsplit(str(value or ''))
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('请选择有效的论文网页。')
    if p.port not in (None,80,443) or p.hostname in ('localhost',) or p.hostname.endswith(('.local','.internal')):
        raise ValueError('只支持公开论文网页。')
    try:address=ipaddress.ip_address(p.hostname)
    except ValueError:address=None
    if address and not address.is_global:raise ValueError('不支持本机或内网采集地址。')
    return value


def write_status(folder,state,message,**extra):
    path=Path(folder)/'status.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps({'state':state,'message':message,**extra},ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def collector_source():
    path=Path(__file__).resolve().parents[2]/'browser_extension/popup.js'
    script=path.read_text(encoding='utf-8')
    start=script.index('function collectArticleSnapshot()')
    end=script.index('\nconst read =',start)
    return '('+script[start:end].strip()+')'


def save_pdf(data,record,folder,kind):
    if len(data)>MAX_PDF_BYTES:raise ValueError('PDF 超过40MB，未接收。')
    checked=inspect_pdf(data,record)
    target=Path(folder)/('browser_original.pdf' if kind=='browser_pdf' else 'browser_visible_text.pdf')
    target.write_bytes(data)
    return {'local_path':str(target.resolve()),'local_sha256':checked['sha256'],
            'acquisition_method':kind,**checked}


def receive_result(run,record_id,folder):
    """Only bind a verified artifact located inside the authorized job folder."""
    import engine
    from pdf_fetch import existing_file
    folder=Path(folder).resolve()
    value=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    record=next(r for r in run['records'] if r['id']==record_id)
    if (record.get('manual_decision') or record.get('effective_decision'))!='target':
        raise ValueError('这篇论文当前未保留，未自动关联文件。')
    if canonical_doi(value.get('doi'))!=canonical_doi(record.get('doi')):
        raise ValueError('浏览器采集的 DOI 与所选论文不一致。')
    source=Path(value['local_path']).resolve()
    if not source.is_relative_to(folder) or source.suffix.lower()!='.pdf':
        raise ValueError('浏览器结果文件不属于本次任务。')
    if source.stat().st_size>MAX_PDF_BYTES:raise ValueError('采集文件超过大小限制。')
    checked=inspect_pdf(source.read_bytes(),record)
    if checked['sha256']!=value['local_sha256']:raise ValueError('采集 PDF 已改变，未接收。')
    if record.get('local_path'):
        if not existing_file(record,run['run_dir']):raise ValueError('原 PDF 需要核对，未自动替换。')
        return '已有已关联 PDF；本次浏览器结果保存在任务目录，未替换。'
    if record.get('pdf_acquisition'):record.setdefault('pdf_acquisition_history',[]).append(record['pdf_acquisition'])
    info={**value,'status':'available','message':'浏览器采集已接收并通过 DOI 与题名检查。',
          'access_basis':'user_authorized_browser_session_not_an_oa_claim'}
    record.update(local_path=str(source),local_sha256=checked['sha256'],pdf_acquisition=info,browser_acquisition=info)
    engine._save(run)
    return info['message']


def run_job(job_path, *, profile=None, headless=False, timeout_seconds=900):
    from playwright.sync_api import sync_playwright, Error
    job_path=Path(job_path).resolve();folder=job_path.parent
    job=json.loads(job_path.read_text(encoding='utf-8'))
    record=job['record'];doi=canonical_doi(record.get('doi'))
    if not doi or job.get('authorized') is not True:raise ValueError('本次浏览器任务未授权或缺少 DOI。')
    url=web_url(job['url']);root=Path(__file__).resolve().parents[2]
    write_status(folder,'opening','正在打开专用 Chrome；请在浏览器内完成学校或出版社登录。')
    with sync_playwright() as pw:
        context=pw.chromium.launch_persistent_context(str(profile or root/'runtime_data/browser_profile'),
                    channel='chrome',headless=headless,accept_downloads=True)
        attempts=set();finished=False;navigation_status={};observations={'pages_checked':0,'exact_doi_seen':False,'visible_body_seen':False,'pdf_http_statuses':[],'pdf_validation_failures':[],'pdf_request_errors':[]}
        def wait(message,code=None):
            if code is None:code=next((c for c in navigation_status.values() if c>=400),None)
            prefix='HTTP '+str(code)+'：' if code else ''
            write_status(folder,'waiting',prefix+message,**({'http_status':code} if code else {}))
        def finish(data,kind,source_url):
            nonlocal finished
            info=save_pdf(data,record,folder,kind)
            result={**info,'doi':doi,'record_id':record['id'],'source_url':source_url,
                    'page_basis':'publisher_pdf' if kind=='browser_pdf' else 'browser_reflowed_text',
                    'fulltext_completeness':'user_review_required'}
            (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            write_status(folder,'ready','已保存并核对所选论文。网页排版件仍需核对完整性。' if kind!='browser_pdf' else '已取得并核对论文 PDF。')
            finished=True
        def download_file(download):
            if finished:return
            try:
                path=folder/'browser_download.part';download.save_as(str(path))
                if path.stat().st_size<=MAX_PDF_BYTES:finish(path.read_bytes(),'browser_pdf',download.url)
                path.unlink(missing_ok=True)
            except Exception:
                write_status(folder,'waiting','下载未通过正文身份检查；请确认正在下载所选论文 PDF。')
        def connect(page):
            page.on('download',download_file)
            def response_status(response):
                if response.request.is_navigation_request() and response.frame==page.main_frame:
                    navigation_status[page]=response.status
            page.on('response',response_status)
        context.on('page',connect)
        for p in context.pages:connect(p)
        page=context.pages[0] if context.pages else context.new_page()
        try:page.goto(url,wait_until='domcontentloaded',timeout=45000)
        except Error:wait('页面尚未完成。请在专用浏览器内处理登录、网络提示或验证码。')
        source=collector_source();deadline=time.monotonic()+max(1,min(900,timeout_seconds))
        try:
            while time.monotonic()<deadline and not finished and not (folder/'stop').exists():
                pages=[p for p in context.pages if not p.is_closed()]
                if not pages:break
                if (folder/'resume').exists():
                    (folder/'resume').unlink()
                    try:pages[-1].goto(url,wait_until='domcontentloaded',timeout=30000)
                    except Error:pass
                for page in pages:
                    if finished:break
                    try:
                        snapshot=page.evaluate(source)
                        observations['pages_checked']+=1
                        if canonical_doi(snapshot.get('doi'))!=doi:
                            wait('等待所选论文页面；请完成登录或验证码，并返回论文。不会采集登录页。');continue
                        observations['exact_doi_seen']=True
                        # Only declared assets from this exact DOI's page are tried.
                        derived=mdpi_asset(page.url,record)
                        candidates=list(dict.fromkeys(([derived] if derived else [])+snapshot.get('pdf_urls',[])))
                        for asset in candidates[:2]:
                            if asset in attempts:continue
                            attempts.add(asset)
                            try:web_url(asset)
                            except ValueError:
                                observations['pdf_request_errors'].append({'error_type':'invalid_url'});continue
                            try:response=context.request.get(asset,timeout=15000)
                            except Error as exc:
                                observations['pdf_request_errors'].append({'error_type':type(exc).__name__})
                                wait('浏览器 PDF 请求未完成；继续检查当前可见正文，也可手动点击 PDF。');continue
                            observations['pdf_http_statuses'].append(response.status)
                            if response.status>=400:
                                wait('浏览器 PDF 请求被拒绝；请在论文页面手动点 PDF。',response.status)
                                continue
                            if int(response.headers.get('content-length') or 0)>MAX_PDF_BYTES:continue
                            try:finish(response.body(),'browser_pdf',asset)
                            except (FetchError,ValueError) as exc:
                                observations['pdf_validation_failures'].append({'status':getattr(exc,'status','invalid_pdf'),'message':str(exc)[:250]})
                            if finished:break
                        if finished:break
                        if snapshot.get('fulltext_available') and snapshot.get('fulltext_html'):
                            observations['visible_body_seen']=True
                            # Save the inspected DOM, then render only its plain visible
                            # text. No article scripts/resources execute in this document.
                            (folder/'capture.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
                            (folder/'visible_article.html').write_text(snapshot['fulltext_html'],encoding='utf-8')
                            text=snapshot.get('fulltext_text') or ''
                            if len(text)<3000:continue
                            printed=context.new_page()
                            printed.set_content('<meta charset="utf-8"><h1>'+html.escape(snapshot.get('title') or '')+'</h1><p>浏览器可见正文排版件；非出版社原版PDF。 DOI: '+html.escape(snapshot['doi'])+'</p><p>来源：'+html.escape(page.url)+'</p><pre style="white-space:pre-wrap;font:12px/1.6 sans-serif">'+html.escape(text)+'</pre>')
                            data=printed.pdf(format='A4',print_background=True);printed.close()
                            finish(data,'browser_visible_text',page.url);break
                        wait('已找到所选论文，但当前只有题录/摘要或正文未展开。请登录并展开正文，或点击 PDF 下载。')
                    except Error:continue
                if not finished:pages[0].wait_for_timeout(2500)
            if not finished:
                timed_out=time.monotonic()>=deadline
                code=next((c for c in navigation_status.values() if c>=400),None)
                write_status(folder,'timed_out' if timed_out else 'stopped',
                    ('HTTP '+str(code)+'：' if code else '')+('等待超时，尚未取得并核对正文；请检查网页是否要求登录、验证码或正文未展开。' if timed_out else '浏览器采集已停止；已有文件保留，可重新授权后继续。'),
                    **({'http_status':code} if code else {}))
        finally:
            (folder/'observations.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2),encoding='utf-8')
            context.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);args=parser.parse_args()
    try:run_job(args.job)
    except Exception as exc:
        write_status(Path(args.job).parent,'error','浏览器采集未完成：'+type(exc).__name__+'。请确认 Chrome 和浏览器组件已安装；已有浏览器任务需先关闭。')


if __name__=='__main__':main()

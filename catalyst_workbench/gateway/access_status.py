"""Readable HTTP failures shared by lists, details and route selection."""
EXPLANATIONS = {
    401: '认证或接口授权未通过；请检查 Key 及已开通的 API 服务。',
    403: '服务器拒绝本次访问；需核对接口授权、机构识别或该篇权限。',
    404: '当前入口未找到资源；不等于论文不存在。',
    429: '请求过多或额度限制；请按服务器要求等待。',
}


def failures(record):
    rows = []
    def visit(value):
        if not isinstance(value, dict):return
        code = value.get('http_status')
        if isinstance(code, int) and not isinstance(code, bool) and code >= 400:
            row = {'http_status':code, 'message':str(value.get('message') or ''),
                   'explanation':EXPLANATIONS.get(code, '服务器请求未完成，请查看原始错误。')}
            if row not in rows:rows.append(row)
        for key in ('publisher_api_check','xml_attempt'):
            visit(value.get(key))
        for item in value.get('attempts',[]):visit(item)
    for key in ('publisher_api_check','pdf_acquisition','xml_acquisition'):
        visit(record.get(key))
    return rows


def failure_text(record):
    return '\n\n'.join('HTTP '+str(r['http_status'])+'：'+r['explanation']+
                      ('\n服务器说明：'+r['message'] if r['message'] else '') for r in failures(record))

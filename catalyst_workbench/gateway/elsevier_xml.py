"""Elsevier FULL XML: retain source bytes and reproducible block locators."""
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 40 * 1024 * 1024
PARSER_VERSION = 'elsevier-fulltext-xml/1.0'

def local(tag):
    return tag.rsplit('}', 1)[-1]

def content(node):
    # Keep Unicode, chemical subscripts and inline formula text in word order.
    return ''.join(node.itertext()).strip()

def doi(value):
    value = re.sub(r'^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)', '', str(value or '').strip(), flags=re.I)
    return value.lower() if re.fullmatch(r'10\.\d{4,9}/[^\s<>]+', value, re.I) else ''

def parse_xml(data, record):
    if not isinstance(data, bytes) or len(data) > MAX_XML_BYTES:
        raise ValueError('XML 超过处理大小上限。')
    if b'<!ENTITY' in data.upper() or b'\x00' in data:
        raise ValueError('XML 含不支持的实体声明或编码，请核对原始文件。')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError('接口未返回可解析的 XML 正文。') from None
    if local(root.tag) != 'full-text-retrieval-response':
        raise ValueError('返回内容不是 Elsevier FULL XML，可能是错误或登录页面。')
    core = next((n for n in root if local(n.tag) == 'coredata'), None)
    expected = doi(record.get('doi'))
    identities = {doi(content(n)) for n in core.iter() if local(n.tag) in ('doi', 'identifier')} if core is not None else set()
    identities.discard('')
    if not expected or identities != {expected}:
        raise ValueError('XML 主元数据的 DOI 与本篇不一致或缺失，未关联正文。')
    title = next((content(n) for n in core.iter() if local(n.tag) == 'title'), '')
    words = {w for w in re.findall(r'[a-z0-9]{3,}', unicodedata.normalize('NFKC', str(record.get('title') or '')).lower())
             if w not in {'the', 'and', 'for', 'with', 'from', 'into'}}
    compact = lambda t: re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKC', t).lower())
    coverage = sum(compact(w) in compact(title) for w in words) / max(1, len(words))
    if len(words) >= 4 and coverage < .6:
        raise ValueError('XML DOI 一致，但题名不匹配，未关联正文。')
    original = next((n for n in root if local(n.tag) == 'originalText'), None)
    if original is None:
        raise ValueError('XML 只有题录或摘要，没有 originalText 全文。')
    # Some API variants embed the article XML as escaped text.
    if not len(original) and (original.text or '').lstrip().startswith('<'):
        embedded = (original.text or '').encode('utf-8')
        if b'<!ENTITY' in embedded.upper():
            raise ValueError('嵌入 XML 含不支持的实体声明。')
        try:
            original.append(ET.fromstring(embedded))
        except ET.ParseError:
            raise ValueError('嵌入的全文 XML 无法解析。') from None
    parents = {child: node for node in root.iter() for child in node}
    paths = {root: '/' + root.tag + '[1]'}
    stack = [root]
    while stack:
        parent = stack.pop()
        counts = {}
        for child in parent:
            counts[child.tag] = counts.get(child.tag, 0) + 1
            paths[child] = paths[parent] + '/' + child.tag + '[' + str(counts[child.tag]) + ']'
            stack.append(child)
    def ancestors(node):
        result = []
        while node in parents:
            node = parents[node]; result.append(node)
        return result
    def section(node):
        headings = []
        for ancestor in reversed(ancestors(node)):
            if local(ancestor.tag) == 'section':
                heading = next((content(n) for n in ancestor if local(n.tag) in ('section-title', 'title')), '')
                if heading: headings.append(heading)
        return ' / '.join(headings) or '正文'
    blocks = []
    tables = []
    for node in original.iter():
        name = local(node.tag)
        chain = ancestors(node)
        names = {local(n.tag) for n in chain}
        if names & {'bibliography', 'bib-reference', 'reference-list', 'references', 'abstract', 'figure'}:
            continue
        # Only article body paragraphs, never abstract/metadata/reference text.
        if name in ('para', 'p') and 'body' in names and not names & {'table', 'entry', 'td', 'th', 'para', 'p'}:
            text = content(node)
            if text:
                blocks.append({'kind': 'paragraph', 'text': text, 'xml_path': paths[node], 'section': section(node)})
        elif name == 'table':
            label = next((content(n) for n in node if local(n.tag) == 'label'), '')
            caption = next((content(n) for n in node if local(n.tag) == 'caption'), '')
            rows = []
            for row in node.iter():
                if local(row.tag) not in ('row', 'tr'): continue
                cells = [{'text': content(cell), 'attributes': dict(cell.attrib), 'xml_path': paths[cell]}
                         for cell in row if local(cell.tag) in ('entry', 'td', 'th')]
                if cells:
                    rows.append({'xml_path': paths[row], 'header': any(local(n.tag) == 'thead' for n in ancestors(row)), 'cells': cells})
            if not rows: continue
            table = {'label': label, 'caption': caption, 'xml_path': paths[node], 'rows': rows,
                     'column_specs': [dict(n.attrib) for n in node.iter() if local(n.tag) == 'colspec']}
            tables.append(table)
            text = '\n'.join([v for v in (label, caption) if v] + ['\t'.join(cell['text'] for cell in row['cells']) for row in rows])
            blocks.append({'kind': 'table', 'text': text, 'xml_path': paths[node], 'section': section(node), 'table': table})
    if not blocks or not any(b['kind'] == 'paragraph' for b in blocks):
        raise ValueError('XML 未包含可定位的正文段落，不能当作完整文章。')
    if len(blocks) > 10000 or any(len(b['text']) > 300000 for b in blocks):
        raise ValueError('XML 段落数量或单段长度超过处理上限，未截断正文。')
    for i, block in enumerate(blocks, 1):
        block.update(block_index=i, source_kind='elsevier_xml', location_label=f"XML 段 {i} · {block['section']}")
    return {'parser_version': PARSER_VERSION, 'source_kind': 'elsevier_xml', 'doi_verified': expected,
            'title': title, 'title_coverage': coverage if len(words) >= 4 else None,
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'blocks': blocks, 'tables': tables,
            'identity_check': 'elsevier_coredata_doi_and_title_full_originalText',
            'warnings': ['XML 定位是章节/段落/表格，不是原版 PDF 页码；表格跨度保留原属性，未猜测缺失单元格或实验关联。']}

def read_xml(path, record):
    from pathlib import Path
    path = Path(path)
    if path.stat().st_size > MAX_XML_BYTES:
        raise ValueError('XML 超过处理大小上限。')
    return parse_xml(path.read_bytes(), record)

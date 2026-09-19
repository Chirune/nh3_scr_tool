import argparse
import csv
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from .core import FIELDS, uid, read_json, write_json, write_csv
from .ingest import blocks, convert
from .extract import rule_records, table_records, llm_records


def run_screen(args):
    from .screening import screen_directory
    report = screen_directory(args.input, args.output, args.pages)
    print(json.dumps(report, ensure_ascii=False))


def run_extract(args):
    root = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if output == root or root in output.parents and root.is_file():
        raise ValueError('Output must be a separate directory')
    output.mkdir(parents=True, exist_ok=True)
    supported = {'.pdf', '.md', '.txt', '.html', '.htm', '.json', '.csv', '.tsv', '.xlsx'}
    excluded = {Path(p).resolve() for p in [args.config, args.mapping, args.manifest] if p}
    paths = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in supported and output not in p.parents and p not in excluded and not p.name.endswith('.provenance.json'))
    if not paths:
        raise ValueError('No supported input files found')
    config = read_json(args.config) if args.config else None
    if args.engine == 'llm' and not config:
        raise ValueError('--engine llm requires --config')
    mapping = read_json(args.mapping) if args.mapping else None
    manifest = read_json(args.manifest) if args.manifest else {}
    from .documents import classify_document
    records, sources, documents, errors, queue = [], [], [], [], []
    for path in paths:
        relative = path.name if root.is_file() else path.relative_to(root).as_posix()
        meta = manifest.get(relative, {})
        paper = args.paper_id or meta.get('paper_id')
        try:
            source_blocks = list(blocks(path, paper))
        except Exception as exc:
            errors.append({'file': str(path), 'error': type(exc).__name__ + ': ' + str(exc)})
            continue
        document = classify_document(source_blocks, path)
        if document['document_type'] == 'supplementary_information' and paper:
            document['training_eligible'] = True
            document['classification_evidence'] += '; linked to primary article by explicit paper_id'
        document['paper_id'] = paper or (source_blocks[0]['paper_id'] if source_blocks else None)
        documents.append(document)
        for block in source_blocks:
            block.update(
                document_type=document['document_type'],
                document_type_confidence=document['document_type_confidence'],
                training_eligible=document['training_eligible'],
            )
        for block in source_blocks:
            sources.append(block)
            if block.get('needs_ocr') or block['kind'] in ['image', 'chart']:
                queue.append({'block_id': block['block_id'], 'file': str(path), 'locator': block['locator'], 'reason': 'needs_ocr_or_curve_digitization', 'image_path': block.get('image_path')})
            try:
                if 'row' in block:
                    new = list(table_records(block, mapping))
                elif args.engine == 'llm':
                    # Each block is bounded. Large PDF pages are rejected rather than silently truncated.
                    if len(block['text']) > args.max_chars:
                        raise ValueError('Block exceeds --max-chars; convert to Markdown or raise limit')
                    new = llm_records(block, config)
                else:
                    new = list(rule_records(block))
                records.extend(new)
                if not new and block['text'].strip():
                    queue.append({'block_id': block['block_id'], 'file': str(path), 'locator': block['locator'], 'reason': 'no_measurements_extracted'})
            except Exception as exc:
                # Do not print HTTP response bodies or API secrets.
                errors.append({'file': str(path), 'block_id': block['block_id'], 'error': type(exc).__name__ + ': ' + str(exc)})
    records = list({r['record_id']: r for r in records}.values())
    write_json(output / 'candidates.json', records)
    write_json(output / 'sources.json', sources)
    write_json(output / 'documents.json', documents)
    write_json(output / 'errors.json', errors)
    write_json(output / 'review_queue.json', queue)
    write_csv(output / 'candidates.csv', records)
    write_csv(output / 'decisions_template.csv', ({'record_id': r['record_id'], 'decision': '', 'reviewer': '', 'note': ''} for r in records), ['record_id', 'decision', 'reviewer', 'note'])
    type_counts = {kind: sum(d['document_type'] == kind for d in documents) for kind in sorted({d['document_type'] for d in documents})}
    report = dict(files=len(paths), blocks=len(sources), candidates=len(records), errors=len(errors), unresolved_blocks=len(queue), document_types=type_counts, engine=args.engine, reaction=args.reaction)
    write_json(output / 'run_report.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 2 if errors else 0


def run_review(args):
    records = read_json(args.candidates)
    index = {r['record_id']: r for r in records}
    seen = set()
    with Path(args.decisions).open(encoding='utf-8-sig', newline='') as f:
        for d in csv.DictReader(f):
            rid = d['record_id']
            if rid in seen or rid not in index:
                raise ValueError('Duplicate or unknown record_id: ' + rid)
            seen.add(rid)
            decision = d.get('decision', '').strip()
            if not decision:
                continue
            if decision not in ['approve', 'reject'] or not d.get('reviewer', '').strip():
                raise ValueError('Use approve/reject and supply reviewer')
            r = index[rid]
            if decision == 'approve' and (r['issues'] or r['value'] is None or not r['catalyst']):
                raise ValueError('Cannot approve unresolved record: ' + rid + '; correct source/mapping and re-extract')
            r.update(review_status='approved' if decision == 'approve' else 'rejected', reviewer=d['reviewer'], review_note=d.get('note', ''))
    write_json(args.output, records)
    print('Review saved: ' + args.output)


def run_export(args):
    records = read_json(args.input)
    approved = [r for r in records if r.get('review_status') == 'approved' and not r['issues'] and r['value'] is not None and r['catalyst']]
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / 'reviewed_measurements.csv', approved)
    ml, withheld = [], []
    for r in approved:
        if r['category'] != 'performance':
            continue
        conditions = r['conditions']
        if r['property'] not in ['t50', 't90'] and conditions.get('temperature', {}).get('value') is None:
            withheld.append(dict(record_id=r['record_id'], reason='missing_explicit_reaction_temperature'))
            continue
        flat = {k: r.get(k) for k in ['record_id', 'paper_id', 'catalyst', 'experiment_id', 'property', 'value', 'unit', 'source_file', 'locator', 'method', 'source_kind', 'estimated', 'reviewer', 'review_level']}
        flat['split_group'] = r['paper_id']
        for key in FIELDS:
            if FIELDS[key][0] == 'condition':
                flat[key] = conditions.get(key, {}).get('value')
        ml.append(flat)
    write_csv(out / 'ml_long.csv', ml)
    write_csv(out / 'withheld.csv', withheld)
    write_json(out / 'export_report.json', dict(approved=len(approved), ml_rows=len(ml), withheld=len(withheld)))
    print(f'Approved measurements: {len(approved)}; ML rows: {len(ml)}; withheld: {len(withheld)}')


def calibrate(value, pixel1, pixel2, real1, real2, scale):
    if scale not in ['linear', 'log']:
        raise ValueError('Scale must be linear or log')
    if not all(math.isfinite(float(v)) for v in [value, pixel1, pixel2, real1, real2]):
        raise ValueError('Non-finite calibration value')
    if pixel1 == pixel2 or real1 == real2:
        raise ValueError('Calibration anchors must differ')
    if scale == 'log':
        if real1 <= 0 or real2 <= 0:
            raise ValueError('Log anchors must be positive')
        real1, real2 = math.log10(real1), math.log10(real2)
    result = real1 + (value - pixel1) / (pixel2 - pixel1) * (real2 - real1)
    return 10 ** result if scale == 'log' else result


def run_digitize(args):
    config = read_json(args.calibration)
    points = list(csv.DictReader(Path(args.points).open(encoding='utf-8-sig', newline='')))
    rows = []
    for p in points:
        values = {}
        for axis in ['x', 'y']:
            c = config[axis]
            if c['property'] not in FIELDS:
                raise ValueError('Unknown calibration property')
            values[f"{c['property']} [{c['unit']}]"] = calibrate(float(p['pixel_' + axis]), c['pixel1'], c['pixel2'], c['value1'], c['value2'], c.get('scale', 'linear'))
        rows.append(dict(catalyst=args.catalyst, **values, figure=args.figure, pixel_x=p['pixel_x'], pixel_y=p['pixel_y'], estimated=True))
    write_csv(args.output, rows)
    write_json(str(args.output) + '.provenance.json', dict(calibration=config, points_file=str(Path(args.points).resolve()), points_sha256=uid(Path(args.points).read_text(encoding='utf-8-sig')), figure=args.figure, estimated=True))
    print('Digitized estimates saved; inspect and extract this CSV with --paper-id before approval.')


def run_pick(args):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title('NH3-SCR curve points: click; right-click undo; Save CSV')
    image = tk.PhotoImage(file=args.image)
    frame = tk.Frame(root)
    frame.pack(fill='both', expand=True)
    canvas = tk.Canvas(frame, width=min(image.width(), 1100), height=min(image.height(), 700), scrollregion=(0, 0, image.width(), image.height()))
    xs, ys = tk.Scrollbar(frame, orient='horizontal', command=canvas.xview), tk.Scrollbar(frame, orient='vertical', command=canvas.yview)
    canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
    canvas.grid(row=0, column=0, sticky='nsew'); ys.grid(row=0, column=1, sticky='ns'); xs.grid(row=1, column=0, sticky='ew')
    frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
    canvas.create_image(0, 0, anchor='nw', image=image)
    points, markers = [], []
    label = tk.Label(root, text='Read anchor pixel positions here; then click one curve at a time.')
    label.pack()

    def click(event):
        x, y = canvas.canvasx(event.x), canvas.canvasy(event.y)
        points.append(dict(pixel_x=x, pixel_y=y))
        markers.append(canvas.create_oval(x-3, y-3, x+3, y+3, fill='red'))
        label.configure(text=f'{len(points)} points; last pixel: {x:.1f}, {y:.1f}')

    def undo(event=None):
        if points:
            points.pop(); canvas.delete(markers.pop())

    def save():
        write_csv(args.output, points, ['pixel_x', 'pixel_y'])
        messagebox.showinfo('Saved', str(Path(args.output).resolve()))

    canvas.bind('<Button-1>', click); canvas.bind('<Button-3>', undo)
    tk.Button(root, text='Save CSV', command=save).pack()
    root.mainloop()


def run_search(args):
    params = urllib.parse.urlencode({'query.bibliographic': args.query, 'rows': args.limit})
    req = urllib.request.Request('https://api.crossref.org/works?' + params, headers={'User-Agent': 'NH3SCRDataTool/0.1 (literature metadata search)'})
    with urllib.request.urlopen(req, timeout=60) as response:
        items = json.load(response)['message']['items']
    rows = [dict(doi=r.get('DOI'), title='; '.join(r.get('title', [])), publisher=r.get('publisher'), url=r.get('URL'), links=r.get('link', [])) for r in items]
    write_json(args.output, rows)
    print(f'{len(rows)} metadata results. Full text and supplementary files must be obtained separately.')


def main():
    parser = argparse.ArgumentParser(description='NH3-SCR auditable literature data collection')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('screen', help='Inventory and screen local PDFs for NH3-SCR relevance')
    p.add_argument('input'); p.add_argument('-o', '--output', required=True)
    p.add_argument('--pages', type=int, choices=range(1, 6), default=2)
    p.set_defaults(func=run_screen)
    p = sub.add_parser('vector', help='Extract discrete PDF plot markers using a visually calibrated profile')
    p.add_argument('input'); p.add_argument('--profile', required=True); p.add_argument('-o', '--output', required=True)
    def vector_command(a):
        from .vectors import extract_vectors
        extract_vectors(a.input, read_json(a.profile), a.output)
    p.set_defaults(func=vector_command)
    p = sub.add_parser('annotate', help='Validate and import curated verbatim evidence annotations')
    p.add_argument('input'); p.add_argument('--annotations', required=True); p.add_argument('-o', '--output', required=True)
    def annotate_command(a):
        from .annotations import import_annotations
        records = import_annotations(a.input, read_json(a.annotations), a.output)
        print(f'Curated observations: {len(records)}; all pending review')
    p.set_defaults(func=annotate_command)
    p = sub.add_parser('features', help='Build wide features from reviewed records without resolving conflicts')
    p.add_argument('input'); p.add_argument('-o', '--output', required=True)
    def features_command(a):
        from .features import build_features
        build_features(read_json(a.input), a.output)
    p.set_defaults(func=features_command)
    p = sub.add_parser('extract', help='Extract candidates from local papers and raw tables')
    p.add_argument('input'); p.add_argument('-o', '--output', required=True)
    p.add_argument('--engine', choices=['rules', 'llm'], default='rules')
    p.add_argument('--config'); p.add_argument('--mapping'); p.add_argument('--manifest')
    p.add_argument('--paper-id'); p.add_argument('--reaction', default='NH3-SCR'); p.add_argument('--max-chars', type=int, default=24000)
    p.set_defaults(func=run_extract)
    p = sub.add_parser('convert', help='Call an installed MinerU or Marker CLI')
    p.add_argument('input'); p.add_argument('-o', '--output', required=True)
    p.add_argument('--backend', choices=['mineru', 'marker'], default='mineru'); p.add_argument('--executable')
    p.add_argument('--formula', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--tables', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    p.set_defaults(func=lambda a: convert(a.input, a.output, a.backend, a.executable, a.formula, a.tables, a.device))
    p = sub.add_parser('review', help='Apply reviewer decisions to candidates')
    p.add_argument('candidates'); p.add_argument('decisions'); p.add_argument('-o', '--output', required=True)
    p.set_defaults(func=run_review)
    p = sub.add_parser('export', help='Export approved observations and ML long table')
    p.add_argument('input'); p.add_argument('-o', '--output', required=True); p.set_defaults(func=run_export)
    p = sub.add_parser('digitize', help='Convert manually selected pixel coordinates to data')
    p.add_argument('points'); p.add_argument('--calibration', required=True); p.add_argument('--catalyst', required=True)
    p.add_argument('--figure', required=True); p.add_argument('-o', '--output', required=True); p.set_defaults(func=run_digitize)
    p = sub.add_parser('pick', help='Click PNG curve points in a local Tk window')
    p.add_argument('image'); p.add_argument('-o', '--output', required=True); p.set_defaults(func=run_pick)
    p = sub.add_parser('search', help='Search Crossref bibliographic metadata, requires network')
    p.add_argument('--query', default='NH3-SCR catalyst'); p.add_argument('--limit', type=int, choices=range(1, 101), default=20)
    p.add_argument('-o', '--output', required=True); p.set_defaults(func=run_search)
    p = sub.add_parser('harvest', help='Search, deduplicate, and download available NH3-SCR literature')
    p.add_argument('--query', action='append', help='Repeat for multiple search expressions; defaults to three NH3-SCR queries')
    p.add_argument('--sources', nargs='+', default=['openalex', 'crossref'], help='One or more of: nature openalex crossref scopus springer; use none for local inventory only')
    p.add_argument('--limit', type=int, default=100, help='Maximum results per query and source (1-1000)')
    p.add_argument('--from-year', type=int); p.add_argument('--to-year', type=int)
    p.add_argument('--email', help='Email for Unpaywall/OpenAlex polite pool; or set UNPAYWALL_EMAIL')
    p.add_argument('--openalex-key', help='OpenAlex API key; or set OPENALEX_API_KEY')
    p.add_argument('--elsevier-key', help='Elsevier API key; or set ELSEVIER_API_KEY')
    p.add_argument('--springer-key', help='Springer Nature API key; or set SPRINGER_API_KEY')
    p.add_argument('--local-papers', action='append', default=[], help='Inventory a local paper directory; repeat if needed')
    p.add_argument('--download', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--max-downloads', type=int, default=20)
    p.add_argument('--max-file-mb', type=int, default=100)
    p.add_argument('--library-dir', help='Shared full-text cache; defaults to paper_library beside the output directory')
    p.add_argument('--timeout', type=int, default=60); p.add_argument('--delay', type=float, default=0.2)
    p.add_argument('-o', '--output', required=True)
    def harvest_command(a):
        from .harvest import run_harvest
        return run_harvest(a)
    p.set_defaults(func=harvest_command)
    args = parser.parse_args()
    try:
        code = args.func(args)
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        code = 2
    raise SystemExit(code or 0)

"""Import explicitly curated source-linked observations; never label as automatic."""
from .core import make_record, write_json, write_csv
from .ingest import blocks


def import_annotations(source, annotations, out):
    from pathlib import Path
    by_locator = {b['locator']: b for b in blocks(source, annotations['paper_id'])}
    records = []
    for item in annotations['records']:
        block = by_locator[item['locator']]
        r = make_record(block, item['catalyst'], item['property'], item['raw_value'], item['unit'], item['evidence'], item.get('conditions'), 'curated_annotation', item.get('experiment_id'))
        r['annotation_author'] = annotations['author']
        r['annotation_note'] = item.get('note', '')
        r['issues'].extend(item.get('issues', []))
        records.append(r)
    out = Path(out)
    write_json(out / 'candidates.json', records)
    write_json(out / 'sources.json', list(by_locator.values()))
    write_csv(out / 'candidates.csv', records)
    write_csv(out / 'decisions_template.csv', [dict(record_id=r['record_id'], decision='', reviewer='', note='') for r in records])
    return records

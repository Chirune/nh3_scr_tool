"""Conservative feature pivot: same paper and verbatim sample identity only."""
from collections import defaultdict
from pathlib import Path
from .core import FIELDS, write_csv, write_json


def build_features(records, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    features = defaultdict(lambda: defaultdict(list))
    # Pending alternatives still make a feature ambiguous. A value explicitly
    # rejected by an expert remains in the audit trail but no longer blocks the
    # accepted value.
    for r in records:
        if r['review_status'] != 'rejected' and r['category'] in ['composition', 'synthesis', 'characterization'] and r['catalyst']:
            features[(r['paper_id'], r['catalyst'])][r['property']].append(r)
    rows, ambiguous = [], []
    for r in records:
        if r['category'] != 'performance' or r['review_status'] != 'approved' or r['issues'] or r['value'] is None:
            continue
        if r['property'] not in ['t50', 't90'] and r['conditions'].get('temperature', {}).get('value') is None:
            continue
        row = dict(paper_id=r['paper_id'], split_group=r['paper_id'], catalyst=r['catalyst'],
                   target_property=r['property'], target_value=r['value'], target_unit=r['unit'],
                   target_record_id=r['record_id'], estimated=r['estimated'], review_level=r.get('review_level'),
                   figure=r.get('figure'), source_locator=r['locator'])
        for key, q in r['conditions'].items():
            row['condition_' + key] = q['value']
        for prop, candidates in features[(r['paper_id'], r['catalyst'])].items():
            numeric_values = {c['value'] for c in candidates if c['value'] is not None}
            usable = [c for c in candidates if c['review_status']=='approved' and not c['issues'] and c['value'] is not None]
            if len(numeric_values) != 1 or not usable or any('cross_source_conflict' in c['issues'] for c in candidates):
                row['feature_' + prop] = None
                ambiguous.append(dict(paper_id=r['paper_id'], catalyst=r['catalyst'], property=prop, reason='ambiguous_or_unapproved', values=sorted(numeric_values)))
                continue
            row['feature_' + prop] = usable[0]['value']
            row['evidence_' + prop] = '|'.join(c['record_id'] for c in usable)
        rows.append(row)
    write_csv(out / 'ml_features.csv', rows)
    dedup = {(r['paper_id'],r['catalyst'],r['property']):r for r in ambiguous}
    write_json(out / 'ambiguous_features.json', list(dedup.values()))
    write_json(out / 'feature_schema.json', {k:{'category':v[0], 'unit':v[1]} for k,v in FIELDS.items()})
    print(f'Feature rows: {len(rows)}; unresolved sample properties: {len(dedup)}')
    return rows

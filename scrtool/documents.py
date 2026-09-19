import re
from pathlib import Path


def classify_document(source_blocks, path):
    """Conservative document-level classification used to protect ML exports."""
    path = Path(path)
    if path.suffix.lower() in {'.csv', '.tsv', '.xlsx'}:
        return _result(path, path.stem, 'structured_data', 1.0, True, 'structured table supplied as data input')
    meta = next((b.get('document_meta') for b in source_blocks if b.get('document_meta')), {})
    declared = ' '.join(str(meta.get(k, '')) for k in (
        'citation_article_type', 'dc.type', 'prism.aggregationtype'
    )).strip()
    title = meta.get('citation_title') or meta.get('og:title') or path.stem
    # MinerU splits one paper into many small blocks, so section headings such as
    # Methods and Data availability may occur well after the first dozen blocks.
    text = ' '.join(b.get('text', '') for b in source_blocks)[:80000]
    haystack = f'{declared} {title} {text}'.lower()

    if re.search(r'\b(review article|systematic review|literature review)\b', declared.lower()):
        return _result(path, title, 'review', 1.0, False, f'declared article type: {declared}')
    if re.search(r'\b(review|recent advances|progress and prospects|future prospects|perspective)\b', title.lower()):
        return _result(path, title, 'review', 0.95, False, f'title indicates review: {title}')
    if re.search(r'\b(this review|we review|in this review)\b', haystack):
        return _result(path, title, 'review', 0.9, False, 'document text identifies itself as a review')
    if re.search(r'\b(supporting information|supplementary information)\b|\bsupplementary(?:\b|[_ -])', title.lower()):
        return _result(path, title, 'supplementary_information', 0.9, False, 'title indicates supplementary information')

    research_markers = sum(bool(re.search(pattern, haystack)) for pattern in (
        r'\bmaterials and methods\b', r'\bexperimental section\b',
        r'\bmethods\b', r'\bresults and discussion\b', r'\bdata availability\b'
    ))
    if research_markers >= 2:
        return _result(path, title, 'research_article', 0.85, True, f'{research_markers} research-section markers')
    return _result(path, title, 'unknown', 0.0, False, 'insufficient evidence for primary research classification')


def _result(path, title, kind, confidence, eligible, evidence):
    return {
        'source_file': str(Path(path).resolve()),
        'title': title,
        'document_type': kind,
        'document_type_confidence': confidence,
        'training_eligible': eligible,
        'classification_evidence': evidence,
    }

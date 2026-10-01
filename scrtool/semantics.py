"""Collect comparative and qualitative statements with their exact source text."""
from __future__ import annotations

import re

from .core import ALIASES, FIELDS, uid

VERSION = 'nh3scr-semantic-candidates-v1'
CHANGE = re.compile(r'\b(?:increas\w*|decreas\w*|improv\w*|enhanc\w*|inhibit\w*|suppress\w*|higher|lower|better|worse|outperform\w*|fold)\b|提高|提升|增加|下降|降低|改善|抑制|优于|高于|低于|倍', re.I)
FOLD = re.compile(r'(?<![A-Za-z0-9_.])(?P<number>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|一|二|三|四|五|六|七|八|九|十)\s*[-–]?\s*(?:fold\b|times\b|倍)', re.I)
WORDS = dict(one=1, two=2, three=3, four=4, five=5, six=6, seven=7, eight=8, nine=9, ten=10,
             一=1, 二=2, 三=3, 四=4, 五=5, 六=6, 七=7, 八=8, 九=9, 十=10)
PROSPECTIVE = re.compile(r'\b(?:may|might|could|potential(?:ly)?|expected|anticipated|promising|future)\b|有望|可能|预计|未来|潜力', re.I)
NEGATED = re.compile(r'\b(?:not|never|no\s+(?:significant\s+)?(?:increase|improvement|enhancement))\b|未提高|未改善|没有改善|并未|未观察到', re.I)
MECHANISM = re.compile(r'\b(?:attributed\s+to|ascribed\s+to|we\s+propose|we\s+suggest|hypothes\w*)\b|归因于|推测|提出.*机理', re.I)


def sentences(text):
    """Keep quotes verbatim, including PDF line wrapping and decimal punctuation."""
    start = 0
    for end in re.finditer(r'[。!?]+|\.(?=\s+[A-Z])', text):
        quote = text[start:end.end()].strip()
        if quote:
            yield quote
        start = end.end()
    quote = text[start:].strip()
    if quote:
        yield quote


def semantic_records(block):
    for quote in sentences(block.get('text', '')):
        if not CHANGE.search(quote) and not FOLD.search(quote):
            continue
        fold = FOLD.search(quote)
        percent = re.search(r'(?:by|提高|提升|增加|降低|下降)\s*(\d+(?:\.\d+)?)\s*%', quote, re.I)
        reported_change = None
        if fold:
            raw = fold.group('number').lower()
            factor = WORDS[raw] if raw in WORDS else float(raw)
            ambiguous = bool(re.search(r'(?:提高|提升|增加)\s*' + re.escape(fold.group(0)), quote))
            reported_change = {'amount': factor, 'unit': 'fold', 'quote': fold.group(0),
                               'interpretation': 'ambiguous_increment' if ambiguous else 'reported_fold_expression'}
        elif percent:
            reported_change = {'amount': float(percent.group(1)), 'unit': '%', 'quote': percent.group(0),
                               'interpretation': 'relative_or_percentage_point_change_unresolved'}
        statement_type = ('prospective_claim' if PROSPECTIVE.search(quote) else
                          'negated_statement' if NEGATED.search(quote) else
                          'mechanistic_interpretation' if MECHANISM.search(quote) else
                          'reported_comparison' if reported_change or re.search(r'\bthan\b|compared|相比|相较|优于', quote, re.I) else
                          'qualitative_observation')
        metrics = []
        normalized = quote.translate(str.maketrans({'₃': '3', '₂': '2'}))
        for prop, (category, _) in FIELDS.items():
            if category not in {'performance', 'characterization'}:
                continue
            labels = [prop.replace('_', ' ')] + ALIASES.get(prop, [])
            if any(re.search(r'(?<!\w)' + re.escape(label) + r'(?!\w)', normalized, re.I) for label in labels):
                metrics.append(prop)
        subject = re.search(r'(?:catalyst|sample|催化剂|样品)\s*[:=]\s*([\w./+%()-]+)', quote, re.I)
        comparator = re.search(r'(?:compared\s+(?:with|to)|relative\s+to|than|相比于|相较于)\s*([^,;。\n]+)', quote, re.I)
        conditions = []
        for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(°\s*C|℃|K\b|ppm\b)', quote):
            conditions.append({'raw_value': match.group(1), 'raw_unit': match.group(2), 'quote': match.group(0),
                               'association': 'unverified_sentence_cooccurrence'})
        issues = []
        if not subject:
            issues.append('unresolved_subject')
        if len(metrics) != 1:
            issues.append('unresolved_metric')
        if statement_type == 'reported_comparison' and not comparator:
            issues.append('missing_comparator')
        if reported_change:
            issues.append('missing_absolute_baseline')
            if reported_change['interpretation'] == 'ambiguous_increment':
                issues.append('ambiguous_fold_wording')
        record = {
            'schema_version': VERSION, 'paper_id': block['paper_id'], 'source_id': block['source_id'],
            'source_file': block['source_file'], 'source_kind': block['kind'],
            'locator': block['locator'], 'block_id': block['block_id'], 'evidence': quote,
            'statement_type': statement_type, 'subject': subject.group(1) if subject else None,
            'prospective': bool(PROSPECTIVE.search(quote)), 'negated': bool(NEGATED.search(quote)),
            'comparator': comparator.group(1).strip() if comparator else None,
            'metric_candidates': metrics, 'reported_change': reported_change,
            'condition_mentions': conditions, 'baseline_value': None, 'absolute_value': None,
            'document_type': block.get('document_type'), 'method': 'semantic_rules',
            'review_status': 'pending', 'issues': issues, 'training_eligible': False,
        }
        record['claim_id'] = uid(VERSION, block['block_id'], quote)
        yield record

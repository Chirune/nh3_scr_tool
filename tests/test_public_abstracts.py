import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from scrtool.abstracts import enrich_public_abstracts, publisher_abstract
from scrtool.literature import rules_result


def response(data, status=200):
    result = Mock(status_code=status, url='https://api.example.test/papers', headers={})
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError('HTTP error', response=result)
    return result


def record(doi):
    return {'doi': doi, 'title': 'Cu-CHA catalysts for NH3-SCR', 'source_names': ['crossref'], 'abstract': None}


class PublicAbstractTests(unittest.TestCase):
    def enrich(self, records, session, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return enrich_public_abstracts(records, session, publisher_limit=0, **kwargs)

    def test_doi_join_survives_reordered_results_and_keeps_complete_abstract(self):
        rows = [record('10.1016/a'), record('10.1016/b'), dict(record('10.1016/c'), abstract='Keep this original abstract')]
        session = Mock()
        session.get.return_value = response({'results': [
            {'doi': 'https://doi.org/10.1016/B', 'id': 'https://openalex.org/W2', 'abstract_inverted_index': {'B': [0], 'abstract': [1]}},
            {'doi': 'https://doi.org/10.1016/a', 'id': 'https://openalex.org/W1', 'abstract_inverted_index': {'A': [0], 'abstract': [1]}}]})
        report = self.enrich(rows, session)
        self.assertEqual([r['abstract'] for r in rows], ['A abstract', 'B abstract', 'Keep this original abstract'])
        self.assertEqual(report['retrieved'], 2)
        session.post.assert_not_called()
        self.assertNotIn('10.1016/c', session.get.call_args.kwargs['params']['filter'])

    def test_semantic_scholar_fallback_does_not_use_batch_position_as_identity(self):
        rows = [record('10.1016/a'), record('10.1016/b')]
        session = Mock()
        session.get.return_value = response({'results': []})
        session.post.return_value = response([
            {'externalIds': {'DOI': '10.1016/B'}, 'abstract': 'B abstract', 'url': 'https://www.semanticscholar.org/paper/b'},
            {'externalIds': {'DOI': '10.1016/unrelated'}, 'abstract': 'Wrong paper'}])
        self.enrich(rows, session)
        self.assertIsNone(rows[0]['abstract'])
        self.assertEqual(rows[1]['abstract'], 'B abstract')
        self.assertEqual(rows[1]['abstract_source'], 'semantic_scholar')
        self.assertNotIn('headers', session.post.call_args.kwargs)

    def test_cache_skips_network_and_rejects_mismatched_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Mock()
            session.get.return_value = response({'results': [{'doi': '10.1016/a', 'id': 'https://openalex.org/W1',
                                                            'abstract_inverted_index': {'Cu': [0], 'SCR': [1]}}]})
            self.enrich([record('10.1016/a')], session, cache_dir=tmp)
            session.reset_mock()
            row = record('10.1016/a')
            report = self.enrich([row], session, cache_dir=tmp)
            self.assertEqual(report['cached'], 1)
            self.assertEqual(row['abstract'], 'Cu SCR')
            session.get.assert_not_called()
            session.post.assert_not_called()
            cache_file = next(Path(tmp).glob('*.json'))
            payload = json.loads(cache_file.read_text(encoding='utf-8'))
            payload['doi'] = '10.1016/other'
            cache_file.write_text(json.dumps(payload), encoding='utf-8')
            self.enrich([record('10.1016/a')], session, cache_dir=tmp)
            session.get.assert_called_once()

    def test_rate_limit_stops_route_and_next_route_can_still_succeed(self):
        rows = [record('10.1016/' + str(i)) for i in range(51)]
        session = Mock()
        session.get.return_value = response({}, status=429)
        session.post.return_value = response([{'externalIds': {'DOI': '10.1016/50'}, 'abstract': 'Last abstract',
                                              'url': 'https://www.semanticscholar.org/paper/last'}])
        report = self.enrich(rows, session)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(rows[-1]['abstract'], 'Last abstract')
        self.assertEqual(report['remaining'], 50)

    def test_publisher_requires_matching_identity_and_explicit_abstract(self):
        abstract = 'Cu-CHA catalysts were prepared and tested for NH3-SCR. Their catalytic activity and stability were measured.'
        html = '<meta name="citation_doi" content="10.1016/a"><div id="abstracts"><h2>Abstract</h2><p>' + abstract + '</p></div>'
        self.assertEqual(publisher_abstract(html, '10.1016/a', 'title'), abstract)
        self.assertIsNone(publisher_abstract(html, '10.1016/b', 'title'))
        self.assertIsNone(publisher_abstract('<meta name="description" content="search snippet">', '10.1016/a', 'title'))

    def test_search_summary_is_replaced_before_screening(self):
        row = dict(record('10.1016/a'), abstract='Brief search summary', abstract_is_full=False)
        self.assertEqual(rules_result(row)['decision'], 'review')
        session = Mock()
        session.get.return_value = response({'results': []})
        session.post.return_value = response([{'externalIds': {'DOI': '10.1016/a'},
            'abstract': 'Cu-CHA catalysts were prepared and tested for NH3-SCR.', 'url': 'https://www.semanticscholar.org/paper/a'}])
        self.enrich([row], session)
        self.assertIs(row['abstract_is_full'], True)
        self.assertEqual(rules_result(row)['decision'], 'target')

    def test_replay_does_not_repeat_crossref_known_missing_abstract(self):
        row = dict(record('10.1016/a'), source_names=['scopus'],
                   abstract_attempts=[{'source': 'crossref', 'status': 'missing_in_response'}])
        session = Mock()
        session.get.return_value = response({'results': []})
        session.post.return_value = response([])
        self.enrich([row], session)
        self.assertEqual(session.get.call_count, 1)  # OpenAlex only.

    def test_semantic_scholar_retries_once_and_respects_retry_after(self):
        row = record('10.1016/a')
        session = Mock()
        session.get.return_value = response({'results': []})
        limited = response({}, status=429)
        limited.headers['Retry-After'] = '2'
        session.post.side_effect = [limited, response([{'externalIds': {'DOI': '10.1016/a'},
                                                       'abstract': 'Recovered abstract', 'url': 'https://www.semanticscholar.org/paper/a'}])]
        with patch('scrtool.abstracts.time.sleep') as sleep:
            self.enrich([row], session)
        sleep.assert_called_once_with(2.0)
        self.assertEqual(row['abstract'], 'Recovered abstract')
        self.assertEqual(session.post.call_count, 2)


if __name__ == '__main__':
    unittest.main()

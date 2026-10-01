import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import requests

from scrtool.harvest import (Harvester, inventory_local, mdpi_static_pdf_urls, merge_records,
                             normalize_doi, parse_crossref, parse_openalex,
                             parse_nature_search, parse_springer,
                             parse_springer_jats, parse_scopus, safe_error,
                             validate_elsevier_fulltext)


class FakeResponse:
    def __init__(self, chunks, status=200):
        self.chunks = chunks
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP error')

    def iter_content(self, size):
        yield from self.chunks


class HarvestTests(unittest.TestCase):
    def test_scopus_search_description_is_preserved(self):
        row = parse_scopus({'dc:title': 'SCR', 'dc:description': {'$': '<p>NH<sub>3</sub>-SCR catalyst.</p>'}}, 'q')
        self.assertEqual(row['abstract'], 'NH3-SCR catalyst.')

    def test_elsevier_abstract_retrieval_and_permission_fallback(self):
        harvester = Harvester(elsevier_key='test-key')
        denied = requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403))
        harvester._get_json = Mock(side_effect=[denied, {
            'full-text-retrieval-response': {'coredata': {'dc:description': 'Cu-CHA catalysts were tested for NH3-SCR.'}}
        }])
        rows = [{'doi': '10.1016/test', 'source_ids': {'scopus': 'SCOPUS_ID:123'}, 'abstract': None},
                {'doi': '10.1016/keep', 'abstract': 'Original abstract'}]
        with contextlib.redirect_stdout(io.StringIO()):
            harvester.enrich_elsevier_abstracts(rows)
        self.assertEqual(rows[0]['abstract_source'], 'elsevier_article_abstract')
        self.assertEqual(rows[0]['abstract_status'], 'retrieved')
        self.assertEqual(rows[0]['abstract_errors'][0]['http_status'], 403)
        self.assertEqual(rows[1]['abstract'], 'Original abstract')
        self.assertEqual(harvester._get_json.call_count, 2)
        self.assertEqual(harvester._get_json.call_args.kwargs['params']['view'], 'META_ABS')

    def test_abstract_permission_denial_is_not_repeated_for_whole_batch(self):
        harvester = Harvester(elsevier_key='test-key')
        harvester._get_json = Mock(side_effect=requests.HTTPError('Forbidden', response=SimpleNamespace(status_code=403)))
        rows = [{'doi': '10.1016/' + str(i), 'source_ids': {'scopus': 'SCOPUS_ID:' + str(i)},
                 'abstract': None} for i in range(1, 4)]
        with contextlib.redirect_stdout(io.StringIO()):
            harvester.enrich_elsevier_abstracts(rows)
        self.assertEqual(harvester._get_json.call_count, 2)
        self.assertTrue(all(row['abstract_status'] == 'access_denied' for row in rows))
        self.assertTrue(all(not row['abstract'] for row in rows))

    def test_absent_abstract_is_not_filled_from_title(self):
        harvester = Harvester(elsevier_key='test-key')
        harvester._get_json = Mock(return_value={'full-text-retrieval-response': {'coredata': {'dc:title': 'NH3-SCR'}}})
        rows = [{'doi': '10.1016/test', 'abstract': None}]
        with contextlib.redirect_stdout(io.StringIO()):
            harvester.enrich_elsevier_abstracts(rows)
        self.assertIsNone(rows[0]['abstract'])
        self.assertEqual(rows[0]['abstract_status'], 'missing_in_response')

    def test_elsevier_fulltext_view_and_metadata_cache_validation(self):
        full = b'<full-text-retrieval-response xmlns:ja="urn:test"><originalText><ja:body><p>Experiment.</p></ja:body></originalText></full-text-retrieval-response>'
        metadata = b'<full-text-retrieval-response><coredata><title>SCR</title></coredata></full-text-retrieval-response>'
        harvester = Harvester(elsevier_key='test-key')
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'paper.xml'
            destination.write_bytes(metadata)
            harvester.session.get = Mock(return_value=SimpleNamespace(content=full, raise_for_status=lambda: None))
            result = harvester.download_elsevier_xml('10.1016/test', destination)
            self.assertEqual(result['status'], 'downloaded_xml')
            self.assertEqual(destination.read_bytes(), full)
            self.assertEqual(harvester.session.get.call_args.kwargs['params']['view'], 'FULL')
            self.assertEqual(harvester.download_elsevier_xml('10.1016/test', destination)['status'], 'existing_xml')
            self.assertEqual(harvester.session.get.call_count, 1)
            destination.unlink()
            harvester.session.get = Mock(return_value=SimpleNamespace(content=metadata, raise_for_status=lambda: None))
            with self.assertRaisesRegex(ValueError, 'full text is absent'):
                harvester.download_elsevier_xml('10.1016/test', destination)
            self.assertFalse(destination.exists())
        with self.assertRaisesRegex(ValueError, 'API error'):
            validate_elsevier_fulltext(b'<service-error><status>AUTHORIZATION_ERROR</status></service-error>')

    def test_normalize_and_merge_by_doi(self):
        self.assertEqual(normalize_doi('https://doi.org/10.1/ABC.'), '10.1/abc')
        a = {'record_id': 'a', 'doi': '10.1/x', 'title': 'A paper', 'year': 2020,
             'authors': ['A'], 'pdf_urls': [], 'source_names': ['crossref'],
             'source_ids': {'crossref': '10.1/x'}, 'query_matches': ['q']}
        b = {'record_id': 'b', 'doi': '10.1/X', 'title': 'A paper', 'year': 2020,
             'abstract': 'text', 'authors': ['B'], 'pdf_urls': ['https://x/p.pdf'],
             'source_names': ['openalex'], 'source_ids': {'openalex': 'W1'}, 'query_matches': ['q2']}
        rows = merge_records([a, b])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['authors'], ['A', 'B'])
        self.assertEqual(set(rows[0]['source_names']), {'crossref', 'openalex'})
        self.assertEqual(rows[0]['doi'], '10.1/x')
        c = dict(b, record_id='c', doi='10.1/other', source_ids={'openalex': 'W2'})
        self.assertEqual(len(merge_records([a, c])), 2)

    def test_parse_sources(self):
        oa = parse_openalex({'id': 'https://openalex.org/W1', 'title': 'SCR',
                            'publication_year': 2024, 'ids': {'doi': 'https://doi.org/10.2/x'},
                            'abstract_inverted_index': {'hello': [0], 'world': [1]},
                            'primary_location': {'pdf_url': 'https://x/a.pdf', 'source': {'display_name': 'J'}},
                            'authorships': [{'author': {'display_name': 'A'}}]}, 'q')
        self.assertEqual(oa['abstract'], 'hello world')
        self.assertEqual(oa['doi'], '10.2/x')
        cr = parse_crossref({'DOI': '10.2/x', 'title': ['SCR'], 'issued': {'date-parts': [[2024]]},
                             'author': [{'given': 'A', 'family': 'B'}],
                             'link': [{'URL': 'https://x/a.pdf', 'content-type': 'application/pdf'}]}, 'q')
        self.assertEqual(cr['year'], 2024)
        self.assertEqual(cr['authors'], ['A B'])
        sn = parse_springer({'identifier': 'doi:10.1038/test', 'title': 'Nature paper',
                             'publicationDate': '2025-01-02', 'publicationName': 'Nature',
                             'creators': [{'creator': 'A Author'}], 'openaccess': 'true',
                             'url': [{'format': 'pdf', 'value': 'https://x/paper.pdf'}]}, 'q')
        self.assertEqual(sn['doi'], '10.1038/test')
        self.assertTrue(sn['is_oa'])
        self.assertEqual(sn['pdf_urls'], ['https://x/paper.pdf'])
        self.assertNotIn('secret', safe_error(RuntimeError('https://x?a=1&api_key=secret')))
        jats = b'''<response><records><article article-type="research-article"><front>
        <journal-meta><journal-title-group><journal-title>Nature Test</journal-title></journal-title-group>
        <publisher><publisher-name>Nature</publisher-name></publisher></journal-meta>
        <article-meta><article-id pub-id-type="doi">10.1038/jats</article-id>
        <title-group><article-title>NH3 <sub>3</sub> SCR</article-title></title-group>
        <contrib-group><contrib contrib-type="author"><name><surname>Li</surname><given-names>A</given-names></name></contrib></contrib-group>
        <pub-date><year>2026</year></pub-date><abstract><p>Full abstract.</p></abstract>
        </article-meta></front><body><p>Body</p></body></article></records></response>'''
        parsed = parse_springer_jats(jats, 'q')
        self.assertEqual(parsed[0]['doi'], '10.1038/jats')
        self.assertEqual(parsed[0]['authors'], ['A Li'])

    def test_pdf_validation_and_local_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            harvester = Harvester(timeout=1)
            harvester.session.get = lambda *a, **k: FakeResponse([b'<html>bad</html>'])
            result = harvester.download_pdf(['https://x/not.pdf'], Path(tmp) / 'bad.pdf')
            self.assertEqual(result['status'], 'no_pdf')
            harvester.session.get = lambda *a, **k: FakeResponse([b'%PDF-1.7\nbody'])
            result = harvester.download_pdf(['https://x/good.pdf'], Path(tmp) / 'good.pdf')
            self.assertEqual(result['status'], 'downloaded_pdf')
            rows = inventory_local([tmp])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['extension'], '.pdf')

    def test_parse_current_nature_search_card(self):
        html = '''<article><h3 class="c-card__title"><a href="/articles/s41467-026-1"
        data-track-action="view article">NH3-SCR catalyst</a></h3>
        <div itemprop="description">Catalyst abstract.</div>
        <li itemprop="creator"><span itemprop="name">A. Author</span></li>
        <span data-test="article.type">Research</span>
        <span data-test="open-access">Open Access</span>
        <time datetime="2026-08-12" itemprop="datePublished">12 Aug 2026</time>
        <div data-test="journal-title-and-link">Nature Communications</div></article>'''
        rows = parse_nature_search(html, 'NH3-SCR')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['doi'], '10.1038/s41467-026-1')
        self.assertEqual(rows[0]['authors'], ['A. Author'])
        self.assertTrue(rows[0]['is_oa'])

    def test_mdpi_static_pdf_url(self):
        urls = mdpi_static_pdf_urls({
            'doi': '10.3390/catal8080336', 'volume': '8',
            'article_number': '336', 'journal': 'Catalysts',
        })
        self.assertEqual(urls, [
            'https://mdpi-res.com/d_attachment/catalysts/catalysts-08-00336/'
            'article_deploy/catalysts-08-00336.pdf'
        ])


if __name__ == '__main__':
    unittest.main()

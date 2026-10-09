"""Old PII-labelled PDFs must be accepted only with matching article evidence."""
import io
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdf_fetch import FetchError, inspect_pdf

DOI = '10.1016/s0016-2361(02)00321-6'
PII = 'S0016-2361(02)00321-6'
TITLE = 'Selective catalytic reduction of NO by ammonia with fly ash catalyst'


def make_pdf(lines, metadata=None, second_page=None):
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    font_ref = writer._add_object(font)
    for page_lines in [lines] + ([second_page] if second_page is not None else []):
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_ref})})
        parts = ['BT /F1 10 Tf 10 760 Td']
        for line in page_lines:
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            parts.append(f'({escaped}) Tj 0 -20 Td')
        parts.append('ET')
        content = DecodedStreamObject()
        content.set_data('\n'.join(parts).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(content)
    if metadata:
        writer.add_metadata(metadata)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class PDFIdentityTests(unittest.TestCase):
    def check(self, data, doi=DOI, title=TITLE):
        return inspect_pdf(data, {'doi': doi, 'title': title})

    def test_spaced_first_page_pii_and_title_pass_without_inventing_printed_doi(self):
        result = self.check(make_pdf([TITLE, 'PII: S 0 0 1 6 - 2 3 6 1 ( 0 2 ) 0 0 3 2 1 - 6']))
        self.assertEqual(result['identifier_type'], 'pii')
        self.assertEqual(result['identifier_value'], 'S0016236102003216')
        self.assertFalse(result['doi_printed_in_pdf'])
        self.assertEqual(result['doi_verified'], DOI)
        self.assertEqual(result['title_coverage'], 1)

    def test_pii_metadata_with_first_page_title_passes(self):
        result = self.check(make_pdf([TITLE], {'/Title': 'PII: ' + PII}))
        self.assertEqual(result['identifier_type'], 'pii')

    def test_other_pii_is_rejected_even_with_matching_title(self):
        with self.assertRaisesRegex(FetchError, '编号不一致'):
            self.check(make_pdf([TITLE, 'PII: S0016-2361(02)00322-6']))

    def test_correct_pii_with_unrelated_title_is_rejected(self):
        with self.assertRaisesRegex(FetchError, '题名证据不足'):
            self.check(make_pdf(['Completely unrelated biological research', 'PII: ' + PII]))

    def test_missing_title_record_cannot_use_pii_fallback(self):
        with self.assertRaises(FetchError):
            self.check(make_pdf([TITLE, 'PII: ' + PII]), title='')

    def test_pii_prefix_longer_number_is_not_an_identity_match(self):
        with self.assertRaises(FetchError):
            self.check(make_pdf([TITLE, 'PII: ' + PII + '9']))

    def test_unlabelled_serial_number_is_insufficient(self):
        with self.assertRaises(FetchError):
            self.check(make_pdf([TITLE, PII]))

    def test_title_alone_is_not_sufficient(self):
        with self.assertRaises(FetchError):
            self.check(make_pdf([TITLE]))

    def test_modern_or_other_publisher_doi_has_no_guessed_pii_mapping(self):
        for doi in ('10.1016/j.fuel.2003.12345', '10.1021/s0016-2361(02)00321-6'):
            with self.assertRaises(FetchError):
                self.check(make_pdf([TITLE, 'PII: ' + PII]), doi=doi)

    def test_second_page_citation_cannot_supply_fallback_identity(self):
        with self.assertRaises(FetchError):
            self.check(make_pdf(['Other manuscript bibliography'], second_page=[TITLE, 'PII: ' + PII]))

    def test_conflicting_metadata_and_front_page_pii_are_not_accepted(self):
        with self.assertRaisesRegex(FetchError, '冲突'):
            self.check(make_pdf([TITLE, 'PII: ' + PII], {'/Title': 'PII: S0016-2361(02)00322-6'}))

    def test_existing_full_doi_path_is_preserved(self):
        result = self.check(make_pdf([TITLE, DOI]))
        self.assertTrue(result['doi_printed_in_pdf'])
        self.assertEqual(result['identifier_type'], 'doi')


if __name__ == '__main__':
    unittest.main()

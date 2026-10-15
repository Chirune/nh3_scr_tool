import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import intake
import paper_workspace as work
from test_paper_workspace import make_pdf


class BrowserProvenanceTests(unittest.TestCase):
    def test_reflowed_page_basis_survives_intake_and_evidence_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            pdf=make_pdf(root/'browser.pdf',[['NO conversion reached 90% at 300 C.']])
            digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
            record={'id':'browser','doi':'10.9999/browser.paper','title':'Synthetic browser case',
                    'manual_decision':'target','effective_decision':'target','local_path':str(pdf),'local_sha256':digest,
                    'pdf_acquisition':{'status':'available','page_basis':'browser_reflowed_text','source_url':'https://publisher.example/paper'}}
            run={'profile':'scr_ammonia','records':[record]};path=root/'run.json'
            path.write_text(json.dumps(run),encoding='utf-8')
            received=intake.load_screened_input(path)
            self.assertEqual(len(received['papers']),1)
            article=received['papers'][0]
            self.assertEqual(article['page_basis'],'browser_reflowed_text')
            project=work.open_paper(article,root/'papers');work.extract_document(project)
            self.assertIn('非出版社原版页码',project['pages'][0]['location_label'])
            self.assertTrue(project['evidence'])
            self.assertTrue(all(e['source_ref']['page_basis']=='browser_reflowed_text' for e in project['evidence']))
            with self.assertRaisesRegex(ValueError,'原版曲线图'):
                work.ensure_image_batch(project,root/'images')


if __name__=='__main__':unittest.main()

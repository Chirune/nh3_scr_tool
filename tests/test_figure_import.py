import copy
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from scrtool.core import read_json, write_json
from scrtool.figure_digitizer.session import new_session, set_calibration, add_points, export_session
from scrtool.figure_import import build_figure_records
from scrtool.workbench import Project

ABSTRACT = 'NH3-SCR catalysts were prepared and experimentally tested. NOx conversion was measured at different temperatures.'
HTML = '<h1>NH3-SCR catalyst study</h1><h2>Materials and methods</h2><p>Cu-A catalyst was prepared.</p><h2>Results and discussion</h2><p>The GHSV was 150000 h^-1. NOx conversion increased by 20% compared with Cu-B.</p>'


class FigureImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'plot.png'; Image.new('RGB', (400, 300), 'white').save(self.image)
        self.session, image = new_session(self.image, output_root=self.root / 'sessions')
        image.close()
        set_calibration(self.session, {
            'x': dict(p1=[20, 260], p2=[380, 260], v1=473.15, v2=673.15, scale='linear', name='temperature', unit='K'),
            'y': dict(p1=[20, 260], p2=[20, 20], v1=0, v2=100, scale='linear', name='NOx conversion', unit='%')})
        self.session.update(roi=[20, 20, 380, 260], figure_label='Fig. 1a', doi='10.1016/test')
        add_points(self.session, [dict(px=200, py=44)], 'Cu-A', sample_label='Cu-A')
        export_session(self.session, {'reviewed': True})
        self.snapshot = Path(self.session['last_export_dir']) / '读数与溯源.json'

    def build(self, **kwargs):
        return build_figure_records(self.snapshot, '10.1016/test', 'nox_conversion', 'temperature',
                                    document_type='research_article', **kwargs)

    def test_units_pending_state_and_group_are_preserved(self):
        rows, sources = self.build(); row = rows[0]
        self.assertAlmostEqual(row['conditions']['temperature']['value'], 300)
        self.assertAlmostEqual(row['value'], 90)
        self.assertEqual(row['review_status'], 'pending')
        self.assertEqual(row['image_review_status'], 'user_reviewed')
        self.assertTrue(row['estimated'])
        self.assertTrue(row['digitization_group'])
        self.assertIn(row['evidence'], sources[0]['text'])

    def test_fixed_conditions_require_verbatim_original_evidence(self):
        context = dict(text='The GHSV was 150000 h^-1.', locator='page:8', block_id='methods')
        conditions = {'ghsv': dict(raw_value='150000', unit='h^-1', evidence=context['text'])}
        rows, _ = self.build(context=context, conditions=conditions)
        self.assertEqual(rows[0]['conditions']['ghsv']['value'], 150000)
        with self.assertRaises(ValueError):
            self.build(context=context, conditions={'ghsv': dict(raw_value='200000', unit='h^-1', evidence=context['text'])})

    def test_wrong_doi_and_tampered_values_rejected(self):
        data = read_json(self.snapshot)
        changed = copy.deepcopy(data); changed['doi'] = '10.1016/wrong'; write_json(self.snapshot, changed)
        with self.assertRaises(ValueError): self.build()
        changed = copy.deepcopy(data); changed['points'][0]['y'] = 99; write_json(self.snapshot, changed)
        with self.assertRaises(ValueError): self.build()

    def test_original_image_change_rejected(self):
        saved_image = self.snapshot.parent.parent / 'source.png'
        Image.new('RGB', (400, 300), 'black').save(saved_image)
        with self.assertRaises(ValueError): self.build()

    def test_bar_pixel_x_cannot_become_temperature(self):
        data = read_json(self.snapshot); data['chart_type'] = 'bar'; write_json(self.snapshot, data)
        with self.assertRaises(ValueError): self.build()

    def test_duplicate_point_id_and_calibration_mismatch_rejected(self):
        data = read_json(self.snapshot)
        duplicate = copy.deepcopy(data); duplicate['points'].append(copy.deepcopy(duplicate['points'][0])); write_json(self.snapshot, duplicate)
        with self.assertRaises(ValueError): self.build()
        data['points'][0]['calibration_id'] = 'stale'; write_json(self.snapshot, data)
        with self.assertRaises(ValueError): self.build()

    def prepare_project(self):
        abstract = self.root / 'abstract.json'
        write_json(abstract, [{'doi': '10.1016/test', 'title': 'NH3-SCR original experiment', 'abstract': ABSTRACT}])
        primary = self.root / 'paper.html'; primary.write_text(HTML, encoding='utf-8')
        project = Project(self.root / 'project'); project.import_abstracts([abstract])
        identity = project.state['papers'][0]['record_id']
        project.decide_papers([identity], 'target', 'expert')
        project.attach(identity, [primary], 'primary'); project.attach(identity, [self.image], 'supplement')
        project.extract([identity])
        context = next(b for b in read_json(project.run_path(identity) / 'workflow_sources.json') if 'GHSV' in b['text'])
        return project, identity, context

    def test_project_import_dedup_resume_review_and_export(self):
        project, identity, context = self.prepare_project()
        conditions = {'ghsv': dict(raw_value='150000', unit='h^-1', evidence='The GHSV was 150000 h^-1.')}
        n = project.import_figure(identity, self.snapshot, 'nox_conversion', 'temperature', context_block_id=context['block_id'], conditions=conditions)
        self.assertEqual(n, 1)
        self.assertEqual(project.import_figure(identity, self.snapshot, 'nox_conversion', 'temperature', context_block_id=context['block_id'], conditions=conditions), 0)
        row = next(r for r in project.candidates() if r['method'] == 'image_digitizer')
        self.assertEqual(row['doi'], '10.1016/test')
        project.review([row['record_id']], 'approve', 'expert')
        resumed = Project(project.folder); output, report = resumed.export()
        self.assertEqual(report['ml_rows'], 1)
        self.assertEqual(read_json(output / 'reviewed.json')[-1]['digitization_group'], row['digitization_group'])
        self.assertTrue(Path(row['provenance']['snapshot_path']).is_file())
        self.assertTrue(Path(row['provenance']['image_path']).is_file())

    def test_unknown_paper_source_is_not_imported(self):
        project, identity, _ = self.prepare_project()
        project.state['attachments'][identity] = [a for a in project.state['attachments'][identity] if not a['path'].endswith('.png')]
        with self.assertRaises(ValueError): project.import_figure(identity, self.snapshot, 'nox_conversion', 'temperature')

    def test_claim_review_does_not_make_absolute_training_data(self):
        project, _, _ = self.prepare_project()
        rows = project.claims(); self.assertEqual(len(rows), 1)
        project.review_claims([rows[0]['claim_id']], 'approve', 'expert', 'Comparison confirmed; baseline not stated')
        claim = project.claims()[0]
        self.assertEqual(claim['review_status'], 'approved')
        self.assertIsNone(claim['absolute_value'])
        self.assertFalse(claim['training_eligible'])
        folder = project.export_claims()
        self.assertTrue((folder / '语义候选.csv').exists())


if __name__ == '__main__': unittest.main()

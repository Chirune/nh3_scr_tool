"""Independent source and review-workflow checks for relationship extraction."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paper_semantic_audit as audit
import paper_workspace as work
import paper_semantic_report as report
import paper_semantic_facets as facets
from test_paper_workspace import make_pdf


class SemanticAuditTests(unittest.TestCase):
    def test_exact_main_quote_does_not_license_invented_condition_quote(self):
        text='Conversion remained stable for 24 h.'
        item={'start':0,'end':len(text),'evidence_quote':text,
              'conditions_mentions':[{'start':31,'end':35,'evidence_quote':'48 h'}]}
        self.assertTrue(audit.source_span_issues(item,text))
        item['conditions_mentions']=[{'start':30,'end':34,'evidence_quote':text[30:34]}]
        self.assertFalse(audit.source_span_issues(item,text))

    def test_repeated_quote_locations_are_preserved_not_averaged(self):
        rows=[{'evidence_id':str(i),'branch':'semantic','page':i,'quote':'Same   measured statement.',
               'kind':'qualitative','metric':'conversion'} for i in (1,2)]
        before=copy.deepcopy(rows);groups=audit.repeated_quote_groups(rows)
        self.assertEqual([x['page'] for x in groups['1']],[1,2]);self.assertEqual(before,rows)

    def test_same_page_repeated_facet_keeps_both_source_locations(self):
        from paper_semantics import semantic_candidates
        from paper_relations import relation_candidates
        sentence='The catalyst showed excellent sulfur tolerance.'
        text=sentence+'\n'+sentence
        precise=semantic_candidates(text)
        rows=facets.enrich_candidates(text,precise+relation_candidates(text,precise))
        sulfur=[e for e in rows if any(f['type']=='perturbation_tolerance' for f in e.get('semantic_facets',[]))]
        self.assertEqual({e['start'] for e in sulfur},{0,len(sentence)+1})
        self.assertTrue(all(text[e['start']:e['end']]==e['evidence_quote'] for e in sulfur))

    def test_training_related_review_prompts_keep_uncertainty_and_scope(self):
        e={'kind':'outlook','assertion_scope':'prior_work','sample_label':'',
           'semantic_facets':[{'type':'detection_limit'},{'type':'stability'}]}
        prompts=' '.join(audit.review_prompts(e))
        self.assertIn('未检出不填0',prompts);self.assertIn('他人研究',prompts)
        self.assertIn('起始与终止',prompts);self.assertIn('预期',prompts)

    def test_changed_parser_cannot_store_unfounded_nested_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf=make_pdf(Path(tmp)/'a.pdf',[['The catalytic activity remained stable for 24 h.']])
            project=work.open_paper({'local_pdf':str(pdf),'source_sha256':work.digest(pdf),'title':'SYNTHETIC'},Path(tmp)/'work')
            text='The catalytic activity remained stable for 24 h.'
            fake={'start':0,'end':len(text),'evidence_quote':text,'kind':'qualitative','metric':'activity',
                  'conditions_mentions':[{'start':0,'end':3,'evidence_quote':'900 C'}]}
            with patch('paper_semantic_facets.enrich_candidates',return_value=[fake]):work.extract_document(project)
            self.assertFalse(any(e['branch']=='semantic' for e in project['evidence']))
            self.assertTrue(any('关系或条件引文' in f for f in project['text_warnings']))

    def test_inventory_is_a_copy_and_exports_relations_separately(self):
        item={'evidence_id':'one','branch':'semantic','kind':'outlook','metric':'NO conversion','quote':'Will improve.',
              'semantic_facets':[{'type':'stability'}],'conditions_mentions':[],'review_status':'unreviewed','page':1}
        project={'evidence':[item],'semantic_extraction_version':'scientific-relations/2.0'}
        before=copy.deepcopy(project);result=report.semantic_inventory(project)
        self.assertEqual(project,before)
        self.assertTrue(result['facet_counts']);self.assertTrue(result['rows'][0]['review_prompts'])

    def test_uncertain_scientific_statement_keeps_stance_in_display(self):
        text='The catalyst may remain stable for 40 h.'
        rows=facets.enrich_candidates(text,[])
        item=next(e for e in rows if any(f['type']=='stability' for f in e['semantic_facets']))
        detail=' '.join(report.facet_lines(item))
        self.assertIn('40 h',detail);self.assertIn('未来预期',detail)
        self.assertIn('预期',' '.join(audit.review_prompts(item)))
        self.assertNotIn("['",detail)

    def test_detection_limit_display_does_not_convert_censoring_to_zero(self):
        text='NH3 was not detected; the detection limit was 2 ppm.'
        rows=facets.enrich_candidates(text,[])
        self.assertTrue(rows)
        detail=' '.join(line for e in rows for line in report.facet_lines(e))
        self.assertIn('不能填0',detail);self.assertIn('2 ppm',detail)
        self.assertTrue(all(e['value'] is None for e in rows))

    def test_context_modal_stance_never_rewrites_existing_numeric_claim(self):
        text='The NO conversion was 90%, but the catalyst may improve its stability for 20 h.'
        end=text.index(',')
        numeric={'start':0,'end':end,'evidence_quote':text[:end],'kind':'absolute','value':90,'value_high':None,
                 'operator':'eq','sample_label':'A','numeric_eligible':True}
        row=facets.enrich_candidates(text,[numeric])[0]
        self.assertEqual(row['kind'],'absolute');self.assertEqual(row['value'],90)
        self.assertEqual(audit.assertion_modes(row),[])
        self.assertTrue(any('future_or_modal' in audit.assertion_modes(f) for f in row['semantic_facets']))

    def test_source_positions_reject_boolean_indices(self):
        self.assertTrue(audit.source_span_issues({'start':False,'end':1,'evidence_quote':'x'},'x'))

    def test_scientific_interpretation_change_requires_review_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf=make_pdf(Path(tmp)/'b.pdf',[['The catalyst may remain stable for 40 h.']])
            project=work.open_paper({'local_pdf':str(pdf),'source_sha256':work.digest(pdf),'title':'SYNTHETIC'},Path(tmp)/'work')
            work.extract_document(project)
            item=next(e for e in project['evidence'] if e.get('semantic_facets'))
            eid=item['evidence_id']
            work.set_semantic_adoption(project,eid,'durability','reviewer-A','Only a future stability claim.',True)
            work.extract_document(project)
            same=next(e for e in project['evidence'] if e['evidence_id']==eid)
            self.assertEqual(same['review_status'],'reviewed')
            original=facets.enrich_candidates
            def changed(text,candidates):
                rows=original(text,candidates)
                for row in rows:
                    for facet in row.get('semantic_facets',[]):facet['parameters']['time_zero_defined']=True
                return rows
            with patch.object(facets,'enrich_candidates',side_effect=changed):work.extract_document(project)
            current=next(e for e in project['evidence'] if e['evidence_id']==eid)
            self.assertEqual(current['review_status'],'unreviewed')
            self.assertNotIn('semantic_adoption',current)
            self.assertEqual(current['previous_interpretation']['semantic_adoption']['purpose'],'durability')
            self.assertEqual(project['records'],[])
            history=copy.deepcopy(current['previous_interpretation'])
            with patch.object(facets,'enrich_candidates',side_effect=changed):work.extract_document(project)
            rescanned=next(e for e in project['evidence'] if e['evidence_id']==eid)
            self.assertEqual(rescanned['previous_interpretation'],history)
            folder=report.export_semantic_inventory(project)
            import json
            exported=json.loads((folder/'语义与原文证据.json').read_text(encoding='utf8'))
            exported_item=next(e for e in exported['report']['rows'] if e['evidence_id']==eid)
            self.assertEqual(exported_item['previous_interpretation']['semantic_adoption']['purpose'],'durability')
            with patch.object(facets,'enrich_candidates',return_value=[]):work.extract_document(project)
            stale=next(e for e in project['evidence'] if e['evidence_id']==eid)
            self.assertTrue(stale['stale'])
            self.assertEqual(stale['previous_interpretation']['semantic_adoption']['purpose'],'durability')


if __name__=='__main__':unittest.main()

"""AI proposals retain exact source evidence and cannot approve training data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import semantic_assistant as ai
import paper_workspace as work
from test_paper_workspace import make_pdf

QUOTE = 'NH3 coordination increases the energy barrier for Cu diffusion, preventing reactive dimer formation.'


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        pdf = make_pdf(root/'source.pdf', [[QUOTE]])
        self.project = work.open_paper({'local_pdf': str(pdf), 'doi': '10.1234/assistant', 'title': 'Synthetic'}, root/'papers')
        work.extract_document(self.project)
        self.item = next(e for e in self.project['evidence'] if e['branch'] == 'semantic')
        self.payload = {'purpose': 'explanation', 'summary_zh': '配位提高扩散能垒，阻碍二聚体形成。',
            'reason_zh': '原句讨论机理，没有给出性能数值。', 'evidence_quote': self.item['quote'], 'questions': []}

    def tearDown(self):
        self.temp.cleanup()

    def response(self, payload=None):
        return {'message': {'content': json.dumps(payload or self.payload)}}

    def test_model_uses_structured_local_request_and_does_not_mutate_evidence(self):
        before = copy.deepcopy(self.project)
        with patch.object(ai, '_request', return_value=self.response()) as request:
            suggestion = ai.suggest(self.project, self.item)
        self.assertEqual(request.call_args.args[0], '/api/chat')
        body = request.call_args.args[1]
        self.assertFalse(body['think'])
        self.assertEqual(body['format']['properties']['purpose'], ai.SCHEMA['properties']['purpose'])
        self.assertEqual(body['format']['properties']['evidence_quote']['const'], self.item['quote'][:120])
        self.assertEqual(suggestion['purpose'], 'explanation')
        self.assertEqual(self.project, before)

    def test_save_suggestion_does_not_confirm_or_create_performance_record(self):
        before = copy.deepcopy(self.item)
        with patch.object(ai, '_request', return_value=self.response()):
            result = ai.suggest(self.project, self.item)
        work.save_assistant_suggestion(self.project, self.item['evidence_id'], result)
        self.assertEqual(self.item['review_status'], 'unreviewed')
        self.assertFalse(self.item['usable']); self.assertFalse(self.project['records'])
        for key in ('value', 'value_high', 'quote', 'source_ref', 'relations'):
            self.assertEqual(self.item.get(key), before.get(key))
        work.extract_document(self.project)
        current=next(e for e in self.project['evidence'] if e['evidence_id']==self.item['evidence_id'])
        self.assertEqual(current['assistant_suggestion'], result)

    def test_rejects_invented_quote_extra_values_and_bad_category(self):
        for updates in ({'evidence_quote': 'This catalyst achieved 99% conversion.'},
                        {'value': 99}, {'purpose': 'train_automatically'}, {'questions': 'wrong type'}):
            with self.subTest(updates=updates), self.assertRaises(ai.AssistantError):
                ai.validate_suggestion({**self.payload, **updates}, self.item)

    def test_invalid_model_json_or_source_offsets_are_not_accepted(self):
        with patch.object(ai, '_request', return_value={'message': {'content': 'not JSON'}}), self.assertRaises(ai.AssistantError):
            ai.suggest(self.project, self.item)
        invalid = copy.deepcopy(self.item); invalid['start'] += 1
        with patch.object(ai, '_request') as request, self.assertRaises(ai.AssistantError):
            ai.suggest(self.project, invalid)
        request.assert_not_called()

    def test_saved_proposal_from_other_source_cannot_be_bound(self):
        with patch.object(ai, '_request', return_value=self.response()):
            result = ai.suggest(self.project, self.item)
        result['source_sha256'] = 'wrong document'
        with self.assertRaises(ValueError):
            work.save_assistant_suggestion(self.project, self.item['evidence_id'], result)
        self.assertNotIn('assistant_suggestion', self.item)

    def test_rules_are_identified_as_rules_and_recognize_mechanism(self):
        suggestion = ai.rule_suggestion(self.item)
        self.assertEqual(suggestion['purpose'], 'explanation')
        self.assertEqual(suggestion['backend'], 'rules')

import json
import tempfile
import unittest
from pathlib import Path

from scrtool.features import build_features


class FeatureResolutionTests(unittest.TestCase):
    def test_expert_rejected_conflict_does_not_block_feature(self):
        base = {"paper_id": "p", "source_id": "s", "source_file": "x", "source_kind": "test",
                "locator": "page:1", "block_id": "b", "conditions": {}, "experiment_id": None,
                "method": "test", "estimated": False, "document_type": "research_article",
                "document_type_confidence": 1, "training_eligible": True, "source_row": None}
        chosen = {**base, "record_id": "chosen", "catalyst": "A", "property": "cu_content",
                  "category": "composition", "raw_value": "0.48", "raw_unit": "wt%", "value": 0.48,
                  "unit": "wt%", "evidence": "e", "issues": [], "review_status": "approved"}
        rejected = {**chosen, "record_id": "rejected", "raw_value": "0.47", "value": 0.47,
                    "issues": ["cross_source_conflict"], "review_status": "rejected"}
        target = {**base, "record_id": "target", "catalyst": "A", "property": "nox_conversion",
                  "category": "performance", "raw_value": "50", "raw_unit": "%", "value": 50,
                  "unit": "%", "evidence": "e", "issues": [], "review_status": "approved",
                  "review_level": "expert_reviewed",
                  "conditions": {"temperature": {"value": 200, "unit": "degC"}}}
        with tempfile.TemporaryDirectory() as folder:
            rows = build_features([chosen, rejected, target], Path(folder))
            self.assertEqual(rows[0]["feature_cu_content"], 0.48)
            ambiguous = json.loads((Path(folder) / "ambiguous_features.json").read_text(encoding="utf-8"))
            self.assertEqual(ambiguous, [])


if __name__ == "__main__":
    unittest.main()

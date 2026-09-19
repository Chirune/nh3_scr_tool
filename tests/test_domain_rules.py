import unittest

from scrtool.extract import rule_records


def block(source):
    return {
        "text": source,
        "paper_id": "10.test/example",
        "source_id": "source",
        "source_file": "paper.pdf",
        "kind": "pdf_text",
        "locator": "page:1",
        "block_id": "block",
        "document_type": "research_article",
        "document_type_confidence": 1.0,
        "training_eligible": True,
    }


class Nh3ScrDomainRuleTests(unittest.TestCase):
    def test_active_site_type_and_distance(self):
        source = ("Hence, each isolated Mo ion and one adjacent outermost surface Fe ion "
                  "with a distance of ∼2.9 Å in between are assembled as one dinuclear site.")
        records = list(rule_records(block(source)))
        by_property = {r["property"]: r for r in records}
        self.assertEqual(by_property["active_site_type"]["value"], "dinuclear Mo1-Fe1")
        self.assertEqual(by_property["active_site_distance"]["value"], 2.9)

    def test_method_conditions_are_separate_experiments(self):
        source = ("Catalytic evaluations. SCR activity measurements were performed under atmospheric pressure. "
                  "The feed gas contained 500 ppm NO, 500 ppm NH3, 3.0 vol% O2. "
                  "The gas hourly space velocity (GHSV) was calculated to be 800,000 h−1. "
                  "H2O and SO2 durability measurements were performed under atmospheric pressure at 300 °C. "
                  "The feed gas contained 200 ppm SO2 (when used), 5.0 vol% H2O (when used).")
        records = list(rule_records(block(source)))
        values = {(r["experiment_id"], r["property"]): r["value"] for r in records}
        self.assertEqual(values[("standard_activity", "no_inlet")], 500)
        self.assertEqual(values[("standard_activity", "ghsv")], 800000)
        self.assertEqual(values[("durability", "temperature")], 300)
        self.assertEqual(values[("durability", "so2_inlet")], 200)
        self.assertEqual(values[("durability", "h2o_inlet")], 5)


if __name__ == "__main__":
    unittest.main()

"""Numbered-caption queue boundaries, including supplement and continuation figures."""
import unittest

from candidate_policy import caption_status, has_numbered_caption, selection_report, split_candidates


class CaptionPolicyTests(unittest.TestCase):
    def test_numbered_captions_and_inherited_subfigures_are_retained(self):
        for caption in ("Fig. 2. Conversion curves.", "Figure 12. Cont.",
                        "FIG.S3: Supplementary figure.", "图 7：性能曲线", "Fig.4(a). Conversion."):
            with self.subTest(caption=caption):
                self.assertTrue(has_numbered_caption({"figure_label": "Fig. 2（子图待框选）", "caption": caption}))

    def test_label_or_body_mention_alone_is_not_a_numbered_caption(self):
        for caption in ("", "Unlabelled diagram", "As shown in Fig. 2, conversion rises.",
                        "Figure 2 shows the conversion.", "Fig. 2 presents results."):
            with self.subTest(caption=caption):
                self.assertFalse(has_numbered_caption({"figure_label": "Fig. 2", "caption": caption}))
        self.assertEqual(caption_status({"caption": "Figure 2 shows results."}), "body_reference_not_caption")

    def test_continuation_and_subfigures_keep_distinct_ids_and_user_decisions(self):
        figures = [
            {"figure_id": "p1", "paper_id": "p", "page": 1, "caption": "Figure 1. Cont.", "review_status": "exclude"},
            {"figure_id": "p2", "paper_id": "p", "page": 2, "caption": "Figure 1. Main caption.", "review_status": "keep"},
            {"figure_id": "p2a", "paper_id": "p", "parent_figure_id": "p2", "caption": "Figure 1. Main caption."},
            {"figure_id": "raw", "paper_id": "p", "caption": "", "review_status": "keep"},
        ]
        batch = {"figures": figures, "papers": [{"paper_id": "p", "pages": [{"page": 3, "text_status": "no_text_layer"}]}]}
        kept, other = split_candidates(batch)
        self.assertEqual([f["figure_id"] for f in kept], ["p1", "p2", "p2a"])
        self.assertIs(other[0], figures[3])
        self.assertEqual(other[0]["review_status"], "keep")
        report = selection_report(batch)
        self.assertEqual(report["numbered_candidate_count"], 3)
        self.assertEqual(report["unmatched_candidate_count"], 1)
        self.assertEqual(report["papers"][0]["pages_without_readable_text"], [3])


if __name__ == "__main__":
    unittest.main()

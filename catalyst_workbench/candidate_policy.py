"""Default review queue: numbered figure captions, with recoverable raw regions.

This is a review-queue policy, not a scientific keep/exclude decision. Existing
candidate IDs, edits, reading projects and raw locator evidence stay intact.
"""
from __future__ import annotations

from collections import Counter

from locate import CAPTION_RE, REFERENCE_VERBS_RE


POLICY = "numbered-caption/1.0"


def caption_status(figure):
    caption = str(figure.get("caption") or "").strip()
    match = CAPTION_RE.match(caption)
    if not match:
        return "caption_missing" if not caption else "numbered_caption_not_recognized"
    remainder = caption[match.end():].lstrip().lstrip(".:：").lstrip()
    if REFERENCE_VERBS_RE.match(remainder):
        return "body_reference_not_caption"
    return "numbered_caption"


def has_numbered_caption(figure):
    # An editable label alone is not evidence that a caption was detected.
    # Subfigures inherit their parent's caption; same-number continuation pages
    # remain separate regions and must not be deduplicated by number alone.
    return caption_status(figure) == "numbered_caption"


def split_candidates(batch):
    numbered, unmatched = [], []
    for figure in batch.get("figures", []):
        (numbered if has_numbered_caption(figure) else unmatched).append(figure)
    return numbered, unmatched


def selection_report(batch):
    numbered, unmatched = split_candidates(batch)
    counts = Counter(f.get("paper_id") for f in numbered)
    other_counts = Counter(f.get("paper_id") for f in unmatched)
    return {
        "policy_version": POLICY,
        "default_view": "numbered_caption",
        "raw_candidate_count": len(numbered) + len(unmatched),
        "numbered_candidate_count": len(numbered),
        "unmatched_candidate_count": len(unmatched),
        "unmatched_reasons": dict(Counter(caption_status(f) for f in unmatched)),
        "papers": [{
            "paper_id": p.get("paper_id"), "doi": p.get("doi", ""),
            "title": p.get("title", ""), "scan_status": p.get("scan_status", ""),
            "numbered_candidate_count": counts[p.get("paper_id")],
            "unmatched_candidate_count": other_counts[p.get("paper_id")],
            "pages_without_readable_text": [page.get("page") for page in p.get("pages", [])
                                            if page.get("text_status") in ("no_text_layer", "failed")],
        } for p in batch.get("papers", [])],
        "note": "主列表和候选图清单只列出识别到编号图注的图片；无图号区域暂存查漏，原记录和人工判定不变。未识别不等于原文无图；有图号也不代表一定是可读数的性能图。",
    }

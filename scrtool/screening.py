"""Stage 1.1: auditable local literature import and NH3-SCR screening."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .core import uid, write_csv, write_json


DECISIONS = {"target", "review", "non_target"}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ABSTRACT_RE = re.compile(
    r"(?is)\babstract\b\s*[:.]?\s*(?P<body>.*?)(?=\n\s*(?:keywords?|key\s+words|"
    r"1\.?\s+introduction|introduction|graphical\s+abstract|highlights|article\s+info)\b)"
)
INLINE_ABSTRACT_RE = re.compile(r"(?is)\babstract\b\s*[:.]?\s*(?P<body>.{250,5000})")
KEYWORDS_RE = re.compile(
    r"(?is)\b(?:keywords?|key\s+words)\b\s*[:.]?\s*(?P<body>.*?)(?=\n\s*(?:1\.?\s+introduction|introduction)\b)"
)

REACTION_STRONG = [
    re.compile(r"\bNH\s*3\s*[- ]?SCR\b", re.IGNORECASE),
    re.compile(r"\bammonia\s*[- ]?SCR\b", re.IGNORECASE),
    re.compile(
        r"(?:NO\s*x|NOx|nitrogen\s+oxides?|\bNO\b).{0,100}selective\s+(?:catalytic\s+)?reduction.{0,100}(?:with|by|using)?\s*(?:NH\s*3|ammonia)",
        re.IGNORECASE,
    ),
    re.compile(
        r"selective\s+(?:catalytic\s+)?reduction.{0,100}(?:NO\s*x|NOx|nitrogen\s+oxides?|\bNO\b).{0,100}(?:NH\s*3|ammonia)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:selective\s+catalytic\s+reduction|\bSCR\b).{0,140}(?:NH\s*3|ammonia).{0,140}(?:NO\s*x|NOx|nitrogen\s+oxides?|\bNO\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:NH\s*3|ammonia).{0,140}(?:selective\s+catalytic\s+reduction|\bSCR\b).{0,140}(?:NO\s*x|NOx|nitrogen\s+oxides?|\bNO\b)",
        re.IGNORECASE,
    ),
]
REACTION_TERMS = {
    "scr": re.compile(r"\bSCR\b|selective\s+catalytic\s+reduction", re.IGNORECASE),
    "nh3": re.compile(r"\bNH\s*3\b|\bammonia\b", re.IGNORECASE),
    "nox": re.compile(r"\bNO\s*x\b|\bNOx\b|nitrogen\s+oxides?|\bNO\b", re.IGNORECASE),
}
EXPERIMENTAL_RE = re.compile(
    r"\b(catalyst|catalysts|catalytic\s+activity|conversion|selectivity|GHSV|space\s+velocity|"
    r"temperature|hydrothermal|stability|deactivation|prepared|synthesi[sz]ed|characteri[sz]ed)\b",
    re.IGNORECASE,
)
REVIEW_RE = re.compile(
    r"\b(review|perspective|recent\s+advances|state\s+of\s+the\s+art|progress\s+in)\b",
    re.IGNORECASE,
)
COMPUTATIONAL_RE = re.compile(
    r"\b(density\s+functional\s+theory|DFT|computational|theoretical|microkinetic)\b",
    re.IGNORECASE,
)
NON_TARGET_RE = re.compile(
    r"\b(CO2\s+hydrogenation|methanol\s+synthesis|photocatalytic|electrocatalytic|"
    r"selective\s+non[- ]catalytic\s+reduction|SNCR)\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("₂", "2").replace("₃", "3").replace("–", "-"))


def _title(text: str) -> str:
    skipped = {"abstract", "article info", "keywords", "highlights", "research article"}
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    candidates = []
    for line in lines[:30]:
        lowered = line.lower().strip(": ")
        if lowered in skipped or "doi" in lowered or len(line) < 12:
            continue
        candidates.append(line)
        if len(" ".join(candidates)) >= 60 or len(candidates) == 2:
            break
    return " ".join(candidates)[:500]


def _abstract(text: str) -> tuple[str, str, str]:
    match = ABSTRACT_RE.search(text)
    status = "abstract_section"
    if not match:
        match = INLINE_ABSTRACT_RE.search(text)
        status = "inline_abstract" if match else "fallback_first_pages"
    abstract = match.group("body") if match else text[:5000]
    keyword_match = KEYWORDS_RE.search(text)
    keywords = keyword_match.group("body") if keyword_match else ""
    return _clean(abstract).strip()[:6000], _clean(keywords).strip()[:1200], status


def classify(title: str, abstract: str, keywords: str = "") -> tuple[str, str, str]:
    """Return decision, article_type and an evidence-based reason."""
    text = _clean(" ".join(part for part in (title, abstract, keywords) if part))
    strong = any(pattern.search(text) for pattern in REACTION_STRONG)
    term_hits = [name for name, pattern in REACTION_TERMS.items() if pattern.search(text)]
    experimental = bool(EXPERIMENTAL_RE.search(text))
    review = bool(REVIEW_RE.search(text))
    computational = bool(COMPUTATIONAL_RE.search(text))
    negative = NON_TARGET_RE.search(text)

    if review and (strong or len(term_hits) >= 2):
        return "review", "review_article", "NH3-SCR evidence present; review/perspective article requires manual scope decision"
    if computational and not experimental and (strong or len(term_hits) >= 2):
        return "review", "computational_or_mechanistic", "NH3-SCR evidence present; no clear experimental catalyst-performance evidence"
    if strong and experimental:
        return "target", "experimental_primary", "strong NH3-SCR reaction match and experimental catalyst/performance terms"
    if strong or len(term_hits) >= 2:
        return "review", "uncertain", "partial NH3-SCR evidence; manual review required"
    if negative:
        return "non_target", "other_reaction", f"non-target marker: {negative.group(0)}"
    return "non_target", "insufficient_evidence", "no sufficient NH3-SCR reaction evidence in title/abstract/keywords"


def _pdf_text(path: Path, pages: int = 2) -> tuple[str, int, bool]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    selected = reader.pages[: max(1, pages)]
    text = "\n".join(page.extract_text() or "" for page in selected)
    return text, len(reader.pages), len(text.strip()) < 200


def _doi(path: Path, text: str) -> str:
    for value in (text, path.parent.name, path.stem):
        match = DOI_RE.search(value.replace("doi.org/", ""))
        if match:
            return match.group(0).rstrip(".,;)]}")
    # Common download-folder form: 10.1038s41467-020-15261-5
    folder = path.parent.name
    match = re.fullmatch(r"(10\.\d{4,9})(.+)", folder, re.IGNORECASE)
    if match and not folder.lower().endswith("scr"):
        return match.group(1) + "/" + match.group(2).lstrip("/_-")
    return ""


def screen_file(path: Path, pages: int = 2) -> dict:
    raw = path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text, page_count, needs_ocr = _pdf_text(path, pages)
        title = _title(text)
        abstract, keywords, extraction_status = _abstract(text)
        decision, article_type, reason = classify(title, abstract, keywords)
        error = ""
    except Exception as exc:
        text, title, abstract, keywords = "", path.stem, "", ""
        page_count, needs_ocr = 0, True
        extraction_status = "failed"
        decision, article_type = "review", "unreadable"
        reason = "PDF text extraction failed; manual review required"
        error = f"{type(exc).__name__}: {exc}"
    doi = _doi(path, text)
    return {
        "paper_id": doi or uid(source_sha256, path.name),
        "doi": doi,
        "filename": path.name,
        "source_path": str(path.resolve()),
        "source_sha256": source_sha256,
        "page_count": page_count,
        "title": title,
        "abstract": abstract,
        "keywords": keywords,
        "article_type": article_type,
        "auto_decision": decision,
        "manual_decision": "",
        "effective_decision": decision,
        "reason": reason,
        "abstract_extraction_status": extraction_status,
        "needs_ocr": needs_ocr,
        "error": error,
    }


def screen_directory(input_path: str | Path, output_path: str | Path, pages: int = 2) -> dict:
    root = Path(input_path).resolve()
    pdfs = [root] if root.is_file() and root.suffix.lower() == ".pdf" else sorted(root.rglob("*.pdf"))
    if not pdfs:
        raise ValueError("No PDF files found")
    rows = [screen_file(path, pages) for path in pdfs]
    out = Path(output_path).resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "literature_manifest.json", rows)
    write_csv(out / "literature_manifest.csv", rows)
    review_fields = ["paper_id", "doi", "filename", "title", "auto_decision", "manual_decision", "reviewer_notes", "reason", "source_path"]
    write_csv(
        out / "manual_review.csv",
        ({**row, "reviewer_notes": ""} for row in rows),
        review_fields,
    )
    counts = {decision: sum(row["effective_decision"] == decision for row in rows) for decision in sorted(DECISIONS)}
    report = {"input": str(root), "pdfs": len(rows), "counts": counts, "needs_ocr": sum(bool(row["needs_ocr"]) for row in rows), "errors": sum(bool(row["error"]) for row in rows)}
    write_json(out / "screening_report.json", report)
    return report

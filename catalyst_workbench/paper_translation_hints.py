"""Conservative Chinese reading checks derived only from the original sentence.

These are reminders about wording, not translated facts, extracted values,
quality scores, or eligibility decisions. A translation may be supplied by the
UI, but is never treated as evidence or used to manufacture an original claim.
"""
from __future__ import annotations

import re
import unicodedata


_UNCERTAIN = re.compile(
    r"\b(?:may|might|could|possibly|potentially|perhaps|presumably|probably|likely|unlikely)\b|"
    r"\b(?:is|are|was|were)\s+(?:likely|unlikely)\b|"
    r"可能|或许|有望|推测|尚不确定", re.I
)
_FUTURE = re.compile(
    r"\b(?:will|would|expected|anticipated|prospective)\b|"
    r"\b(?:predict(?:ed|s)?|forecast(?:ed|s)?)\b|"
    r"预计|预期|将会|将有|预测|未来", re.I
)
_CAUSAL = re.compile(
    r"\bbecause\b|\bdue\s+to\b|\bowing\s+to\b|\bon\s+account\s+of\b|"
    r"\b(?:attribut(?:ed|able)|ascribed)\s+to\b|\bresult(?:ed|ing)?\s+from\b|"
    r"归因于|由于|因为|原因|所致", re.I
)
_DECLINE = re.compile(r"\b(?:decreas\w*|declin\w*|drop\w*|fell|fallen|lowered)\b|下降|降低|减少", re.I)
_NEGATION = re.compile(
    r"\bnot\b(?!\s+only\b)|\b(?:cannot|never|neither|without)\b|"
    r"(?-i:\b(?:no|No)\b)(?!\s*\.)|\b(?:lack(?:s|ed)?|absence)\s+of\b|"
    r"\b(?:failed|fails?)\s+to\b|\b(?:doesn['’]t|isn['’]t|wasn['’]t|"
    r"aren['’]t|weren['’]t|didn['’]t|don['’]t|can['’]t|couldn['’]t)\b|"
    r"并未|没有|未能|未见|未发现|不能|不显著|无明显|不再|不增加|不降低|不支持", re.I
)
_SIGNIFICANT = re.compile(r"\bsignificant(?:ly)?\b|显著", re.I)
_FOLD = re.compile(
    r"\b(?:\d+(?:\.\d+)?|two|three|four|five|six|seven|eight|nine|ten|hundred)"
    r"\s*[- ]?fold\b|\b(?:doubled|tripled)\b|"
    r"\b(?:twice|thrice)\s+(?:as|that|the)\b|"
    r"\b(?:\d+(?:\.\d+)?|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s+times\s+(?:higher|lower|greater|larger|smaller|as|that)\b|"
    r"(?:\d+(?:\.\d+)?|一|两|二|三|四|五|六|七|八|九|十|百)\s*倍|翻倍|减半", re.I
)
_PERCENTAGE = re.compile(r"%|\bpercent(?:age)?\b|百分比|百分点|百分之", re.I)
_POINTS = re.compile(r"\bpercentage[- ]points?\b|百分点", re.I)
_COMPARISON = re.compile(
    r"\bcompar(?:ed|ison)\s+(?:with|to)\b|\brelative\s+to\b|"
    r"\b(?:versus|vs\.?)\b|\bthan\b|\b(?:control|reference)\s+(?:sample|catalyst)s?\b|"
    r"\bbaseline\b|相比|相较|相对于|对照样品|参照样品|基准值|比.{1,25}(?:增加|降低|高|低|多|少)", re.I
)
_BOUND = re.compile(
    r"\b(?:at\s+(?:least|most)|(?:less|more|greater|lower|higher)\s+than|"
    r"not\s+exceed(?:ing)?|no\s+(?:less|more)\s+than|up\s+to)\s+"
    r"(?:(?:about|approximately)\s+)?(?:[+\-]?\d|\bthe\b)|"
    r"\b(?:above|below|over|under)\s+(?:(?:(?:about|approximately)\s+)?[+\-]?\d|(?:the\s+)?(?:detection|quantification)\s+limit)|"
    r"[<>≤≥]\s*[+\-]?\d|至少|至多|不超过|不低于|高于\s*\d|低于\s*\d|低于.{0,4}检出限", re.I
)
_APPROX = re.compile(
    r"\b(?:approximately|approx\.?|roughly|nearly|circa)\s+[+\-]?\d|"
    r"\babout\s+[+\-]?\d|\bca\.\s*\d|[~≈]\s*[+\-]?\d|"
    r"约\s*\d|大约|近似|左右|大致", re.I
)


def reading_hints(original: str, translated: str = "") -> list[str]:
    """Return up to four concise checks triggered by source wording.

    The optional ``translated`` argument is intentionally unused: this first
    version cannot reliably align formulas, number words, or translated values,
    and therefore does not claim a translation is correct or numerically wrong.
    """
    if not isinstance(original, str) or not original.strip():
        return []
    text = unicodedata.normalize("NFKC", original)
    # A month/date is not the modal verb "may". This is a matching buffer only.
    text = re.sub(r"\bMay\s+(?:\d{1,2}(?:st|nd|rd|th)?\b|\d{4}\b)", " ", text)
    hints: list[str] = []

    uncertain, future = bool(_UNCERTAIN.search(text)), bool(_FUTURE.search(text))
    if uncertain and future:
        hints.append("原文同时含可能性与预期／未来表述：请保留‘可能、预计、将会’的区别，不能读成已经证实的结果。")
    elif uncertain:
        hints.append("原文含可能性表述：may、might、could 等不等于‘必然’或‘已经证实’，请保留不确定性。")
    elif future:
        hints.append("原文含预期／未来表述：请区分预测、计划和已经观察到的结果，不能直接当作已完成实验。")
    if uncertain and _CAUSAL.search(text):
        scope = "下降本身与下降原因的推测" if _DECLINE.search(text) else "结果本身与原因的推测"
        hints[0] += scope + "需分开核对，不确定性未必修饰整个结果。"

    assertions = []
    if _NEGATION.search(text):
        assertions.append("请保留否定及其作用范围；‘没有显著增加’不等于‘显著减少’")
    if _SIGNIFICANT.search(text):
        assertions.append("‘显著’未必指统计显著性；需核对是否报告统计检验或显著性标准")
    if assertions:
        hints.append("；".join(assertions) + "。")

    comparisons = []
    if _FOLD.search(text):
        comparisons.append("倍数需核对基准，‘增加到’与‘增加了’不等价；含混倍数先保留原句")
    if _POINTS.search(text):
        comparisons.append("百分点是两个百分数之差，不能改写成同数值的相对百分比增长")
    elif _PERCENTAGE.search(text):
        comparisons.append("百分数需保留计量基准；百分含量、相对增幅和百分点不能混用")
    if _COMPARISON.search(text) or comparisons:
        comparisons.append("核对比较主体、参照样品、指标及测试条件")
    if comparisons:
        hints.append("；".join(comparisons) + "。")

    qualifiers = []
    if _BOUND.search(text):
        qualifiers.append("上下限或不等号应保留方向；高于、低于、至多不等于精确值，低于检出限不等于零")
    if _APPROX.search(text):
        qualifiers.append("原文含近似表述，请保留‘约／大致’，不要增加原文没有的精度")
    if qualifiers:
        hints.append("；".join(qualifiers) + "。")
    return hints[:4]

"""Transparent, high-recall literature triage; no network or model calls.

The groups contain OR alternatives and a branch requires AND across its groups.
`coverage` is the fraction of required concept groups found, NOT a probability,
relevance score or measured accuracy. Co-occurrence does not establish a causal
or sample-level relationship. Decisions always remain subject to full-text review.

CuZn article-type handling can be configured in
PROFILE_INFO['cuzn']['article_type_decisions']; changing the setting changes only
fully matching records, never the material/reaction requirements.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any


PROFILE_INFO: dict[str, dict[str, Any]] = {
    "cuzn": {
        "title": "铜锌催化 CO₂ 加氢制甲醇",
        "description": "铜锌材料 AND CO₂ AND 加氢 AND 甲醇；综述及理论研究默认待复核。",
        "queries": [
            "copper zinc carbon dioxide hydrogenation methanol",
            "Cu ZnO CO2 hydrogenation methanol",
        ],
        "article_type_decisions": {
            "review": "review", "theoretical": "review", "combined": "review"
        },
    },
    "scr_ammonia": {
        "title": "NH₃-SCR（不限催化剂材料）",
        "description": (
            "NH₃/氨 AND SCR/选择性催化还原/NOx 还原；不限材料。"
            "另保留 Pd 或 Ru 与 CeO₂ 体系的氨吸附子方向；理论文章不自动排除。"
        ),
        "queries": [
            "ammonia selective catalytic reduction",
            "palladium ceria ammonia adsorption",
            "ruthenium ceria ammonia adsorption",
        ],
        "article_type_decisions": {},
    },
}


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Named concept alternatives are kept separate so the UI can show exactly what
# matched. Boundaries prevent element symbols being found inside unrelated words.
_COPPER = _rx(r"\bcopper\b|(?<![a-z])Cu(?:\d*(?:O\d*)?)?(?![a-z])|铜")
_ZINC = _rx(r"\bzinc\b|(?<![a-z])Zn(?:\d*(?:O\d*)?)?(?![a-z])|锌")
_CUZN = _rx(r"(?<![a-z])Cu\d*(?:[-/\s]+)?Zn(?:\d*O\d*)?(?![a-z])|铜\s*[-/]?\s*锌|铜锌")
_CO2 = _rx(r"(?<![a-z0-9])CO\s*2(?![a-z0-9])|\bcarbon\s+dioxide\b|二氧化碳")
_HYDROGENATION = _rx(r"\bhydrogenat(?:ion|ions|e|ed|ing)\b|加氢|氢化")
_METHANOL = _rx(r"\bmethanol\b|(?<![a-z0-9])CH3OH(?![a-z0-9])|甲醇")
_AMMONIA = _rx(r"(?<![a-z0-9])NH\s*3(?![a-z0-9])|\bammonia\b|氨(?!基酸)")
_SCR = _rx(
    r"(?<![a-z0-9])SCR(?![a-z0-9])|"
    r"\bselective\s+catalytic\s+reduction\b|选择性催化还原|"
    r"\b(?:NOx|NO|nitrogen\s+oxides?|nitric\s+oxide)\s+(?:catalytic\s+)?reduction\b|"
    r"\breduction\s+of\s+(?:(?:the|nitrogen)\s+)?(?:NOx|NO|nitrogen\s+oxides?|nitric\s+oxide)\b|"
    r"氮氧化物还原|氮氧化物的还原|(?:NOx|NO)\s*还原"
)
_CERIA = _rx(r"\bceria\b|\bcerium\s+(?:di)?oxide\b|(?<![a-z])CeO\s*2(?![a-z0-9])|氧化铈|二氧化铈")
_NOBLE_METAL = _rx(
    r"\bpalladium\b|\bruthenium\b|"
    r"(?<![a-z])(?:Pd|Ru)(?:\d*(?:O\d*)?)?(?![a-z])|钯|钌"
)
_ADSORPTION = _rx(r"\badsor(?:ption|ptive|b|bs|bed|bing)\b|\bchemisor(?:ption|bed)\b|吸附")
_THEORY = _rx(
    r"(?<![a-z])DFT(?![a-z])|\bdensity[-\s]+functional\s+theory\b|"
    r"\bfirst[-\s]+principles\b|\bab\s+initio\b|\btheoretical\b|"
    r"\bcomputational\b|第一性原理|密度泛函|理论计算|计算研究"
)
_EXPERIMENTAL = _rx(r"\bexperimental(?:ly)?\b|\bsynthesi[sz](?:ed|ing)\b|\bmeasured\b|实验研究|实验测量|制备了")
_REVIEW_TITLE = _rx(r"\breview\b|\bperspective\b|\brecent\s+advances\b|\bprogress\s+in\b|综述|研究进展|展望")
_REVIEW_ABSTRACT = _rx(r"\b(?:this|present|our)\s+(?:systematic\s+)?review\b|本文综述|本文回顾|本综述")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(item) for item in value if item is not None)
    if not isinstance(value, str):
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-"}))
    return re.sub(r"\s+", " ", value).strip()


def _hits(pattern: re.Pattern[str], text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in pattern.finditer(text)))[:12]


def _article_type(record: dict[str, Any], title: str, abstract: str) -> str:
    supplied = _text(record.get("article_type") or record.get("type") or record.get("document_type")).lower()
    if re.search(r"\breview\b|综述", supplied):
        return "review"
    if _REVIEW_TITLE.search(title) or _REVIEW_ABSTRACT.search(abstract):
        return "review"
    combined_text = title + "\n" + abstract
    theoretical = bool(_THEORY.search(combined_text))
    experimental = bool(_EXPERIMENTAL.search(combined_text))
    if theoretical and experimental:
        return "combined"
    if theoretical:
        return "theoretical"
    if experimental:
        return "experimental"
    return "unspecified"


def _evidence(groups: dict[str, list[str]], sections: list[tuple[str, str]]) -> list[str]:
    evidence: list[str] = []
    for group, matches in groups.items():
        if not matches:
            continue
        for section, text in sections:
            found = next((re.search(re.escape(hit), text, re.I) for hit in matches if re.search(re.escape(hit), text, re.I)), None)
            if found is not None:
                start, end = max(0, found.start() - 60), min(len(text), found.end() + 100)
                evidence.append(f"{group}｜{section}：{text[start:end]}")
                break
    return evidence


def screen_record(record: dict, profile: str) -> dict:
    """Triage one metadata record using transparent keyword group rules.

    Expected inputs: title, abstract (or abstract_text), keywords, optional type.
    Empty/unavailable abstracts never justify a non_target decision. A complete
    match in title/keywords can still be a preliminary target without an abstract.
    The function never fetches data or interprets experimental numerical values.
    """
    if profile not in PROFILE_INFO:
        raise ValueError(f"未知筛选方向：{profile}；可选：{', '.join(PROFILE_INFO)}")
    title = _text(record.get("title"))
    abstract = _text(record.get("abstract") or record.get("abstract_text"))
    if abstract.lower() in {"no abstract available", "abstract unavailable", "n/a", "none", "null", "无摘要"}:
        abstract = ""
    keywords = _text(record.get("keywords"))
    text = "\n".join([title, abstract, keywords])
    article_type = _article_type(record, title, abstract)
    ammonia = _hits(_AMMONIA, text)
    ceria = _hits(_CERIA, text)
    noble_metal = _hits(_NOBLE_METAL, text)
    priority_focus = bool(ammonia and ceria and noble_metal) if profile == "scr_ammonia" else False
    topic_branch = "background"

    if profile == "cuzn":
        copper, zinc, composite = _hits(_COPPER, text), _hits(_ZINC, text), _hits(_CUZN, text)
        material = list(dict.fromkeys(composite + copper + zinc)) if composite or (copper and zinc) else []
        groups = {
            "铜锌材料": material,
            "CO₂": _hits(_CO2, text),
            "加氢": _hits(_HYDROGENATION, text),
            "甲醇": _hits(_METHANOL, text),
        }
        full_match = all(groups.values())
        if full_match:
            topic_branch = "CuZn-CO2加氢制甲醇"
    else:
        scr_process = _hits(_SCR, text)
        adsorption = _hits(_ADSORPTION, text)
        scr_match = bool(ammonia and scr_process)
        adsorption_match = bool(ammonia and ceria and noble_metal and adsorption)
        if scr_match:
            topic_branch = "NH3-SCR"
            groups = {"NH₃/氨": ammonia, "SCR/NOx还原": scr_process}
        elif adsorption_match or priority_focus:
            topic_branch = "Pd-Ru-CeO2氨吸附"
            groups = {"NH₃/氨": ammonia, "氧化铈": ceria, "Pd或Ru": noble_metal, "氨吸附": adsorption}
        else:
            groups = {"NH₃/氨": ammonia, "SCR/NOx还原": scr_process}
        full_match = scr_match or adsorption_match

    missing = [name for name, matches in groups.items() if not matches]
    coverage = (len(groups) - len(missing)) / len(groups)
    if full_match:
        decision = PROFILE_INFO[profile].get("article_type_decisions", {}).get(article_type, "target")
        if decision not in {"target", "review"}:
            raise ValueError("完整命中文献的 article_type_decisions 只能设置为 target 或 review")
        if decision == "review":
            reason = "必需概念组均命中；该方向将综述或理论研究列为待复核，避免混入实验建模数据。"
        elif topic_branch == "Pd-Ru-CeO2氨吸附":
            reason = "命中氨吸附相关子方向：NH₃、氧化铈、Pd或Ru及吸附；保留供机理研究，不等同于已证明具备SCR性能。"
        else:
            reason = "必需概念组均命中，列入相关候选；关键词共现尚不能证明属于同一反应或样品。"
        if not abstract:
            reason += " 当前缺少摘要，依据标题/关键词初判，须全文确认。"
    elif not abstract:
        decision = "review"
        reason = "缺少摘要且概念组未完整命中，证据不足；补充摘要或全文后复核，不直接排除。"
    elif any(groups.values()):
        decision = "review"
        reason = "仅部分概念组命中，需人工确认研究对象和反应。缺少：" + "、".join(missing) + "。"
        if priority_focus:
            reason += " 已涉及用户关注的Pd/Ru-氧化铈与氨，吸附关系尚未明确。"
    else:
        decision = "non_target"
        reason = "现有标题、摘要和关键词未命中该方向的必需概念组，初步判为不相关；规则可能漏掉同义表达。"

    return {
        "decision": decision,
        "reason": reason,
        "matched_groups": groups,
        "missing_groups": missing,
        "coverage": round(coverage, 4),
        "article_type": article_type,
        "evidence": _evidence(groups, [("标题", title), ("摘要", abstract), ("关键词", keywords)]),
        "topic_branch": topic_branch,
        "priority_focus": priority_focus,
    }

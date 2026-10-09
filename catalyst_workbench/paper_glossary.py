"""Small, local reading glossary for catalysis evidence.

This is explanatory UI metadata, never a translation or scientific-data parser.
Only terms present in the supplied text are returned. Numeric facts, original
sentences, review decisions and downstream encoded records are not modified.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


GLOSSARY_VERSION = "catalysis-reading-glossary/1.0"
_ADS = "https://goldbook.iupac.org/terms/view/A00155"
_ABS = "https://goldbook.iupac.org/terms/view/A00036"
_TOF = "https://goldbook.iupac.org/terms/view/T06534"
# PDFs often detach a formula's subscript. Permit only short gaps at these
# known formula positions, never strip whitespace globally (NO 2 stays NO 2).
# At most one line break avoids joining a formula fragment across paragraphs.
_PDF_SUBSCRIPT_GAP = r"[ \t]{0,2}(?:\r?\n[ \t]{0,2})?"
_NH3 = rf"NH{_PDF_SUBSCRIPT_GAP}3"
_H2 = rf"H{_PDF_SUBSCRIPT_GAP}2"


@dataclass(frozen=True)
class _Entry:
    term: str
    zh: str
    explanation: str
    pattern: re.Pattern
    source: str = ""
    context: str = ""


def _entry(term, zh, explanation, words=(), symbols=(), source="", context=""):
    # The scoped flag allows English words to ignore case while preserving
    # chemical-symbol / abbreviation case (NO is not the English word 'no').
    parts = [f"(?i:{p})" for p in words] + list(symbols)
    pattern = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(parts) + r")(?![A-Za-z0-9_])")
    return _Entry(term, zh, explanation, pattern, source, context)


_ENTRIES = (
    _entry("NH₃-SCR", "氨选择性催化还原", "以氨作还原剂催化去除氮氧化物的反应体系。", words=(r"ammonia[- ]selective catalytic reduction", r"selective catalytic reduction (?:of NOx? )?(?:by|with|using) ammonia"), symbols=(_NH3 + r"\s*[-–]\s*SCR",)),
    _entry("SCR", "选择性催化还原", "一种催化还原过程；是否以氨为还原剂要看原文。", words=(r"selective catalytic reduction",), symbols=(r"SCR",)),
    _entry("NH₃", "氨", "在本项目中可作为还原剂或吸附探针，具体角色以原文为准。", words=(r"ammonia",), symbols=(_NH3,)),
    _entry("NOₓ", "氮氧化物", "本研究常涉及 NO 和 NO₂；原文的具体统计口径需要核对。", words=(r"nitrogen oxides",), symbols=(r"NO[xX]",)),
    _entry("NO₂", "二氧化氮", "氮氧化物的一种，不应与 NO 合并为同一组分。", words=(r"nitrogen dioxide",), symbols=(r"NO2",)),
    _entry("NO", "一氧化氮", "SCR 研究中常见的反应物；不是英文否定词 no。", words=(r"nitric oxide",), symbols=(r"NO",)),
    _entry("N₂O", "氧化亚氮", "部分反应中的副产物，与氮气 N₂ 不同。", words=(r"nitrous oxide",), symbols=(r"N2O",)),
    _entry("CO₂", "二氧化碳", "铜锌催化甲醇合成研究中的常见原料。", words=(r"carbon dioxide",), symbols=(r"CO2",)),
    _entry("methanol", "甲醇", "化学式 CH₃OH，需区分其生成速率、收率和选择性。", words=(r"methanol",), symbols=(r"CH3OH",)),
    _entry("CO₂ hydrogenation", "二氧化碳加氢", "二氧化碳与氢参与的反应；产物不限于甲醇。", words=(r"carbon dioxide hydrogenation", r"CO2 hydrogenation")),
    _entry("RWGS", "逆水煤气变换", "通常指 CO₂ 与 H₂ 生成 CO 和 H₂O 的反应。", words=(r"reverse water[- ]gas shift",), symbols=(r"RWGS",)),
    _entry("CeO₂ / ceria", "氧化铈", "可作催化材料或载体；化学式 CeO₂。", words=(r"ceria", r"cerium (?:di)?oxide"), symbols=(r"CeO2",)),
    _entry("Pd / palladium", "钯", "元素 Pd；单凭元素名不能确定其价态或活性位。", words=(r"palladium",), symbols=(r"Pd",)),
    _entry("Ru / ruthenium", "钌", "元素 Ru；单凭元素名不能确定其价态或活性位。", words=(r"ruthenium",), symbols=(r"Ru",)),
    _entry("Cu / copper", "铜", "元素 Cu；需另核对铜含量、价态及所在结构。", words=(r"copper",), symbols=(r"Cu",)),
    _entry("Zn / zinc", "锌", "元素 Zn；ZnO 指氧化锌。", words=(r"zinc",), symbols=(r"Zn",)),
    _entry("CuZn", "铜锌体系", "包含铜和锌的材料简称，并不自动说明配比或物相。", words=(r"copper[- ]zinc",), symbols=(r"Cu[-/]?Zn",)),
    _entry("Ce³⁺", "三价铈", "指铈的 +3 氧化态；比例需以原文的分析方法与分母为准。", symbols=(r"Ce\s*3\s*\+",)),
    _entry("Si/Al", "硅铝比", "通常指硅与铝的摩尔或原子数量比，需核对作者定义；与 SiO₂/Al₂O₃ 比值不同。", words=(r"silicon[- ]to[- ]alumin(?:um|ium) ratio",), symbols=(r"Si\s*/\s*Al",)),
    _entry("SiO₂/Al₂O₃", "二氧化硅与氧化铝之比", "若按氧化物摩尔数计，数值是 Si/Al 原子比的两倍；须先确认作者的计量基准。", words=(r"silica[- ]to[- ]alumina ratio",), symbols=(r"SiO2\s*/\s*Al2O3",)),
    _entry("adsorption", "吸附", "分子等在表面或界面富集；不要与 absorption（吸收）混淆。", words=(r"adsorption", r"adsorb(?:ed|ing|s)?"), source=_ADS),
    _entry("absorption", "吸收", "可指物质被另一物质吸收，或光等辐射能量被物质吸收；须看语境。", words=(r"absorption", r"absorb(?:ed|ing|s)?"), source=_ABS),
    _entry("desorption", "脱附", "先前吸附在表面的物种离开表面的过程。", words=(r"desorption", r"desorb(?:ed|ing|s)?")),
    _entry("chemisorption", "化学吸附", "吸附物与表面形成化学相互作用的吸附。", words=(r"chemisorption", r"chemical adsorption")),
    _entry("physisorption", "物理吸附", "以分子间作用为主的吸附；不能仅凭峰高判断具体吸附量。", words=(r"physisorption", r"physical adsorption")),
    _entry("adsorption energy", "吸附能", "描述吸附前后能量差；正负号及参照态依作者公式确定。", words=(r"adsorption energ(?:y|ies)",)),
    _entry("adsorption capacity", "吸附容量", "给定条件下可吸附多少物质，常按材料质量归一。", words=(r"adsorption capacit(?:y|ies)",)),
    _entry("surface coverage", "表面覆盖度", "表示表面被吸附物占据的程度；分母定义须看原文。", words=(r"surface coverages?",)),
    _entry("conversion", "转化率", "反应物被消耗的比例；不等于目标产物的选择性或收率。", words=(r"conversions?",)),
    _entry("selectivity", "选择性", "已反应部分有多大比例形成指定产物；须核对计量与计算基准。", words=(r"selectivit(?:y|ies)",)),
    _entry("yield", "收率", "相对某一进料或理论产量基准获得的目标产物量；按原文定义比较。", words=(r"yields?",)),
    _entry("TOF", "周转频率", "单位活性位点在单位时间内的反应次数；位点计数方式会影响结果。", words=(r"turnover frequenc(?:y|ies)",), symbols=(r"TOF",), source=_TOF),
    _entry("space-time yield", "时空收率", "单位时间、单位催化剂质量或体积的产物量；归一基准必须核对。", words=(r"space[- ]time yield",), symbols=(r"STY",)),
    _entry("GHSV", "气体体积空速", "气体体积流量除以催化剂或床层体积；常用 h⁻¹，需核对温压基准。", words=(r"gas hourly space velocity",), symbols=(r"GHSV",)),
    _entry("WHSV", "质量空速", "进料质量流量除以催化剂质量；通常用 h⁻¹。", words=(r"weight hourly space velocity",), symbols=(r"WHSV",)),
    _entry("residence time", "停留时间", "流体在反应区停留的时间；其计算依赖所选体积与流量基准。", words=(r"residence times?",)),
    _entry("time on stream", "连续运行时间", "催化剂在反应物流中已运行的时间，常用于评价稳定性。", words=(r"time[- ]on[- ]stream",), symbols=(r"TOS",)),
    _entry("active site", "活性位点", "参与催化反应的位置或局部结构；具体组成需证据支持。", words=(r"active sites?", r"catalytic sites?")),
    _entry("oxygen vacancy", "氧空位", "晶格中原本由氧占据的位置缺少氧；不自动等于某一种活性位点。", words=(r"oxygen vacanc(?:y|ies)",)),
    _entry("lattice oxygen", "晶格氧", "构成固体晶格的氧，与表面吸附氧的归属需结合表征。", words=(r"lattice oxygen",)),
    _entry("Brønsted acid site", "布朗斯特酸位", "能提供质子的酸性位点。", words=(r"Br[oø]nsted(?:[- ]Lowry)? acid(?:ic)?(?: sites?)?",)),
    _entry("Lewis acid site", "路易斯酸位", "能接受电子对的酸性位点。", words=(r"Lewis acid(?:ic)?(?: sites?)?",)),
    _entry("metal dispersion", "金属分散度", "金属在材料中分散的程度；定量时通常依赖特定表征和计数假设。", words=(r"metal dispersion",)),
    _entry("metal-support interaction", "金属与载体相互作用", "金属组分与承载材料之间的相互影响；名称本身不说明方向或强弱。", words=(r"(?:strong )?metal[- ]support interactions?",), symbols=(r"SMSI",)),
    _entry("support", "载体（催化材料语境）", "催化研究中常指承载活性组分的材料；普通英语也可表示支持，需看原句。", words=(r"supports?",), context="support"),
    _entry("activity", "活性（催化语境）", "这里常指催化反应能力；比较时须看具体速率、转化率和测试条件。", words=(r"(?:catalytic )?activit(?:y|ies)",), context="activity"),
    _entry("deactivation", "失活", "运行中催化性能下降；不能仅凭该词判断原因。", words=(r"deactivation", r"deactivat(?:e|ed|es|ing)")),
    _entry("sintering", "烧结", "颗粒在热处理或运行中长大、聚并的过程，可能改变暴露表面。", words=(r"sintering", r"sintered")),
    _entry("poisoning", "中毒", "某些物种使催化位点失去或降低功能；可逆性需原文说明。", words=(r"poisoning", r"poisoned")),
    _entry("regeneration", "再生", "通过处理恢复催化性能；不意味着一定完全恢复。", words=(r"regeneration", r"regenerat(?:e|ed|ing)")),
    _entry("hydrothermal aging", "水热老化", "在含水、高温条件下的处理或长期运行；强度依温度、时间和含水量决定。", words=(r"hydrothermal ag(?:e)?ing", r"hydrothermally aged")),
    _entry("sulfur resistance", "抗硫能力", "含硫条件下保持催化性能的能力；需核对硫浓度与测试时长。", words=(r"sul(?:f|ph)ur (?:resistance|tolerance)", r"SO2 (?:resistance|tolerance)")),
    _entry("water resistance", "耐水能力", "含水条件下保持性能的能力；不能直接等同于长期水热稳定性。", words=(r"water (?:resistance|tolerance)", r"H2O (?:resistance|tolerance)")),
    _entry("calcination", "焙烧", "制备中的受控热处理；与催化反应测试温度要分开。", words=(r"calcination", r"calcin(?:ed|ing)")),
    _entry("impregnation", "浸渍", "使前驱体溶液进入或接触载体的制备方法。", words=(r"impregnation", r"impregnat(?:ed|ing)")),
    _entry("co-precipitation", "共沉淀", "多种组分共同沉淀的一类制备过程。", words=(r"co[- ]?precipitation", r"co[- ]?precipitated")),
    _entry("ion exchange", "离子交换", "材料中的可交换离子被其他离子替换。", words=(r"ion[- ]exchange", r"ion[- ]exchanged")),
    _entry("BET", "BET 比表面积分析", "用气体吸附数据估计表面积的方法；结果依吸附气体与拟合区间。", words=(r"Brunauer[-– ]Emmett[-– ]Teller",), symbols=(r"BET",)),
    _entry("specific surface area", "比表面积", "单位质量材料的表面积，常用 m²/g。", words=(r"specific surface areas?",)),
    _entry("pore volume", "孔体积", "材料孔隙所占体积，常按质量归一。", words=(r"pore volumes?",)),
    _entry("pore size", "孔径", "孔的大小；平均孔径与孔径分布不是同一个量。", words=(r"pore (?:size|diameter)s?",)),
    _entry("NH₃-TPD", "氨程序升温脱附", "升温时检测氨的脱附，常辅助研究表面酸性；峰温并非酸量。", words=(_NH3 + r"[- ]temperature[- ]programmed desorption",), symbols=(_NH3 + r"[ \t]*[-–][ \t]*TPD",)),
    _entry("TPD", "程序升温脱附", "按程序升温并检测脱附物种的方法；须辨明脱附气体。", words=(r"temperature[- ]programmed desorption",), symbols=(r"TPD",)),
    _entry("H₂-TPR", "氢程序升温还原", "在氢气条件下升温，观察样品的还原过程。", symbols=(_H2 + r"[ \t]*[-–][ \t]*TPR",)),
    _entry("TPR", "程序升温还原", "按程序升温研究材料的还原行为；还原温度与还原量应分开。", words=(r"temperature[- ]programmed reduction",), symbols=(r"TPR",)),
    _entry("XRD", "X 射线衍射", "常用于分析晶体物相与结构；峰强不直接等于组分含量。", words=(r"X[- ]ray diffraction",), symbols=(r"XRD",)),
    _entry("XPS", "X 射线光电子能谱", "常用于分析表面元素与化学状态；峰拟合归属需核对。", words=(r"X[- ]ray photoelectron spectroscopy",), symbols=(r"XPS",)),
    _entry("XAS", "X 射线吸收谱", "研究元素附近电子与局部结构的信息。", words=(r"X[- ]ray absorption spectroscopy",), symbols=(r"XAS",)),
    _entry("XANES", "X 射线吸收近边结构", "吸收边附近的谱学信息，可辅助分析价态与局部结构。", words=(r"X[- ]ray absorption near[- ]edge structure",), symbols=(r"XANES",)),
    _entry("EXAFS", "扩展 X 射线吸收精细结构", "可用于分析吸收原子周围的配位环境；结果依赖拟合。", words=(r"extended X[- ]ray absorption fine structure",), symbols=(r"EXAFS",)),
    _entry("DRIFTS", "漫反射红外傅里叶变换光谱", "常用于观察表面吸附物种；谱峰与物种的对应需证据支持。", words=(r"diffuse reflectance infrared Fourier transform spectroscopy",), symbols=(r"DRIFTS",)),
    _entry("FTIR", "傅里叶变换红外光谱", "通过红外吸收等信息研究振动与化学基团。", words=(r"Fourier[- ]transform infrared(?: spectroscopy)?",), symbols=(r"FT[- ]?IR",)),
    _entry("TEM", "透射电子显微镜", "利用透过样品的电子观察微观结构。", words=(r"transmission electron microscop(?:y|e)",), symbols=(r"TEM",)),
    _entry("SEM", "扫描电子显微镜", "用扫描电子束观察形貌等信息；此缩写也可能表示标准误，须看语境。", words=(r"scanning electron microscop(?:y|e)",), symbols=(r"SEM",), context="sem"),
    _entry("HAADF-STEM", "高角环形暗场扫描透射电镜", "一种电子显微成像方式；亮度还受厚度、成像条件等影响。", words=(r"high[- ]angle annular dark[- ]field(?: scanning transmission electron microscop(?:y|e))?",), symbols=(r"HAADF[- ]STEM",)),
    _entry("EDS / EDX", "能量色散 X 射线谱", "常用于分析元素种类和分布。", words=(r"energy[- ]dispersive X[- ]ray spectroscopy",), symbols=(r"EDS", r"EDX")),
    _entry("Raman", "拉曼光谱", "由非弹性散射获得振动等信息，可辅助研究结构变化。", words=(r"Raman(?: spectroscopy)?",)),
    _entry("in situ", "原位", "在指定环境或处理过程中进行观察；不一定同步测量催化性能。", words=(r"in[- ]situ",)),
    _entry("operando", "工作状态下联用表征", "通常强调反应运行时同步获取结构信息与催化性能。", words=(r"operando",)),
    _entry("ex situ", "非原位／离位", "通常在处理或反应环境之外进行分析。", words=(r"ex[- ]situ",)),
    _entry("DFT", "密度泛函理论", "电子结构计算方法；计算结果还取决于模型与计算设置。", words=(r"density[- ]functional theory",), symbols=(r"DFT",)),
    _entry("activation energy", "活化能", "描述反应速率随温度变化或反应能垒的量；表观活化能与微观能垒需区分。", words=(r"(?:apparent )?activation energ(?:y|ies)",)),
    _entry("energy barrier", "能垒", "反应路径上需跨越的能量差；需核对起点和是否包含自由能修正。", words=(r"(?:free[- ]energy |reaction |energy )barriers?",)),
    _entry("transition state", "过渡态", "反应路径上连接反应物与产物的一种临界结构。", words=(r"transition states?",)),
    _entry("rate-determining step", "速率控制步骤", "对整体反应速率起主要限制作用的步骤；需要动力学或计算支持。", words=(r"rate[- ](?:determining|limiting) steps?",), symbols=(r"RDS",)),
    _entry("adsorption isotherm", "吸附等温线", "固定温度下吸附量与压力或浓度的关系。", words=(r"adsorption isotherms?",)),
    _entry("wt.%", "质量百分比", "按质量计的百分含量；与原子百分比、体积百分比不能直接互换。", words=(r"weight percent(?:age)?",), symbols=(r"wt\.?\s*%",)),
    _entry("at.%", "原子百分比", "某元素原子数占所计入总原子数的百分比。", words=(r"atomic percent(?:age)?",), symbols=(r"at\.?\s*%",)),
    _entry("vol.%", "体积百分比", "按体积计的百分含量；气体比较还需一致温压基准。", words=(r"volume percent(?:age)?",), symbols=(r"vol\.?\s*%",)),
    _entry("ppm", "百万分比", "表示 10⁻⁶ 量级的比例；质量、体积或摩尔基准必须看原文。", symbols=(r"ppm",)),
    _entry("a.u.", "任意单位", "用于相对强度等量；不能直接当作绝对浓度或产量。", words=(r"arbitrary units?",), symbols=(r"a\.\s*u\.",)),
    _entry("percentage point", "百分点", "两个百分数直接相减的差；例如 20% 到 30% 是增加 10 个百分点。", words=(r"percentage points?",)),
    _entry("fold change", "倍数变化", "比较两者之比；increase by 与 increase to 的基准不同，需核对原句。", words=(r"fold[- ]changes?", r"\d+(?:\.\d+)?[- ]fold", r"(?:two|three|four|five|ten)[- ]fold")),
    _entry("standard deviation", "标准差", "描述数据的分散程度，不等于均值估计的标准误。", words=(r"standard deviations?",), symbols=(r"SD",)),
    _entry("standard error", "标准误", "描述某个统计量估计的不确定性；与原始数据分散程度不同。", words=(r"standard errors?(?: of (?:the )?mean)?",)),
    _entry("confidence interval", "置信区间", "在指定统计方法和置信水平下得到的参数区间。", words=(r"confidence intervals?",)),
    _entry("detection limit", "检出限", "在规定方法和判据下可被检出的最低水平；低于检出限不等于零。", words=(r"(?:limit of detection|detection limits?)",), symbols=(r"LOD",)),
    _entry("T₅₀ / T₉₀", "达到指定转化率的温度", "T₅₀、T₉₀ 通常表示达到 50%、90% 转化率时的温度，须确认所指反应物。", symbols=(r"T\s*(?:50|90)",)),
    _entry("temperature window", "温度窗口", "满足某一性能标准的温度范围；标准及边界以原文为准。", words=(r"(?:operating )?temperature windows?",)),
    _entry("mass transfer", "传质", "物质在不同位置或相之间迁移的过程；可能影响测到的反应速率。", words=(r"mass[- ]transfer",)),
    _entry("Langmuir-Hinshelwood", "朗缪尔–欣谢尔伍德机理", "常指参与反应的物种先吸附到表面再反应；具体形式以作者为准。", words=(r"Langmuir[-– ]Hinshelwood",)),
    _entry("Eley-Rideal", "伊利–里迪尔机理", "常指气相等流体物种与已吸附物种直接反应；具体形式以作者为准。", words=(r"Eley[-– ]Rideal",)),
)


_MATERIAL_CONTEXT = re.compile(
    r"\bcatal\w*|\badsorp\w*|\bceria\b|\bzeolites?\b|\bmetal\b|"
    r"\boxides?\b|\breaction\b|\bconversion\b|\bselectivity\b|"
    r"\b(?:CeO2|NH3|Pd|Ru|Cu|Zn|SCR)\b|催化|吸附|载体|氧化铈", re.I
)


def _context_ok(text: str, start: int, end: int, entry: _Entry) -> bool:
    if not entry.context:
        return True
    # Context stays within a sentence so unrelated paragraphs do not turn
    # ordinary 'support' or thermodynamic/radioactive 'activity' into catalysis.
    before = re.split(r"[.!?。！？;；\n]", text[:start])[-1]
    after = re.split(r"[.!?。！？;；\n]", text[end:])[0]
    window = before[-160:] + text[start:end] + after[:160]
    if entry.context == "sem":
        return not re.search(r"standard error|mean\s*±|mean\s*\+/-", window, re.I)
    if not _MATERIAL_CONTEXT.search(window):
        return False
    if entry.context == "support":
        if re.search(r"(?:authors?|we|they|results?|data|evidence|findings?)\s*$", before, re.I):
            return False
        if re.match(r"\s+(?:the\s+)?(?:claim|conclusion|hypothesis|idea|proposal|interpretation)", after, re.I):
            return False
        if re.search(r"(?:financial|technical|moral)\s*$", before, re.I):
            return False
    if entry.context == "activity" and re.search(r"(?:thermodynamic|radioactive|biological|antibacterial)\s*$", before, re.I):
        return False
    return True


def terms_for_text(text: str) -> list[dict[str, str]]:
    """Return unique, matched glossary explanations in first-mention order.

    Longer overlapping expressions win (NH3-TPD beats NH3 and TPD at that
    location). Unicode subscripts/superscripts are normalized only in a local
    matching buffer, never in the caller's source text. ``term`` is the
    canonical display name, not a claimed verbatim source span.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    normalized = unicodedata.normalize("NFKC", text).translate(
        str.maketrans({"−": "-", "‐": "-", "‑": "-", "\u00a0": " "})
    )
    found = []
    for index, entry in enumerate(_ENTRIES):
        for match in entry.pattern.finditer(normalized):
            if _context_ok(normalized, match.start(), match.end(), entry):
                found.append((match.start(), match.end(), index))
    selected = []
    for item in sorted(found, key=lambda m: (-(m[1] - m[0]), m[0], m[2])):
        if not any(item[0] < old[1] and old[0] < item[1] for old in selected):
            selected.append(item)
    rows, seen = [], set()
    for _, _, index in sorted(selected):
        if index in seen:
            continue
        seen.add(index)
        entry = _ENTRIES[index]
        row = {"term": entry.term, "zh": entry.zh, "explanation": entry.explanation}
        if entry.source:
            row["source"] = entry.source
        rows.append(row)
    return rows


def glossary_entry_count() -> int:
    """Number of local glossary concepts (aliases are not counted again)."""
    return len(_ENTRIES)

"""Caption clues for scientific use, separate from geometric digitization.

These are suggestions with evidence, never acceptance criteria for ML training.
"""
from __future__ import annotations
import re

# geometry, example, use, current handling, required context
CATALOG = [
 ('曲线 / 散点','转化率、选择性、产率—温度；稳定性—时间','性能目标或条件响应','已有：刻度与系列清晰时自动读数','样品、温度、气体组成、压力、空速；连续采样点不是独立实验'),
 ('竖柱 / 分组柱 / 横条','不同样品的转化率、BET 面积、酸量','性能目标或材料特征，取决于指标','新增：实心、可分离、单数值轴的柱体端点自动读取','类别与图例必须核对；不从误差线顶端读取均值'),
 ('吸附 / 脱附等温线','N₂ 吸附量—相对压力；NH₃ 吸附量—压力','材料特征；吸附研究中也可作为目标','已有：按曲线 / 标记读取，分开吸附与脱附','吸附质、温度、单位、分支；BET 面积需另作适用区间分析'),
 ('TPD / TPR / 光谱 / 衍射谱','NH₃-TPD、H₂-TPR、XPS、FTIR、XRD','材料表征；通常先提取可解释特征','已有 XY 通道可尝试有完整刻度且系列可分离的谱线；成功率取决于图像','归一化、平移、基线、校准、峰归属；任意强度不能直接变成物质量'),
 ('直方图 / 粒径分布','粒径区间—频数 / 概率密度','粒径、分布宽度等描述符','暂不自动按柱图输出；需要识别每个区间边界','频数、比例、密度含义及箱宽；柱中心不能当作每颗粒子的尺寸'),
 ('带误差线的曲线 / 柱图','均值 ± SD、SE 或置信区间','中心值与可靠性信息','柱图仅尝试柱体端点；误差线数值和含义待单独提取','图注明确 SD / SE / CI、重复数；像素分辨率不是实验误差'),
 ('堆叠柱 / 百分比堆叠柱','不同价态 / 产物所占比例','组成或选择性特征','暂不自动读数；不能把某段上边界当该段含量','逐段上下界、归一化口径、单位'),
 ('双纵轴 / 断轴 / 3D 图','转化率与选择性共图；坐标中断','可能有用，需专用标定','本次未扩展支持；疑似双轴柱图会停止','每条系列对应哪条轴；断点 / 投影关系'),
 ('热图 / 等高线 / 相图','温度—组成—性能；相稳定区域','条件响应或区域分类','暂不自动生成数值','连续色标或离散类别、三个变量、插值来源；无色标不猜数值'),
 ('SEM / TEM / 元素分布图','颗粒、晶格、元素空间分布','粒径、形貌、分散度等潜在特征','保留图供查看；未实现自动分割与统计','比例尺、颗粒分割、代表性视野；像素不是纳米'),
 ('机理 / 结构 / 能级 / 反应路径','结构示意、吸附构型、势垒','定性知识；标明数值的能量可另提取','不作为普通性能曲线自动读数','实验 / 计算来源、参考能量、构型和方法'),
 ('模型评价图','预测值—实测值、残差、SHAP、特征重要性','用于评价已有模型','可查看；不作为新实验训练样本','防止把模型预测值再次当真值'),
 ('箱线 / 小提琴 / 饼图 / 雷达 / 三元图','统计摘要、占比、多指标、组成空间','部分可用，需解释统计量与坐标','未实现专用读数','分位数不等于原始样本；雷达每轴尺度可不同'),
]

RULES = [
 ('model_evaluation','模型评价','模型评价用途；不能作为新的实验真值',r'predicted.{0,30}(?:measured|experimental|actual)|parity\s+plot|SHAP|feature\s+importance|confusion\s+matrix|residual\s+plot|预测值|特征重要'),
 ('microscopy','显微 / 空间分布','可用于形貌特征；需要比例尺、分割与统计',r'\b(?:SEM|TEM|HRTEM|STEM)\b|micrograph|microscopy|ptychograph|elemental\s+mapping|显微|元素分布'),
 ('distribution','统计分布','可用于分布特征；需要区间边界与统计口径',r'histogram|particle\s+size\s+distribution|box\s*plot|violin\s+plot|直方图|粒径分布|箱线'),
 ('spectrum','表征谱图','材料特征候选；先核对归一化、平移与物理意义',r'\b(?:TPD|TPR|XPS|XRD|FT.?IR|DRIFTS|NMR)\b|spectr(?:um|a|oscopy)|diffraction|M.ss(?:bauer)?|谱图|衍射'),
 ('sorption','吸附 / 脱附','吸附或孔结构特征候选；需核对吸附质、温度、分支',r'isotherm|sorption|adsorption\s+(?:capacity|uptake|amount)|孔径|等温线|吸附量'),
 ('performance','催化性能','预测目标候选；必须补齐样品与反应条件',r'conversion|selectivity|yield|reaction\s+rate|catalytic\s+(?:activity|performance)|stability|转化率|选择性|产率|稳定性'),
 ('material_property','材料性质','输入特征候选；需同一测试口径',r'BET|surface\s+area|pore\s+volume|acid\s+(?:amount|density)|dispersion|比表面积|孔容|酸量|分散度'),
 ('spatial_map','热图 / 相图','需要色标或类别定义；不能直接套用二维曲线读取',r'heat\s*map|contour|phase\s+diagram|热图|等高线|相图'),
 ('schematic','示意 / 机理','用于结构与机理解释；不能当作普通性能曲线',r'schematic|mechanism|reaction\s+pathway|framework\s+model|示意|机理'),
]

def classify_chart(caption='', axis_text=''):
    text=' '.join((str(caption),str(axis_text)))
    hits=[{'code':code,'label':label,'advice':advice,'evidence':m.group(0)}
          for code,label,advice,pattern in RULES if (m:=re.search(pattern,text,re.I))]
    # Multi-panel captions can legitimately match several roles.
    return {'roles':hits,'basis':'caption_and_visible_axis_keywords','requires_review':True,
            'summary':'；'.join(v['label'] for v in hits) or '用途待核对',
            'advice':'\n'.join(v['advice'] for v in hits) or '先确认图中物理量、样品与测试条件，再决定是否用于预测。'}

def describe_chart(caption='', axis_text=''):
    profile=classify_chart(caption,axis_text)
    return '数据用途提示（文字线索，待核对）：'+profile['summary']+'\n'+profile['advice']

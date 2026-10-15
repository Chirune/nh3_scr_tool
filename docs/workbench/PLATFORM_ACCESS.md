# 统一检索与官方出版社通道：本地更新

交付版：2026-10-15。源码版打开 `OPEN_THIS.cmd`，Windows 便携版打开 `CatalystWorkbench.exe`。新增 API / 浏览器双入口；本地包由用户上传 GitHub。

## 现在怎么用

1. 重开板块1。检索来源现有 Crossref、OpenAlex、Europe PMC、CORE、Semantic Scholar。
2. 输入检索词，一行一条。结果按DOI去重，并记录来源及摘要出处。Semantic Scholar的相关性检索不支持带连字符的词，本地会把NH3-SCR转换为NH3 SCR，并保留实际发送的检索词。
3. 点击“检索平台 Key”，分别填写OpenAlex、CORE和Semantic Scholar自己的Key。留空可尝试匿名模式；凭据只在本次内存使用。
4. 核对摘要并保留论文。程序结合链接、出版方信息及DOI前缀识别平台；前缀推断会明确标记，已知出版方信息优先。
5. 点击“出版社 API 设置 / 验证”，填写对应出版社凭据，然后单篇验证或点击“自动获取正文 → 进入第二板块”。
6. Elsevier API 暂时不可用时，可以用“学校全文 / Zotero 接收”：浏览器取得权限、Connector 保存正文、本工具按 DOI 接收已有 PDF。[逐步操作](ZOTERO_QUICKSTART.html)。新增“导出保留论文清单（RIS）”供 Zotero 批量导入；清单不包含 PDF。

## 检索平台

| 平台 | 本地接入 | 官方入口与待办 |
| --- | --- | --- |
| OpenAlex | 关键词、DOI和摘要补全；个人Key已验证 | [Key说明](https://help.openalex.org/api/authentication/) |
| Crossref | 已有DOI、题录和可用摘要；保留平台报告的全文候选 | [官方REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) |
| Europe PMC | 已有题录、摘要及其开放全文入口 | [官方接口](https://europepmc.org/RestfulWebService) |
| CORE | 新增关键词检索、摘要、知识库来源和报告的全文链接 | [申请Key](https://core.ac.uk/services/api)；首次匿名调用200，之后联调429，待个人Key稳定验收 |
| Semantic Scholar | 新增关键词检索、摘要和报告的开放PDF链接 | [申请Key与官方说明](https://www.semanticscholar.org/product/api)；首次匿名调用200，之后联调429，待个人Key稳定验收 |

CORE全文字段可能是“Not available for public API users.”，不会当作摘要或正文。CORE可能包含预印本、学位论文及机构存档，筛选时必须核对文献类型。全文候选链接不等于已下载，也不直接成为科研样本。

CORE与Semantic Scholar发生401/403/429时，本批剩余同来源查询暂停；其他来源继续，已有结果保留。它们的Key分别通过官方域名的请求头发送，不放进网址、缓存或结果。

## 出版社及官方客户端

| 出版社 | 本地实现 | 本次真实测试与限制 |
| --- | --- | --- |
| Wiley | 默认用官方 `wiley-tdm 1.2.0` 下载；工作台再检查PDF结构、DOI及题名 | 开放综述下载成功（20页）；另一实验论文403，机构全文权限未确认 |
| Springer Nature | 默认用官方 `springernature-api-client 0.0.10` 查询Open Access；可另填Meta Key补查题录与开放状态，再沿已接入下载路径取得公开PDF | Open Access查询成功；独立Meta Key已返回Cu-CHA测试论文题录与摘要（1422字符）；官方TDM `xmldata/jats` 使用OA Key请求401，Full Text/TDM需另外申请 |
| Elsevier | 使用官方Article Retrieval API，支持XML/FULL和PDF/FULL | XML实测403；校园网络与机构权益仍需对照 |
| MDPI | 现有公开PDF路线，仍需文件身份检查 | 不是已接入的新TDM Key服务 |

- [Wiley官方客户端](https://github.com/WileyLabs/tdm-client)
- [Springer官方Python封装](https://dev.springernature.com/docs/python-api-wrapper/)
- [Elsevier官方TDM教程](https://dev.elsevier.com/tecdoc_text_mining.html)

官方客户端没有修改或复制进仓库。安装清单已加入依赖。Wiley下载保留工作台的限流、取消、大小限制、临时文件和凭据跳转控制。Springer 0.0.10的默认日志会输出含Key的请求参数，适配层提供安静日志配置，官方请求方法不变，关闭自动多次重试。

Springer设置中，第一栏填写Open Access API Key，第二栏填写Meta API Key（可选）。两种Key不混用，Meta认证失败不会停用正常的OA凭据。Meta通道用于OA查询404后的核对，尚未成为统一关键词检索的第六来源，也不表示已获得TDM权限。

## 其他检索平台的评估

- [Lens](https://docs.api.lens.org/getting-started.html)：适合论文与专利关联，需申请Scholarly API及Token；尚未接入。
- [Dimensions](https://docs.dimensions.ai/dsl/api.html)：API通常需机构订阅，平台产品用途也有适用范围；确认授权后再接入。
- Scopus和Web of Science：通过图书馆确认API方案。Scopus检索先前成功，不等于Elsevier全文权限满足。
- [DataCite](https://support.datacite.org/docs/rest-api)：公共检索无需Key，适合后续查数据集和论文关联附件；暂不把数据集自动混入论文结果和训练样本。

## 验证

新增检查覆盖两平台元数据、摘要来源、候选链接、跨来源去重、凭据隔离、限流停止及官方客户端下载后的身份复验。相关回归、最小窗口检查及板块1启动通过，依赖检查通过。

真实调用结果位于忽略目录 `runtime_data/platform_expansion_20261013/`；本次没有新增正式机器学习实验样本。

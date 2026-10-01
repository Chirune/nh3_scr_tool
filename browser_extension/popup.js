"use strict";

// Self-contained: Chrome executes this function in the user-clicked tab.
function collectArticleSnapshot() {
  const clean = v => String(v || "").replace(/\s+/g, " ").trim();
  const meta = {};
  for (const e of document.querySelectorAll("meta")) {
    const k = (e.getAttribute("name") || e.getAttribute("property") || "").toLowerCase();
    if (k && e.content) (meta[k] ||= []).push(e.content);
  }
  const first = (...keys) => keys.map(k => meta[k]?.[0]).find(Boolean) || "";
  let doi = clean(first("citation_doi", "dc.identifier.doi", "dc.identifier", "prism.doi")).replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "").toLowerCase().replace(/[.,;]+$/, "");
  if (!/^10\.\d{4,9}\/\S+$/i.test(doi)) doi = "";
  const title = clean(first("citation_title", "dc.title", "og:title") || document.querySelector("h1")?.textContent);
  let abstract = "", locator = "";
  for (const heading of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
    if (!["abstract", "摘要"].includes(clean(heading.textContent).toLowerCase())) continue;
    let parent = heading.parentElement;
    for (let depth = 0; depth < 3 && parent && !["BODY", "HTML"].includes(parent.tagName); depth++, parent = parent.parentElement) {
      const copy = parent.cloneNode(true);
      const headings = [...copy.querySelectorAll("h1,h2,h3,h4,h5,h6")];
      if (headings.some(h => ["highlights", "graphical abstract", "keywords", "introduction"].includes(clean(h.textContent).toLowerCase()))) break;
      for (const h of headings) if (["abstract", "摘要"].includes(clean(h.textContent).toLowerCase())) h.remove();
      for (const node of copy.querySelectorAll("script,style,nav,button")) node.remove();
      for (const p of copy.querySelectorAll("p")) p.appendChild(document.createTextNode(" "));
      const candidate = clean(copy.textContent);
      if (candidate.length >= 80) { abstract = candidate; locator = (parent.id ? "#" + parent.id : parent.tagName.toLowerCase()) + " > heading:Abstract"; break; }
    }
    if (abstract) break;
  }
  if (!abstract) for (const key of ["citation_abstract", "dcterms.abstract", "dc.description"]) {
    const value = clean(new DOMParser().parseFromString(first(key), "text/html").body.textContent);
    if (value.length >= 80) { abstract = value; locator = "meta:" + key; break; }
  }
  const issues = [];
  if (!doi) issues.push("missing_doi");
  if (!abstract) issues.push("missing_abstract");
  const full = abstract.length >= 80 && !/(\.\.\.|…)$/.test(abstract);
  if (abstract && !full) issues.push("short_or_truncated_abstract");
  const absolute = value => {
    try { const url = new URL(value, location.href); return /^https?:$/.test(url.protocol) ? url.href : ""; }
    catch { return ""; }
  };
  const linkRoot = document.querySelector("main,[itemprop='articleBody'],article") || document;
  const pdf_urls = [...new Set([
    ...(meta.citation_pdf_url || []),
    ...[...linkRoot.querySelectorAll("a[href]")]
      .filter(a => /(?:^|\s)(?:download\s+)?pdf(?:\s|$)|full\s*text\s*pdf/i.test(clean(a.textContent)) || /(?:\/pdf(?:\/|\?|$)|\.pdf(?:\?|$))/i.test(a.getAttribute("href") || ""))
      .map(a => a.href)
  ].map(absolute).filter(Boolean))].slice(0, 12);
  const supplement_urls = [...new Set([...linkRoot.querySelectorAll("a[href]")]
    .filter(a => /supplementary|supporting information|supporting data|补充材料/i.test(clean(a.textContent)))
    .map(a => absolute(a.href)).filter(Boolean))].slice(0, 30);
  const selectors = ["article", "[itemprop='articleBody']", ".article-body", ".article__body", "#main-content", "main"];
  let article = null, sections = [];
  for (const selector of selectors) {
    for (const candidate of document.querySelectorAll(selector)) {
      const text = clean(candidate.textContent);
      const names = [...candidate.querySelectorAll("h1,h2,h3,h4,h5,h6")].map(h => clean(h.textContent));
      const useful = names.filter(h => /introduction|methods?|experimental|results?|discussion|conclusions?/i.test(h));
      if (text.length >= 3000 && useful.length >= 2) { article = candidate; sections = useful; break; }
    }
    if (article) break;
  }
  let fulltext_html = "", figure_urls = [], table_count = 0;
  if (article) {
    const copy = article.cloneNode(true);
    for (const node of copy.querySelectorAll("script,style,nav,header,footer,aside,form,button,iframe,object,embed")) node.remove();
    for (const node of copy.querySelectorAll("*")) {
      for (const attr of [...node.attributes]) if (/^on/i.test(attr.name)) node.removeAttribute(attr.name);
      if (node.tagName === "IMG") {
        const source = node.getAttribute("src") || node.getAttribute("data-src") || node.getAttribute("data-original");
        if (source) node.setAttribute("src", absolute(source));
        node.removeAttribute("srcset");
      }
    }
    figure_urls = [...new Set([...copy.querySelectorAll("figure img[src]")]
      .map(img => absolute(img.getAttribute("src"))).filter(Boolean))].slice(0, 100);
    table_count = copy.querySelectorAll("table").length;
    const saved = document.implementation.createHTMLDocument(title);
    const charset = saved.createElement("meta"); charset.setAttribute("charset", "utf-8"); saved.head.appendChild(charset);
    const base = saved.createElement("base"); base.href = location.href; saved.head.appendChild(base);
    for (const [name, content] of [["citation_doi", doi], ["citation_title", title],
                                   ["citation_journal_title", first("citation_journal_title", "prism.publicationname")]]) {
      const tag = saved.createElement("meta"); tag.name = name; tag.content = content; saved.head.appendChild(tag);
    }
    saved.body.appendChild(copy);
    const candidate = "<!doctype html>\n" + saved.documentElement.outerHTML;
    if (candidate.length <= 8_000_000) fulltext_html = candidate;
    else issues.push("fulltext_too_large");
  }
  return {doi, title, abstract, abstract_is_full: full,
    abstract_source: "browser_extension", abstract_url: location.href, landing_page_url: location.href,
    abstract_locator: locator, authors: meta.citation_author || [],
    journal: clean(first("citation_journal_title", "prism.publicationname")),
    publisher: clean(first("citation_publisher", "dc.publisher")),
    date: first("citation_publication_date", "dc.date"), keywords: first("citation_keywords", "keywords"),
    document_type: first("citation_article_type", "dc.type"), captured_at: new Date().toISOString(),
    fulltext_available: !!fulltext_html, fulltext_html, fulltext_sections: sections,
    figure_urls, table_count, pdf_urls, supplement_urls, import_issues: issues};
}

const read = document.getElementById("read"), save = document.getElementById("save"),
      all = document.getElementById("all"),
      pdf = document.getElementById("pdf"), supp = document.getElementById("supp"),
      preview = document.getElementById("preview");
let item;
read.addEventListener("click", async () => {
  read.disabled = true; save.disabled = true; all.disabled = true; pdf.disabled = true; supp.disabled = true;
  item = undefined; preview.textContent = "正在读取……";
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab || !/^https?:\/\//.test(tab.url || "")) throw new Error("请在普通论文网页上使用采集按钮。");
    const result = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: collectArticleSnapshot});
    item = result[0]?.result;
    if (!item?.title && !item?.doi) throw new Error("未取得题录，请确认打开的是论文页。");
    preview.textContent = `${item.title}\nDOI：${item.doi || "缺失，需核对"}\n摘要字符：${item.abstract.length}\n可见正文：${item.fulltext_available ? "已采集" : "此页未识别到完整正文"}\n图像链接 ${item.figure_urls.length} 个，表格 ${item.table_count} 个，PDF 链接 ${item.pdf_urls.length} 个，补充材料链接 ${item.supplement_urls.length} 个。\n${item.import_issues.length ? "需核对：" + item.import_issues.join("、") + "\n" : ""}\n${item.abstract || "未取得摘要，请展开 Abstract 或改用 HTML / Zotero 导出。"}`;
    save.disabled = false; all.disabled = false;
    pdf.disabled = !item.pdf_urls.length;
    supp.disabled = !item.supplement_urls.length;
  } catch (error) { preview.textContent = error.message || "读取失败，请检查网页。"; }
  finally { read.disabled = false; }
});
function saveCapture(capture) {
  const data = {schema: "nh3scr-browser-capture-v2", items: [capture]};
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:"application/json;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url;
  link.download = "NH3SCR_" + (capture.doi || capture.title || "paper").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 100) + ".json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
save.addEventListener("click", () => { if (item) saveCapture(item); });

async function downloadAndWait(url) {
  try {
    const id = await chrome.downloads.download({url, conflictAction:"uniquify"});
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      const [download] = await chrome.downloads.search({id});
      if (download?.state === "complete" && download.filename) return download.filename;
      if (download?.state === "interrupted") return "";
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  } catch { /* Keep the link in the JSON for manual retrieval. */ }
  return "";
}
all.addEventListener("click", async () => {
  if (!item) return;
  all.disabled = true; save.disabled = true;
  const supplementLinks = item.supplement_urls.slice(0, 10);
  const figureLinks = item.figure_urls.slice(0, 20);
  preview.textContent += `\n正在等 Chrome 下载：PDF ${item.pdf_urls.length ? 1 : 0} 个，补充材料 ${supplementLinks.length} 个，论文图片 ${figureLinks.length} 个。请保持此窗口打开。`;
  const pdfPath = item.pdf_urls.length ? await downloadAndWait(item.pdf_urls[0]) : "";
  const [supplements, figures] = await Promise.all([
    Promise.all(supplementLinks.map(downloadAndWait)),
    Promise.all(figureLinks.map(downloadAndWait))
  ]);
  saveCapture({...item, downloaded_pdf_path:pdfPath,
               downloaded_supplement_paths:supplements.filter(Boolean),
               downloaded_figure_paths:figures.filter(Boolean)});
  preview.textContent += `\n采集包已保存；完成 PDF ${pdfPath ? 1 : 0} 个、补充材料 ${supplements.filter(Boolean).length} 个、图片 ${figures.filter(Boolean).length} 个。回工作台导入这个 JSON。`;
  all.disabled = false; save.disabled = false;
});
pdf.addEventListener("click", async () => {
  if (!item?.pdf_urls?.length) return;
  try {
    await chrome.downloads.download({url: item.pdf_urls[0], saveAs: true});
    preview.textContent += "\n已交给 Chrome 下载。请确认得到的是正确论文 PDF，再在工作台添加。";
  } catch (error) { preview.textContent += "\nPDF 下载未成功：" + (error.message || "请从论文网页手动下载。"); }
});
supp.addEventListener("click", async () => {
  if (!item?.supplement_urls?.length) return;
  const urls = item.supplement_urls.slice(0, 10);
  let started = 0;
  for (const url of urls) {
    try { await chrome.downloads.download({url, conflictAction:"uniquify"}); started++; }
    catch { /* Report the batch count below; unavailable files remain as links in JSON. */ }
  }
  preview.textContent += `\n已向 Chrome 提交 ${started}/${urls.length} 个补充材料链接。请在下载列表核对实际文件，并在工作台手动添加。`;
});

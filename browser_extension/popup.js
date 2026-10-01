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
  return {doi, title, abstract, abstract_is_full: full,
    abstract_source: "browser_extension", abstract_url: location.href, landing_page_url: location.href,
    abstract_locator: locator, authors: meta.citation_author || [],
    journal: clean(first("citation_journal_title", "prism.publicationname")),
    date: first("citation_publication_date", "dc.date"), keywords: first("citation_keywords", "keywords"),
    document_type: first("citation_article_type", "dc.type"), captured_at: new Date().toISOString(), import_issues: issues};
}

const read = document.getElementById("read"), save = document.getElementById("save"), preview = document.getElementById("preview");
let item;
read.addEventListener("click", async () => {
  read.disabled = true; save.disabled = true; item = undefined; preview.textContent = "正在读取……";
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab || !/^https?:\/\//.test(tab.url || "")) throw new Error("请在普通论文网页上使用采集按钮。");
    const result = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: collectArticleSnapshot});
    item = result[0]?.result;
    if (!item?.title && !item?.doi) throw new Error("未取得题录，请确认打开的是论文页。");
    preview.textContent = `${item.title}\nDOI：${item.doi || "缺失，需核对"}\n摘要字符：${item.abstract.length}\n${item.import_issues.length ? "需核对：" + item.import_issues.join("、") + "\n" : ""}\n${item.abstract || "未取得摘要，请展开 Abstract 或改用 HTML / Zotero 导出。"}`;
    save.disabled = false;
  } catch (error) { preview.textContent = error.message || "读取失败，请检查网页。"; }
  finally { read.disabled = false; }
});
save.addEventListener("click", () => {
  if (!item) return;
  const data = {schema: "nh3scr-browser-abstract-v1", items: [item]};
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:"application/json;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url;
  link.download = "NH3SCR_" + (item.doi || item.title || "abstract").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 100) + ".json";
  link.click();
});

# 252 篇无 DOI 文献补全记录

## 背景

`batch_screen.py` 筛选出 8748 篇相关文献，其中 252 篇在 `abstracts.json` 中无 DOI，无法通过 Sci-Hub/Unpaywall 下载 PDF。

## 补全流程

### Step 1: CrossRef 反查 DOI（23 个）

用文献标题在 [CrossRef API](https://api.crossref.org/) 搜索，反查对应 DOI。

```
GET https://api.crossref.org/works?query.bibliographic={title}&rows=1
```

结果：252 篇中找到 23 个 DOI，写入 `abstracts.json`。

### Step 2: PMC ID 查询（0 篇直接下载）

用 PMID 在 [PMC ID Converter](https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/) 批量查询 PMC ID，再通过 PMC PDF 链接直接下载。

```
GET https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid1,pmid2,...}&format=json
```

结果：229 篇中大部分无 PMC ID，极少数有 PMC ID 但 PDF 下载失败。

### Step 3: Sci-Hub PMID 直搜（0 篇）

尝试直接用 PMID 在 Sci-Hub 搜索。

结果：Sci-Hub 只认 DOI，不认 PMID，返回搜索首页，无法下载。

### Step 4: Unpaywall 标题搜索（0 篇）

用文献标题在 [Unpaywall API](https://api.unpaywall.org/) 搜索 OA 版本。

```
GET https://api.unpaywall.org/v2/search?query={title}
```

结果：20 篇测试中均未找到 OA PDF。

### Step 5: OpenAlex 反查（83 个 DOI + 8 篇直接下载）

用文献标题在 [OpenAlex API](https://docs.openalex.org/) 搜索，获取 DOI 和 OA PDF 链接。

```
GET https://api.openalex.org/works?filter=title.search:{title}&per_page=1
```

结果：
- 找到 83 个 DOI（写入 `abstracts.json`）
- 66 个有 PDF/OA 链接
- 直接下载 8 篇 OA PDF

## 最终结果

| 方法 | 补全 DOI | 直接下载 |
|------|---------|---------|
| CrossRef | 23 | - |
| PMC | - | 0 |
| Sci-Hub PMID 直搜 | - | 0 |
| Unpaywall | - | 0 |
| OpenAlex | 83 | 8 |
| **合计** | **106** | **8** |

## 剩余

- 229 - 8（直接下载）= **221 篇仍缺失**
- 缺失列表保存在 `missing_pmids.txt`
- 这些大概率是老文献、会议摘要、或无电子版的文献
- 补全的 106 个 DOI 已写入 `abstracts.json`，重新运行 `download_pdfs.py` 可继续通过 Sci-Hub 下载

## 相关文件

- `supplement_dois.py` — 补全脚本（CrossRef + PMC + Sci-Hub PMID）
- `download_pdfs.py` — 主下载脚本（支持断点续传）
- `abstracts.json` — DOI 已更新
- `missing_pmids.txt` — 仍缺失的 PMID 列表

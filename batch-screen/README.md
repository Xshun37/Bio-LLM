# batch-screen

PubMed TF-Target 批量搜索 + LLM 筛选。

## 流程

```
TF列表 → PubMed esearch (别名扩展) → efetch 摘要 → LLM 筛选 → 下载 PDF
```

1. 加载 TF 列表 + HGNC 别名 (`data/TF.txt`, `data/TF_aliases.json`)
2. PubMed esearch: 每个 TF 用别名 + 关键词构建查询，拉取 PMID
3. efetch: 批量获取摘要 + DOI
4. LLM 筛选: 并行调用 Qwen 判断是否涉及 TF-Target 调控
5. (可选) Sci-Hub + Unpaywall 下载相关论文 PDF

## 用法

```bash
# 完整流程（新建运行）
python batch_screen.py

# 继续最近一次运行（断点续传）
python batch_screen.py --resume

# 只搜索不筛选
python batch_screen.py --skip-llm

# 限制 TF 数量 (调试)
python batch_screen.py --limit-tfs 5

# 调整并发
python batch_screen.py --workers 8

# 关键词提取 (辅助)
python extract_keywords.py
```

## 输出

每次运行创建独立的时间戳目录，互不干扰：

```
outputs/batch_screen/
├── run_20260530_140000/         # 第一次运行
│   ├── search_results.json
│   ├── abstracts.json
│   ├── screen_results.json
│   ├── relevant_pmids.txt
│   └── papers/
├── run_20260531_100000/         # 第二次运行
│   ├── search_results.json
│   ├── abstracts.json
│   ├── screen_results.json
│   ├── relevant_pmids.txt
│   └── papers/
└── ...
```

`download_pdfs.py` 自动使用最近的 `run_*` 目录。

## 配置

复制示例配置：

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 是本地文件，不提交。API key 优先从环境变量读取：

```bash
export DASHSCOPE_API_KEY="..."
```

配置字段：

```yaml
email: "xxx@xxx.edu.cn"         # PubMed API email
api_key: ""                     # 可留空，优先使用 DASHSCOPE_API_KEY
model: "qwen3.7-max"
temperature: 0
seed: 42
workers: 4
```

`prompts/screen_prompt.yaml`: LLM 筛选的 system/user prompt 模板。

## 辅助脚本

```bash
# 关键词提取（从 PMID 列表提取搜索关键词）
python extract_keywords.py
# → 读取 outputs/PMID_20.tsv，输出到 outputs/keyword_extraction/
```

## 依赖

- `biopython`: PubMed API
- `openai`: LLM 调用
- `pyyaml`, `requests`, `beautifulsoup4`
- conda env: `bio_llm`

## PDF 下载

`download_pdfs.py` 从最近一次运行的 `relevant_pmids.txt` 读取 PMID，自动提取 DOI 后下载 PDF:

1. PubMed API 提取 DOI
2. Sci-Hub (多镜像 fallback)
3. Unpaywall (OA fallback)

```bash
python download_pdfs.py
# → 输出到 outputs/batch_screen/run_XXXXXXXX_XXXXXX/papers/
```

机构订阅的 Elsevier/SAGE 等付费刊需手动下载或走文献传递。

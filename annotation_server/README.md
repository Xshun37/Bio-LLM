# Annotation Server

Simple Flask server using native sqlite3 for manual PubMed annotation tasks.

Features:
- Form with fields: `pubmed_id`, `TF`, `gene`, `cellline`, `assay`, `complex`.
- TF search with UniProt lookup and disambiguation options.
- Gene search returning ENSG entries via MyGene.info.
- Assay field presented as selectable options.
- Save and list annotations (SQLite).

Run:

1. Create a virtualenv and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Set optional env vars (e.g. `OPENAI_API_KEY`) and run:

```bash
set FLASK_APP=app.py
flask run
```

AI 审核：打开 `/ai` 页面可运行 AI 审核任务。服务器默认使用 dashscope 地址、示例 Key 与模型（可通过环境变量覆盖）：

- `AI_API_URL`（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`）
- `AI_API_KEY`（默认示例 Key，可在环境中设置为你的 Key）
- `AI_MODEL`（默认 `qwen3.6-plus`）

若配置了 `AI_API_KEY`，审计时将向该接口发送请求并尝试解析 JSON 输出；否则仅使用启发式规则标记可疑项。审核结果会写回数据库字段 `ai_flags` 与 `ai_notes`，供人工复核。

导出 CSV：新增接口 `/api/export_csv`，可导出包含 AI 标注的 CSV 文件（列包括 `ai_flags`、`ai_notes`、`ai_reviewed`）。

## 迁移 gs_review 数据

`tools/standardize_gs_review.py` 用于把旧 `gs_review/final.tsv` 转换为本服务的标准 CSV 输出列，并可选导入 SQLite。转换过程只使用本地数据：

- `data/curated/gene_ensg_map.json`：ENSG 反查 gene symbol
- `data/raw/hgnc_complete_set.txt`：TF symbol 补齐 UniProt ID

生成标准 CSV：

```bash
python annotation_server/tools/standardize_gs_review.py \
  --output annotation_server/gs50_standardized.csv
```

导入 annotation_server SQLite：

```bash
python annotation_server/tools/standardize_gs_review.py \
  --output annotation_server/gs50_standardized.csv \
  --db annotation_server/annotations.db
```

如需清空目标库后重导：

```bash
python annotation_server/tools/standardize_gs_review.py \
  --output annotation_server/gs50_standardized.csv \
  --db annotation_server/annotations.db \
  --replace
```

运行测试：

```bash
python -m unittest discover -s annotation_server/tests -p 'test_*.py'
```

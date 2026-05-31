# review-server

Flask Web 应用，用于人工审核金标准数据集 (GS_50)。

## 功能

- **GS Review** (`/gs_review`): 50 篇 PMID 金标准审核
  - 左侧边栏列出所有 PMID，按状态颜色标记
  - 每页一篇 PMID，展示 TF/Target 调控关系表
  - TF 搜索 (UniProt API)、Gene 搜索 (MyGene.info API)
  - Assay 多选 chips (35 选项, 8 分类)
  - Complex / Notes / Reviewed 字段
  - 键盘快捷键 (j/k/n/p/Enter/g)
  - 双重持久化: localStorage + SQLite

- **AI Audit** (`/ai`): 启发式 + AI 辅助审核

- **导出**: CSV (`/api/export_csv`) / TSV (`/api/gs_review/export/tsv`)

## 运行

```bash
cd review-server
pip install -r requirements.txt
python app.py
```

打开 `http://127.0.0.1:5000/gs_review`

`data/` 保留在工程内，包含金标准输入、SQLite 审核数据库、导出结果和本地基因映射文件。可用 `ANNOTATION_DB` 指定其他 SQLite 路径。

## 结构

```
review-server/
├── app.py              # Flask 应用
├── templates/          # HTML 模板
├── static/             # JS/CSS
├── tools/              # 数据处理脚本
│   ├── clean_export.py         # 清洗导出 (去重 Notes, 标准化基因名, ENSG 映射)
│   ├── standardize_gs_review.py # 迁移: final.tsv → DB/CSV
│   ├── build_ensg_map.py       # 构建 gene→ENSG 映射
│   └── normalize_export.py     # 标准化导出
└── data/
    ├── annotations.db          # SQLite 数据库 (自动创建)
    ├── GS_50.tsv               # 金标准输入
    ├── trrust_rawdata.human.tsv # TRRUST 参考数据
    ├── gene_alias_index.json   # 基因别名索引
    ├── gene_ensg_map.json      # gene→ENSG 映射
    └── gs50_abstracts.json     # 50 篇摘要缓存
```

## 数据清洗

导出 TSV 后，用 `clean_export.py` 标准化:

```bash
python tools/clean_export.py data/gs_review_export_new.tsv > data/gs_review_clean.tsv
```

- 去重 Notes (同一 PMID 只保留第一行)
- 标准化 TF/Target 基因名 (HGNC 别名)
- Target → ENSG 映射
- 删除 Direction 列

## API

| Route | Method | 用途 |
|-------|--------|------|
| `/gs_review` | GET | GS Review 页面 |
| `/ai` | GET | AI audit 页面 |
| `/api/gs_review/save` | POST | 保存 PMID 审核状态 |
| `/api/gs_review/load` | GET | 加载所有审核状态 |
| `/api/gs_review/progress` | GET | 进度 (done/total) |
| `/api/gs_review/export/tsv` | GET | 导出 TSV |
| `/api/search_protein` | GET | UniProt 搜索 |
| `/api/search_gene` | GET | MyGene 搜索 |

## 依赖

- Flask, python-dotenv, requests
- conda env: `bio_llm`

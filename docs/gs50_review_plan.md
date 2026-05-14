# 金标准数据集人工审核工具

## 背景

用户有50篇来自 TRRUST 的 PubMed 文献（`data/raw/GS_50(1).tsv`，56条 TF-target 对），需要人工阅读全文后构建金标准数据集。输出格式：`pubmed_id | TF | gene(ENSG) | cellline | assay`。禁止使用 AI 分析。

## 方案

两个数据准备脚本 + 一个自包含的交互式 HTML 数据录入页面。

## 文件1：`scripts/build_ensg_map.py`（~50行）

一次性构建 gene symbol → ENSG ID 映射。

**数据源**：`data/raw/hgnc_complete_set.txt`（已存在，TSV格式）
- 第2列 `symbol`：HGNC 官方 gene symbol（如 `CDH1`）
- 第20列 `ensembl_gene_id`：Ensembl gene ID（如 `ENSG00000039068`）

**逻辑**：
```python
import csv, json

ensg_map = {}
with open("data/raw/hgnc_complete_set.txt") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        symbol = row["symbol"].strip().upper()
        ensg = row["ensembl_gene_id"].strip()
        if symbol and ensg:
            ensg_map[symbol] = ensg   # "CDH1" → "ENSG00000039068"

with open("data/curated/gene_ensg_map.json", "w") as f:
    json.dump(ensg_map, f, indent=2)
```

**输出**：`data/curated/gene_ensg_map.json`，`{"CDH1": "ENSG00000039068", "GLS": "ENSG00000115419", ...}`

## 文件2：`scripts/build_gs50_review.py`（~150行）

**步骤1** — 读 GS 数据并做 ENSG 转换：
```python
# 读取 GS_50(1).tsv（3列：PMID, TF, Target）
# 加载 gene_ensg_map.json
# 对每个 target gene symbol，查表得到 ENSG ID
# 查不到的用 normalize_gene_name() 标准化后重试
```

**步骤2** — 可选抓取摘要

**步骤3** — 生成 `outputs/gs50_review.html`，内嵌所有数据

## 文件3：`outputs/gs50_review.html`（自动生成，~400行 JS+CSS）

自包含数据录入页面。核心功能：
- 50张卡片，每张：PMID + PubMed链接 + TRRUST参考对(TF→Target[ENSG]) + cellline输入 + assay输入 + 备注 + 完成勾选
- localStorage 自动保存
- 导出 TSV：`PMID\tTF\tENSG\tcellline\tassay`（56行）
- 筛选、进度统计、键盘导航

## ENSG 转换流程（关键）

```
GS_50(1).tsv 中的 target gene symbol
        │
        ▼
gene_ensg_map.json 直接查表（symbol → ENSG）
        │
        ├── 命中 → 使用 ENSG ID
        │
        └── 未命中 → normalize_gene_name() 标准化后重试
                         │
                         ├── 命中 → 使用 ENSG ID  
                         └── 仍未命中 → 标记 "NOT_FOUND"，人工填写
```

## 复用现有代码

- `data/raw/hgnc_complete_set.txt` — 第2列symbol，第20列ensembl_gene_id
- `bio_llm.gene_aliases.normalize_gene_name()` — 基因名标准化兜底
- `bio_llm.abstracts.fetch_abstracts()` — 可选抓摘要

## 验证

1. `python scripts/build_ensg_map.py` → 确认所有52个target gene都能映射到ENSG ID
2. `python scripts/build_gs50_review.py` → HTML生成无误
3. 浏览器打开 → 卡片正常、链接跳转正常
4. 填入测试数据 → 刷新后localStorage恢复
5. 导出TSV → 56行，ENSG列格式如 `ENSG00000039068`

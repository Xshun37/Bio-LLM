# Bioextract

TF-Target 调控关系提取流水线。支持两种模式：
- **金标准模式**：48 篇人工标注数据，评估 LLM 提取准确率（Precision/Recall/F1）
- **生产模式**：批量处理 PDF 论文（支持 7000+ 篇），提取 TF-target 调控关系

使用 LLM (`qwen3.7-max`) 进行两轮 CoT 提取，输出结构化 JSON + HTML 报告。

## 项目结构

```text
Bioextract/
├── config/
│   ├── config.example.yaml     # 配置模版
│   └── config.yaml             # 运行参数 (gitignore)
├── data/
│   ├── raw/
│   │   ├── finalresult.tsv     # 人工金标准 (48 PMID, 8 列)
│   │   ├── paper_for_produce/  # 生产模式 PDF 论文
│   │   ├── paper_for_produce_txt/  # PDF→文本缓存 (自动生成)
│   │   ├── hgnc_complete_set.txt  # HGNC 完整基因集 (gitignore)
│   │   └── papers_txt/         # 金标准 PDF→文本
│   │       ├── fitz/           # PyMuPDF (fitz) 输出
│   │       ├── Nougat/         # Nougat OCR 输出
│   │       └── hybrid/         # 混合输出
│   ├── curated/
│   │   ├── gene_alias_index.json         # HGNC 别名索引
│   │   ├── gene_alias_overrides.json     # 人工标注覆盖规则
│   │   └── gene_ensg_map.json            # Gene → ENSG 映射表
├── outputs/                     # 输出 (gitignore)
│   ├── YYYYMMDD_HHMMSS/        # 金标准模式输出
│   └── production_YYYYMMDD_HHMMSS/  # 生产模式输出
├── src/bio_llm/
│   ├── __init__.py              # 包入口 + 基因名标准化
│   ├── gene_aliases.py          # 基因别名解析 (role-aware)
│   ├── analysis.py              # 核心: 两轮 LLM 抽取 + PDF 解析 + 断点续传
│   ├── evaluation.py            # 评估: 金标准加载 + 四维匹配
│   └── reporting.py             # HTML 报告 (金标准对比 / 生产展示)
├── scripts/
│   ├── build_alias_map.py       # 从 HGNC 构建别名索引
│   ├── merge_debug.py           # 合并多次运行的 debug JSON
│   ├── debug_to_excel.py        # debug JSON → Excel
│   ├── rerun_pmids.sh           # 重跑指定 PMID 子集
│   └── review_debug.sh          # 一键生成含 debug 面板的报告
├── run.sh                       # 统一入口 (金标准 + 生产 + 断点续传)
├── snakefile                    # Snakemake 工作流
├── archive/                     # 旧数据/脚本/文档归档
├── docs/
│   └── 2026-05-10_optimization_log.md  # 优化记录
├── requirements.txt
└── .gitignore
```

## 快速开始

### 金标准模式

```bash
./run.sh                              # 全量 48 篇
./run.sh --sample-size 5              # 测试 5 篇
./run.sh --sample-size 5 --pmid-seed 42  # 指定随机种子
```

### 生产模式

```bash
./run.sh --production                 # 全部论文 (data/raw/paper_for_produce/)
./run.sh --production --sample-size 100  # 前 100 篇
./run.sh --production --workers 4     # 4 worker 并行
./run.sh --production --input path/to/pdfs  # 指定输入目录
```

### 断点续传

跑到一半 Ctrl+C 后，用 `--resume` 继续：

```bash
./run.sh --production --resume        # 自动找最新目录续传
./run.sh --production --resume outputs/production_20260531_xxx  # 指定目录
```

断点续传会自动：
- ✅ 跳过已成功的条目
- 🔄 重跑 `{"error": ...}` 的条目
- 🔄 失败自动重试 3 次（指数退避）
- 💾 每 50 篇自动存盘一次

## 模型选择

| 模型 | RPM | TPM | 适用场景 |
|------|-----|-----|---------|
| `qwen3.7-max` | 30,000 | 5,000,000 | **生产模式**（大批量，高并发）|
| `qwen3.7-max-2026-05-20` | 600 | 1,000,000 | 需要固定模型版本的场景 |

默认使用 `qwen3.7-max`（滚动版本），在 `config/config.yaml` 中修改 `model` 字段切换。

## 金标准数据集

`data/raw/finalresult.tsv` 包含 48 篇论文（含 2 篇空测试用例）的 TF-target 调控关系：

| 列名 | 说明 |
|------|------|
| PMID | PubMed ID |
| TF | 转录因子符号 |
| ENSG | 目标基因 Ensembl ID |
| CellLine | 实验细胞系 |
| Assay | 实验方法 (分号分隔) |
| Complex | 蛋白复合物名 |
| Target | 目标基因符号 |
| Cofactor | 辅因子标记 (0/1) |

## 核心特性

### 两轮 CoT LLM 提取

- Round 1: 自由文本分析（Q1-Q4 问卷），模型逐句扫描摘要
- Round 2: 基于 Round 1 分析，输出结构化 JSON（0-10 条关系）
- 输出字段：TF, Target, direction, evidence, assay, cellLine

### 基因名自动标准化

四层防护确保输出为标准 HGNC 符号：

1. Prompt 层：强制要求模型输出 HGNC 符号，提供内联别名映射
2. Post-processing 层：JSON 解析后通过 `gene_aliases.py` 运行 role-aware 归一化
3. Reporting 层：对比时使用 `evaluation.py` 统一标准化 + 异构体模糊匹配
4. 数据层：`gene_alias_overrides.json` 支持 map/block action + per-rule reason

### 评估指标

两级评估：关系级（TF+Target）为辅助参考，完全级（TF+Target+Assay+CellLine）为主指标。

**完全级（主指标）：**

| 指标 | 计算方式 |
|------|---------|
| Precision | TP_full / (TP_full + Partial + NewFound) |
| Recall | TP_full / GT 总数 |
| F1 | 2PR/(P+R) |
| Recall (experimental) | 仅 Assay ≠ Literature 子集的 Recall |

**关系级（辅助）：**

| 指标 | 计算方式 |
|------|---------|
| Precision | TP_rel / (TP_rel + NewFound) |
| Recall | TP_rel / GT 总数 |
| F1 | 2PR/(P+R) |

匹配规则：Assay 使用 GT ⊆ LLM 子集匹配，CellLine 使用交集匹配。Greedy 1-to-1 匹配防止重复计数。

### 评估分类标准

| 状态 | 含义 |
|------|------|
| 完全匹配 | TF+Target+Assay+CellLine 全对 |
| 部分匹配 | TF+Target 对，Assay 或 CellLine 不对 |
| 新发现 | (TF, Target) 不在金标准 |
| 遗漏 | 金标准有但 LLM 未找到 |

## Debug 与评估

```bash
# 重跑指定 PMID（调试提示词）
./scripts/rerun_pmids.sh 18776923,22479354

# 生成含 debug 面板的报告（自动定位最新输出目录）
./scripts/review_debug.sh

# 或指定目录
./scripts/review_debug.sh outputs/merged_20260528_192933

# 合并多次运行的 debug JSON（同一 PMID 取最新结果）
python scripts/merge_debug.py --clean

# debug JSON → Excel（方便在 Excel 中筛选查看）
python scripts/debug_to_excel.py outputs/merged_*/analysis_results_debug.json
```

## 通用报告与后处理

后处理脚本可以直接处理子集 JSON，不要求输入一定来自一次完整 pipeline。

```bash
# 任意 analysis_results 子集 → HTML 报告
PYTHONPATH=src python -m bio_llm.reporting \
  --llm-json outputs/subset.json \
  --output outputs/subset_report.html \
  --mode production

# 带金标准对比的子集报告
PYTHONPATH=src python -m bio_llm.reporting \
  --llm-json outputs/subset.json \
  --gold-standard data/raw/finalresult.tsv \
  --text-source fitz \
  --output outputs/subset_gs_report.html

# 过滤任意输出目录中的 analysis_results.json
python scripts/filter_relations.py outputs/production_20260531_xxx
```

## 手动分步运行

```bash
# 1. LLM 分析（直接从 papers_txt/ 读取全文）
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis \
  --gold-standard data/raw/finalresult.tsv \
  --text-source fitz \
  --output outputs/myrun/analysis_results.json --debug

# 2. 生成报告
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting \
  --llm-json outputs/myrun/analysis_results.json \
  --text-source fitz \
  --debug-json outputs/myrun/analysis_results_debug.json \
  --gold-standard data/raw/finalresult.tsv \
  --output outputs/myrun/report.html
```

## 配置文件

从模版复制并修改：

```bash
cp config/config.example.yaml config/config.yaml
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| sample_size | 48 | 选取 PMID 数 (金标准) / 论文数 (生产) |
| seed | 42 | LLM 输出确定性种子 |
| email | (必填) | NCBI Entrez 邮箱 |
| model | qwen3.7-max | 阿里云百炼 Qwen 模型 (滚动版本，高 RPM) |
| temperature | 0 | LLM 温度 (0 = 确定性) |
| workers | 4 | API 并发数 |
| ncbi_bypass_proxy | false | 绕过代理直连 PubMed |
| text_source | fitz | 金标准文本来源 (fitz/hybrid/nougat) |

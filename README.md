# Bio-LLM

面向小规模实验的生物文本抽取流水线。从人工金标准数据集获取 PMID，拉取 PubMed 摘要，用 LLM (qwen3.7-max-2026-05-20) 提取 TF-target 调控关系（含 Assay 和 CellLine），生成 HTML 对比报告。

## 项目结构

```text
Bio-LLM/
├── config/
│   ├── config.example.yaml     # 配置模版
│   └── config.yaml             # 运行参数 (gitignore)
├── data/
│   ├── raw/
│   │   ├── finalresult.tsv     # 人工金标准 (48 PMID, 8 列)
│   │   ├── hgnc_complete_set.txt  # HGNC 完整基因集 (gitignore)
│   │   ├── papers/             # 下载的 PDF 论文 (gitignore, 48 篇)
│   │   └── papers_txt/         # PDF→文本转换输出
│   │       ├── Nougat/         # 纯 Nougat OCR 输出
│   │       ├── fitz/           # 纯 PyMuPDF (fitz) 输出
│   │       └── hybrid/         # Nougat + pymupdf4llm 混合输出 (最终使用)
│   ├── curated/
│   │   ├── gene_alias_index.json         # HGNC 别名索引 (自动生成)
│   │   ├── gene_alias_map.json           # 旧版别名映射 (向后兼容)
│   │   ├── gene_alias_overrides.json     # 人工标注覆盖规则 (最高优先级)
│   │   ├── gene_alias_conflicts.json     # 歧义别名列表 (自动生成)
│   │   └── gene_ensg_map.json            # Gene → ENSG 映射表
│   └── archive/                # 旧数据/代码归档 (TRRUST/GS50/abstracts.py, gitignore)
├── outputs/                     # 输出 (gitignore)
├── src/bio_llm/
│   ├── __init__.py              # 包入口
│   ├── gene_aliases.py          # 基因名标准化 (role-aware + 元数据追踪)
│   ├── analysis.py              # 两轮 LLM 抽取 + 本地全文加载
│   ├── evaluation.py            # 评估: 金标准加载 + 四维匹配
│   └── reporting.py             # 生成 HTML 报告 + 统计
├── scripts/
│   ├── build_alias_map.py       # 从 HGNC 构建别名索引
│   ├── build_ensg_map.py        # 构建 Gene→ENSG 映射表
│   ├── hybrid_convert_v2.py     # Nougat + pymupdf4llm 混合转换 (主用)
│   └── review_debug.sh          # 一键生成含 debug 面板的报告
├── run.sh                       # 一键启动入口
├── snakefile                    # Snakemake 工作流
├── docs/
│   ├── extraction_strategy.md          # 提取策略规范
│   └── 2026-05-10_optimization_log.md  # 优化记录
├── requirements.txt
└── .gitignore
```

## 流程

```text
data/raw/finalresult.tsv + data/raw/papers_txt/{source}/
    → outputs/analysis_results.json   (analysis.py, 提示词内嵌在代码中)
    → outputs/report.html             (reporting.py)
```

## 环境

- `conda` + 名为 `bio_llm` 的环境
- 阿里云百炼 API Key (`DASHSCOPE_API_KEY`)
- [requirements.txt](requirements.txt)

```bash
conda create -n bio_llm python=3.10 -y
conda activate bio_llm
pip install -r requirements.txt
export DASHSCOPE_API_KEY="your_api_key"
```

## 快速开始

```bash
./run.sh        # 默认全量 48 PMID
./run.sh 5      # 快速测试 5 条
```

## PDF 转文本

PMC XML 全文仅覆盖 ~15% 论文，其余使用 Nougat OCR + pymupdf4llm 混合方案：

```bash
# 幻觉检测（无 GPU，快速调试）
python scripts/hybrid_convert_v2.py --detect

# 已有 Nougat 文本的后处理修复
python scripts/hybrid_convert_v2.py --existing --pmid 10453008

# 新论文完整流程（Nougat 推理 + pymupdf4llm fallback）
python scripts/hybrid_convert_v2.py --full --pmids 15184388 15195143
```

**混合策略**：
- Nougat 为主（希腊字母正确，自动 Markdown 结构）
- 5 种幻觉检测器：行级重复、token 重复（backreference 正则）、句子 n-gram、单字符重复、跨段落重复
- 三级修复：轻度 inline 删除 → 中度段落替换 pymupdf4llm → 重度整页替换

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
# 单条摘要交互调试
PYTHONPATH=src python -m bio_llm.analysis --test-abstract "STAT3 binds to..."

# 批量模式输出 debug (含归一化日志)
PYTHONPATH=src python -m bio_llm.analysis --input ... --output ... --debug

# 生成含 debug 面板的报告
./scripts/review_debug.sh
```

## 手动分步运行

```bash
# 1. LLM 分析（直接从 papers_txt/ 读取全文）
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis \
  --gold-standard data/raw/finalresult.tsv \
  --text-source fitz \
  --output outputs/analysis_results.json --debug

# 2. 生成报告
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting \
  --llm-json outputs/analysis_results.json \
  --text-source fitz \
  --debug-json outputs/analysis_results_debug.json \
  --gold-standard data/raw/finalresult.tsv \
  --output outputs/report.html
```

## 配置文件

从模版复制并修改：

```bash
cp config/config.example.yaml config/config.yaml
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| sample_size | 48 | 选取 PMID 数 (全量金标准) |
| seed | (无) | 随机种子 |
| email | (必填) | NCBI Entrez 邮箱 |
| model | qwen3.7-max-2026-05-20 | 阿里云百炼 Qwen 模型 |
| temperature | 0 | LLM 温度 (0 = 确定性) |
| workers | 4 | API 并发数 |
| ncbi_bypass_proxy | false | 绕过代理直连 PubMed |
| text_source | fitz | 论文文本来源 (fitz/hybrid/nougat) |

# Bio-LLM

面向小规模实验的生物文本抽取流水线。从人工金标准数据集获取 PMID，拉取 PubMed 摘要，用 LLM (qwen3.7-max-2026-05-20) 提取 TF-target 调控关系（含 Assay 和 CellLine），生成 HTML 对比报告。

## 项目结构

```text
Bio-LLM/
├── config/
│   ├── config.example.yaml     # 配置模版
│   ├── config.yaml             # 运行参数 (gitignore)
│   └── prompts/
│       ├── round1.txt          # Round 1 提示词模板 (可被调试器编辑)
│       └── round2.txt          # Round 2 提示词模板 (可被调试器编辑)
├── data/
│   ├── raw/
│   │   ├── finalresult.tsv     # 人工金标准 (46 PMID, 226 行, 8 列)
│   │   └── hgnc_complete_set.txt  # HGNC 完整基因集 (gitignore)
│   ├── interim/                # 中间文件 (gitignore)
│   ├── curated/
│   │   ├── gene_alias_index.json         # HGNC 别名索引 (自动生成)
│   │   ├── gene_alias_map.json           # 旧版别名映射 (向后兼容)
│   │   ├── gene_alias_overrides.json     # 人工标注覆盖规则 (最高优先级)
│   │   ├── gene_alias_conflicts.json     # 歧义别名列表 (自动生成)
│   │   └── gene_ensg_map.json            # Gene → ENSG 映射表
│   └── archive/                # 旧数据归档 (TRRUST/GS50, gitignore)
├── outputs/                     # 输出 (gitignore)
├── src/bio_llm/
│   ├── __init__.py              # 包入口
│   ├── gene_aliases.py          # 基因名标准化 (role-aware + 元数据追踪)
│   ├── abstracts.py             # 从金标准拉取 PubMed 摘要
│   ├── analysis.py              # 两轮 LLM 抽取 TF-Target-Assay-CellLine
│   ├── evaluation.py            # 评估: 金标准加载 + 四维匹配
│   └── reporting.py             # 生成 HTML 报告 + 统计
├── scripts/
│   ├── build_alias_map.py       # 从 HGNC 构建别名索引
│   ├── build_ensg_map.py        # 构建 Gene→ENSG 映射表
│   ├── review_debug.sh          # 一键生成含 debug 面板的报告
│   └── prompt_debugger.py       # Gradio 提示词调试 Web UI
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
data/raw/finalresult.tsv
    → data/interim/abstracts_for_test.txt   (abstracts.py)
    → outputs/analysis_results.json         (analysis.py)
    → outputs/report.html                   (reporting.py)

提示词:
    config/prompts/round1.txt              (LLM Round 1 模板)
    config/prompts/round2.txt              (LLM Round 2 模板)
    scripts/prompt_debugger.py             (交互式调试)
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
./run.sh        # 默认全量 46 PMID
./run.sh 5      # 快速测试 5 条
```

## 提示词调试器

```bash
PYTHONPATH=src python scripts/prompt_debugger.py
# 浏览器打开 http://localhost:7860
```

功能：
- 左右分栏：编辑提示词 / 测试分析
- 支持选择金标准 PMID 或粘贴摘要文本
- 实时显示 Round 1/2 输出、解析 JSON、与金标准对比
- 一键保存提示词到 `config/prompts/`

## 金标准数据集

`data/raw/finalresult.tsv` 包含 46 篇论文、226 条 TF-target 调控关系：

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
- 输出字段：TF, Target, direction, confidence, evidence, assay, cellLine
- 置信度 1-5（基于实验方法 + 证据强度）

### 基因名自动标准化

四层防护确保输出为标准 HGNC 符号：

1. Prompt 层：强制要求模型输出 HGNC 符号，提供内联别名映射
2. Post-processing 层：JSON 解析后通过 `gene_aliases.py` 运行 role-aware 归一化
3. Reporting 层：对比时使用 `evaluation.py` 统一标准化 + 异构体模糊匹配
4. 数据层：`gene_alias_overrides.json` 支持 map/block action + per-rule reason

### 评估指标

| 指标 | 计算方式 |
|------|---------|
| Recall (all) | LLM 匹配到的 GT 数 / GT 总数 |
| Recall (experimental) | 仅 Assay ≠ Literature 的子集 |
| Evaluable Precision | 匹配 GT 的预测数 / (总预测 - New Found - New) |
| Assay Accuracy | 匹配对中 GT assay ⊆ LLM assay 的比例 |
| CellLine Accuracy | 匹配对中细胞系模糊匹配的比例 |

### 评估分类标准

| 状态 | 含义 |
|------|------|
| Consistent | (TF, Target) 在金标准中 |
| New Found | (TF, Target) 不在金标准 — LLM 新发现 |
| Missed | 金标准有但 LLM 未找到 |

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
# 1. 拉取摘要
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.abstracts \
  --input data/raw/finalresult.tsv \
  --output data/interim/abstracts_for_test.txt \
  --sample-size 5

# 2. LLM 分析
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis \
  --input data/interim/abstracts_for_test.txt \
  --output outputs/analysis_results.json --debug

# 3. 生成报告
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting \
  --llm-json outputs/analysis_results.json \
  --abstracts data/interim/abstracts_for_test.txt \
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
| sample_size | 46 | 选取 PMID 数 (全量金标准) |
| seed | (无) | 随机种子 |
| email | (必填) | NCBI Entrez 邮箱 |
| model | qwen3.7-max-2026-05-20 | 阿里云百炼 Qwen 模型 |
| temperature | 0 | LLM 温度 (0 = 确定性) |
| workers | 4 | API 并发数 |
| ncbi_bypass_proxy | false | 绕过代理直连 PubMed |
| prompt_dir | config/prompts | 提示词文件目录 |

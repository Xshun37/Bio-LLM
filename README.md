# Bio-LLM

面向小规模实验的生物文本抽取流水线。从 TRRUST 采样 PMID，拉取 PubMed 摘要，用 LLM (qwq-plus) 提取 TF-target 调控关系，生成 HTML 对比报告。

## 项目结构

```text
Bio-LLM/
├── config/
│   └── config.yaml              # 运行参数
├── data/
│   ├── raw/
│   │   ├── trrust_rawdata.human.tsv  # TRRUST 原始数据
│   │   └── hgnc_complete_set.txt    # HGNC 完整基因集 (可选)
│   ├── interim/                 # 中间文件 (gitignore)
│   └── curated/
│       ├── trrust_anomalies.jsonl    # TRRUST 已知错误记录
│       ├── gene_alias_map.json       # HGNC 别名映射表 (自动生成)
│       └── gene_alias_curated.json   # 手动补充别名
├── outputs/                     # 输出 (gitignore)
├── src/bio_llm/
│   ├── __init__.py              # 共享：别名映射、异常加载
│   ├── abstracts.py             # 拉取 PubMed 摘要
│   ├── analysis.py              # 两轮 LLM 抽取 TF-Target
│   └── reporting.py             # 生成 HTML 报告 + 统计
├── build_alias_map.py           # 从 HGNC 构建别名映射表
├── group_by_pmid.py             # TRRUST 按 PMID 分组
├── review_debug.sh              # 一键生成含 debug 面板的报告
├── run.sh                       # 一键启动入口
├── snakefile                    # Snakemake 工作流
├── 2026-05-09_optimization_log.md  # 优化记录
├── requirements.txt
└── .gitignore
```

## 流程

```text
data/raw/trrust_rawdata.human.tsv
    → data/interim/abstracts_for_test.txt   (abstracts.py)
    → outputs/analysis_results.json         (analysis.py)
    → outputs/report.html                   (reporting.py)

辅助数据:
    outputs/trrust_by_pmid.tsv              (group_by_pmid.py)
    outputs/analysis_results_debug.json     (--debug 模式)
    data/curated/gene_alias_map.json        (build_alias_map.py)
```

## 环境

- `conda` + 名为 `bio_llm` 的环境
- DashScope API Key (`DASHSCOPE_API_KEY`)
- [requirements.txt](requirements.txt)

```bash
conda create -n bio_llm python=3.10 -y
conda activate bio_llm
pip install -r requirements.txt
export DASHSCOPE_API_KEY="your_api_key"
```

## 快速开始

```bash
./run.sh        # 默认 5 条
./run.sh 20     # 抽样 20 条
```

## 核心特性

### 两轮 CoT LLM 提取

- Round 1: 自由文本分析（不限制 JSON），模型逐句扫描摘要
- Round 2: 基于 Round 1 分析，输出结构化 JSON（0-10 条关系）
- 支持方向：Activation / Repression
- 置信度 1-5（基于实验方法 + 证据强度）

### 基因名自动标准化

三层防护确保输出为标准 HGNC 符号：

1. Prompt 层：强制要求模型输出 HGNC 符号
2. Post-processing 层：JSON 解析后用 58K 别名表纠正
3. Reporting 层：对比时再次标准化

别名映射表通过 `build_alias_map.py` 从 HGNC 官方数据集自动生成，手动补充通过 `gene_alias_curated.json`。

### Debug 与评估

```bash
# 单条摘要交互调试
PYTHONPATH=src python -m bio_llm.analysis --test-abstract "STAT3 binds to..."

# 批量模式输出 debug
PYTHONPATH=src python -m bio_llm.analysis --input ... --output ... --debug

# 生成含 debug 面板的报告
./review_debug.sh
```

报告包含：
- 统计面板（Recall / Precision / Evaluable Precision / Direction Accuracy）
- 每个 PMID 的黄色 TRRUST Reference 条
- 可折叠 Debug 面板（Round 1/2 分析 + reasoning + token 用量）
- 底部的异常 PMID 排除列表

### 评估分类标准

| 状态 | 含义 |
|------|------|
| Consistent | (TF, Target) 在 TRRUST 中，方向一致 |
| Conflict | (TF, Target) 在 TRRUST 中，方向不同 |
| New Found | (TF, Target) 不在 TRRUST — LLM 新发现 |
| Missed | TRRUST 有但 LLM 未找到 |

### TRRUST 数据质量管理

- `data/curated/trrust_anomalies.jsonl` 记录已知错误（phantom gene、indirect chain）
- 记录的 PMID 自动从采样中排除
- `group_by_pmid.py` 生成 PMID 分组视图

## 手动分步运行

```bash
# 1. 拉取摘要
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.abstracts \
  --input data/raw/trrust_rawdata.human.tsv \
  --output data/interim/abstracts_for_test.txt

# 2. LLM 分析
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis \
  --input data/interim/abstracts_for_test.txt \
  --output outputs/analysis_results.json --debug

# 3. 生成报告
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting \
  --llm-json outputs/analysis_results.json \
  --abstracts data/interim/abstracts_for_test.txt \
  --debug-json outputs/analysis_results_debug.json \
  --trrust-by-pmid outputs/trrust_by_pmid.tsv \
  --output outputs/report.html
```

## 配置文件

`config/config.yaml` 主要参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| sample_size | 5 | 抽样 PMID 数 |
| seed | 42 | 随机种子 |
| model | qwq-plus | DashScope 推理模型 |
| temperature | 0 | LLM 温度 |
| workers | 16 | 并发数 |

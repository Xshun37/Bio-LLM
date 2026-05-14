# Bio-LLM 信息提取策略规范

## 1. 概述

**目的**：从 PubMed 摘要中提取 TF-Target 转录调控关系，并与 TRRUST v2 基准进行对比。

**模型**：DashScope `qwq-plus`（Qwen 推理模型），temperature = 0（确定性输出）。

**架构**：两轮链式思维（Chain-of-Thought, CoT）提取：
- **第一轮**：自由文本分析 — 模型完整阅读摘要后，以纯文本形式回答结构化问卷（Q1-Q4），不受 JSON 格式约束。
- **第二轮**：结构化提取 — 基于第一轮自身的分析结果，输出 JSON 数组形式的调控关系。

设计理由：将"理解"（第一轮）与"格式化"（第二轮）分离，防止两者相互干扰。

## 2. 第一轮：自由文本分析

### 2.1 问卷设计

模型需要回答关于摘要的四个具体问题：

**Q1 — 基因表达证据**：列出摘要中每个因另一基因被敲低或过表达而发生 mRNA/蛋白水平变化的基因。引用原文原句。

**Q2 — 机制句检测**：找出包含 `mediates`、`mediated`、`via`、`through`、`by regulating`、`which in turn` 或 `in turn` 的句子。这些句子描述了调控机制。

**Q3 — 模式识别**：识别 `X inhibits Y-mediated Z` 模式。直接关系为 X→Y（而非 X→Z）。列出所有此类模式。

**Q4 — 综合输出**：列出所有有效的直接调控关系（最多 10 条，置信度 ≥ 2）。注明调控因子、靶基因、方向、置信度和实验证据。

### 2.2 设计历程

模型存在"首句偏好"——倾向于读完摘要第一句就下结论，跳过后续内容。开放式的分步指令无法克服这一问题，但具体的枚举式问题能迫使模型系统性地扫描全文。问卷格式由此而来。

## 3. 调控关系判定规则

### 3.1 TF 定义

转录因子（TF）是直接在 DNA 层面（或通过复合物间接）调控基因转录的蛋白质。

**可接受**：经典 TF（STAT3, TP53, NFKB1, RELA, MYC, GATA1, FOXO, JUN, FOS, HNF1, HNF4, SP1, SP3）及转录调控因子（EZH2, HDAC1/3, EP300, MECP2）。

**不可接受**：激素（RA/视黄酸、雌激素）、生长因子（TGF-beta 配体形式）、细胞因子、药物、信号激酶（JNK, p38MAPK, PI3K, AKT, MEK1/MAP2K1）、代谢物。

若调控因子不是 TF，则排除该关系。

### 3.2 直接调控 vs 间接级联

**规则**：若摘要描述 `A 通过 B 调控 C`，则直接关系为 `A→B`。A→C 为间接级联，不应包含在输出中。

**检测手段**：Q2 和 Q3 用于检测机制句和 `X 抑制 Y 介导的 Z` 模式。这些信号表明作者描述了间接机制——必须提取直接关系。

**示例**：IFI16 通过先抑制 MYC 来抑制 hTERT → 输出 IFI16→MYC，而非 IFI16→hTERT。

### 3.3 自调控规则

TF 结合到基因 X 的启动子上，意味着 TF→X（而非 TF→TF）。

仅当摘要使用明确的自调控语言时才报告 TF→TF：如"regulates its own expression"、"auto-regulates"、"binds its own promoter"。

IL4 启动子上的"CEBPB 结合位点"意味着 CEBPB→IL4，而非 CEBPB→CEBPB。

### 3.4 异构体规则

若同一 TF 对同一基因的不同异构体有相反效应（如激活异构体 A、抑制异构体 B），可在 evidence 字段中注明异构体后输出两条记录（如 `Target = "LEF1"`，`evidence = "activates Lef-1 FL isoform"`）。

方向仅可为 `"Activation"` 或 `"Repression"`——禁止使用 `"Regulation"`。若无法确定方向，则排除该关系。

### 3.5 融合蛋白规则

禁止使用融合蛋白名称（MLL-AF9, BCR-ABL）作为基因符号。必须拆分：MLL-AF9 → KMT2A 和 MLLT3。

## 4. 基因名标准化

### 4.1 三层归一化

所有基因名通过三层级联归一化：

| 优先级 | 规模 | 示例 |
|--------|------|------|------|
| 1（最高） | 硬编码人工映射表 | NF-KB P65 → RELA, C-MYC → MYC |
| 2 | HGNC 全量别名映射 | ZBP-89 → ZNF148, Oct-1 → POU2F1 |
| 3（兜底） | 去特殊字符大写 | 无匹配时返回清理后的原名 |

第一层包含两个子表：`_SYNONYM_MAP`（TF 专用）和 `_TARGET_SYNONYM_MAP`（靶基因专用）。

### 4.2 HGNC 强制执行

模型提示词明确要求：
- 输出官方 HGNC 批准符号
- 将所有蛋白质名称和别名转换为 HGNC 符号
- 提示词中内联提供别名映射：ZBP-89→ZNF148, SAF-1→MAZ, Oct-1→POU2F1, c-Myc→MYC, NF-kB p65→RELA

JSON 解析后，对每个 TF 和 Target 字段运行归一化级联。

### 4.3 归一化日志

每次归一化事件（原始名 → 标准符号）会记录在 debug 输出的 `normalization_log` 中，可追溯基因名纠正过程。

## 5. 置信度标度

| 分值 | 等级 | 所需证据 |
|------|------|----------|
| 5 | 金标准 | ChIP + 报告基因实验 + 突变实验 |
| 4 | 强 | ChIP 或 EMSA + 敲低/过表达表型 |
| 3 | 中等 | 明确功能证据但结合方法不明确 |
| 2 | 提示性 | 有提及但实验细节稀疏 |
| 1 | 推测性 | 仅在讨论部分提及 |

置信度 < 2 的关系不输出。

## 6. 第二轮：结构化 JSON 提取

### 6.1 输出 Schema

```json
[{
  "TF": "GENE",
  "Target": "GENE",
  "direction": "Activation",
  "confidence": 5,
  "evidence": "ChIP+luciferase+mutagenesis"
}]
```

### 6.2 选择优先级

直接优先于介导：若摘要描述 `A 通过 B 调控 C`，输出 `A→B`（A 为 TF），而非 `A→C`。

### 6.3 去重规则

每个 (TF, Target) 对在数组中只能出现一次。若多个实验支持同一对，合并为一条并保留最佳证据。

### 6.4 数量上限

最多 10 条关系。若发现更多，保留置信度最高的 10 条。

### 6.5 空输出

若未发现有效 TF-Target 关系，输出空数组：`[]`。

## 7. 评估标准

### 7.1 状态分类

每条 LLM 预测与 TRRUST 基准比对后分为：

| 状态 | 含义 |
|------|------|
| **Consistent** | (TF, Target) 在 TRRUST 中，方向一致 |
| **Conflict** | (TF, Target) 在 TRRUST 中，方向不同 |
| **New Found** | (TF, Target) 不在 TRRUST 中 —— LLM 新发现 |
| **Missed** | TRRUST 中有但 LLM 未找到 |
| **New** | 该 PMID 无 TRRUST 条目 |

### 7.2 指标定义

| 指标 | 公式 | 含义 |
|------|------|------|
| **Recall（召回率）** | matched_GT / total_GT | TRRUST 中被 LLM 找到的比例 |
| **Overall Precision（总体精确率）** | (Consistent + Conflict) / total_LLM | LLM 结果中命中 TRRUST 的比例 |
| **Evaluable Precision（可评估精确率）** | (Consistent + Conflict) / (total_LLM - New Found) | 排除新发现后的精确率 |
| **Direction Accuracy（方向准确率）** | Consistent / (Consistent + Conflict) | 匹配到的配对中方向正确的比例 |

### 7.3 模糊基因匹配

容忍异构体后缀差异：`RASSF1` 匹配 `RASSF1A`，`HNF1` 匹配 `HNF1A`。匹配规则为剥离数字后的单个大写字母后缀。

## 8. 异常管理

### 8.1 异常类型

| 类型 | 说明 |
|------|------|
| `phantom_gene` | 基因名未出现在该论文中（如核苷酸被误读为基因名） |
| `indirect_chain` | TRRUST 记录为直接调控，实际为间接级联 |
| `wrong_direction` | TRRUST 记录的调控方向与论文不符 |
| `other` | 其他数据质量问题 |

### 8.2 异常格式

异常记录存储在 `data/curated/trrust_anomalies.jsonl`（JSONL 格式，每行一个对象）：

```json
{
  "pmid": "9792724",
  "anomaly_type": "phantom_gene",
  "trrust_entry": "HNF4G->AFP (Activation)",
  "issue": "HNF4G 未在论文中出现。'G' 为鸟嘌呤核苷酸。",
  "corrected": null,
  "curated_date": "2026-05-10"
}
```

### 8.3 标注流程

使用 `src/bio_llm/curate.py`（可作为模块 `python -m bio_llm.curate` 运行） 交互式、分步引导添加异常记录。脚本会校验所有字段并确保格式一致。

`abstracts.py` 在采样时会自动排除异常文件中记录的 PMID。

## 9. 已知局限

1. **TF 定义边界**：转录共调控因子（EP300, HDACs）被宽松纳入，可能对表观遗传调控因子产生假阳性。
2. **首句偏好**：问卷格式缓解但未消除模型倾向于关注摘要开头句的问题。
3. **HGNC 覆盖**：~58K 别名覆盖大部分人类基因，但新分配符号或非人物种同源基因可能无法识别。
4. **方向模糊**：摘要常使用"regulates"而不指明方向，此类关系被排除，可能降低召回率。
5. **置信度主观性**：1-5 分标度依赖模型从摘要文本中识别实验方法的能力。

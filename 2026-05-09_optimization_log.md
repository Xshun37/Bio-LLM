# Bio-LLM TF-Target 提取流水线 — 2026-05-09 优化记录

## 1. Debug 基础设施

**问题**：无法查看 LLM 推理过程，只返回最终 JSON，中间输出全部丢失。

**改动**：
- `analysis.py`：`analyze_tf_interaction()` 新增 `debug=False` 参数
- 开启后返回完整字典，包含：
  - `round1_analysis` — 第一轮自由文本分析（模型推理过程）
  - `round2_raw` — 第二轮原始 JSON 输出（清洗前）
  - `round2_clean` — 经过 `clean_json_text()` 后的 JSON
  - `round1_usage` / `round2_usage` — token 用量和 request ID
- 新增 `_extract_usage()` 辅助函数，安全提取 `GenerationResponse` 的用量信息
- `run_analysis()` 自动拆解 debug 字典，主 JSON 格式不变，额外写 `_debug.json`
- CLI 新增 `--debug` 和 `--test-abstract` 参数
- `test_single()` 函数支持单条摘要交互调试，直接打印两轮完整回复
- `reporting.py`：`--debug-json` 加载 debug 数据，在每个 PMID 卡片底部渲染可折叠面板
- `review_debug.sh`：一键脚本，合并 debug 数据生成报告并打开浏览器
- `snakefile`：`analyze_abstracts` 规则默认启用 `--debug`

## 2. 摘要解析器 Bug 修复

**问题**：PMID 20052289 模型一直输出 IFI16→hTERT 而不是 IFI16→MYC。根因不是 prompt，而是摘要文本被截断。

摘要使用了双括号标签如 `[[METHODOLOGY/PRINCIPAL FINDINGS]]`，但 `parse_test_file()` 的正则 `r"\[([^\]]+)\]\s*\n(.*)"` 只能匹配单括号 `[BACKGROUND]`，导致 METHODOLOGY 和 CONCLUSIONS 段落被静默丢弃。而包含 "knockdown of IFI16 increased c-Myc" 的关键句子恰好在被丢弃的部分。

**修复**：正则改为 `r"\[\[?([^\]\[]+)\]\]?\s*\n(.*)"`，同时兼容单双括号。

**教训**：在 prompt 优化之前，务必先确认数据完整性。prompt 质量的上限取决于输入数据的完整性。

## 3. 直接调控 vs 间接级联检测

**问题**：模型容易被摘要的标题发现误导。PMID 20052289 摘要的前言和结论写的是 "IFI16 negatively regulates hTERT"，但 METHODOLOGY 部分揭示了真实机制：IFI16 → MYC → hTERT。直接关系是 IFI16→MYC。

**Prompt 迭代过程**：

| 尝试 | 方法 | 结果 |
|------|------|------|
| 1 | 添加 DIRECT vs INDIRECT 定义 | 仍输出 hTERT |
| 2 | 添加 IFI16→MYC 级联示例 | 仍输出 hTERT |
| 3 | 分步机制追踪指令 | 仍只读首句 |
| 4 | **问卷格式** (Q1-Q4) 强制逐句阅读 | **成功**，正确识别 IFI16→MYC |

**关键发现**：第 4 次尝试结合了第 2 节的正则修复后才生效。问卷格式强制模型扫描全文并回答具体问题：

- Q1：列出所有被调控的基因及其 mRNA/蛋白变化证据
- Q2：找出包含 "mediates", "via", "through" 等关键词的机制句
- Q3：识别 "X inhibits Y-mediated Z" 模式 → X→Y 为直接关系
- Q4：列出所有有效直接调控关系

## 4. 多关系提取

**问题**：一个 PMID 可能描述多条调控关系（如 PMID 9694713 同时有 POU2F1→VWF 和 POU2F1→VCAM1）。原设计只提取一条。

**改动**：
- `abstracts.py`：按 PMID 分组再采样，每个 PMID block 写入全部 TRRUST 行
- `analysis.py` Round1 Q4：从 "SINGLE BEST" 改为 "ALL valid（最多5条，置信度≥2）"
- `analysis.py` Round2：从 "ONE element" 改为 "0-5 elements"
- 添加 TF 过滤器：排除激素(RA/视黄酸)、激酶(MEK1/MAP2K1)、药物、代谢物等
- 添加去重规则：不允许重复的 (TF, Target) 配对
- 硬上限 max 5 + confidence ≥ 2 防止输出失控

## 5. Ground Truth 数据重构

- 新建 `group_by_pmid.py`：读取 `trrust_rawdata.human.tsv`，按 PMID 分组，处理分号分隔的多 PMID 引用，输出 `trrust_by_pmid.tsv`
- `reporting.py`：`load_trrust_by_pmid()` 加载每个 PMID 的全部 TRRUST 关系
- 每个 PMID 卡片顶部显示黄色 "TRRUST Reference" 条，含所有已知关系
- 测试文件支持多条 `TRRUST Standard:` 行（使用 `re.findall`）

## 6. 基因名标准化改进（Isoform 模糊匹配）

**问题**：RASSF1 vs RASSF1A、HNF1 vs HNF1A — isoform 后缀差异导致假阴性。模型输出正确（摘要明确写 RASSF1A），但 TRRUST 记录 RASSF1，严格匹配失败。

**修复**：新增 `_fuzzy_gene_match(a, b)` — 比较时剥离数字后的单尾字母（isoform 后缀）：

- RASSF1 == RASSF1A ✓
- HNF1 == HNF1A ✓
- TP53 == TP53 ✓ (无后缀可剥离)
- CDKN1A == CDKN1A ✓ (正确不剥离，A 是基因名的一部分)

正则 `^(.+\d)[A-Z]$` 确保只剥离 "数字+单字母" 结尾的后缀。

## 7. 方向归一化

**问题**：模型输出 "Synergistic Activation"，TRRUST 记录 "Activation"，严格比较产生假 Conflict。"inhibition" vs "Repression" 同理。

**修复**：新增 `normalize_dir()`：

- 包含 "activation" → Activation
- 包含 "repression" 或 "inhibition" → Repression

## 8. 分类标准简化

**问题**：原分类体系混乱：
- "TF-Match" (TF 匹配但 Target 不同) 令人困惑，Target 可能实际上是正确的生物发现
- "Mismatch" 把 "TF 不在 TRRUST 中" 和 "预测错误" 混为一谈

**迭代**：经过多轮调整，最终简化为 4 类：

| 状态 | 含义 |
|------|------|
| **Consistent** | (TF, Target) 配对在 TRRUST 中，方向一致 |
| **Conflict** | (TF, Target) 配对在 TRRUST 中，方向不同 |
| **New Found** | (TF, Target) 配对**不在** TRRUST 中 — LLM 新发现 |
| **Missed** | TRRUST 中有但 LLM 未找到 |

移除了 "TF-Match" 和 "Mismatch"。

## 9. 报告增强

- 报告顶部新增统计面板：
  - Recall（召回率）：TRRUST 中被 LLM 找到的比例
  - Precision（精确率）：LLM 结果中命中 TRRUST 的比例
  - Consistent / Conflict / New Found / New 计数
- 每个 PMID 卡片顶部黄色 TRRUST Reference 条
- 表格列：TF→Target | TRRUST Dir | LLM Dir | Conf | Evidence | Status
- Debug 可折叠面板：Round 1 Analysis、Round 2 Raw、Round 2 Cleaned

## 10. Prompt 优化经验总结

1. **qwen-max 有首句偏好**：模型倾向于读完第一句就下结论。问卷格式（具体问题列表）比开放式步骤指令更有效。

2. **数据完整性是前提**：prompt 质量的上限由输入数据决定。摘要被截断（正则 bug）时，任何 prompt 都无法挽救输出。

3. **具体反例比抽象定义有效**："IFI16 通过先抑制 MYC 来间接抑制 hTERT，所以应输出 IFI16→MYC" 比 "优先直接调控而非间接调控" 更有效。

4. **显式排除列表效果好**："排除：RA/retinoic acid、JNK、p38MAPK、PI3K、AKT、MEK1/MAP2K1" 优于 "排除信号激酶"。

5. **硬数字上限有用**：max 5 条 + confidence ≥ 2 防止模型输出过多低质量关系。

6. **两轮 CoT 设计稳健**：第一轮做语义分析（自由文本，不受 JSON 格式约束），第二轮做结构化提取（JSON）。这种分离防止了"理解"和"格式化"的相互干扰。

## 文件变更汇总

| 文件 | 变更 |
|------|------|
| `src/bio_llm/analysis.py` | 核心：prompt 优化、debug 基础设施、多关系输出、TF 过滤 |
| `src/bio_llm/reporting.py` | 多 ground truth 对比、模糊匹配、方向归一化、统计面板、debug 面板 |
| `src/bio_llm/abstracts.py` | 按 PMID 分组采样、多 TRRUST 行输出 |
| `snakefile` | debug 默认启用、trrust_by_pmid 输入、配置路径修正 |
| `run.sh` | 路径修正、跨平台浏览器打开 |
| `.gitignore` | 新目录结构适配 |
| `group_by_pmid.py` (新建) | TRRUST 按 PMID 分组工具 |
| `review_debug.sh` (新建) | 一键 debug 报告查看 |
| `IDtoAbstract.py` (删除) | → `src/bio_llm/abstracts.py` |
| `main.py` (删除) | → `src/bio_llm/analysis.py` |
| `generate_result.py` (删除) | → `src/bio_llm/reporting.py` |

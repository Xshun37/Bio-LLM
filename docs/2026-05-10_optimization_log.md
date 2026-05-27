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

---

## 2026-05-10 优化记录

### 11. TRRUST 异常记录与自动排除

- 创建 `data/curated/trrust_anomalies.jsonl` 记录已知 TRRUST 错误
- PMID 9792724: HNF4G→AFP (phantom gene: 核苷酸 G 被当成基因名), HNF4A→AFP (indirect chain)
- PMID 17350963: HOXA10→BHLHE22 (phantom gene: BHLHE22 不出现在论文中)
- `abstracts.py` 采样时自动排除异常 PMID
- 报告中末尾展示被排除的 PMID 列表及原因

### 12. 基因别名自动化标准化

**背景**：模型遇到 ZBP-89 (论文) 但 TRRUST 用 ZNF148 (HGNC)。手动添加别名不持久。

**方案**：从 HGNC 官方数据集 (`hgnc_complete_set.txt`) 自动构建 58K 条目别名映射表。

**工具链**：
- `build_alias_map.py`：解析 HGNC TSV，过滤垃圾条目（<3 字符），合并手动补充 (`gene_alias_curated.json`)
- `src/bio_llm/__init__.py`：`normalize_gene_name()` 先查硬编码映射，再查 HGNC 映射，最后 fallback
- `analysis.py`：JSON 解析后自动跑 `normalize_tf` / `normalize_target`
- Prompt 加强制要求模型输出 HGNC 符号："You know these mappings — use them"

**关键别名覆盖**：
| 论文用名 | HGNC 标准 |
|----------|-----------|
| ZBP-89 | ZNF148 |
| SAF-1 | MAZ |
| Oct-1 | POU2F1 |
| c-Myc | MYC |
| BMAL1 | ARNTL |

### 13. Prompt 优化 — 自调控与异构体

**自调控误判**：模型将 TF 在靶基因启动子上的结合位点误解为 TF 调控自身。
- 新增 AUTO-REGULATION RULE：只有摘要明确说 TF 调控自身表达时才输出 TF→TF

**异构体冲突**：同一 TF 对不同异构体相反效应，模型输出矛盾方向。
- 新增 ISOFORM RULE：允许拆成两条（不同方向），但禁止输出 "Regulation" 作为模糊方向
- 方向只能是 Activation 或 Repression

**去重强化**：多个实验支持同一 (TF,Target) 对应合并为一条。

### 14. 评估指标完善

- 新增 Missed 计数
- 新增 Evaluable Precision（排除 New Found 后的精确率）
- 新增 Direction Accuracy（匹配到的配对中方向正确率）
- Recall 统计唯一 GT 命中数（非 LLM 结果数）

### 15. 分类标准最终简化

| 状态 | 含义 |
|------|------|
| Consistent | (TF, Target) 在 TRRUST，方向一致 |
| Conflict | (TF, Target) 在 TRRUST，方向不同 |
| New Found | (TF, Target) 不在 TRRUST |
| Missed | TRRUST 有但 LLM 未找到 |

移除 TF-Match、Mismatch。Unknown 方向在 reporting 时自动筛除。

### 16. 模型切换至 qwq-plus

- 从 qwen-max 切换到 qwq-plus（Qwen 推理模型）
- qwq 要求 `stream=True`，添加 `_collect_stream()` 处理流式响应
- 新增 `extract_reasoning_content()` 捕获思考 token
- Recall: 74.3% → 85.7% → **88.6%**

### 17. 项目文档更新

- README.md 重写：完整项目结构、核心特性、手动运行说明
- 优化日志更新至 2026-05-10

---

## 2026-05-10 第二次优化记录

### 18. 评估标准模块化

**问题**：评估逻辑（方向归一化、模糊匹配、状态分类、指标计算）散落在 `reporting.py` 中，与 HTML 渲染耦合。同名映射在 `__init__.py` 和 `reporting.py` 两份维护，已有偏差。

**改动**：
- 新建 `src/bio_llm/evaluation.py`：集中 `normalize_direction`、`fuzzy_gene_match`、`classify_llm_entry`、`classify_missed_gt`、`compute_metrics`、`is_suspicious_gene_name`
- `reporting.py` 删除 ~130 行重复代码，改为从 `evaluation` 和 `__init__` 导入
- `__init__.py` 补充缺失别名（YAN→ETS1, POINTEDP2→ETS1），成为唯一真相源
- 评估指标与 HTML 报告生成完全解耦

### 19. 异常标注入口

**问题**：`trrust_anomalies.jsonl` 只能手动编辑 JSON，容易写错格式。

**改动**：
- 新建 `src/bio_llm/curate.py`（非一次性脚本，作为正式模块）
- 交互式 5 步引导添加：PMID → 异常类型 → 原始条目 → 问题说明 → 修正条目
- 字段校验：PMID 纯数字、异常类型枚举、条目格式、非空 issue
- 支持 `add` / `list` / `remove` / `export` 子命令
- 自动记录 `curated_date`

### 20. 归一化日志

**问题**：基因名被纠正后无法追溯（NF-KB P65 → RELA 等静默发生）。

**改动**：
- `evaluation.py` 新增 `normalize_and_log()`，每次归一化记录 `{original, normalized, type}`
- `analysis.py` JSON 后处理阶段改为 `normalize_and_log`，`norm_log` 写入 debug 输出
- Debug 模式下可在 `analysis_results_debug.json` 的 `normalization_log` 字段查看所有纠正记录

### 21. 提取策略规范文档

- 新建 `docs/extraction_strategy.md`（中文）：完整记录两轮 CoT 设计、四问卷、调控规则、三层归一化、置信度标度、评估指标定义、异常管理流程、已知局限

### 22. 进度条

**问题**：`qwq-plus` 推理模型极慢，无进度反馈无法判断是否卡死。

**改动**：
- `requirements.txt` 添加 `tqdm>=4.67`
- `analysis.py` 的 `run_analysis` 用 tqdm 包裹 `as_completed`，实时显示进度 + 最新完成的 PMID 和关系数
- tqdm 未安装时自动回退到逐行打印

### 23. 429 限流重试

**问题**：并发过高时 DashScope API 返回 429，任务直接失败。

**改动**：
- `analysis.py` 新增 `_call_llm()` 封装 API 调用，检测 429 后指数退避重试（1s → 2s → 4s，最多 3 次）
- 默认并发从 16 降至 4（snakefile + config 模版）

### 24. 抽样随机化

**问题**：config 写死 `seed: 42`，每次运行抽样完全相同的 PMID，测试阶段需要不同数据。

**改动**：
- config 去掉 seed，不设则每次随机（需要复现时显式指定）
- snakefile 相应调整：seed 未配置时不传 `--seed` 参数

### 25. 项目结构整理

- `trrust_by_pmid.tsv` 定位为预处理源数据，归入 `data/raw/`
- `outputs/` 仅保留流水线产物
- 新建 `config/config.example.yaml` 配置模版，真实 config 保持 gitignore
- README 全面更新：结构树、配置表、特性说明、手动运行示例
- requirements.txt 更新包含 tqdm

---

## 2026-05-12 优化记录

### 26. 基因别名系统重构

**问题**：旧的别名架构有三重冗余和维护负担：
- `__init__.py` 中 ~130 行硬编码 `_SYNONYM_MAP` / `_TARGET_SYNONYM_MAP`
- `gene_alias_curated.json` 中的手动补充
- `gene_alias_map.json` 中 ~58K 条 HGNC 自动生成条目

三份数据存在重复（ZBP-89、SAF-1、OCT-1 等同时出现在硬编码和 curated 中），且查询逻辑不支持按 TF/Target 角色区分。

**改动**：

**新模块 `src/bio_llm/gene_aliases.py`**：
- 统一的 `normalize_gene_name(raw_name, role)` 入口，支持 `role="tf"` / `role="target"` 区分
- `normalize_gene_name_with_meta()` 返回 `NormalizationResult` 数据类，包含完整的决策元数据（status, source, candidates, matched_key）
- 查询优先级：overrides → HGNC index → 官方符号直匹配 → 兜底
- 歧义别名（一个别名映射到多个 HGNC 符号）被保守拒绝，避免错误归一化
- `normalize_tf.with_meta` / `normalize_target.with_meta` 附加到函数对象上，`evaluation.py` 的 `normalize_and_log()` 通过 `getattr(fn, "with_meta", None)` 检测并获取元数据

**新数据文件 `gene_alias_overrides.json`**：
- JSON 数组格式，每条规则包含：`alias`, `symbol`, `roles`（可选 `tf`/`target`/`all`）, `action`（`map` 或 `block`）, `reason`
- 支持 **block action**：COX-2 作为 TF 角色时被 block（非转录因子），作为 Target 角色时映射到 PTGS2
- 替换旧的 `gene_alias_curated.json`（已删除）和 `__init__.py` 中的硬编码映射表（已删除）
- 所有规则附带 `reason` 字段，记录映射依据

**重构 `build_alias_map.py`**：
- 新增 `gene_alias_index.json`：可审计的别名索引，包含每个别名的候选符号列表、来源（`hgnc_alias_symbol` / `hgnc_prev_symbol`）、冲突标记
- 新增 `gene_alias_conflicts.json`：自动检测并导出的歧义别名列表
- `gene_alias_map.json` 保留为向后兼容的旧格式（仅唯一映射 + overrides 覆盖）
- 新增 `clean_key()` / `compact_key()` / `key_variants()` 辅助函数统一化名处理

**清理 `__init__.py`**：
- 删除 ~130 行硬编码的 `_SYNONYM_MAP`、`_TARGET_SYNONYM_MAP`、`_load_hgnc_map()`、`normalize_gene_name()`、`normalize_tf()`、`normalize_target()`
- 改为从 `bio_llm.gene_aliases` 重导入，保持向后兼容

### 27. 模型切换至 DeepSeek

- 从 DashScope `qwq-plus` 切换到 DeepSeek `deepseek-chat`
- SDK 从 `dashscope` 替换为 OpenAI-compatible `openai` 库
- API Key 环境变量从 `DASHSCOPE_API_KEY` 改为 `DEEPSEEK_API_KEY`
- `analysis.py`：`_call_llm()` 改用 `client.chat.completions.create()`，移除流式响应收集逻辑（`_collect_stream`），重试逻辑改用 `openai.RateLimitError`
- `config.example.yaml` 和 `snakefile` 默认模型更新
- 移除对 `dashscope` 的依赖，`requirements.txt` 中删除

### 28. 清理与文档

- `reporting.py`：删除未使用的 pandas 依赖和 `load_trrust()` / `build_pair_map()` 函数
- `evaluation.py`：`normalize_and_log()` 支持 `gene_aliases` 的元数据输出，日志包含 status / source / candidates / matched_key
- `.gitignore`：新增 `.claude` 目录
- `docs/extraction_strategy.md`：更新第 4 节基因名标准化为四层架构
- README 更新项目结构、API 配置、别名系统说明

---

## 2026-05-26 优化记录

### 29. 模型切换至 Qwen 3.7 Max

- 从 DeepSeek `deepseek-chat` 切换到阿里云百炼 `qwen3.7-max-2026-05-20`
- SDK 不变，仍使用 OpenAI 兼容接口
- API Key 环境变量从 `DEEPSEEK_API_KEY` 改为 `DASHSCOPE_API_KEY`
- `analysis.py`：`base_url` 改为 `https://dashscope.aliyuncs.com/compatible-mode/v1`，`DEFAULT_MODEL` 更新
- `config.yaml` / `config.example.yaml` / `snakefile`：默认模型名同步更新
- README、`extraction_strategy.md`：所有 DeepSeek 引用替换为阿里云百炼 Qwen

**改动**：
- `src/bio_llm/analysis.py`：`DEFAULT_MODEL`、`init_client()`、docstring、argparse 帮助文本（共 5 处）
- `config/config.yaml`：`model` 字段
- `config/config.example.yaml`：`model` 字段 + 注释
- `snakefile`：`analyze_abstracts` 规则默认值
- `README.md`：4 处 DeepSeek 引用
- `docs/extraction_strategy.md`：模型描述

### 30. 创建 CLAUDE.md 自动日志维护指令

**问题**：优化日志需要手动提醒才能更新，容易遗漏重要改动记录

**改动**：
- `CLAUDE.md`（新建）：加入日志维护规则，定义触发条件（非平凡改动）与格式模板，模型在每次重要改动后主动追加条目

---

### 31. 工程重构：TRRUST → 金标准数据集

**问题**：原先基于 TRRUST 数据库的流水线不再适应新的人工金标准数据需求。需要替换数据源、清理冗余代码、更新评估逻辑、新增提示词调试工具。

**改动**：
- `data/`：TRRUST 相关文件移至 `data/archive/`（gitignore），金标准 `finalresult.tsv`（46 PMID, 226 行, 8 列）作为唯一数据源
- `src/bio_llm/__init__.py`：删除 `load_anomalies()`
- `src/bio_llm/curate.py`（删除）：TRRUST 异常标注工具，金标准已人工审核不再需要
- `scripts/group_by_pmid.py`（删除）：TRRUST 按 PMID 分组，金标准已是按 PMID 组织
- `src/bio_llm/abstracts.py`：重写，从 `finalresult.tsv` 读取 PMID，输出 `Gold Standard: TF -> Target [Assay: ...] [CellLine: ...]` 格式
- `src/bio_llm/analysis.py`：提示词外部化到 `config/prompts/round1.txt` 和 `round2.txt`，输出新增 `assay` 和 `cellLine` 字段，`parse_test_file()` 解析 Gold Standard 行
- `src/bio_llm/evaluation.py`：删除 Direction 评估，新增 `load_gold_standard()`、`match_assays()`（GT ⊆ LLM）、`match_cellline()`（模糊匹配），`classify_llm_entry()` 不再比较方向
- `src/bio_llm/reporting.py`：删除 `load_trrust_by_pmid()` 和 `parse_abstracts_file()`（消除重复），导入共享解析器，比较表新增 Assay/CellLine 列
- `scripts/prompt_debugger.py`（新建）：Gradio Web UI 用于编辑提示词和单 PMID 测试
- `snakefile`、`run.sh`、`config/config.example.yaml`：更新数据源和默认参数

### 32. 报告与调试器界面中文化

**问题**：reporting.py 生成的 HTML 报告和 prompt_debugger.py Gradio UI 全部使用英文，不利于中文用户阅读。

**改动**：
- `src/bio_llm/reporting.py`：标题、统计面板（召回率/精确率/准确率）、表头、状态标签（一致/新发现/遗漏/无金标准）、调试面板全部中文化
- `scripts/prompt_debugger.py`：UI 标题/按钮/标签/状态消息/金标准对比输出全部中文化

### 33. 全文获取替代摘要方案

**问题**：仅使用 PubMed 摘要（~200-300 词）作为 LLM 输入，信息密度不足以准确提取 TF-target 调控关系（Assay、CellLine 等细节常在摘要中被省略）。

**改动**：
- `src/bio_llm/fulltext.py`（新建）：PMC 全文获取模块
  - `pmid_to_pmcid()`：PMID → PMCID 映射（Entrez.elink）
  - `fetch_pmc_xml()`：获取 PMC XML（Europe PMC REST API，fallback 到 NCBI efetch）
  - `parse_pmc_xml()`：XML → 结构化 sections（跳过 References/Acknowledgments 等）
  - `fetch_fulltexts()`：批量获取 + 本地缓存（XML + TXT）
  - CLI：`python -m bio_llm.fulltext --pmid 20052289` 单篇测试
- `src/bio_llm/abstracts.py`：重写 `generate_test_file()` 整合全文获取
  - 输出格式从 `Abstract:` 改为 `Full Text:`
  - 无 PMC 全文时降级到摘要（`fetch_abstracts()` fallback）
- `src/bio_llm/analysis.py`：`parse_test_file()` 兼容 `Full Text:` 和 `Abstract:` 标记
- `config/prompts/round1.txt`：新增全文分析指引
  - 标注 Introduction/Results/Discussion 为重点
  - Methods 提供实验技术上下文
- `snakefile`：`generate_abstracts` → `generate_fulltexts`
- `config/config.example.yaml`：新增 `fulltext_dir` 参数
- `src/bio_llm/reporting.py`：报告标题和标签从"摘要"改为"全文"
- `scripts/prompt_debugger.py`：UI 标签同步更新

**验证**：`./run.sh 3` 端到端测试通过（2 篇全文 + 1 篇摘要降级）

---

## 2026-05-27 优化记录

### 34. PDF 转文本方案探索

**问题**：PMC XML 全文覆盖率仅 ~15%（48 篇中 6 篇有完整 XML），需要 PDF→TXT 兜底方案。现有 `scripts/pdf_to_txt.py` 用简单 `page.get_text("text")` 输出噪声严重（表格散乱、图注/页码/running header 混入、参考文献未过滤）。

**探索过程**：

1. **手写 PyMuPDF 规则**：完成初版 `scripts/pdf_to_txt.py`，48 篇全部转换但质量不可用

2. **调研开源库**：
   - [Marker](https://github.com/datalab-to/marker)（深度学习版面分析，LayoutLMv3）
   - [Docling](https://github.com/docling-project/docling)（IBM，结构化 JSON）
   - [pymupdf4llm](https://pypi.org/project/pymupdf4llm/)（PyMuPDF 生态，Markdown 输出）

3. **调研大模型直接读 PDF**：
   - Qwen 3.7（当前使用）：纯文本模型，不支持
   - Qwen3-VL：PDF→图片→视觉理解，可行但 token 成本高
   - Gemini：原生 PDF 直传，最方便但需切换模型

4. **安装 Marker**：`pip install marker-pdf`，遇到 CUDA 兼容问题
   - 系统驱动 CUDA 12.9，Marker 装了 CUDA 13.0 的 PyTorch → GPU 不可用
   - 降级到 `torch cu124` → CUDA 可用（RTX 4060 Ti），但 `Recognizing Text` 仍需 ~3 小时/篇（405 个文本块逐个 OCR）

5. **换 pymupdf4llm**：秒级转换，5 篇测试结果 4/5 成功
   - 11706010（JBC）、16628196（JID）、10453008、22227292：OK
   - 7876096（1995 JBC 特殊排版）：失败，只提取到表格

**遗留问题**：
- pymupdf4llm 输出仍有残留：页码、running header、图注、脚注打断段落、References 未过滤
- PDF 自定义字体编码导致希腊字母乱码（`β-protein` → `-protein`），Marker `--force_ocr` 可解决但速度不可接受
- 48 篇 PDF 已下载至 `data/raw/papers/`，`data/raw/papers_txt/` 里仍是第一版噪声输出

**环境变更**：
- 新装：`marker-pdf 1.10.2`、`pymupdf4llm 1.27.2.3`、`pymupdf_layout 1.27.2.3`、`onnxruntime 1.23.2`
- 降级：`torch 2.6.0+cu124`（从 cu130）
- 各种 `nvidia-cu*` cu12 + cu13 共存

### 35. Nougat 方案探索与混合策略

**问题**：上一轮（#34）遗留两个核心问题：(1) PDF 自定义字体导致希腊字母乱码；(2) pymupdf4llm 输出仍有页眉/页码/图注噪声。

**方案：Nougat（Meta/Facebook Research）**

Nougat 是 Vision Encoder-Decoder 模型（Swin Transformer encoder + mBART decoder），将 PDF 页面渲染为图片后进行 OCR，输出 Markdown。

- 优点：希腊字母正确（走视觉路径，不依赖 PDF 字体编码）；自动输出带 `#`/`##` 结构的 Markdown；自动移除 References
- 速度：~8-12s/页（RTX 4060 Ti）
- 调用方式：通过 HuggingFace `transformers`（`VisionEncoderDecoderModel.from_pretrained('facebook/nougat-base')`），不使用 Nougat CLI（CLI 依赖 albumentations 1.x，与 2.x 不兼容）
- HuggingFace 镜像：`os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'`

**测试 11706010（9 页）**：成功，51276 chars，希腊字母正确（`β-protein`、`APB_β_`），结构清晰。

**批量转换 48 篇**：先清除旧 pymupdf4llm 输出，后台运行 Nougat。前 8 篇生成后停止（发现幻觉问题）。

**发现幻觉问题**：

Nougat 在部分页面产生重复幻觉（decoder 自回归的固有特性）：

| PMID | 问题页 | 幻觉类型 | 示例 |
|---|---|---|---|
| 15178343 | 首页 | 行级重复 | `PREDI, PREDI, PREDI...` 几百次 |
| 10082553 | 首页 | 行级重复 | 作者名重复几十次 |
| 10453008 | Introduction | Token 级重复 | `_i.e._, a _i.e._, a _i.e._...`（行内） |
| 10453008 | M&M | Token 级重复 | `_Hind_III_III_III_III...`（行内） |
| 11706010 | 部分页 | 表格行重复 | `\(-\) & \(-\) & \(-\)...` |

**其他方案评估**：
- [MinerU/Magic-PDF](https://github.com/opendatalab/MinerU)（OpenDataLab）：综合能力强但本地部署太重（~20GB 模型权重），放弃
- Layout Detection 模型：只能帮 fitz 过滤噪声（标注 body text / figure / table 区域），但 fitz 只是备用方案，收益有限，不需要

**解决方案：Nougat + fitz 混合**

`scripts/hybrid_convert.py`：逐页跑 Nougat，检测两类幻觉，幻觉页用 fitz 替换：
1. **行级重复检测**：非空行去重率 >20% → 幻觉
2. **Token 级重复检测**：(a) 正则匹配 `_X_X_X` 模式（如 `_Hind_III_III`）；(b) 单行内同一词出现 >10 次且占比 >15%（排除停用词）

**当前状态**：
- 8 篇已有 Nougat 输出（`papers_txt/`）
- 48 篇已有 fitz 原始输出（`papers_txt/fitz/`）
- 混合脚本已更新（增加 token 级检测），正在后台重跑 8 篇（task: `b6a0xur2y`）
- 输出目录：`papers_txt/hybrid/`

**遗留问题**：
- 混合策略无法解决 Nougat "编造型" 幻觉（生成不存在的内容），只能检测重复型
- 希腊字母在 Nougat 正常页正确，在 fitz 兜底页仍乱码
- 待评估：微调 Nougat 是否能消除幻觉（需要 PMC 配对训练数据，48 篇 ~400 样本偏少，RTX 4060 Ti 8GB 仅够 LoRA）

**新建文件**：
- `scripts/nougat_convert.py`：纯 Nougat 批量转换
- `scripts/hybrid_convert.py`：Nougat + fitz 混合转换（幻觉检测 + 兜底）
- `scripts/hybrid_merge.py`：基于已有 Nougat 输出的后处理合并
- `scripts/clean_pdf_txt.py`：fitz 输出的后处理清洗

### 36. Nougat + pymupdf4llm 混合转换 v2（段落级修复）

**问题**：v1 (`hybrid_convert.py`) 三个局限：(1) 整页替换丢失同页正常段落的正确希腊字母；(2) raw fitz 输出噪声多；(3) 页面边界丢失。

**改动**：
- `scripts/hybrid_convert_v2.py`（新建）：段落级幻觉检测 + pymupdf4llm 替代 fitz
  - **5 种幻觉检测器**：line_dup（行级重复）、token_rep（backreference 正则 + 单词高频）、sentence_rep（8-gram）、char_rep（单字符重复）、cross_para_dup（跨段落重复，新增）
  - **三级修复策略**：轻度→inline 删除重复 token；中度→段落级替换为 p4 对应段；重度→整页替换 p4
  - **段落对齐**：section header 锚点 + difflib.SequenceMatcher 模糊匹配 + 位置 fallback
  - **两种模式**：`--existing`（后处理已有 Nougat 文本，无 GPU）/ `--full`（完整流程）
  - `clean_p4_text()`：复用 `clean_pdf_txt.py` 元数据模式 + 页码/图片占位符清洗 + References 截断

**8 篇已有 Nougat 输出检测结果**：

| PMID | 严重度 | 坏段/总段 | 检测器 |
|------|--------|-----------|--------|
| 10082553 | severe | 177/223 | cross_para_dup（作者名重复） |
| 10453008 | moderate | 2/41 | token_rep（`_i.e._` + `_Hind_III`） |
| 10978529 | moderate | 1/30 | token_rep（CTG DNA 重复） |
| 11013233 | moderate | 1/47 | token_rep（`_M_M_M` 质粒序列） |
| 11706010 | severe | 1/58 | line_dup（表格行重复） |
| 11854297 | moderate | 1/54 | — |
| 14642566 | moderate | 1/61 | — |
| 15178343 | moderate | 1/30 | — |

**遗留问题**：
- 编造型幻觉仍无法检测（无重复模式）
- pymupdf4llm fallback 段落希腊字母乱码（PDF 字体编码）
- 重度幻觉页整页替换丢失 Nougat 正常段落的正确希腊字母

---

## 2026-05-27 第二次优化记录

### 37. hybrid_convert_v2 四项缺陷修复

**问题**：用户检查 13 篇 hybrid 输出后发现 4 个缺陷：
1. PMID 10978529 表格段落变成 `` 乱码 — Nougat 表格被 token_rep 检测器标记后替换为 p4，但 p4 默认 layout 模式对老 PDF 输出也是乱码
2. PMID 10082553 References 未删除 — `clean_nougat_page()` 正则不匹配 `## **REFERENCES**` 格式
3. PMID 15184388/11706010 段落中仍有乱码 — p4 对某些 PDF 质量差
4. PMID 10453008 Introduction 段落只替换了半段 — `align_paragraphs()` 用 `[:100]` 截断匹配到错误位置

**改动**：
- `scripts/hybrid_convert_v2.py`：
  - **Fix 1**：`_strip_repeated_tokens()` 重写 — 旧版删除整个 boundary 区间（含首次有效出现），新版只删除重复部分，保留首次出现的 token（如 `CTGCTGCTG...` → `CTG`），表格 LaTeX 结构不再被破坏
  - **Fix 2**：`_is_p4_quality_ok()` 新增 — 检查 p4 替换段是否可用（ replacement char 占比 < 5%，长度 > Nougat 段 20%）；`repair_page()` severe 路径增加质量检查，p4 质量差时降级为段落级修复
  - **Fix 3**：`clean_nougat_page()` 和 `clean_p4_text()` References 正则增强 — 兼容 `## **REFERENCES**`、`**References**`、`# References`、`References` 等格式
  - **Fix 4**：`align_paragraphs()` 接受 `bad_indices` 参数 — 幻觉段落跳过模糊匹配，直接使用位置 fallback（避免匹配到错误 p4 段落）；正常段落匹配文本从 `[:100]` 增加到 `[:200]`，阈值从 0.3 提高到 0.4
  - **Fix 5**：`process_paper_full()` 保存 Nougat 原始输出到 `data/raw/papers_txt/Nougat/{pmid}.txt`

**验证结果**：
- ✅ PMID 10082553 References 已删除（仅剩正文中的 "references" 引用）
- ✅ PMID 10978529 表格 inline strip 后保留完整 LaTeX 表格结构
- ✅ PMID 11706010 p4 质量通过检查，输出无乱码
- ⚠️ PMID 10453008 Introduction 首段截断 — 根因是 p4 和 fitz 都未提取到该段落（PDF 排版问题），非脚本缺陷

---

## 2026-05-27 第三次优化记录

### 38. 废弃 pymupdf4llm，迁移到纯 fitz（PyMuPDF）

**问题**：Entry 37 的 pymupdf4llm 方案在老 PDF（2000 年代，Acrobat Distiller 3.0）上失败：
- **layout 模式**：把整页识别为图片区域，输出 `█` 块（表格、公式全部丢失）
- **RAG 模式**：输出 `` (U+FFFD replacement characters)，表格内容乱码
- **PMID 10453008 Introduction**：pymupdf4llm 两种模式都未提取到该段落

用户指示：**废弃 pymupdf4llm，改用纯 fitz**。Nougat 已经提供正确的 Markdown 格式，fitz 只需在幻觉段落提供正确文本。

**根因分析**：
- pymupdf4llm 的 layout 分析对老 PDF 的字体/排版识别错误
- fitz 的 `page.get_text("text")` 对同样的 PDF 能正确提取文本（包括表格）
- fitz 的 `page.get_text("blocks")` 能正确分段，段落结构与 Nougat 对齐良好

**改动**：
- `scripts/hybrid_convert_v2.py` 完全重写：
  - **移除**：所有 pymupdf4llm 依赖（`extract_pymupdf4llm()`, `clean_p4_text()`, `_is_p4_quality_ok()`）
  - **新增**：`extract_fitz_pages()` — 使用 `page.get_text("blocks")` 提取段落级文本（而非 `page.get_text("text")` 的整页文本），每段用 `\n\n` 分隔
  - **新增**：`clean_fitz_text()` — 复用 `clean_pdf_txt.py` 元数据模式 + 页码/图片占位符清洗 + References 截断
  - **新增**：`_is_fitz_header()` — 识别 fitz 纯文本中的 section header（Introduction, Methods, Results 等）
  - **重写**：`align_paragraphs()` — 不再依赖 section header 锚点（许多老 PDF 的字体 fitz 无法识别），改用全局内容匹配：
    1. 先用非幻觉段落（good paragraphs）全局匹配 fitz，建立位置锚点
    2. 对幻觉段落（bad paragraphs），用相邻锚点插值位置，在 fitz 中搜索最佳匹配
    3. 匹配文本长度从 `[:100]` 增加到 `[:500]`，阈值从 0.3 提高到 0.4
  - **重写**：`repair_page()` — 调整修复优先级：
    1. **优先用 fitz 替换**（最可靠，fitz 总是提取正确文本）
    2. **次选 inline strip**（仅当 fitz 匹配失败时，用于 token_rep/char_rep）
    3. **最后保留原文**（兜底）
  - **增强**：References 正则 — 兼容 `## **REFERENCES**`、`**References**` 等格式
  - **新增**：`process_paper_full()` 保存 Nougat 原始输出到 `data/raw/papers_txt/Nougat/{pmid}.txt`

**验证结果（8 篇 PMID）**：
- ✅ PMID 10978529 表格段落：fitz blocks 正确提取表格 LaTeX 结构，替换 Nougat 幻觉段
- ✅ PMID 10082553 References：`clean_nougat_page()` 正则增强后正确删除
- ✅ PMID 11706010 乱码段落：fitz 提取正确文本，无 `` 或 `█` 乱码
- ✅ PMID 10453008 Introduction：fitz blocks 正确提取该段落（`ematopoiesis is a developmental process...`），完整替换 Nougat 幻觉段（旧版 `_i.e._, a _i.e._, a ...`）

**关键发现**：
- pymupdf4llm 对老 PDF（2000 年代）的 layout 分析不可靠，输出质量不如纯 fitz
- fitz 的 `blocks` 模式比 `text` 模式更适合段落级对齐，输出结构与 Nougat 段落对应良好
- Nougat + fitz 混合策略的核心思想不变：Nougat 提供 Markdown 格式 + 正确希腊字母，fitz 在幻觉段落提供正确文本

**遗留问题**：
- 编造型幻觉仍无法检测（无重复模式）
- 某些 PDF 的 section header 字体 fitz 无法识别（如 PMID 10453008 的 "Introduction"），但不影响全局内容匹配

---

## 2026-05-27 第四次优化记录

### 39. 取消 severe 整页替换，保留 Nougat 格式

**问题**：severity=severe 时整页替换为 fitz 纯文本，丢失 Nougat 的 Markdown 格式（`##` 标题、`\(\beta\)` 希腊字母、`_italic_` 等）。

**根因**：
- PMID 10082553 有 177/223 个坏段落（79%），全是作者行重复（`cross_para_dup`）
- fitz 里只有 1 份作者行，无法对齐 177 份 → 超过 50% 坏段落未修复 → 触发 page_replace fallback
- PMID 11706010 类似问题

**改动**：
- `scripts/hybrid_convert_v2.py`：
  - **删除**：`repair_page()` 中 severity=severe 的 early return（整页替换）
  - **新增**：Priority 0 处理 `cross_para_dup` / `line_dup` — 重复段落去重（保留首次出现，删除后续重复），line_dup 段落内部行级去重
  - **调整**：page_replace fallback 条件改为"超过 50% 坏段落未修复"（而非"severity=severe 就整页替换"）
  - **效果**：10082553、11706010 改为 paragraph_replace，Nougat 格式完整保留

**验证结果**：
- ✅ PMID 10082553：`action=paragraph_replace`，标题、`\(\beta\)`、`_italic_` 格式保留
- ✅ PMID 11706010：`action=paragraph_replace`，Nougat 格式保留
- ✅ 4 个原始 bug 全部修复（References 删除、Introduction 完整、表格正确、无乱码）

**关键发现**：
- `cross_para_dup`（跨段落重复）不需要 fitz 替换，直接删除重复即可（保留首次出现）
- Nougat 为主、fitz 替换幻觉部分 — 这个原则适用于所有 severity，包括 severe

### 40. 简化逻辑：去掉 severity 分类

**问题**：severity 分类（none/moderate/severe）是多余概念，导致 `severe` 页面整页替换丢失 Nougat 格式。

**根因**：修复策略只需要"识别坏段落 → 修复坏段落"，不需要先判断页面严重程度再决定修复方式。severity 引入了不必要的分支逻辑。

**改动**：
- `scripts/hybrid_convert_v2.py`：
  - **重命名**：`assess_page()` → `detect_hallucinations()`，只返回 bad_indices 和段落详情，不再返回 severity
  - **简化**：`repair_page()` 去掉 severity 参数，统一走段落级修复
  - **简化**：`process_paper_existing()` 和 `process_paper_full()` 去掉 severity 判断
  - **简化**：stats 输出只保留 `n_replaced`、`n_inline`、`n_deduped`
  - **修复**：当 Nougat 把多个段落压缩成一个坏段时，插入 fitz 的连续段落（而非只替换一段）

**验证结果**：
- ✅ 8 篇 PMID 全部正常，4 个原始 bug 保持修复
- ✅ PMID 10453008 Introduction 现在包含完整的两段内容（"ematopoiesis..." + "GM-CSFRa..."）
- ✅ 输出更清晰：`[10082553] 177 bad paras → paragraph_replace (replace=1 inline=1 dedup=174)`

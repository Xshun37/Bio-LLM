import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI, RateLimitError, APIStatusError

try:
    from tqdm import tqdm as _tqdm
except ImportError:
    _tqdm = None
from bio_llm import normalize_tf as _norm_tf, normalize_target as _norm_target
from bio_llm.evaluation import normalize_and_log, load_gold_standard

# ───────────────────────────────────────────────────────────
#  § 1  Constants  (被 config.yaml / CLI 覆盖，一般不改)
# ───────────────────────────────────────────────────────────

DEFAULT_OUTPUT = "outputs/analysis_results.json"
DEFAULT_MODEL = "qwen3.7-max"
DEFAULT_TEXT_SOURCE = "fitz"
DEFAULT_GOLD_STANDARD = "data/raw/finalresult.tsv"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_TEXT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "papers_txt")


# ───────────────────────────────────────────────────────────
#  § 2  ★ Prompts Engineering ★
#
#  ROUND1_PROMPT: 第一轮自由文本分析（CoT）
#  ROUND2_PROMPT: 第二轮结构化 JSON 输出
# ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
##背景##
作为一名生物化学与分子生物学专家，你精通转录因子、靶基因、细胞系和生物化学方法的概念、分类以及使用方法，专门负责这些内容的提取。

##文献分块要求##
对于一篇完整的文献，按照以下顺序进行分析：
- **引言（Introduction）**：说明了全文的研究目的，并往往直接给出文献设计的TF-Target关系；
- **方法（Methods）**：给出了文献中所有会用到的Assay和Cellline，提取Assay和Cellline应尽可能只在该部分涉及的内容里选取；
- **结果（Results）**：包含 TF-target 关系的实验证据，每条TF-Target的Assay和Cellline应只包含在Methods，同时在Results里有针对性验证；
- **讨论（Discussion）**：对发现的解释与验证，并提及大量直接**引用（方法为Literature）**的调控关系，尤其需要关注；
- 致谢（Acknowledgments）、参考文献（References）和补充材料部分不要阅读。

##实体提取要求##
======
TF栏提取的实体必须只能是以下两种中的一种：
1.转录因子（TF）
2.共调控因子（Cofactor/Coregulator）

转录因子（TF）必须是一种蛋白质，只会通过以下两种形式调控基因的转录：
1. 以单体形式结合DNA序列；
2. 以复合物形式结合DNA序列。
不可接受的转录因子：激素、生长因子、细胞因子、药物、信号激酶或代谢物。

共调控因子（Cofactor/Coregulator）不直接结合DNA，需要TF进行**招募**，大部分以以下两种形式作用：
1.表观遗传修饰，改变染色质结构
2.桥梁介导，在 TF 与 RNA 聚合酶之间传递信号
======

======
靶基因（Target）必须是一段**编码蛋白质的**DNA序列，不能是某种蛋白质，拒绝所有蛋白互作形式的调控关系。
======

**基因命名规则（GENE NAME RULES）**
- 你必须对所有TF和Target输出官方 HGNC 批准符号。不要使用蛋白质名称、通用名或文献别名
  例如以下映射关系：
  ZBP-89 → ZNF148, SAF-1 → MAZ, Oct-1 → POU2F1, c-Myc → MYC,
  NF-kB p65 → RELA, AP-2 → TFAP2A, C/EBPbeta → CEBPB, YB-1 → YBX1。
  在列出每个TF前，将其转换为 HGNC 符号；
- 如果文献使用了**蛋白质家族**，不明确指出亚基/通路，拒绝这条调控关系；
- 如果文献使用了**复合体/融合蛋白**，**拆分成**复合体的组成单体记成多条调控关系。

======
Assay必须是仅用于验证TF-Target调控关系的手段，而不涉及文献讨论的其他命题，有且仅有以下的数种：
  - **Luciferase** (Dual-Luciferase, firefly luciferase, reporter assay)
  - **CAT_assay** (Chloramphenicol Acetyltransferase)
  - **RT-PCR** (qRT-PCR, qPCR, real-time PCR)
  - **RNA-seq**
  - **Microarray** (DNA microarray, expression array)
  - **NB** (Northern Blot)
  - **RPA** (RNase Protection Assay)
  - **ChIP** (ChIP-seq, ChIP-chip, ChIPmentation, ChIP-qPCR)
  - **CUT&RUN**
  - **CUT&Tag**
  - **EMSA** (gel shift, supershift, band shift)
  - **DNase-seq**
  - **ATAC-seq**
  - **DAPA** (DNA Affinity Purification Assay)
  - **Footprinting** (DNase I Footprinting)
  - **WB** (Western Blot, immunoblot)
  - **Co-IP** (Co-Immunoprecipitation, IP)
  - **Pull-down** (GST pull-down)
  - **Mass_spec** (LC-MS/MS)
  - **PLA** (Proximity Ligation Assay)
  - **IHC** (Immunohistochemistry)
  - **IF** (Immunofluorescence)
  - **ELISA**
  - **RNAi_KD** (shRNA knockdown)
  - **siRNA**
  - **CRISPR_KO** (CRISPR/Cas9 knockout)
  - **CRISPRi**
  - **CRISPRa**
  - **OE** (Overexpression)
  - **Mutation** (Site-directed Mutagenesis, point mutation)
  - **4C-seq**
  - **Hi-C**
  - **Flow_Cytometry** (FACS)
  - **Cell_viability** (MTT, CCK-8, EdU)
  - **Migration** (Transwell, wound healing)
  - **Patch_clamp**
  - **Literature** (previously shown, has been reported)
======

======
CellLine 指在体外培养的细胞，包括：
- 连续细胞系（如 HEK293T、HeLa、MCF-7、NIH3T3）
- 原代细胞（如大鼠系膜细胞、HUVEC、原代成纤维细胞）
======

接下来你必须严格基于用户提供的文献内容和要求，提取其中所有明确提及或实验验证的“转录因子调控靶基因”关系。
"""

ROUND1_PROMPT = """
#文献获取#
以下是这篇关于调控关系的研究论文的全文：

=======
{abstract_text}
=======

#问题回答#
按照以下步骤，基于整篇论文逐步输出答案：

**Step 1：扫描论文中的调控事件**
逐段阅读论文，找出所有 TF 调控基因的事件。
- 对每个事件记录：TF（HGNC）、Target（HGNC）、Assay、CellLine，文献的原始语句；
- 如果是引用文献的关系，Assay标记Literature，必须给出Literatrue的序号；
- 对于给出的每个Cellline，**逐字抄录**其出现位置，在该Cellline上使用的Asaay和选取的原因；
- 如果检测到复合体（complex）字样，应当要从前后文找到该复合体两个以上亚基；
- 使用中文推理，文献引用为英文。
======
输出样例：
**KLF5 -> BIRC5 (survivin)**
   - 原因及证据：KLF5结合survivin核心启动子的GT-box并强烈诱导其活性；过表达KLF5上调survivin，siRNA敲低KLF5下调survivin。
   - 原文句子抄录：（此处略）
   - Assay：Luciferase, ChIP, EMSA, OE, siRNA, NB, WB
   - CellLine：
   - EU-4：证据1；
   - EU-8：证据2.
======

**Step 2：审查与过滤**
逐条检查 Step 1 的结果：
- 检查Step1输出的细胞系，如果不含人/小鼠/大鼠/猴的细胞系，拒绝这条调控关系；
- Literatrue按下面**两条原则**处理：
    a. 接受没有Cellline和Assay的Literatrue
    b. 若有Cellline和Assay，按正常的的审查标准审查是否符合要求
- 处理间接链（例如：TF→B→C）
    a. **保留**：如果论文中有实验证据（如 siRNA/OE/WB）证明 TF->C，而未明确/推测存在中间因子B，即便没有直接结合证据，也要保留这条关系
    b. **拒绝**：论文中确信TF->C通过中间因子B作用，拒绝这条关系，只保留TF->B
- 排除无具体蛋白的家族/结合位点
- 接受WB, siRNA等非直接结合证据，排除无实验证据的推测
- 排除蛋白互作（靶标必须是 DNA 序列）
- 拒绝计算/生物信息学方法的纯计算预测证据
- 拒绝高通量方法证据
输出最终保留的列表，每条注明保留/排除原因。
======
输出样例：
**最终保留的调控关系列表：**
| TF (HGNC) | Target (HGNC) | Assay | CellLine | 保留原因 |
**最终排除的调控关系列表：**
| TF (HGNC) | Target (HGNC) | Assay | CellLine | 排除原因 |
======"""

ROUND2_PROMPT = """基于上面的分析，将所有有效的TF-target 关系计数，输出总数，然后以 JSON 数组输出。

#额外规则#：
1.  'TF'：将前文中为复合体的蛋白拆成单体，拒绝不明确的（如蛋白质家族）TF；
2.  'assay'：分号分隔的检测方法（例如 "Luciferase;ChIP;WB"）。
    **Literature 规则**：
        a. 如果论文中引用了文献来支持某调控关系（如"previously shown"、"has been reported"、引用编号[xx]），必须在 assay 中包含 "Literature"；
        b. 如果该关系同时有本文实验验证，则同时列出实验方法和 Literature（例如 "EMSA;Mutation;Literature"）。
3. 'cellLine'：分号分隔的细胞系，仅包含论文中明确提及的细胞系（例如 "HEK293T" 或 "HeLa;MCF-7"）。对于纯 Literature 引用的关系，如果论文未提及该关系的实验细胞系，使用空字符串。
4. 不要输出重复的 (TF, Target) 对。每个 (TF, Target) 对在数组中只能出现一次。若多个实验支持同一对，将它们合并为一条记录。

#输出要求#
只输出 JSON 数组，不输出其他内容，必须遵循**正确格式**，一个例子如下：
[{"TF": "PROTEIN", "Target": "GENE", "assay": "Luciferase;ChIP", "cellLine": "HEK293T"}]"""

# ───────────────────────────────────────────────────────────
#  § 3  Local full-text loading  (基础设施，一般不改)
# ───────────────────────────────────────────────────────────


def load_local_fulltexts(pmids, source="fitz"):
    """Load full-text articles from local papers_txt/{source}/ directory.

    Args:
        pmids: list of PMID strings
        source: subdirectory name — "hybrid", "fitz", or "nougat"

    Returns:
        dict[str, dict]: pmid → parsed result with 'full_text' and 'sections' keys
    """
    source_dir = os.path.join(LOCAL_TEXT_DIR, source)
    if not os.path.isdir(source_dir):
        print(f"本地全文目录不存在: {source_dir}")
        return {}

    results = {}
    missing = []

    for pmid in pmids:
        txt_path = os.path.join(source_dir, f"{pmid}.txt")
        if not os.path.exists(txt_path):
            missing.append(pmid)
            continue

        with open(txt_path, "r", encoding="utf-8") as f:
            text = f.read().strip()

        if not text:
            missing.append(pmid)
            continue

        sections = _parse_markdown_sections(text)

        results[pmid] = {
            "title": "",
            "authors": [],
            "journal": "",
            "sections": sections,
            "full_text": text,
        }

    if results:
        print(f"本地全文加载 ({source}): {len(results)}/{len(pmids)} 篇")
    if missing:
        print(f"  未找到: {', '.join(missing[:10])}"
              + ("..." if len(missing) > 10 else ""))

    return results


def _parse_markdown_sections(text):
    """Parse Markdown text into (title, content) section tuples."""
    lines = text.split("\n")
    sections = []
    current_title = None
    current_lines = []

    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.+)", line)
        if m:
            if current_title is not None or current_lines:
                title = current_title or "Preamble"
                content = "\n".join(current_lines).strip()
                if content:
                    sections.append((title, content))
            current_title = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None or current_lines:
        title = current_title or "Full Paper"
        content = "\n".join(current_lines).strip()
        if content:
            sections.append((title, content))

    if not sections:
        sections = [("Full Paper", text)]

    return sections


# ───────────────────────────────────────────────────────────
#  § 4  LLM 客户端（基础设施，一般不改）
# ───────────────────────────────────────────────────────────

_client = None


def init_client(api_key=None):
    global _client
    key = api_key or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("缺少阿里云百炼 API Key，请设置环境变量 DASHSCOPE_API_KEY 或使用 --api-key 参数。")
    _client = OpenAI(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")


def _get_client():
    if _client is None:
        init_client()
    return _client


def _call_llm(model, temperature, messages, max_retries=3, seed=None):
    """调用阿里云百炼 API，传入 messages 列表，自动处理 429 限流重试。"""
    client = _get_client()
    kwargs = {"model": model, "temperature": temperature, "messages": messages}
    if seed is not None:
        kwargs["extra_body"] = {"seed": seed}
    print(f"  [_call_llm] temperature={temperature}, seed={seed}")
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs)
        except (RateLimitError, APIStatusError) as e:
            if isinstance(e, APIStatusError) and e.status_code != 429:
                raise
            delay = 2 ** attempt
            print(f"  API 限流 (429)，{delay}s 后重试 ({attempt + 1}/{max_retries})...")
            time.sleep(delay)
    return None


def clean_json_text(text):
    """从模型响应中提取有效 JSON 文本。"""
    if not text:
        return text
    text = text.strip()
    code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1).strip()
    bracket_match = re.search(r"(\[.*\])", text, re.DOTALL)
    if bracket_match:
        text = bracket_match.group(1)
    text = re.sub(r"//.*?$|#.*?$", "", text, flags=re.MULTILINE)
    text = re.sub(r",\s*([\]}])", r"\1", text)
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return text
    first = text.find("[")
    last = text.rfind("]")
    if first != -1 and last != -1 and first < last:
        return text[first:last + 1]
    return text


# ───────────────────────────────────────────────────────────
#  § 5  ★ 核心分析 — 构建 messages、调用模型、解析结果 ★
#
#  提示词工程改这里：
#    - 改 ROUND1_PROMPT / ROUND2_PROMPT 的内容 → 在 § 2
#    - 增加新的对话轮次 → 在这里往 messages 列表 append
#
#  添加第三轮的方法（注释中有示例）：
#    1. 在 § 2 定义 ROUND3_PROMPT
#    2. 在这里把模型回复和新提示词追加到 messages
#    3. 再调一次 _call_llm(messages)
# ───────────────────────────────────────────────────────────


def analyze_tf_interaction(abstract_text, model_name=DEFAULT_MODEL,
                           temperature=0, debug=False, seed=None):
    round1_text = ROUND1_PROMPT.replace("{abstract_text}", abstract_text)
    round2_text = ROUND2_PROMPT

    # ══════════════════════════════════════════════════════
    #  messages 就是发给模型的全部消息
    #  格式: [{"role": "user"/"assistant", "content": "..."}]
    #  加新轮次：append 新消息 → 调 _call_llm(messages)
    # ══════════════════════════════════════════════════════
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": round1_text},
    ]

    # ── 第一轮：自由文本分析 ──
    resp1 = _call_llm(model_name, temperature, messages, seed=seed)
    if resp1 is None:
        return {"error": "第一轮 API 限流重试已耗尽"}
    reply1 = resp1.choices[0].message.content

    # 把模型的回复加入消息历史
    messages.append({"role": "assistant", "content": reply1})

    # ── 第二轮：结构化 JSON 输出 ──
    messages.append({"role": "user", "content": round2_text})
    resp2 = _call_llm(model_name, temperature, messages, seed=seed)
    if resp2 is None:
        return {"error": "第二轮 API 限流重试已耗尽", "round1_analysis": reply1}
    reply2 = resp2.choices[0].message.content

    # ── 解析 JSON + 基因名标准化 ──
    clean = clean_json_text(reply2)
    try:
        parsed = json.loads(clean)
        norm_log = []
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict):
                    if "TF" in entry:
                        entry["TF"] = normalize_and_log(entry["TF"], _norm_tf, "TF", norm_log)
                    if "Target" in entry:
                        entry["Target"] = normalize_and_log(entry["Target"], _norm_target, "Target", norm_log)
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败: {exc}")
        if debug:
            return {"error": "parse_fail", "round1_analysis": reply1,
                    "round2_raw": reply2, "round2_clean": clean}
        return {"error": "parse_fail", "content": reply2}

    # ── 返回结果 ──
    if debug:
        return {
            "result": parsed,
            "round1_analysis": reply1,
            "round2_raw": reply2,
            "round2_clean": clean,
            "round1_usage": _extract_usage(resp1),
            "round2_usage": _extract_usage(resp2),
            "normalization_log": norm_log,
        }
    if norm_log:
        return {"result": parsed, "normalization_log": norm_log}
    return parsed


def _extract_usage(resp):
    """安全提取 API 响应的 token 用量。"""
    usage = getattr(resp, "usage", None)
    return {
        "request_id": getattr(resp, "id", ""),
        "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    }


# ───────────────────────────────────────────────────────────
#  § 6  批量运行（并行调度，一般不改）
# ───────────────────────────────────────────────────────────


def run_analysis(gold_standard, text_source, output_path, model_name,
                 temperature=0, workers=1, debug=False, sample_size=None,
                 pmid_seed=None, seed=None, pmids_filter=None):
    gs_data = load_gold_standard(gold_standard)
    if not gs_data:
        print(f"未找到金标准数据: {gold_standard}")
        return

    pmids = list(gs_data.keys())
    if pmids_filter is not None:
        pmids = [p for p in pmids_filter if p in gs_data]
        print(f"指定 PMID 过滤: {len(pmids)}/{len(pmids_filter)} 篇在金标准中")
    elif pmid_seed is not None:
        rng = random.Random(pmid_seed)
        rng.shuffle(pmids)
    if sample_size and sample_size < len(pmids) and pmids_filter is None:
        pmids = pmids[:sample_size]

    fulltexts = load_local_fulltexts(pmids, source=text_source)
    if not fulltexts:
        print(f"未找到任何本地全文 (source={text_source})")
        return

    tasks = [
        {"pmid": pmid, "abstract": fulltexts[pmid]["full_text"]}
        for pmid in pmids if pmid in fulltexts
    ]
    skipped = [p for p in pmids if p not in fulltexts]
    if skipped:
        print(f"跳过 {len(skipped)} 篇无全文的 PMID: {', '.join(skipped[:5])}...")

    if not tasks:
        print("未发现待处理任务。")
        return

    results = {}
    debug_info = {}
    worker_count = max(1, min(workers, len(tasks)))
    print(f"开始分析 {len(tasks)} 篇论文 (source={text_source}, workers={worker_count}, pmid_seed={pmid_seed}, seed={seed}, temp={temperature})...")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(analyze_tf_interaction, task["abstract"],
                            model_name=model_name, temperature=temperature,
                            debug=debug, seed=seed): task["pmid"]
            for task in tasks
        }
        pbar = _tqdm(total=len(tasks), desc="LLM 分析", unit="PMID",
                      disable=_tqdm is None) if True else None
        for future in as_completed(future_map):
            pmid = future_map[future]
            try:
                raw = future.result()
                if isinstance(raw, dict) and "result" in raw:
                    if "round1_analysis" in raw:
                        debug_info[pmid] = raw
                    elif "normalization_log" in raw:
                        debug_info.setdefault(pmid, {}).update(raw)
                    results[pmid] = raw["result"]
                elif debug and isinstance(raw, dict) and "round1_analysis" in raw:
                    debug_info[pmid] = raw
                    results[pmid] = raw.get("result", raw)
                else:
                    results[pmid] = raw
                count = len(results[pmid]) if isinstance(results[pmid], list) else 0
                if pbar:
                    pbar.set_postfix_str(f"PMID {pmid} → {count}条", refresh=True)
                else:
                    print(f"PMID {pmid}: {count} relationships")
            except Exception as exc:
                if pbar:
                    pbar.set_postfix_str(f"PMID {pmid} ✗ {exc}", refresh=True)
                else:
                    print(f"PMID {pmid}: ERROR - {exc}")
                results[pmid] = {"error": str(exc)}
            if pbar:
                pbar.update(1)
        if pbar:
            pbar.close()

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=4)

    if debug and debug_info:
        debug_path = output_path.replace(".json", "_debug.json")
        with open(debug_path, "w", encoding="utf-8") as handle:
            json.dump(debug_info, handle, ensure_ascii=False, indent=4)
        print(f"Debug info saved to: {debug_path}")

    print(f"分析完成！结果已存至: {output_path}")


# ───────────────────────────────────────────────────────────
#  § 7  命令行入口
# ───────────────────────────────────────────────────────────


def build_parser():
    parser = argparse.ArgumentParser(description="从论文全文提取 TF-Target 关系并保存 JSON 结果。")
    parser.add_argument("--gold-standard", default=DEFAULT_GOLD_STANDARD, help="金标准 TSV 文件路径")
    parser.add_argument("--text-source", default=DEFAULT_TEXT_SOURCE,
                        choices=["fitz", "hybrid", "nougat"], help="论文全文来源目录")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 文件路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="阿里云百炼 Qwen 模型名称")
    parser.add_argument("--api-key", default=None, help="阿里云百炼 API Key")
    parser.add_argument("--temperature", type=float, default=0, help="LLM temperature")
    parser.add_argument("--workers", type=int, default=1, help="并行 worker 数量")
    parser.add_argument("--sample-size", type=int, default=None, help="限制 PMID 数量 (快速测试)")
    parser.add_argument("--pmid-seed", type=int, default=None, help="PMID 随机抽取种子 (控制抽取顺序)")
    parser.add_argument("--seed", type=int, default=None, help="LLM 输出确定性种子 (控制模型输出)")
    parser.add_argument("--pmids", default=None,
                        help="指定 PMID 列表 (逗号分隔，如 18776923,22479354)，跳过 pmid_seed/sample_size")
    parser.add_argument("--debug", action="store_true", default=False,
                        help="保存中间 LLM 输出到 *_debug.json")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        init_client(args.api_key)
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    run_analysis(
        gold_standard=args.gold_standard,
        text_source=args.text_source,
        output_path=args.output,
        model_name=args.model,
        temperature=args.temperature,
        workers=args.workers,
        debug=args.debug,
        sample_size=args.sample_size,
        pmid_seed=args.pmid_seed,
        seed=args.seed,
        pmids_filter=[p.strip() for p in args.pmids.split(",")] if args.pmids else None,
    )


if __name__ == "__main__":
    main()

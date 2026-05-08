import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import dashscope
from dashscope import Generation

DEFAULT_INPUT = "abstracts_for_test.txt"
DEFAULT_OUTPUT = "analysis_results.json"
DEFAULT_MODEL = "qwen-max"


def get_api_key(cli_key=None):
    api_key = cli_key or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 DashScope API Key，请设置环境变量 DASHSCOPE_API_KEY 或使用 --api-key 参数。")
    return api_key


def parse_test_file(file_path):
    """从文本文件中提取 PMID 及结构化摘要。"""
    if not os.path.exists(file_path):
        print(f"错误: 找不到输入文件 {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 按 PMID 区块分割
    blocks = re.split(r"={10,}", content)
    tasks = []
    for block in blocks:
        pmid_m = re.search(r"PMID:\s*(\d+)", block)
        if not pmid_m:
            continue
        pmid = pmid_m.group(1).strip()

        # 提取 Abstract: 后面的所有内容
        abstract_m = re.search(r"Abstract:\s*-{3,}\s*(.*?)(?:\n(?=={10,})|\Z)", block, re.DOTALL)
        if not abstract_m:
            continue
        raw = abstract_m.group(1).strip()

        # 解析结构化段落: [LABEL]\ntext\n---
        sections = {}
        if raw.startswith("["):
            segs = re.split(r"\n---+\n?", raw)
            for seg in segs:
                label_m = re.match(r"\[([^\]]+)\]\s*\n(.*)", seg, re.DOTALL)
                if label_m:
                    sections[label_m.group(1).strip()] = label_m.group(2).strip()
            abstract_text = "\n\n".join(
                f"[{k}]\n{v}" for k, v in sections.items()
            ) if sections else raw
        else:
            abstract_text = raw
            sections = {}

        tasks.append({"pmid": pmid, "abstract": abstract_text, "sections": sections})

    return tasks


def clean_json_text(text):
    """从模型返回文本中提取出有效 JSON。"""
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
    text = re.sub(r",\s*([\]}])", r"", text)
    text = text.strip()

    if text.startswith("[") and text.endswith("]"):
        return text

    first = text.find("[")
    last = text.rfind("]")
    if first != -1 and last != -1 and first < last:
        return text[first:last + 1]

    return text


def extract_model_content(response):
    """兼容 dashscope SDK 中不同返回对象结构。"""
    try:
        choice = response.output.choices[0]
        if hasattr(choice, "message"):
            return choice.message.content
        return choice["message"]["content"]
    except Exception:
        return str(response)


def analyze_tf_interaction(abstract_text, model_name=DEFAULT_MODEL, temperature=0):
    # ── 第一轮：阅读理解 + 推理 ──
    round1_user = (
        "You are a bioinformatics expert. Read the following PubMed abstract carefully.\n"
        "Pay special attention to the METHODS and RESULTS sections.\n\n"
        f"{abstract_text}\n\n"
        "Analyze in plain English:\n\n"
        "A. List every regulatory relationship with specific experimental evidence. For each:\n"
        "   - REGULATOR and TARGET gene.\n"
        "   - Is the regulator a transcription factor? A TF is a protein that directly\n"
        "     or indirectly (via a complex) regulates gene transcription. Acceptable:\n"
        "     classical TFs (STAT3, TP53, NFKB1, RELA, MYC, GATA1, FOXO, JUN, FOS),\n"
        "     and transcriptional regulators (EZH2, HDAC1/3, EP300, MECP2).\n"
        "     NOT acceptable: hormones, growth factors, cytokines, drugs, or signaling\n"
        "     kinases (JNK, p38MAPK, PI3K, AKT). If borderline, mark as EXCLUDED.\n"
        "   - NF-kB: 'NF-kB' or 'NF-kappaB' → identify the subunit if mentioned\n"
        "     (NFKB1/p50, RELA/p65, REL, RELB, NFKB2/p52). If unspecified, use NFKB1.\n"
        "   - FUSION PROTEINS: NEVER output a fusion protein name as a gene symbol.\n"
        "     'MLL-AF9' → the genes are KMT2A (MLL) and MLLT3 (AF9). If the abstract\n"
        "     describes a fusion, output the INDIVIDUAL gene symbols separately.\n"
        "   - DIRECTION: Activation or Repression.\n"
        "   - CONFIDENCE (1-5):\n"
        "     5 = ChIP + reporter + mutagenesis (gold standard)\n"
        "     4 = ChIP or EMSA + knockdown/overexpression phenotype\n"
        "     3 = clear functional evidence but binding method unclear\n"
        "     2 = mentioned but experimental details sparse\n"
        "     1 = speculated or only mentioned in discussion\n"
        "   - EVIDENCE: specific experimental method.\n\n"
        "B. Gene name standardization: AP-2→TFAP2A, C/EBPbeta→CEBPB, YB-1→YBX1,\n"
        "   Nanog→NANOG, c-Myc→MYC, cPLA2→PLA2G4A, cox-2→PTGS2, dPRL→PRL.\n\n"
        "C. RANK your findings by confidence. Mark the SINGLE BEST relationship with "
        "the highest confidence and clearest evidence. This is the one that will go "
        "into the final output.\n\n"
        "D. If after analysis you found ZERO TF-target relationships, re-read the abstract\n"
        "   once more. Look for any sentence describing a TF regulating a gene.\n\n"
        "Do NOT output JSON yet. Just analyze in plain text."
    )

    resp1 = Generation.call(
        model=model_name,
        prompt=round1_user,
        temperature=temperature,
        result_format="message",
    )

    if getattr(resp1, "status_code", None) != 200:
        return {"error": f"Round1_API_Error: {getattr(resp1, 'status_code', 'unknown')}"}

    analysis = extract_model_content(resp1)

    # ── 第二轮：严格 JSON 输出（传入完整对话历史） ──
    round2_user = (
        "Now, based on YOUR analysis above, output the SINGLE BEST TF-target relationship "
        "— the one with the highest confidence score and clearest experimental evidence. "
        "If multiple have equal confidence, pick the one with the strongest direct evidence.\n\n"
        "Rules:\n"
        "1. Only include genuine transcription factors or transcriptional regulators.\n"
        "2. NEVER use fusion protein names (MLL-AF9, BCR-ABL, etc.) as gene symbols.\n"
        "3. Gene symbols MUST be standard HGNC human gene symbols.\n"
        "4. Target MUST be a specific gene symbol.\n"
        "5. Direction: 'Activation' or 'Repression' only.\n"
        "6. 'confidence': integer 1-5.\n"
        "7. 'evidence': experimental method name.\n"
        "8. NF-kB family: output the specific subunit. If unspecified, use NFKB1.\n\n"
        "Output ONLY a JSON array with ONE element, nothing else:\n"
        '[{"TF": "GENE", "Target": "GENE", "direction": "Activation", '
        '"confidence": 5, "evidence": "ChIP+luciferase"}]'
    )

    resp2 = Generation.call(
        model=model_name,
        messages=[
            {"role": "user", "content": round1_user},
            {"role": "assistant", "content": analysis},
            {"role": "user", "content": round2_user},
        ],
        temperature=temperature,
        result_format="message",
    )

    if getattr(resp2, "status_code", None) != 200:
        # 如果第二轮失败但第一轮成功，保留分析文本用于调试
        return {"error": f"Round2_API_Error: {getattr(resp2, 'status_code', 'unknown')}",
                "analysis": analysis}

    content = extract_model_content(resp2)
    clean = clean_json_text(content)

    try:
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败。错误: {e}")
        return {"error": "parse_fail", "content": content, "analysis": analysis}


def main():
    parser = argparse.ArgumentParser(description="从 PubMed 摘要提取 TF-Target 关系并保存 JSON 结果。")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入摘要文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 文件路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="DashScope 模型名称")
    parser.add_argument("--api-key", default=None, help="DashScope API Key")
    parser.add_argument("--temperature", type=float, default=0, help="LLM temperature")
    parser.add_argument("--workers", type=int, default=1, help="并行 worker 数量")
    args = parser.parse_args()

    try:
        dashscope.api_key = get_api_key(args.api_key)
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    tasks = parse_test_file(args.input)
    if not tasks:
        print("未发现待处理任务。")
        return

    results = {}
    n_workers = max(1, min(args.workers, len(tasks)))
    print(f"开始分析 {len(tasks)} 条摘要 (并行 workers={n_workers})...")

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        future_map = {
            executor.submit(
                analyze_tf_interaction, t["abstract"],
                model_name=args.model, temperature=args.temperature
            ): t["pmid"]
            for t in tasks
        }
        for future in as_completed(future_map):
            pmid = future_map[future]
            try:
                results[pmid] = future.result()
                n = len(results[pmid]) if isinstance(results[pmid], list) else 0
                print(f"PMID {pmid}: {n} relationships")
            except Exception as exc:
                print(f"PMID {pmid}: ERROR - {exc}")
                results[pmid] = {"error": str(exc)}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    print(f"分析完成！结果已存至: {args.output}")


if __name__ == "__main__":
    main()

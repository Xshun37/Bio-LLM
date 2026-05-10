import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import dashscope

try:
    from tqdm import tqdm as _tqdm
except ImportError:
    _tqdm = None
from dashscope import Generation
from bio_llm import normalize_tf as _norm_tf, normalize_target as _norm_target
from bio_llm.evaluation import normalize_and_log

DEFAULT_INPUT = "data/interim/abstracts_for_test.txt"
DEFAULT_OUTPUT = "outputs/analysis_results.json"
DEFAULT_MODEL = "qwq-plus"


def get_api_key(cli_key=None):
    api_key = cli_key or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 DashScope API Key，请设置环境变量 DASHSCOPE_API_KEY 或使用 --api-key 参数。")
    return api_key


def parse_test_file(file_path):
    """Parse PMID blocks and structured abstracts from the test file."""
    if not os.path.exists(file_path):
        print(f"错误: 找不到输入文件 {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as handle:
        content = handle.read()

    blocks = re.split(r"={10,}", content)
    tasks = []
    for block in blocks:
        pmid_match = re.search(r"PMID:\s*(\d+)", block)
        if not pmid_match:
            continue

        abstract_match = re.search(
            r"Abstract:\s*-{3,}\s*(.*?)(?:\n(?=={10,})|\Z)",
            block,
            re.DOTALL,
        )
        if not abstract_match:
            continue

        pmid = pmid_match.group(1).strip()
        raw_abstract = abstract_match.group(1).strip()

        sections = {}
        if raw_abstract.startswith("["):
            segments = re.split(r"\n---+\n?", raw_abstract)
            for segment in segments:
                label_match = re.match(r"\[\[?([^\]\[]+)\]\]?\s*\n(.*)", segment, re.DOTALL)
                if label_match:
                    sections[label_match.group(1).strip()] = label_match.group(2).strip()
            abstract_text = (
                "\n\n".join(f"[{label}]\n{text}" for label, text in sections.items())
                if sections
                else raw_abstract
            )
        else:
            abstract_text = raw_abstract

        tasks.append({"pmid": pmid, "abstract": abstract_text, "sections": sections})

    return tasks


def clean_json_text(text):
    """Extract valid JSON text from a model response."""
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


def _collect_stream(gen):
    """Collect streaming response into a single response-like object."""
    final = None
    for resp in gen:
        final = resp
    return final


def extract_model_content(response):
    """Handle different dashscope SDK response shapes."""
    try:
        choice = response.output.choices[0]
        if hasattr(choice, "message"):
            return choice.message.content
        return choice["message"]["content"]
    except Exception:
        return str(response)


def extract_reasoning_content(response):
    """Extract reasoning/thinking content from qvq models (if available)."""
    try:
        choice = response.output.choices[0]
        msg = choice.message if hasattr(choice, "message") else choice["message"]
        return getattr(msg, "reasoning_content", "") or ""
    except Exception:
        return ""


def _extract_usage(resp):
    """Safely extract token usage and request_id from a GenerationResponse."""
    usage = getattr(resp, "usage", None)
    return {
        "request_id": getattr(resp, "request_id", ""),
        "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
    }


def _call_llm(model, temperature, prompt=None, messages=None, max_retries=3):
    """Call DashScope Generation with exponential backoff on 429 rate-limit."""
    for attempt in range(max_retries):
        kwargs = {"model": model, "temperature": temperature,
                  "result_format": "message", "stream": True,
                  "incremental_output": False}
        if messages is not None:
            kwargs["messages"] = messages
        else:
            kwargs["prompt"] = prompt

        resp = _collect_stream(Generation.call(**kwargs))
        if getattr(resp, "status_code", None) == 429:
            delay = 2 ** attempt
            print(f"  API 限流 (429)，{delay}s 后重试 (attempt {attempt + 1}/{max_retries})...")
            time.sleep(delay)
            continue
        return resp
    return resp  # return last attempt even if it's still 429


def analyze_tf_interaction(abstract_text, model_name=DEFAULT_MODEL, temperature=0, debug=False):
    round1_user = (
        "You are a bioinformatics expert. Read the following PubMed abstract carefully.\n"
        "Pay special attention to the METHODS and RESULTS sections.\n\n"
        f"{abstract_text}\n\n"
        "You are analyzing a PubMed abstract to find DIRECT TF-target relationships.\n"
        "A transcription factor (TF) is a protein that directly or indirectly (via a\n"
        "complex) regulates gene transcription. ACCEPTABLE: classical TFs (STAT3, TP53,\n"
        "NFKB1, RELA, MYC, GATA1, FOXO, JUN, FOS, HNF1, HNF4, SP1, SP3) and\n"
        "transcriptional regulators (EZH2, HDAC1/3, EP300, MECP2). NOT acceptable:\n"
        "hormones (RA/retinoic acid, estrogen), growth factors, cytokines, drugs,\n"
        "signaling kinases (JNK, p38MAPK, PI3K, AKT, MEK1/MAP2K1), or metabolites.\n"
        "If a regulator is NOT a TF, exclude that relationship.\n\n"
        "Answer these specific questions based on the ENTIRE abstract:\n\n"
        "Q1: List every gene mentioned whose mRNA or protein level changes when\n"
        "    another gene/protein is knocked down or overexpressed. Give the exact\n"
        "    sentence from the abstract for each.\n\n"
        "Q2: Are there sentences containing the words 'mediates', 'mediated',\n"
        "    'via', 'through', 'by regulating', 'which in turn', or 'in turn'?\n"
        "    Copy those sentences verbatim. These describe the MECHANISM.\n\n"
        "Q3: The pattern 'X inhibits Y-mediated Z' means X acts on Y, and Y acts on Z.\n"
        "    The DIRECT relationship is X→Y (not X→Z). Check if the abstract has\n"
        "    any such pattern: 'A inhibits/blocks/suppresses B-mediated C'.\n"
        "    If yes, list A→B as the DIRECT relationship.\n\n"
        "Q4: List ALL valid direct regulatory relationships found in this abstract.\n"
        "    - Only include links where the regulator DIRECTLY alters the target.\n"
        "    - If TF regulates B which then affects C, the link is TF→B (not TF→C).\n"
        "    - Include only relationships with specific experimental evidence (confidence ≥ 2).\n"
        "    - Maximum 10 relationships. If more exist, keep the top 10 by confidence.\n"
        "    - For each relationship, clearly state the regulator, target, direction,\n"
        "      confidence score, and experimental evidence.\n\n"
        "    ISOFORM RULE: If a TF has opposite effects on different isoforms of the\n"
        "    SAME gene (e.g., activates isoform A, represses isoform B), you MAY\n"
        "    output TWO entries if you specify the isoform in the evidence field\n"
        "    (e.g., Target='LEF1', evidence='activates Lef-1 FL isoform'). Direction\n"
        "    MUST be 'Activation' or 'Repression' — NEVER use 'Regulation'.\n"
        "    If you cannot determine the direction, exclude the relationship.\n\n"
        "    AUTO-REGULATION RULE: A TF binding to the promoter of Gene X means\n"
        "    TF → X (NOT TF → TF). Only report TF → TF if the abstract uses explicit\n"
        "    self-regulation language (e.g., 'regulates its own expression',\n"
        "    'auto-regulates', 'binds its own promoter'). If a TF binds to a binding\n"
        "    site ON ANOTHER gene's promoter, that is TF→that_gene, never TF→TF.\n"
        "    A 'binding site for CEBPB' on the IL4 promoter means CEBPB→IL4, not\n"
        "    CEBPB→CEBPB. Do NOT confuse binding sites with self-regulation.\n\n"
        "For the final answer, also include:\n"
        "  - DIRECTION: Activation or Repression.\n"
        "  - CONFIDENCE (1-5):\n"
        "    5 = ChIP + reporter + mutagenesis\n"
        "    4 = ChIP or EMSA + knockdown/overexpression phenotype\n"
        "    3 = clear functional evidence but binding method unclear\n"
        "    2 = mentioned but sparse experimental details\n"
        "    1 = speculated or only in discussion\n"
        "  - EVIDENCE: specific experimental method.\n\n"
        "GENE NAME RULES — CRITICAL:\n"
        "  You MUST output official HGNC approved symbols for ALL genes. Do NOT use\n"
        "  protein names, common names, or literature aliases. You know the mapping:\n"
        "  ZBP-89 → ZNF148, SAF-1 → MAZ, Oct-1 → POU2F1, c-Myc → MYC,\n"
        "  NF-kB p65 → RELA, AP-2 → TFAP2A, C/EBPbeta → CEBPB, YB-1 → YBX1.\n"
        "  Convert EVERY gene to its HGNC symbol before listing it.\n\n"
        "FUSION PROTEINS: NEVER use fusion names. 'MLL-AF9' → KMT2A and MLLT3.\n\n"
        "C. If after analysis you found ZERO TF-target relationships, re-read the abstract\n"
        "   once more. Look for any sentence describing a TF regulating a gene.\n\n"
        "Do NOT output JSON yet. Just analyze in plain text."
    )

    resp1 = _call_llm(model_name, temperature, prompt=round1_user)
    if getattr(resp1, "status_code", None) != 200:
        err_msg = f"Round1_API_Error: {getattr(resp1, 'status_code', 'unknown')}"
        if debug:
            return {"error": err_msg, "round1_usage": _extract_usage(resp1)}
        return {"error": err_msg}

    analysis = extract_model_content(resp1)
    round2_user = (
        "Now, based on YOUR analysis above, output ALL valid TF-target relationships "
        "as a JSON array.\n\n"
        "Selection Priority:\n"
        "DIRECT OVER MEDIATED: If the abstract says 'A regulates C via B', output 'A -> B' (if A is a TF), not 'A -> C'.\n"
        "EXAMPLE: IFI16 represses hTERT by first repressing MYC → output IFI16 -> MYC, not IFI16 -> hTERT.\n\n"
        "Rules:\n"
        "0. The regulator MUST be a transcription factor. Exclude: hormones (RA/retinoic\n"
        "   acid, estrogen), growth factors, cytokines, drugs, signaling kinases (JNK,\n"
        "   p38MAPK, PI3K, AKT, MEK1/MAP2K1), and metabolites.\n"
        "1. Include ONLY direct regulatory relationships (confidence ≥ 2).\n"
        "2. Maximum 10 relationships. If you found more, keep only the top 10 by confidence.\n"
        "3. If ZERO valid TF-target relationships found, output an empty array: []\n"
        "4. NEVER use fusion protein names (MLL-AF9, BCR-ABL, etc.) as gene symbols.\n"
        "5. Gene symbols MUST be official HGNC human symbols. Convert ALL protein\n"
        "   names and aliases to HGNC: ZBP-89→ZNF148, SAF-1→MAZ, Oct-1→POU2F1,\n"
        "   c-Myc→MYC, NF-kB p65→RELA. You know these mappings — use them.\n"
        "6. Direction: 'Activation' or 'Repression' only. NEVER output 'Regulation'\n"
        "   or any other direction value. If direction is unclear, exclude the entry.\n"
        "7. 'confidence': integer 2-5 (do NOT include confidence-1 relationships).\n"
        "8. 'evidence': specific experimental method name.\n"
        "9. NF-kB family: output the specific subunit. If unspecified, use NFKB1.\n"
        "10. Do NOT output duplicate (TF, Target) pairs. Each (TF, Target) pair\n"
        "    must appear exactly ONCE in the array. If multiple experiments support\n"
        "    the same pair, merge them into a single entry with the best evidence.\n\n"
        "Output ONLY a JSON array (0-10 elements), nothing else:\n"
        '[{"TF": "GENE", "Target": "GENE", "direction": "Activation", '
        '"confidence": 5, "evidence": "ChIP+luciferase"}]'
    )

    resp2 = _call_llm(model_name, temperature, messages=[
        {"role": "user", "content": round1_user},
        {"role": "assistant", "content": analysis},
        {"role": "user", "content": round2_user},
    ])
    if getattr(resp2, "status_code", None) != 200:
        err_msg = f"Round2_API_Error: {getattr(resp2, 'status_code', 'unknown')}"
        if debug:
            return {
                "error": err_msg,
                "round1_analysis": analysis,
                "round1_reasoning": extract_reasoning_content(resp1),
                "round1_usage": _extract_usage(resp1),
                "round2_usage": _extract_usage(resp2),
            }
        return {"error": err_msg, "analysis": analysis}

    content = extract_model_content(resp2)
    clean = clean_json_text(content)
    try:
        parsed = json.loads(clean)
        # Post-process: normalize gene names through synonym maps
        norm_log = []
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict):
                    if "TF" in entry:
                        entry["TF"] = normalize_and_log(
                            entry["TF"], _norm_tf, "TF", norm_log)
                    if "Target" in entry:
                        entry["Target"] = normalize_and_log(
                            entry["Target"], _norm_target, "Target", norm_log)
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败。错误: {exc}")
        if debug:
            return {
                "error": "parse_fail",
                "round1_analysis": analysis,
                "round1_reasoning": extract_reasoning_content(resp1),
                "round2_raw": content,
                "round2_clean": clean,
                "round2_reasoning": extract_reasoning_content(resp2),
                "round1_usage": _extract_usage(resp1),
                "round2_usage": _extract_usage(resp2),
            }
        return {"error": "parse_fail", "content": content, "analysis": analysis}

    if debug:
        return {
            "result": parsed,
            "round1_analysis": analysis,
            "round1_reasoning": extract_reasoning_content(resp1),
            "round1_usage": _extract_usage(resp1),
            "round2_raw": content,
            "round2_clean": clean,
            "round2_reasoning": extract_reasoning_content(resp2),
            "round2_usage": _extract_usage(resp2),
            "normalization_log": norm_log,
        }
    if norm_log:
        return {"result": parsed, "normalization_log": norm_log}
    return parsed


def run_analysis(input_path, output_path, model_name, temperature=0, workers=1, debug=False):
    tasks = parse_test_file(input_path)
    if not tasks:
        print("未发现待处理任务。")
        return

    results = {}
    debug_info = {}
    worker_count = max(1, min(workers, len(tasks)))
    print(f"开始分析 {len(tasks)} 条摘要 (并行 workers={worker_count})...")

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                analyze_tf_interaction,
                task["abstract"],
                model_name=model_name,
                temperature=temperature,
                debug=debug,
            ): task["pmid"]
            for task in tasks
        }
        pbar = _tqdm(total=len(tasks), desc="LLM 分析", unit="PMID",
                      disable=_tqdm is None) if True else None
        for future in as_completed(future_map):
            pmid = future_map[future]
            try:
                raw_result = future.result()
                if isinstance(raw_result, dict) and "result" in raw_result:
                    if "round1_analysis" in raw_result:
                        debug_info[pmid] = raw_result
                    elif "normalization_log" in raw_result:
                        debug_info.setdefault(pmid, {}).update(raw_result)
                    results[pmid] = raw_result["result"]
                elif debug and isinstance(raw_result, dict) and "round1_analysis" in raw_result:
                    debug_info[pmid] = raw_result
                    results[pmid] = raw_result.get("result", raw_result)
                else:
                    results[pmid] = raw_result
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


def test_single(abstract_text, model_name=DEFAULT_MODEL, temperature=0):
    """Run analyze_tf_interaction in debug mode and pretty-print all outputs.

    Useful for iterating on prompt design with a single abstract.
    """
    result = analyze_tf_interaction(
        abstract_text, model_name=model_name, temperature=temperature, debug=True
    )

    print("=" * 60)
    print("ROUND 1 — Free-text Analysis")
    print("=" * 60)
    print(result.get("round1_analysis", "(not available)"))
    if "round1_usage" in result:
        u = result["round1_usage"]
        print(f"\n[Round 1 tokens: {u['input_tokens']} in, {u['output_tokens']} out"
              f" | request: {u['request_id']}]")

    print("\n" + "=" * 60)
    print("ROUND 2 — Raw Output (before cleaning)")
    print("=" * 60)
    print(result.get("round2_raw", "(not available)"))
    if "round2_usage" in result:
        u = result["round2_usage"]
        print(f"\n[Round 2 tokens: {u['input_tokens']} in, {u['output_tokens']} out"
              f" | request: {u['request_id']}]")

    print("\n" + "=" * 60)
    print("ROUND 2 — Cleaned JSON")
    print("=" * 60)
    print(result.get("round2_clean", "(not available)"))

    if "error" in result:
        print("\n" + "=" * 60)
        print(f"ERROR: {result['error']}")
    elif "result" in result:
        print("\n" + "=" * 60)
        print("FINAL PARSED RESULT:")
        print(json.dumps(result["result"], indent=2, ensure_ascii=False))

    return result


def build_parser():
    parser = argparse.ArgumentParser(description="从 PubMed 摘要提取 TF-Target 关系并保存 JSON 结果。")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入摘要文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 JSON 文件路径")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="DashScope 模型名称")
    parser.add_argument("--api-key", default=None, help="DashScope API Key")
    parser.add_argument("--temperature", type=float, default=0, help="LLM temperature")
    parser.add_argument("--workers", type=int, default=1, help="并行 worker 数量")
    parser.add_argument("--debug", action="store_true", default=False,
                        help="Save intermediate LLM outputs and token usage to *_debug.json")
    parser.add_argument("--test-abstract", default=None,
                        help="Test a single abstract interactively (for prompt iteration)")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        dashscope.api_key = get_api_key(args.api_key)
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    if args.test_abstract:
        test_single(args.test_abstract, model_name=args.model, temperature=args.temperature)
        return

    run_analysis(
        input_path=args.input,
        output_path=args.output,
        model_name=args.model,
        temperature=args.temperature,
        workers=args.workers,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()

